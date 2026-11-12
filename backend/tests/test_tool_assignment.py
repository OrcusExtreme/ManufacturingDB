"""Job 별 공구 지정 (backend/tool_assignment.py).

실제 MySQL 은 건드리지 않는다. 인메모리 SQLite 에 모델 테이블을 만들고, tool 테이블은 MySQL 전용
기본값(ON UPDATE CURRENT_TIMESTAMP) 때문에 모델로 만들 수 없어 공구 마스터 열 그대로 직접 만든다.
"""
import os
import sys
from datetime import datetime
from decimal import Decimal

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from DB.database import Base
from DB.models import Job, Part, Workingstep, Workplan
import tool_assignment as ta
from parsers.nc_parser import parse_nc_cutting_conditions

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
O1110_PATH = os.path.join(FIXTURES, "O1110_20261111_mmas.nc")

TOOL_TABLE_DDL = """
CREATE TABLE tool (
    tool_id INTEGER PRIMARY KEY, tool_code VARCHAR(20), manufacturer VARCHAR(50), model_code VARCHAR(100),
    iso14649_class VARCHAR(50), hand_of_cut VARCHAR(10), cutter_diameter NUMERIC(6,3),
    shank_diameter NUMERIC(6,3), cutting_edge_length NUMERIC(6,2), overall_length NUMERIC(6,2),
    usable_length NUMERIC(6,2), corner_radius NUMERIC(5,3), point_angle NUMERIC(5,2),
    approach_angle NUMERIC(5,2), helix_angle NUMERIC(4,2), number_of_teeth INTEGER,
    tool_material VARCHAR(30), coating_type VARCHAR(50), coolant_through BOOLEAN,
    stock_count INTEGER, gauge_length NUMERIC(6,3), memo TEXT
)"""

# docs/공구 마스터 CSV 에서 고른 행들. tool_id 는 일부러 띄엄띄엄 둔다.
T06_WIDIN = dict(tool_code="T06", manufacturer="WIDIN", iso14649_class="ENDMILL", hand_of_cut="RIGHT",
                 cutter_diameter=12, shank_diameter=12, corner_radius=0, helix_angle=45,
                 number_of_teeth=3, tool_material="CARBIDE", coating_type="DLC", stock_count=5)
T01_YG = dict(tool_code="T01", manufacturer="YG-1", iso14649_class="ENDMILL", hand_of_cut="RIGHT",
              cutter_diameter=2, shank_diameter=6, cutting_edge_length=8, overall_length=60,
              corner_radius=0, helix_angle=45, number_of_teeth=3, tool_material="CARBIDE", stock_count=5)
STOCK_OSG_DRILL = dict(manufacturer="OSG", model_code="GP3D1100K", iso14649_class="TWIST_DRILL",
                       hand_of_cut="RIGHT", cutter_diameter=10, shank_diameter=10, cutting_edge_length=47,
                       overall_length=89, usable_length=29, point_angle=140, helix_angle=30,
                       number_of_teeth=2, tool_material="CARBIDE", coating_type="WDI", stock_count=3)
STOCK_OSG_ENDMILL = dict(manufacturer="OSG", iso14649_class="ENDMILL", hand_of_cut="RIGHT",
                         cutter_diameter=12, shank_diameter=12, cutting_edge_length=26, overall_length=75,
                         corner_radius=0, helix_angle=30, number_of_teeth=4, tool_material="CARBIDE",
                         stock_count=3)
MASTER = {6: T06_WIDIN, 11: T01_YG, 30: STOCK_OSG_DRILL, 31: STOCK_OSG_ENDMILL}


def _insert_tools(conn, tools):
    for tool_id, row in tools.items():
        cols = ["tool_id", *row]
        conn.execute(text(f"INSERT INTO tool ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"),
                     {"tool_id": tool_id, **row})


@pytest.fixture
def eng():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(engine)
    with engine.begin() as conn:
        conn.execute(text(TOOL_TABLE_DDL))
        _insert_tools(conn, MASTER)
    return engine


@pytest.fixture
def jobs(eng):
    """같은 NC(T06 한 공구)를 두 번 돌린 Job 둘. 워킹스텝의 자동 연결은 T06 = tool_id 6."""
    db = sessionmaker(bind=eng, autoflush=False)()
    part = Part(part_name="Part", project_code="Proj", material_code="AL6061")
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code="O0911.nc", nc_hash="00183ad8")
    db.add(wp)
    db.flush()
    db.add(Workingstep(workplan_id=wp.workplan_id, tool_id=6, step_order=1, tool_number=6,
                       xml_tool_code="T06", spindle_speed=3800.0, feed_rate=710.0))
    first = Job(workplan_id=wp.workplan_id, work_id="A", source_folder="Proj/Part/1")
    second = Job(workplan_id=wp.workplan_id, work_id="B", source_folder="Proj/Part/2")
    db.add_all([first, second])
    db.commit()
    ids = first.job_id, second.job_id
    db.close()
    return ids


def _replace_master(conn, tools):
    """tool_inserter.replace_tool_master 처럼 공구를 모두 지우고 새 번호로 넣는다."""
    conn.execute(text("DELETE FROM tool"))
    _insert_tools(conn, tools)


# ---------------------------------------------------------------------------
# 업로드한 파일에서 공구 번호 읽기
# ---------------------------------------------------------------------------

def test_tools_called_in_nc_follows_the_workingstep_rule():
    with open(O1110_PATH, "rb") as f:
        called = ta.tools_called_in_nc(f.read())       # 디스크에 쓰기 전 바이트열 그대로
    steps = parse_nc_cutting_conditions(O1110_PATH)
    assert list(called) == [1, 2, 3]
    assert sum(info["steps"] for info in called.values()) == len(steps) == 32
    for number, info in called.items():
        mine = [s for s in steps if s["tool_number"] == number]
        assert info["steps"] == len(mine)
        assert info["spindle"] == (min(s["spindle_speed"] for s in mine), max(s["spindle_speed"] for s in mine))


def test_tools_called_in_xml_reads_tool_numbers_in_order():
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <WorkModel><ToolNumbers><int>6</int><int>1</int><int>6</int></ToolNumbers></WorkModel>"""
    assert list(ta.tools_called_in_xml(xml)) == [6, 1]
    assert ta.tools_called_in_xml(b"<Other><ToolNumbers><int>1</int></ToolNumbers></Other>") == {}


# ---------------------------------------------------------------------------
# 지정 · 표시
# ---------------------------------------------------------------------------

def test_without_assignment_the_job_shows_the_t_number_link(eng, jobs):
    with eng.connect() as conn:
        rows = ta.job_tool_rows(conn, jobs[0])
    assert [(r["tool_code"], r["basis"], r["tool_id"], r["steps"]) for r in rows] == [("T06", "자동", 6, 1)]
    assert rows[0]["label"] == "T06 장착 · WIDIN · ENDMILL Ø12 Z3 · DLC"


def test_assignment_is_per_job_and_can_pick_unmounted_stock(eng, jobs):
    first, second = jobs
    with eng.begin() as conn:
        assert ta.save_assignments(conn, first, {6: 31}, assigned_by="홍길동",
                                   now=datetime(2026, 11, 12, 9, 0)) == [6]
    with eng.connect() as conn:
        row = ta.job_tool_rows(conn, first)[0]
        other = ta.job_tool_rows(conn, second)[0]
        saved = ta.load_assignments(conn, first)[6]
    assert (row["basis"], row["tool_id"], row["in_master"]) == ("지정", 31, True)
    assert row["label"] == "재고 · OSG · ENDMILL Ø12 Z4"
    assert row["assigned_by"] == "홍길동"
    # 같은 NC 를 돌린 다른 Job 은 그대로 T번호 자동 연결이다 (워킹스텝을 건드리지 않았다)
    assert (other["basis"], other["tool_id"]) == ("자동", 6)
    # 스냅샷은 공구 마스터 CSV 와 같은 글자 표기다
    assert saved["tool_snapshot"]["cutter_diameter"] == "12"
    assert saved["tool_snapshot"]["model_code"] == ""
    assert saved["source"] == ta.SOURCE_MANUAL


def test_saving_again_replaces_the_previous_pick(eng, jobs):
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {6: 31})
        ta.save_assignments(conn, jobs[0], {6: 6})
    with eng.connect() as conn:
        assert ta.load_assignments(conn, jobs[0])[6]["tool_id"] == 6
        assert conn.execute(text("SELECT COUNT(*) FROM job_tool_assignment")).scalar() == 1


def test_not_in_master_pick_does_not_fall_back_to_the_t_number(eng, jobs):
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {6: None})
    with eng.connect() as conn:
        row = ta.job_tool_rows(conn, jobs[0])[0]
    assert (row["basis"], row["tool_id"], row["label"]) == ("지정", None, None)


def test_saving_an_unknown_tool_id_is_refused(eng, jobs):
    with eng.begin() as conn:
        with pytest.raises(ValueError, match="T06"):
            ta.save_assignments(conn, jobs[0], {6: 999})


def test_assignment_for_a_number_outside_the_workplan_is_listed_after(eng, jobs):
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {29: 30})
    with eng.connect() as conn:
        rows = ta.job_tool_rows(conn, jobs[0])
    assert [(r["tool_code"], r["in_workplan"]) for r in rows] == [("T06", True), ("T29", False)]


# ---------------------------------------------------------------------------
# 공구 마스터 교체 뒤 다시 잇기
# ---------------------------------------------------------------------------

def test_relink_finds_the_same_tool_after_ids_change_and_port_moves(eng, jobs):
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {6: 6})          # 그때 T06 에 꽂혀 있던 WIDIN
        ta.save_assignments(conn, jobs[1], {6: 30})         # 재고 OSG 드릴
    moved = {**T06_WIDIN, "tool_code": "T07", "stock_count": 4, "gauge_length": 101.25}
    new_t06 = {**T01_YG, "tool_code": "T06"}                # T06 포트에 다른 공구를 꽂았다
    with eng.begin() as conn:
        _replace_master(conn, {1: new_t06, 2: moved, 3: STOCK_OSG_DRILL})
        assert ta.relink_job_tool_assignments(conn) == (2, 0)
    with eng.connect() as conn:
        first = ta.job_tool_rows(conn, jobs[0])[0]
        second = ta.job_tool_rows(conn, jobs[1])[0]
    # 재고 수량 · 포트 번호 · 게이지 길이가 바뀌어도 같은 공구로 찾는다 (T번호로 맞추지 않는다)
    assert (first["tool_id"], first["label"]) == (2, "T07 장착 · WIDIN · ENDMILL Ø12 Z3 · DLC")
    assert second["tool_id"] == 3


def test_relink_leaves_the_snapshot_when_the_tool_is_gone(eng, jobs):
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {6: 31})
    with eng.begin() as conn:
        _replace_master(conn, {1: T06_WIDIN})
        assert ta.relink_job_tool_assignments(conn) == (0, 1)
    with eng.connect() as conn:
        row = ta.job_tool_rows(conn, jobs[0])[0]
    # 지금 T06 공구로 대신하지 않고, 지정 당시 제원을 그대로 보여 준다
    assert (row["basis"], row["tool_id"], row["in_master"]) == ("지정", None, False)
    assert row["label"] == "재고 · OSG · ENDMILL Ø12 Z4"


def test_relink_with_identical_specs_uses_the_t_number_or_gives_up(eng, jobs):
    twin = {**T06_WIDIN, "tool_code": None}
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {6: 6})
    with eng.begin() as conn:
        _replace_master(conn, {1: twin, 2: T06_WIDIN})            # 제원이 같은 두 행 중 T06 은 하나
        assert ta.relink_job_tool_assignments(conn) == (1, 0)
        assert ta.load_assignments(conn, jobs[0])[6]["tool_id"] == 2
    with eng.begin() as conn:
        _replace_master(conn, {1: twin, 2: {**twin}})             # 구별할 근거가 없다
        assert ta.relink_job_tool_assignments(conn) == (0, 1)


def test_relink_keeps_not_in_master_rows(eng, jobs):
    with eng.begin() as conn:
        ta.save_assignments(conn, jobs[0], {6: None})
        _replace_master(conn, {1: T06_WIDIN})
        assert ta.relink_job_tool_assignments(conn) == (0, 0)
        assert ta.load_assignments(conn, jobs[0])[6]["tool_id"] is None


# ---------------------------------------------------------------------------
# 표기
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, out", [
    (Decimal("12.000"), "12"), (12.0, "12"), (6.8, "6.8"), (Decimal("0.800"), "0.8"),
    (float("nan"), ""), (None, ""), (True, "1"), (1, "1"), ("GP3D1068K", "GP3D1068K"),
])
def test_spec_text_matches_the_tool_master_csv(value, out):
    assert ta.spec_text(value) == out


def test_tool_label_shows_radius_only_when_it_matters():
    ball = dict(manufacturer="WIDIN", iso14649_class="BALLNOSE_ENDMILL", cutter_diameter=5,
                corner_radius=Decimal("2.500"), number_of_teeth=2, tool_code="T11")
    assert ta.tool_label(ball) == "T11 장착 · WIDIN · BALLNOSE_ENDMILL Ø5 R2.5 Z2"
    assert ta.tool_label({"cutter_diameter": 9.7}) == "재고 · 제조사 미상 · 분류 미상 Ø9.7"
