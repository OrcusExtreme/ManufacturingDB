"""공구 마스터 입력기(tool_inserter)의 파일 검사 — DB 없이 돈다.

실제 MySQL 에 넣어 보는 검증은 verify_tool_csv.py 가 한다.
"""
import csv
import os
import sys
from decimal import Decimal

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from openpyxl import Workbook

import tool_inserter as ti
from tool_inserter import TOOL_COLUMNS, ToolMasterError, parse_tool_master

PROJECT_CSV = os.path.join(os.path.dirname(backend_dir), "docs", "tool_iso14649.csv")


def _csv_rows():
    with open(PROJECT_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def _write_csv(path, rows, encoding="utf-8-sig"):
    with open(path, "w", encoding=encoding, newline="") as f:
        csv.writer(f).writerows(rows)
    return str(path)


def _one_row(tmp_path, **values):
    """열은 모두 있고 값은 주어진 것만 채운 한 행짜리 CSV."""
    row = [str(values.get(c, "")) for c in TOOL_COLUMNS]
    return _write_csv(tmp_path / "one.csv", [list(TOOL_COLUMNS), row])


def _problems(path):
    with pytest.raises(ToolMasterError) as e:
        parse_tool_master(path)
    return " / ".join([e.value.summary] + e.value.problems)


def test_project_csv_passes():
    rows = parse_tool_master(PROJECT_CSV)
    assert len(rows) == 50
    assert sum(1 for r in rows if r["tool_code"]) == 18
    t06 = next(r for r in rows if r["tool_code"] == "T06")
    assert t06["iso14649_class"] == "ENDMILL" and t06["corner_radius"] == Decimal("0")


def test_blank_cells_stay_none_not_default(tmp_path):
    row = parse_tool_master(_one_row(tmp_path, memo="정체 불명"))[0]
    assert row["corner_radius"] is None          # DEFAULT 0.000 이 아니라 '모름'
    assert row["number_of_teeth"] is None        # DEFAULT 1 이 아니라 '모름'
    assert row["hand_of_cut"] is None and row["coolant_through"] is None
    assert row["memo"] == "정체 불명"


def test_cp949_csv_reads_the_same(tmp_path):
    path = _write_csv(tmp_path / "cp949.csv", _csv_rows(), encoding="cp949")
    assert parse_tool_master(path) == parse_tool_master(PROJECT_CSV)


def test_xlsx_reads_the_same(tmp_path):
    wb = Workbook()
    ws = wb.active
    header, *body = _csv_rows()
    ws.append(header)
    for row in body:   # 엑셀에서 숫자 칸은 숫자로 들어온다
        ws.append([float(v) if v.replace(".", "", 1).isdigit() and "." in v else
                   int(v) if v.isdigit() else (v or None) for v in row])
    path = tmp_path / "master.xlsx"
    wb.save(path)
    assert parse_tool_master(str(path)) == parse_tool_master(PROJECT_CSV)


def test_legacy_lab_sheet_is_refused(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["회사명 ", "공구 종류", "직경", "규격", "날 수", "총 개수", "사용", "5축 가공기"])
    ws.append(["OSG", "드릴", "6.8Ø", "GD-3D1068K", 2, 2, 1, "T16"])
    path = tmp_path / "실험실 공구 정리.xlsx"
    wb.save(path)
    assert "옛 실험실 공구 시트" in _problems(str(path))


def test_tool_code_is_canonicalized_and_duplicates_by_number_refused(tmp_path):
    assert parse_tool_master(_one_row(tmp_path, tool_code="t4", iso14649_class="endmill",
                                      cutter_diameter="6", number_of_teeth="2"))[0]["tool_code"] == "T04"
    row = {c: "" for c in TOOL_COLUMNS}
    a = dict(row, tool_code="T06", iso14649_class="ENDMILL", cutter_diameter="12", number_of_teeth="3")
    b = dict(a, tool_code="T6")       # 표기만 다른 같은 번호
    path = _write_csv(tmp_path / "dup.csv", [list(TOOL_COLUMNS), [a[c] for c in TOOL_COLUMNS],
                                             [b[c] for c in TOOL_COLUMNS]])
    assert "T06 가 2행에도" in _problems(path)


def test_bad_tool_code_refused(tmp_path):
    assert "T번호 형식이 아닙니다" in _problems(_one_row(tmp_path, tool_code="T19-TIP"))


@pytest.mark.parametrize("missing", ["iso14649_class", "cutter_diameter", "number_of_teeth"])
def test_mounted_tool_needs_class_diameter_teeth(tmp_path, missing):
    values = dict(tool_code="T98", iso14649_class="ENDMILL", cutter_diameter="10", number_of_teeth="2")
    values.pop(missing)
    assert f"{missing}: 장착 공구" in _problems(_one_row(tmp_path, **values))


def test_unmounted_tool_may_lack_everything(tmp_path):
    assert parse_tool_master(_one_row(tmp_path, cutter_diameter="9.7"))[0]["tool_code"] is None


@pytest.mark.parametrize("col,value,why", [
    ("iso14649_class", "BALL_ENDMILL", "정해 둔 분류명"),       # 옛 초안 이름
    ("hand_of_cut", "CW", "RIGHT / LEFT / NEUTRAL"),
    ("corner_radius", "0.1234", "소수점 아래 3자리"),
    ("cutter_diameter", "1000", "보다 작아야"),
    ("helix_angle", "100", "보다 작아야"),
    ("overall_length", "-5", "0 이상"),
    ("stock_count", "2.5", "정수"),
    ("coolant_through", "maybe", "1 / 0"),
    ("tool_material", "X" * 31, "30자"),
])
def test_value_errors_name_the_row_and_column(tmp_path, col, value, why):
    message = _problems(_one_row(tmp_path, **{col: value}))
    assert f"2행 {col}" in message and why in message


def test_accepted_spellings(tmp_path):
    row = parse_tool_master(_one_row(tmp_path, iso14649_class="facemill", hand_of_cut="right",
                                     coolant_through="TRUE", corner_radius="0.800"))[0]
    assert (row["iso14649_class"], row["hand_of_cut"], row["coolant_through"]) == ("FACEMILL", "RIGHT", True)
    assert row["corner_radius"] == Decimal("0.8")


def test_column_problems(tmp_path):
    header = [c for c in TOOL_COLUMNS if c != "memo"] + ["spec"]
    message = _problems(_write_csv(tmp_path / "cols.csv", [header, ["T01"] + [""] * (len(header) - 1)]))
    assert "알 수 없는 열: spec" in message and "없는 열: memo" in message


def test_value_in_unnamed_column_refused(tmp_path):
    row = [""] * len(TOOL_COLUMNS) + ["떨어진 값"]
    row[TOOL_COLUMNS.index("memo")] = "x"
    assert "열 이름이 없는 칸" in _problems(_write_csv(tmp_path / "extra.csv", [list(TOOL_COLUMNS), row]))


def test_db_filled_columns_are_ignored(tmp_path):
    header = ["tool_id"] + list(TOOL_COLUMNS) + ["created_at", "updated_at"]
    row = ["7"] + [""] * len(TOOL_COLUMNS) + ["2026-01-01", "2026-01-01"]
    row[1 + TOOL_COLUMNS.index("memo")] = "내보낸 파일을 그대로 다시 올림"
    assert parse_tool_master(_write_csv(tmp_path / "export.csv", [header, row]))[0]["memo"]


def test_tool_master_file_names():
    assert ti.is_tool_master_file("공구 마스터.csv")
    assert ti.is_tool_master_file("공구 마스터_20261002.xlsx")
    assert ti.is_tool_master_file("실험실 공구 정리.xlsx")      # 받아서 '옛 형식'이라고 알린다
    assert ti.is_tool_master_file("tool_iso14649.csv")
    assert not ti.is_tool_master_file("surface_roughness_01.csv")
    assert not ti.is_tool_master_file("공구 마스터.txt")


@pytest.mark.parametrize("value,out", [
    (Decimal("2.000"), "2"), (Decimal("0.000"), "0"), (Decimal("152.210"), "152.21"),
    (Decimal("100.00"), "100"), (Decimal("6.800"), "6.8"), (True, "1"), (False, "0"), (None, ""), (7, "7"),
])
def test_export_cell_format(value, out):
    assert ti._cell_out(value) == out


# ---------------------------------------------------------------------------
# 수집기의 반영 판단 (sync_from_watch_folder) — DB 를 부르는 곳은 가짜로 바꾼다
# ---------------------------------------------------------------------------
@pytest.fixture
def watch(tmp_path, monkeypatch):
    """감시 폴더 파일 · 보관본 · DB 내보내기를 흉내 낸다. calls 에 실제 교체가 기록된다."""
    state = {"db": b"", "calls": []}
    vault = tmp_path / "vault" / "공구 마스터.csv"
    vault.parent.mkdir()
    monkeypatch.setattr(ti, "get_vault_path", lambda *_: str(vault))
    monkeypatch.setattr(ti, "export_tool_master_csv", lambda: state["db"])
    monkeypatch.setattr(ti, "archive_tool_master", lambda: str(vault))

    def fake_replace(rows):
        state["calls"].append(len(rows))
        return {"deleted": 0, "inserted": len(rows), "mounted": sum(1 for r in rows if r["tool_code"]),
                "linked": 0, "unlinked": 0, "resequenced": 0}
    monkeypatch.setattr(ti, "replace_tool_master", fake_replace)

    incoming = tmp_path / "공구 마스터.csv"
    incoming.write_bytes(open(PROJECT_CSV, "rb").read())
    return state, vault, incoming


def test_watch_skips_same_content(watch):
    state, vault, incoming = watch
    state["db"] = incoming.read_bytes()            # DB 가 이미 이 내용
    result = ti.sync_from_watch_folder(str(incoming))
    assert result.ok and "이미 반영" in result.message and state["calls"] == []


def test_watch_refuses_file_older_than_last_master(watch):
    state, vault, incoming = watch
    state["db"] = b"different"                       # 대시보드에서 다른 마스터를 올린 뒤
    vault.write_bytes(b"newer master")
    os.utime(incoming, (1_000_000, 1_000_000))        # 감시 폴더에 남은 옛 파일
    os.utime(vault, (2_000_000, 2_000_000))
    result = ti.sync_from_watch_folder(str(incoming))
    assert not result.ok and "오래된 파일" in result.message and state["calls"] == []


def test_watch_applies_newer_file(watch):
    state, vault, incoming = watch
    state["db"] = b"different"
    vault.write_bytes(b"old master")
    os.utime(vault, (1_000_000, 1_000_000))
    os.utime(incoming, (2_000_000, 2_000_000))
    result = ti.sync_from_watch_folder(str(incoming))
    assert result.ok and state["calls"] == [50] and result.mounted == 18


def test_watch_refills_after_reset_and_restore(watch):
    state, vault, incoming = watch
    state["db"] = ti.rows_to_csv([])                 # 초기화로 DB 가 빈 상태
    vault.write_bytes(incoming.read_bytes())          # 복원은 보관본을 그대로 복사 (수정 시각도 같음)
    os.utime(vault, (1_500_000, 1_500_000))
    os.utime(incoming, (1_500_000, 1_500_000))
    result = ti.sync_from_watch_folder(str(incoming))
    assert result.ok and state["calls"] == [50]


def test_watch_applies_when_no_vault_copy(watch):
    state, vault, incoming = watch
    state["db"] = b"different"
    assert not vault.exists()
    assert ti.sync_from_watch_folder(str(incoming)).ok and state["calls"] == [50]


def test_rows_to_csv_matches_project_file():
    """파일 → 검사 → 다시 CSV 로 쓰면 원본과 바이트까지 같다 (같은 내용 판정의 근거)."""
    assert ti.rows_to_csv(parse_tool_master(PROJECT_CSV)) == open(PROJECT_CSV, "rb").read()
