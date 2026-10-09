"""공구 마스터 입력기 — ISO 14649 형식 파일(CSV · XLSX)로 `tool` 테이블을 통째로 교체한다.

파일 형식은 docs/tool_iso14649.csv 와 같다. 첫 줄이 열 이름(TOOL_COLUMNS)이고, 빈 칸은 '모름'이라
NULL 로 들어간다(DEFAULT 로 채우지 않는다 — 모르는 코너 R 이 0 으로 저장되면 거짓이 된다).
대시보드 '공구 마스터' 화면의 업로드와, 수집기(Watchdog)가 감시 폴더에서 공구 마스터 파일을
발견했을 때 이 모듈을 부른다.

교체 절차
  1. 파일 전체를 먼저 검사한다. 한 칸이라도 틀리면 **DB 를 건드리지 않고** 이유를 돌려준다.
  2. 기존 공구를 모두 지우고 파일 내용을 넣는다 (한 트랜잭션).
  3. 가공 이력(workingstep)의 공구 연결을 T번호 기준으로 다시 맺는다.
  4. tool_id 를 1번부터 다시 매기고, DB 내용을 그대로 내보낸 CSV 를 보관소에
     '공구 마스터.csv' 로 남긴다. 무엇을 올렸든(CSV · XLSX) 보관본은 이 한 가지 형식이다.

옛 실험실 시트(실험실 공구 정리.xlsx — 회사명 / 공구 종류 / 규격 / 5축 가공기 열)는 받지 않는다.
그 시트에는 ISO 분류와 치수가 없어서, 받아들이면 조사해 채운 공구 마스터를 덮어써 버린다.
'보관소에서 원본 복원' 이 옛 시트를 감시 폴더로 되돌려 놓아도 공구 마스터는 그대로 남는다.
"""
import csv
import io
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import text

from DB.database import SessionLocal, engine
from DB.models import Tool, Workingstep
import vault_layout
from vault_manager import get_vault_path

# 공구 코드 표기·매칭 규칙은 backend/tool_codes.py 한 곳에만 둔다.
from tool_codes import canonical_tool_code, index_by_number, tool_code_number

# 보관소 루트(= 감시 폴더 루트와 같은 자리)에 남기는 공구 마스터 파일 이름
TOOL_MASTER_VAULT_FILENAME = vault_layout.TOOL_MASTER_FILENAME
TOOL_MASTER_VAULT_REL_PATH = vault_layout.tool_master_rel()

# 파일 열 = 테이블 열 (순서 포함). tool_id · created_at · updated_at 은 DB 가 채운다.
TOOL_COLUMNS = (
    "tool_code", "manufacturer", "model_code", "iso14649_class", "hand_of_cut",
    "cutter_diameter", "shank_diameter", "cutting_edge_length", "overall_length", "usable_length",
    "corner_radius", "point_angle", "approach_angle", "helix_angle",
    "number_of_teeth", "tool_material", "coating_type", "coolant_through",
    "stock_count", "gauge_length", "memo",
)
DB_FILLED_COLUMNS = ("tool_id", "created_at", "updated_at")

# ISO 14649-111:2010 공구 엔티티 + -10 의 touch_probe + -121(선반) 공구. 고른 근거는
# docs/tool_iso14649_sources.md. 오타가 그대로 들어가지 않도록 이 이름만 받는다.
ISO14649_CLASSES = frozenset({
    "ENDMILL", "BALLNOSE_ENDMILL", "BULLNOSE_ENDMILL", "PROFILED_END_MILL", "FACEMILL",
    "SHOULDERMILL", "SIDE_MILL", "T_SLOT_MILL", "DOVETAIL_MILL", "THREAD_MILL",
    "TWIST_DRILL", "TAPERED_DRILL", "SPADE_DRILL", "SPOTDRILL", "STEP_DRILL",
    "COUNTERBORE", "COUNTERSINK", "REAMING_CUTTING_TOOL", "TAPERED_REAMER",
    "COMBINED_DRILL_AND_REAMER", "ROTATING_BORING_CUTTING_TOOL", "TAPPING_CUTTING_TOOL",
    "COMBINED_DRILL_AND_TAP", "TOUCH_PROBE",
    "GENERAL_TURNING_TOOL", "GROOVING_TOOL", "KNURLING_TOOL", "TURNING_THREADING_TOOL",
})
# 표준에 해당 엔티티가 없어 따로 둔 이름 (마스터/게이지 툴)
LOCAL_CLASSES = frozenset({"REFERENCE_TOOL"})
ALLOWED_CLASSES = ISO14649_CLASSES | LOCAL_CLASSES
HAND_OF_CUT_VALUES = ("RIGHT", "LEFT", "NEUTRAL")

_DECIMAL_COLUMNS = {   # (전체 자릿수, 소수 자릿수) — 스키마의 DECIMAL(p, s)
    "cutter_diameter": (6, 3), "shank_diameter": (6, 3), "cutting_edge_length": (6, 2),
    "overall_length": (6, 2), "usable_length": (6, 2), "corner_radius": (5, 3),
    "point_angle": (5, 2), "approach_angle": (5, 2), "helix_angle": (4, 2), "gauge_length": (6, 3),
}
_INTEGER_COLUMNS = ("number_of_teeth", "stock_count")
_STRING_LIMITS = {"manufacturer": 50, "model_code": 100, "tool_material": 30, "coating_type": 50}
_TRUE = {"1", "TRUE", "Y", "YES"}
_FALSE = {"0", "FALSE", "N", "NO"}

# 옛 실험실 시트의 열 이름. 이것이 보이고 새 열이 없으면 옛 형식이다.
_LEGACY_MARKERS = ("5축 가공기", "공구 종류", "회사명")
# 감시 폴더에서 공구 마스터로 다룰 파일 이름 (옛 시트도 받아서 '옛 형식'이라고 알려 준다)
_TOOL_MASTER_NAME_HINTS = ("공구 마스터", "tool_iso14649", "실험실 공구 정리")


class ToolMasterError(Exception):
    """파일을 반영할 수 없다. problems 에 사람이 읽을 이유가 행 번호와 함께 담긴다."""

    def __init__(self, summary, problems=()):
        super().__init__(summary)
        self.summary = summary
        self.problems = list(problems)


@dataclass
class ImportResult:
    """parse_and_insert_tools 의 결과. ok 가 False 면 DB 는 그대로다."""
    ok: bool
    message: str
    problems: list = field(default_factory=list)
    inserted: int = 0
    mounted: int = 0
    deleted: int = 0
    linked: int = 0
    unlinked: int = 0
    resequenced: int = 0
    archived: str = ""

    @property
    def unmounted(self):
        return self.inserted - self.mounted


def is_tool_master_file(file_name):
    """수집기가 공구 마스터로 넘길 파일인지 (이름과 확장자로 판단)."""
    base = os.path.basename(file_name)
    ext = os.path.splitext(base)[1].lower()
    return ext in (".csv", ".xlsx") and any(h in base for h in _TOOL_MASTER_NAME_HINTS)


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------
def _cell_text(value):
    """엑셀 칸 값을 CSV 와 같은 글자로. 6.8 이 6.800000001 같은 꼴로 바뀌지 않게 한다."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return format(Decimal(repr(value)).normalize(), "f")
    return str(value).strip()


def _read_table(file_path):
    """(열 이름 목록, [(파일 행 번호, 칸 목록), ...]). 빈 줄은 건너뛴다."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            raw = [[_cell_text(v) for v in row] for row in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()
    elif ext == ".csv":
        # 엑셀에서 'CSV' 로 저장하면 한국어 Windows 는 CP949 로 쓴다. UTF-8 이 아니면 그쪽으로 읽는다.
        for encoding in ("utf-8-sig", "cp949"):
            try:
                with open(file_path, encoding=encoding, newline="") as f:
                    raw = [[c.strip() for c in row] for row in csv.reader(f)]
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ToolMasterError("파일 인코딩을 읽지 못했습니다 (UTF-8 또는 CP949 CSV 여야 합니다).")
    else:
        raise ToolMasterError(f"지원하지 않는 파일 형식입니다: {ext} (CSV 또는 XLSX)")

    if not raw:
        raise ToolMasterError("빈 파일입니다.")
    header = [h.strip() for h in raw[0]]
    while header and header[-1] == "":        # 엑셀이 남기는 오른쪽 빈 열
        header.pop()
    body = []
    for line_no, cells in enumerate(raw[1:], start=2):
        cells = list(cells) + [""] * max(len(header) - len(cells), 0)
        if any(c != "" for c in cells):
            body.append((line_no, cells))
    return header, body


def _to_decimal(raw, precision, scale):
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"숫자가 아닙니다 ('{raw}')")
    if not value.is_finite() or value < 0:
        raise ValueError(f"0 이상의 숫자여야 합니다 ('{raw}')")
    exponent = value.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > scale:
        raise ValueError(f"소수점 아래 {scale}자리까지만 됩니다 ('{raw}')")
    if value >= Decimal(10) ** (precision - scale):
        raise ValueError(f"{Decimal(10) ** (precision - scale)} 보다 작아야 합니다 ('{raw}')")
    return value


def _to_int(raw):
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"정수가 아닙니다 ('{raw}')")
    if value != value.to_integral_value() or value < 0:
        raise ValueError(f"0 이상의 정수여야 합니다 ('{raw}')")
    return int(value)


def _normalize_row(cells_by_name):
    """한 행을 DB 에 넣을 값으로 바꾼다. 문제는 (열, 이유) 목록으로 돌려준다."""
    row, problems = {}, []
    for col in TOOL_COLUMNS:
        raw = cells_by_name.get(col, "")
        try:
            if raw == "":
                row[col] = None
            elif col == "tool_code":
                code = canonical_tool_code(raw)
                if code is None:
                    raise ValueError(f"T번호 형식이 아닙니다 ('{raw}', 예: T06). 미장착이면 비워 두세요")
                row[col] = code
            elif col == "iso14649_class":
                value = raw.upper()
                if value not in ALLOWED_CLASSES:
                    raise ValueError(f"정해 둔 분류명이 아닙니다 ('{raw}')")
                row[col] = value
            elif col == "hand_of_cut":
                value = raw.upper()
                if value not in HAND_OF_CUT_VALUES:
                    raise ValueError(f"RIGHT / LEFT / NEUTRAL 중 하나여야 합니다 ('{raw}')")
                row[col] = value
            elif col in _DECIMAL_COLUMNS:
                row[col] = _to_decimal(raw, *_DECIMAL_COLUMNS[col])
            elif col in _INTEGER_COLUMNS:
                row[col] = _to_int(raw)
            elif col == "coolant_through":
                value = raw.upper()
                if value not in _TRUE | _FALSE:
                    raise ValueError(f"1 / 0 (또는 TRUE / FALSE) 이어야 합니다 ('{raw}')")
                row[col] = value in _TRUE
            else:
                limit = _STRING_LIMITS.get(col)
                if limit and len(raw) > limit:
                    raise ValueError(f"{limit}자를 넘습니다 ({len(raw)}자)")
                row[col] = raw
        except ValueError as e:
            row[col] = None
            problems.append((col, str(e)))

    # 장착 공구(T번호 있음)는 분류·직경·날 수가 반드시 있어야 한다 (DB 의 CHECK 와 같은 규칙)
    if row.get("tool_code"):
        for col in ("iso14649_class", "cutter_diameter", "number_of_teeth"):
            if row.get(col) is None and not any(p[0] == col for p in problems):
                problems.append((col, "장착 공구(T번호 있음)는 비워 둘 수 없습니다"))
    return row, problems


def parse_tool_master(file_path):
    """파일을 읽어 검사를 통과한 행 목록을 돌려준다. 틀린 곳이 있으면 ToolMasterError."""
    header, body = _read_table(file_path)

    if "iso14649_class" not in header and any(m in header for m in _LEGACY_MARKERS):
        raise ToolMasterError(
            "옛 실험실 공구 시트 형식이라 반영하지 않았습니다.",
            ["ISO 14649 형식(docs/tool_iso14649.csv 와 같은 열)의 CSV · XLSX 를 올려 주세요. "
             "대시보드 '공구 마스터' 화면에서 현재 공구 마스터를 CSV 로 내려받아 고쳐 쓰면 됩니다."])

    problems = []
    unknown = [h or "(빈 이름)" for h in header if h not in TOOL_COLUMNS and h not in DB_FILLED_COLUMNS]
    missing = [c for c in TOOL_COLUMNS if c not in header]
    duplicated = sorted({h for h in header if h and header.count(h) > 1})
    if unknown:
        problems.append(f"알 수 없는 열: {', '.join(unknown)}")
    if missing:
        problems.append(f"없는 열: {', '.join(missing)}")
    if duplicated:
        problems.append(f"두 번 나온 열: {', '.join(duplicated)}")
    if problems:
        raise ToolMasterError("열 구성이 공구 마스터 형식과 다릅니다.", problems)
    if not body:
        raise ToolMasterError("공구 행이 하나도 없습니다.")

    rows, seen_codes = [], {}
    for line_no, cells in body:
        if any(c != "" for c in cells[len(header):]):
            problems.append(f"{line_no}행: 열 이름이 없는 칸에 값이 있습니다")
        row, row_problems = _normalize_row(dict(zip(header, cells)))
        problems += [f"{line_no}행 {col}: {why}" for col, why in row_problems]
        code = row.get("tool_code")
        if code:
            if code in seen_codes:
                problems.append(f"{line_no}행 tool_code: {code} 가 {seen_codes[code]}행에도 있습니다")
            else:
                seen_codes[code] = line_no
        rows.append(row)
    if problems:
        raise ToolMasterError(f"{len(problems)}곳이 형식에 맞지 않습니다.", problems)
    return rows


# ---------------------------------------------------------------------------
# 내보내기 (대시보드 내려받기 · 보관소 보관본)
# ---------------------------------------------------------------------------
def _cell_out(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        text_value = format(value, "f")
        return text_value.rstrip("0").rstrip(".") if "." in text_value else text_value
    return str(value)


def rows_to_csv(rows):
    """행 목록(열 이름 → 값)을 업로드 형식 CSV(UTF-8 BOM, bytes)로. 내용이 같으면 바이트도 같다."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(TOOL_COLUMNS)
    writer.writerows([_cell_out(row[col]) for col in TOOL_COLUMNS] for row in rows)
    return buf.getvalue().encode("utf-8-sig")


def export_tool_master_csv():
    """현재 공구 마스터를 업로드 형식 그대로의 CSV(UTF-8 BOM, bytes)로 내보낸다.

    올린 파일을 그대로 다시 내려받으면 같은 내용이 되도록, tool_id 순서(= 올린 행 순서)를 지킨다.
    """
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT {', '.join(TOOL_COLUMNS)} FROM tool ORDER BY tool_id"))
        rows = [dict(zip(TOOL_COLUMNS, r)) for r in result]
    for row in rows:
        if row["coolant_through"] is not None:      # MySQL BOOLEAN 은 0/1 정수로 돌아온다
            row["coolant_through"] = bool(row["coolant_through"])
    return rows_to_csv(rows)


def archive_tool_master():
    """DB 의 공구 마스터를 보관소 루트에 '공구 마스터.csv' 로 남긴다. 보관 경로(실패하면 None).

    보관소를 감시 폴더로 되돌리면(보관소에서 원본 복원) 수집기가 이 파일로 공구 마스터를 다시 채운다.
    """
    try:
        # 내용을 먼저 다 만든 뒤 임시 파일에 쓰고 바꿔 끼운다. 파일을 먼저 열면 그 순간 기존
        # 보관본이 비워져, 내보내기가 실패했을 때 보관소에 빈 파일만 남는다.
        data = export_tool_master_csv()
        dest = get_vault_path(TOOL_MASTER_VAULT_FILENAME)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return dest
    except Exception as e:
        print(f"    - [경고] 공구 마스터 보관 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# 교체
# ---------------------------------------------------------------------------
def resequence_tool_ids():
    """
    tool_id를 1번부터 빈 번호 없이 다시 부여한다.
    기존 정렬 순서는 그대로 두고, 삭제 등으로 생긴 중간 빈 번호만 앞으로 당긴다.
    workingstep.tool_id 참조도 같은 트랜잭션에서 함께 옮기므로 가공 이력 연결은 유지된다.
    반환값: 번호가 실제로 바뀐 공구 수
    """
    with engine.begin() as conn:
        old_ids = [row[0] for row in conn.execute(text("SELECT tool_id FROM tool ORDER BY tool_id"))]
        if not old_ids:
            conn.execute(text("ALTER TABLE tool AUTO_INCREMENT = 1"))
            return 0

        next_auto_increment = len(old_ids) + 1
        changed = sum(1 for new_id, old_id in enumerate(old_ids, start=1) if new_id != old_id)
        if changed == 0:
            conn.execute(text(f"ALTER TABLE tool AUTO_INCREMENT = {next_auto_increment}"))
            return 0

        # PK 충돌을 피하기 위해 전체를 임시 번호 구간으로 밀어둔 뒤 1번부터 다시 부여한다.
        offset = max(old_ids) + 1000
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            conn.execute(text("UPDATE tool SET tool_id = tool_id + :off"), {"off": offset})
            conn.execute(text("UPDATE workingstep SET tool_id = tool_id + :off WHERE tool_id IS NOT NULL"),
                         {"off": offset})
            for new_id, old_id in enumerate(old_ids, start=1):
                conn.execute(text("UPDATE tool SET tool_id = :new_id WHERE tool_id = :old_id"),
                             {"new_id": new_id, "old_id": old_id + offset})
                conn.execute(text("UPDATE workingstep SET tool_id = :new_id WHERE tool_id = :old_id"),
                             {"new_id": new_id, "old_id": old_id + offset})
        finally:
            # 커넥션이 풀에 반납되기 전에 반드시 원복한다 (FK 검사 해제가 다른 작업으로 새는 것 방지)
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

        # 다음 신규 공구가 N+1번을 받도록 AUTO_INCREMENT도 맞춰준다.
        conn.execute(text(f"ALTER TABLE tool AUTO_INCREMENT = {next_auto_increment}"))
        return changed


def relink_workingsteps(db):
    """
    공구 마스터를 교체한 뒤 workingstep의 공구 연결을 tool_code 기준으로 다시 맺는다.
    전체 교체 과정에서 tool_id가 새로 부여되므로, XML이 남긴 xml_tool_code(없으면 tool_number)로 재매칭한다.
    이번 파일에 없는 공구 번호를 호출한 Workingstep은 tool_id가 NULL로 남지만
    tool_number / xml_tool_code는 그대로 보존되므로, 나중에 그 공구를 장착 공구로 넣으면 다시 연결된다.
    미장착 재고(tool_code 없음)는 T번호가 없으므로 연결 대상이 아니다.
    반환값: (연결된 Workingstep 수, 연결되지 않은 Workingstep 수)
    """
    # 매칭은 표기가 아니라 **번호**로 한다.
    #
    # 예전에는 `t.tool_code = CONCAT('T', w.tool_number)` 처럼 문자열을 만들어 비교했다.
    # 저장 표기를 T6 -> T06 으로 바꾸는 순간 그 비교는 전부 어긋나고, 연결이 조용히 끊겨
    # 화면에서는 공구가 '미연결' 로 보인다. 번호로 비교하면 어느 쪽이 T6 이든 T06 이든
    # 같은 공구로 이어진다 (옛 표기가 남아 있는 DB 에서도 그대로 동작한다).
    db.execute(text("UPDATE workingstep SET tool_id = NULL"))

    tool_by_number = index_by_number(
        db.execute(text("SELECT tool_code, tool_id FROM tool WHERE tool_code IS NOT NULL"))
    )
    linked = unlinked = 0
    for ws in db.query(Workingstep).all():
        # tool_number(정수)가 1순위, 그것이 비어 있으면 xml_tool_code 표기에서 번호를 뽑는다.
        number = ws.tool_number if ws.tool_number else tool_code_number(ws.xml_tool_code)
        tool_id = tool_by_number.get(number) if number is not None else None
        ws.tool_id = tool_id
        if tool_id is None:
            unlinked += 1
        else:
            linked += 1
    db.flush()
    return linked, unlinked


def replace_tool_master(rows):
    """검사를 통과한 행으로 공구 마스터를 통째로 교체한다. 통계 dict 를 돌려준다.

    ORM 객체가 아니라 Core INSERT 에 모든 열을 명시해 넣는다. 그래야 빈 칸이 확실히 NULL 로
    들어간다(열을 빼면 DB 가 DEFAULT 로 채운다 — 모르는 코너 R 이 0, 모르는 날 수가 1 이 된다).
    """
    db = SessionLocal()
    try:
        deleted = db.query(Tool).delete(synchronize_session=False)
        db.execute(Tool.__table__.insert(), [{col: row[col] for col in TOOL_COLUMNS} for row in rows])
        linked, unlinked = relink_workingsteps(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 삭제·삽입으로 밀린 번호를 1번부터 다시 정렬한다 (가공 이력 연결도 함께 옮긴다).
    # 교체는 이미 커밋됐으므로, 여기서 실패해도 교체 실패로 보고하지 않고 경고만 남긴다.
    try:
        resequenced = resequence_tool_ids()
    except Exception as e:
        print(f"    - [경고] tool_id 정리 실패 (공구 마스터 교체는 완료): {e}")
        resequenced = 0
    return {
        "deleted": deleted,
        "inserted": len(rows),
        "mounted": sum(1 for r in rows if r["tool_code"]),
        "linked": linked,
        "unlinked": unlinked,
        "resequenced": resequenced,
    }


def _read_or_report(file_path):
    """파일을 검사해 (행 목록, None) 또는 (None, 실패 ImportResult) 를 돌려준다."""
    print(f"\n  -> [Tool Inserter] 공구 마스터 파일 읽는 중: {os.path.basename(file_path)}")
    try:
        return parse_tool_master(file_path), None
    except ToolMasterError as e:
        print(f"    - [반영 안 함] {e.summary} 기존 공구 마스터는 그대로입니다.")
        for problem in e.problems[:30]:
            print(f"        · {problem}")
        if len(e.problems) > 30:
            print(f"        · … 외 {len(e.problems) - 30}곳")
        return None, ImportResult(ok=False, message=e.summary, problems=e.problems)
    except Exception as e:
        print(f"    - [오류] 공구 마스터 파일을 읽지 못했습니다: {e}")
        return None, ImportResult(ok=False, message=f"파일을 읽지 못했습니다: {e}")


def parse_and_insert_tools(file_path):
    """파일로 공구 마스터를 교체한다 (대시보드 업로드 — 사람이 고른 파일이라 항상 반영).

    ImportResult 를 돌려준다. 수집기는 sync_from_watch_folder 를 쓴다.
    """
    rows, failure = _read_or_report(file_path)
    if failure:
        return failure
    return _apply(rows)


def sync_from_watch_folder(file_path):
    """수집기용: 감시 폴더의 공구 마스터 파일을 반영하되, 반영할 이유가 없으면 건너뛴다.

    수집기는 기동할 때 감시 폴더의 기존 파일을 모두 다시 처리한다. 보관소 복원으로 놓인
    '공구 마스터.csv' 가 감시 폴더에 그대로 남아 있으면, 그 뒤 대시보드에서 올린 새 공구 마스터가
    재시작할 때마다 옛 파일로 조용히 되돌아간다. 그래서 두 경우는 반영하지 않는다.
      - 내용이 지금 DB 와 같다            → 이미 반영됨 (재시작마다 다시 쓰지 않는다)
      - 내용이 다른데 마지막으로 반영된 보관본보다 오래된 파일이다 → 되돌림 방지, 경고만
    DB 를 초기화한 뒤 복원하면 DB 가 비어 있고 파일은 보관본보다 오래되지 않으므로 다시 채워진다.
    """
    rows, failure = _read_or_report(file_path)
    if failure:
        return failure
    if rows_to_csv(rows) == export_tool_master_csv():
        print("    - 지금 공구 마스터와 내용이 같아 건너뜁니다.")
        return ImportResult(ok=True, message="이미 반영된 내용입니다.")
    vault_copy = get_vault_path(TOOL_MASTER_VAULT_FILENAME)
    if os.path.exists(vault_copy) and os.path.getmtime(vault_copy) > os.path.getmtime(file_path):
        print(f"    - [반영 안 함] 마지막으로 반영된 공구 마스터({vault_copy})보다 오래된 파일입니다. "
              f"이 파일로 바꾸려면 대시보드 '공구 마스터' 화면에서 올리세요.")
        return ImportResult(ok=False, message="마지막으로 반영된 공구 마스터보다 오래된 파일입니다.")
    return _apply(rows)


def _apply(rows):
    try:
        stats = replace_tool_master(rows)
    except Exception as e:
        print(f"    - [오류] 공구 DB 교체 실패 (기존 공구 마스터 유지): {e}")
        return ImportResult(ok=False, message=f"DB 교체에 실패했습니다: {e}")

    result = ImportResult(ok=True, message="공구 마스터를 교체했습니다.",
                          archived=archive_tool_master() or "", **stats)
    print(f"    - 공구 마스터 교체 완료: {result.inserted}건 (장착 {result.mounted} · "
          f"미장착 {result.unmounted}), 기존 {result.deleted}건 삭제")
    print(f"    - Workingstep 공구 재연결: 연결 {result.linked}건 / 미연결 {result.unlinked}건"
          f"(장착 공구에 없는 T번호)")
    if result.archived:
        print(f"    - 보관: {result.archived}")
    print("")
    return result
