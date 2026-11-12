"""Job 별 공구 지정(job_tool_assignment) 규칙 — 단일 출처.

업로드 화면(frontend/components/data_upload.py) · Job 워크스페이스 · REST API · 공구 마스터 교체
(tool_inserter) 가 모두 이 모듈을 쓴다.

용어
  자동 연결  공구 마스터에서 NC 의 T번호와 같은 번호로 장착된 공구. workingstep.tool_id 와 같은 규칙이다.
  지정       사람이 업로드 화면이나 Job 화면에서 고른 공구 (job_tool_assignment 한 행).

Job 의 T번호별 공구는 '지정이 있으면 지정, 없으면 자동 연결' 이다. 지정 행이 있는데 tool_id 가 비어
있으면 자동 연결로 대신하지 않는다. 사람이 '공구 마스터에 없는 공구' 라고 확인했거나, 지정한 공구가
마스터 교체로 사라져 스냅샷만 남은 경우라서, 지금 그 T번호에 꽂힌 공구를 대신 내세우면 틀린 기록이 된다.

왜 따로 두나
  workingstep 은 Workplan(NC 프로그램)에 속해 같은 NC 를 돌린 Job 들이 함께 쓴다. 공구 마스터를 교체하면
  relink_workingsteps 가 모든 워킹스텝을 '지금 매거진' 기준으로 다시 잇는다. 그래서 '그 Job 에서 실제로
  꽂혀 있던 공구' 는 workingstep 에 적을 수 없고, Job 단위로 여기에 남긴다.
"""
import json
import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import bindparam, inspect, text

from tool_codes import format_tool_code, index_by_number
# 제원 글자 표기는 공구 마스터 CSV 내보내기와 같은 규칙(_cell_out)을 쓴다. 스냅샷과 새 마스터를
# 글자로 비교하므로 규칙이 두 곳으로 갈라지면 같은 공구를 다른 공구로 보게 된다.
from tool_inserter import TOOL_COLUMNS, _cell_out

TABLE = "job_tool_assignment"
SOURCE_MANUAL = "MANUAL"

# 스냅샷에 남기는 제원 = 공구 마스터 파일의 열에서 재고·비고를 뺀 것 (수시로 바뀌는 관리 값이다).
SNAPSHOT_COLUMNS = tuple(c for c in TOOL_COLUMNS if c not in ("stock_count", "memo"))
# 마스터 교체 뒤 '같은 공구' 를 찾을 때 비교하는 제원.
#   tool_code     매거진 포트 번호라 공구를 다른 포트로 옮기거나 재고로 빼면 바뀐다 (동률일 때만 본다)
#   gauge_length  홀더에 다시 물리면 바뀐다
MATCH_COLUMNS = tuple(c for c in SNAPSHOT_COLUMNS if c not in ("tool_code", "gauge_length"))


# ---------------------------------------------------------------------------
# 제원 표기
# ---------------------------------------------------------------------------
def spec_text(value):
    """제원 한 칸을 공구 마스터 CSV 와 같은 글자로 ('' 는 모름). 판다스가 넘기는 float · NaN 도 받는다."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        value = Decimal(repr(value))
    return _cell_out(value)


def snapshot_of(tool_row):
    """공구 한 행(열 이름 -> 값)의 제원 스냅샷. 값은 공구 마스터 CSV 와 같은 글자 표기다."""
    return {col: spec_text(tool_row.get(col)) for col in SNAPSHOT_COLUMNS}


def _match_key(row):
    return tuple(spec_text(row.get(col)) for col in MATCH_COLUMNS)


def tool_label(row):
    """공구 한 행을 한 줄로. 'T06 장착 · WIDIN · ENDMILL Ø12 Z3 · DLC' / '재고 · OSG GP3D1100K · TWIST_DRILL Ø10 Z2'."""
    if not row:
        return "-"
    code = spec_text(row.get("tool_code"))
    where = f"{code} 장착" if code else "재고"
    maker = " ".join(v for v in (spec_text(row.get("manufacturer")), spec_text(row.get("model_code"))) if v)
    shape = [spec_text(row.get("iso14649_class")) or "분류 미상"]
    diameter = spec_text(row.get("cutter_diameter"))
    if diameter:
        shape.append(f"Ø{diameter}")
    radius = spec_text(row.get("corner_radius"))
    if radius and radius != "0":
        shape.append(f"R{radius}")
    teeth = spec_text(row.get("number_of_teeth"))
    if teeth:
        shape.append(f"Z{teeth}")
    parts = [where, maker or "제조사 미상", " ".join(shape)]
    coating = spec_text(row.get("coating_type"))
    if coating:
        parts.append(coating)
    return " · ".join(parts)


def _as_dict(raw):
    """JSON 칸 값. MySQL 을 text() 로 읽으면 문자열, ORM 으로 읽으면 dict 로 온다."""
    if raw is None or isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _iso(value):
    if value is None:
        return None
    return value.isoformat(sep=" ") if hasattr(value, "isoformat") else str(value)


# ---------------------------------------------------------------------------
# 공구 번호 읽기 (업로드한 파일 · 이미 들어간 Job)
# ---------------------------------------------------------------------------
def _span(values):
    return (min(values), max(values)) if values else None


def tools_called_in_nc(nc_input):
    """NC 가 부른 공구를 가공 순서대로. {번호: {"steps": 스텝 수, "spindle": (최소, 최대), "feed": (최소, 최대)}}

    워킹스텝 분리 규칙(parsers/nc_parser.py)을 그대로 쓰므로 스텝 수가 수집 뒤 워킹스텝과 같다.
    경로 · 바이트열 · 문자열을 모두 받아, 파일을 디스크에 쓰기 전(업로드 화면)에도 쓸 수 있다.
    공구를 부르기 전에 깎은 구간은 0번이다 (워킹스텝과 같은 규칙).
    """
    from parsers.nc_parser import parse_nc_cutting_conditions

    collected = {}
    for step in parse_nc_cutting_conditions(nc_input):
        number = step["tool_number"] if step["tool_number"] is not None else 0
        info = collected.setdefault(number, {"steps": 0, "spindle": [], "feed": []})
        info["steps"] += 1
        if step["spindle_speed"] is not None:
            info["spindle"].append(step["spindle_speed"])
        if step["feed_rate"] is not None:
            info["feed"].append(step["feed_rate"])
    return {number: {"steps": info["steps"], "spindle": _span(info["spindle"]),
                     "feed": _span(info["feed"])}
            for number, info in collected.items()}


def tools_called_in_xml(xml_bytes):
    """XML(WorkModel)의 <ToolNumbers> 를 호출 순서대로. NC 가 없을 때만 쓴다 (스텝 수는 모른다)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_bytes)
    if root.tag != "WorkModel":
        return {}
    called = {}
    for node in root.findall("ToolNumbers/int"):
        raw = (node.text or "").strip()
        if raw.isdigit() and int(raw) not in called:
            called[int(raw)] = {"steps": None, "spindle": None, "feed": None}
    return called


def tools_in_job_workplan(conn, job_id):
    """이미 들어간 Job 의 워킹스텝에서 공구를 가공 순서대로. plan_tool_id 는 워킹스텝의 자동 연결이다."""
    rows = conn.execute(text("""
        SELECT ws.tool_number, COUNT(*) AS steps, MIN(ws.step_order) AS first_order,
               MIN(ws.spindle_speed) AS s_min, MAX(ws.spindle_speed) AS s_max,
               MIN(ws.feed_rate) AS f_min, MAX(ws.feed_rate) AS f_max,
               MAX(ws.tool_id) AS plan_tool_id
        FROM workingstep ws
        JOIN job j ON j.workplan_id = ws.workplan_id
        WHERE j.job_id = :jid
        GROUP BY ws.tool_number
        ORDER BY first_order
    """), {"jid": job_id}).mappings().all()
    return {int(r["tool_number"]): {
        "steps": int(r["steps"]),
        "spindle": (r["s_min"], r["s_max"]) if r["s_min"] is not None else None,
        "feed": (r["f_min"], r["f_max"]) if r["f_min"] is not None else None,
        "plan_tool_id": int(r["plan_tool_id"]) if r["plan_tool_id"] is not None else None,
    } for r in rows}


# ---------------------------------------------------------------------------
# 공구 마스터 · 지정 읽기
# ---------------------------------------------------------------------------
def has_table(conn):
    """job_tool_assignment 가 있는지. 스키마 보정(schema_patch) 전의 DB 에서도 화면이 깨지지 않게 한다."""
    return inspect(conn).has_table(TABLE)


def load_tools(conn):
    """공구 마스터 전체 {tool_id: 행}. 장착 공구(T번호 순) 다음에 미장착 재고."""
    cols = ", ".join(SNAPSHOT_COLUMNS)
    rows = conn.execute(text(
        f"SELECT tool_id, {cols} FROM tool ORDER BY tool_code IS NULL, tool_code, tool_id"
    )).mappings().all()
    return {int(r["tool_id"]): dict(r) for r in rows}


def auto_tool_ids(tools):
    """T번호 -> 그 번호로 장착된 공구의 tool_id (workingstep 자동 연결과 같은 규칙, 번호로 맞춘다)."""
    return index_by_number((row.get("tool_code"), tool_id)
                           for tool_id, row in tools.items() if row.get("tool_code"))


def load_assignments(conn, job_id):
    """Job 의 지정 {T번호: {tool_id, source, tool_snapshot, assigned_by, assigned_at}}."""
    if not has_table(conn):
        return {}
    rows = conn.execute(text(
        f"SELECT tool_number, tool_id, source, tool_snapshot, assigned_by, assigned_at "
        f"FROM {TABLE} WHERE job_id = :jid"
    ), {"jid": job_id}).mappings().all()
    return {int(r["tool_number"]): {
        "tool_id": int(r["tool_id"]) if r["tool_id"] is not None else None,
        "source": r["source"],
        "tool_snapshot": _as_dict(r["tool_snapshot"]),
        "assigned_by": r["assigned_by"],
        "assigned_at": r["assigned_at"],
    } for r in rows}


# ---------------------------------------------------------------------------
# 쓰기
# ---------------------------------------------------------------------------
def save_assignments(conn, job_id, picks, assigned_by=None, now=None):
    """사람이 고른 공구를 기록한다. picks: {T번호: tool_id 또는 None(공구 마스터에 없는 공구)}.

    지정할 때의 제원을 스냅샷으로 함께 남긴다. 트랜잭션은 호출한 쪽이 쥔다 (engine.begin()).
    기록한 T번호 목록을 돌려준다.
    """
    if not picks:
        return []
    now = now or datetime.now()
    wanted = sorted({int(tid) for tid in picks.values() if tid is not None})
    tools = {}
    if wanted:
        query = text(f"SELECT tool_id, {', '.join(SNAPSHOT_COLUMNS)} FROM tool WHERE tool_id IN :ids"
                     ).bindparams(bindparam("ids", expanding=True))
        tools = {int(r["tool_id"]): dict(r) for r in conn.execute(query, {"ids": wanted}).mappings()}

    written = []
    for number, tool_id in picks.items():
        tool_id = int(tool_id) if tool_id is not None else None
        if tool_id is not None and tool_id not in tools:
            # 화면을 띄운 사이에 공구 마스터가 교체되면 tool_id 가 다른 공구를 가리킬 수 있다.
            raise ValueError(f"{format_tool_code(number)}: 공구 마스터에 tool_id {tool_id} 가 없습니다. "
                             "공구 마스터가 방금 바뀌었다면 화면을 새로 고친 뒤 다시 고르세요.")
        snapshot = snapshot_of(tools[tool_id]) if tool_id is not None else None
        params = {"jid": job_id, "n": int(number)}
        # MySQL·SQLite 공통으로 쓰려고 upsert 대신 지우고 넣는다 (같은 트랜잭션이라 중간 상태는 안 보인다).
        conn.execute(text(f"DELETE FROM {TABLE} WHERE job_id = :jid AND tool_number = :n"), params)
        conn.execute(text(
            f"INSERT INTO {TABLE} (job_id, tool_number, tool_id, source, tool_snapshot, assigned_by, assigned_at) "
            f"VALUES (:jid, :n, :tid, :src, :snap, :by, :at)"
        ), {**params, "tid": tool_id, "src": SOURCE_MANUAL,
            "snap": json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
            "by": assigned_by, "at": now})
        written.append(int(number))
    return written


def relink_job_tool_assignments(conn):
    """공구 마스터를 교체한 뒤 지정의 tool_id 를 스냅샷 제원으로 다시 잇는다. (연결, 미연결) 수.

    교체(tool_inserter.replace_tool_master)는 공구를 모두 지우고 새로 넣으므로 tool_id 가 전부 바뀐다.
    지정마다 스냅샷 제원(MATCH_COLUMNS)이 같은 공구를 찾아
      - 정확히 한 행이면 그 행에 잇는다.
      - 여러 행이면(제원이 똑같은 공구가 둘 이상) T번호까지 같은 한 행일 때만 잇는다.
      - 없으면 tool_id 를 비워 둔다. 스냅샷이 '그때 쓴 공구' 의 근거로 남는다.
    스냅샷이 없는 행('공구 마스터에 없음' 으로 확인한 지정)은 건드리지 않는다.
    """
    if not has_table(conn):
        return 0, 0
    # FK(ON DELETE SET NULL)가 이미 비웠겠지만, 외래키가 없는 DB 에서도 옛 번호가 엉뚱한 새 공구를
    # 가리키지 않도록 스냅샷이 있는 행은 모두 비우고 다시 찾는다 (relink_workingsteps 와 같은 방식).
    conn.execute(text(f"UPDATE {TABLE} SET tool_id = NULL WHERE tool_snapshot IS NOT NULL"))

    by_key = {}
    for tool_id, row in load_tools(conn).items():
        by_key.setdefault(_match_key(row), []).append((tool_id, spec_text(row.get("tool_code"))))

    linked = unlinked = 0
    rows = conn.execute(text(
        f"SELECT job_id, tool_number, tool_snapshot FROM {TABLE} WHERE tool_snapshot IS NOT NULL"
    )).all()
    for job_id, number, raw in rows:
        snapshot = _as_dict(raw) or {}
        candidates = by_key.get(_match_key(snapshot), [])
        if len(candidates) > 1:
            candidates = [c for c in candidates if c[1] == snapshot.get("tool_code", "")]
        if len(candidates) == 1:
            conn.execute(text(f"UPDATE {TABLE} SET tool_id = :tid WHERE job_id = :jid AND tool_number = :n"),
                         {"tid": candidates[0][0], "jid": job_id, "n": number})
            linked += 1
        else:
            unlinked += 1
    return linked, unlinked


# ---------------------------------------------------------------------------
# Job 의 T번호별 공구 (화면 · API)
# ---------------------------------------------------------------------------
def job_tool_rows(conn, job_id):
    """Job 의 T번호별 공구를 가공 순서대로. 지정이 있으면 지정, 없으면 워킹스텝의 T번호 자동 연결.

    각 행 (JSON 으로 바로 내보낼 수 있는 값만):
      tool_number, tool_code(T06), steps(워킹스텝 수), in_workplan(현재 워킹스텝에 있는 번호인지),
      basis('지정' | '자동' | '없음'), tool_id(지금 공구 마스터 행, 없으면 None),
      in_master(지금 공구 마스터에 있는지), label, spec(제원, CSV 표기), assigned_by, assigned_at
    """
    called = tools_in_job_workplan(conn, job_id)
    assigned = load_assignments(conn, job_id)
    tools = load_tools(conn)

    numbers = list(called) + sorted(n for n in assigned if n not in called)
    rows = []
    for number in numbers:
        info = called.get(number, {})
        pick = assigned.get(number)
        if pick is not None:
            basis = "지정"
            tool_id = pick["tool_id"]
            if tool_id is not None and tool_id in tools:
                spec, in_master = tools[tool_id], True
            else:
                spec, in_master = pick["tool_snapshot"], False   # 스냅샷만 남았거나 '마스터에 없음'
        else:
            tool_id = info.get("plan_tool_id")
            if tool_id is not None and tool_id in tools:
                basis, spec, in_master = "자동", tools[tool_id], True
            else:
                basis, spec, in_master = "없음", None, False
        rows.append({
            "tool_number": number,
            "tool_code": format_tool_code(number),
            "steps": info.get("steps"),
            "in_workplan": number in called,
            "basis": basis,
            "tool_id": tool_id if in_master else None,
            "in_master": in_master,
            "label": tool_label(spec) if spec else None,
            "spec": {col: spec_text(spec.get(col)) for col in SNAPSHOT_COLUMNS} if spec else None,
            "assigned_by": pick["assigned_by"] if pick else None,
            "assigned_at": _iso(pick["assigned_at"]) if pick else None,
        })
    return rows
