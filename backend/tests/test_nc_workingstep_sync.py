"""NC 를 워킹스텝 목록의 기준으로 삼는 흐름 (nc_parser.sync_workplan_nc_cutting_conditions,
xml_parser.parse_xml, nc_parser.parse_nc).

실제 MySQL 은 건드리지 않는다. 인메모리 SQLite 에 테이블을 만들고 파서들이 쓰는 세션·보관소 함수를
바꿔 끼운다. tool 테이블은 MySQL 전용 기본값(ON UPDATE CURRENT_TIMESTAMP) 때문에 모델로 만들 수
없어 파서가 읽는 두 칸만 직접 만든다. XML 에는 StartTime 을 넣지 않는다 (SQLite 의 DateTime 은
문자열을 받지 않는다 — 실제 MySQL 은 받는다).
"""
import hashlib
import os
import shutil
import sys

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from DB.database import Base
from DB.models import Job, Part, Workingstep, Workplan, WorkplanFileArchive
import parsers.nc_parser as nc_parser
import parsers.xml_parser as xml_parser
from parsers.nc_parser import parse_nc_cutting_conditions, sync_workplan_nc_cutting_conditions

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
O1110_PATH = os.path.join(FIXTURES, "O1110_20261111_mmas.nc")
with open(O1110_PATH, "rb") as _f:
    O1110_BYTES = _f.read()
O1110_HASH = hashlib.md5(O1110_BYTES).hexdigest()[:8]
# Job 1(2026-09-11 DB_Test) 의 NC 원본 그대로: T6 하나로 같은 조건으로 네 줄 (X26 · X34 · X87 · X95)
O0911_PATH = os.path.join(FIXTURES, "O0911_20260911.nc")
with open(O0911_PATH, "rb") as _f:
    O0911_BYTES = _f.read()

# 공구 마스터: T01·T02·T03·T06 이 tool_id 11·12·13·16 (번호로 연결되는지 보려고 일부러 어긋나게 둔다).
# 지름은 연구실 공구 마스터와 같다 — 워킹스텝의 가공 형상을 실제 공구 지름으로 가른다.
TOOL_IDS = {1: 11, 2: 12, 3: 13, 6: 16}
TOOL_DIAMETERS = {1: 2.0, 2: 4.0, 3: 6.0, 6: 12.0}

XML_O1110 = """<?xml version="1.0" encoding="utf-8"?>
<WorkModel>
  <WorkID>261111185522</WorkID>
  <ProjectCode>P005_232</ProjectCode>
  <MachineCode>DNM200</MachineCode>
  <MaterialCode>Inconel</MaterialCode>
  <PartCode>PROBE START</PartCode>
  <ProgramCode>O1110.NC</ProgramCode>
  <ToolNumbers><int>1</int><int>2</int><int>3</int></ToolNumbers>
  <CuttingSeconds>598.2</CuttingSeconds>
  <IsFinish>true</IsFinish>
  <IsError>false</IsError>
</WorkModel>
"""
FOLDER_ID = "Proj/Part/1"


@pytest.fixture
def Session(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE tool (tool_id INTEGER PRIMARY KEY, tool_code VARCHAR(20), "
                          "cutter_diameter NUMERIC(8, 3), iso14649_class VARCHAR(40))"))
        for number, tool_id in TOOL_IDS.items():
            conn.execute(text("INSERT INTO tool (tool_id, tool_code, cutter_diameter, iso14649_class) "
                              "VALUES (:i, :c, :d, 'ENDMILL')"),
                         {"i": tool_id, "c": f"T{number:02d}", "d": TOOL_DIAMETERS[number]})
    factory = sessionmaker(bind=eng, autocommit=False, autoflush=False)   # 운영과 같은 설정

    def _get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(xml_parser, "SessionLocal", factory)
    monkeypatch.setattr(nc_parser, "get_db", _get_db)
    # 실제 보관소에 사본을 남기지 않는다
    monkeypatch.setattr(xml_parser, "archive_file", lambda *a, **k: None)
    monkeypatch.setattr(nc_parser, "archive_file", lambda *a, **k: None)
    return factory


# 연구실 시험 소재(블록) 121 x 101 x 28 mm, 원점은 윗면 모서리. 가공 형상 분류 · 크기는 소재 크기가 있어야 적는다.
LAB_STOCK = (121.0, 101.0, 28.0, "corner")


def _workplan(db, nc_hash=O1110_HASH, program="O1110.NC", stock=None):
    part = Part(part_name="Part", project_code="Proj", material_code="Inconel")
    if stock:
        part.stock_x, part.stock_y, part.stock_z, part.stock_origin = stock
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code=program, nc_hash=nc_hash)
    db.add(wp)
    db.flush()
    return wp


def _old_rule_steps(db, workplan_id, operation_types=None):
    """예전 규칙(공구 교환에서만 끊음)으로 들어간 O1110 의 세 행: 마지막 S 와 첫 F 가 섞였다."""
    operation_types = operation_types or {}
    rows = [(1, 9500.0, 150.0), (2, 7500.0, 300.0), (3, 6000.0, 400.0)]
    for order, (tool, s, f) in enumerate(rows, start=1):
        db.add(Workingstep(workplan_id=workplan_id, tool_id=TOOL_IDS[tool], step_order=order,
                           tool_number=tool, xml_tool_code=f"T{tool:02d}", spindle_speed=s,
                           feed_rate=f, operation_type=operation_types.get(tool)))
    db.flush()


def _steps(db, workplan_id):
    return (db.query(Workingstep).filter(Workingstep.workplan_id == workplan_id)
            .order_by(Workingstep.step_order).all())


def _as_parsed(rows):
    return [(ws.step_order, ws.tool_number, ws.spindle_speed, ws.feed_rate, ws.depth_of_cut)
            for ws in rows]


EXPECTED = [(p["step_order"], p["tool_number"], p["spindle_speed"], p["feed_rate"],
             p["depth_of_cut"]) for p in parse_nc_cutting_conditions(O1110_PATH)]


def _job_folder(tmp_path, with_nc=True, part="Part",
                nc_name="DNM200__O1110.NC__PROBE START__261111185522_O1110.NC.nc"):
    job_dir = tmp_path / "machining_raw_data" / "Proj" / part / "1"
    (job_dir / "XML").mkdir(parents=True)
    xml_path = job_dir / "XML" / "DNM200__O1110.NC__PROBE START__261111185522_0.xml"
    xml_path.write_text(XML_O1110, encoding="utf-8")
    nc_path = job_dir / "NC" / nc_name
    if with_nc:
        nc_path.parent.mkdir()
        shutil.copyfile(O1110_PATH, nc_path)
    return str(xml_path), str(nc_path)


# ---------------------------------------------------------------------------
# sync_workplan_nc_cutting_conditions
# ---------------------------------------------------------------------------

def test_sync_creates_condition_steps_when_none_exist(Session):
    db = Session()
    wp_id = _workplan(db).workplan_id
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_file_path=O1110_PATH)
    rows = _steps(Session(), wp_id)
    assert len(rows) == 32
    assert _as_parsed(rows) == EXPECTED
    assert {ws.tool_number: ws.tool_id for ws in rows} == {1: 11, 2: 12, 3: 13}   # 번호로 공구 마스터와 연결
    assert {ws.xml_tool_code for ws in rows} == {"T01", "T02", "T03"}


def test_sync_rebuilds_steps_made_by_the_old_rule(Session):
    db = Session()
    wp_id = _workplan(db, stock=LAB_STOCK).workplan_id
    _old_rule_steps(db, wp_id, operation_types={1: "FACE"})
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=O1110_BYTES)
    rows = _steps(Session(), wp_id)
    assert _as_parsed(rows) == EXPECTED
    # 실제로 깎은 적 없는 조합(T1 S9500 / F150)은 남지 않는다
    assert (1, 9500.0, 150.0) not in [(ws.tool_number, ws.spindle_speed, ws.feed_rate) for ws in rows]
    # 공정은 NC 가 정한 ISO 14649 이름이다 (사람이 T1 에 적어 둔 'FACE' 대신)
    assert {ws.operation_type for ws in rows} == {"bottom_and_side_rough_milling"}
    assert [ws.feature_type for ws in rows] == ["slot"] * 21 + ["step"] * 11
    # 공구마다 홈들이 겹쳐 형상 하나 (Ø2 를 1.5mm 간격 등) -> 형상 세 개, 조건마다 워킹스텝
    assert [ws.feature_no for ws in rows] == [1] * 10 + [2] * 11 + [3] * 11


def test_without_a_stock_size_the_nc_does_not_name_milling_operations(Session):
    """소재 크기가 없으면 밀링 공정 이름(형상에 따라 갈림)을 기록하지 않는다. 그래서 사람이 T1 에 적은 'FACE' 가
    남고, 형상 분류 · 크기 · 제거 체적 · 반경 절입도 비어 있다. 형상 번호(워킹스텝 분리)와 ap 는 적는다."""
    db = Session()
    wp_id = _workplan(db).workplan_id
    _old_rule_steps(db, wp_id, operation_types={1: "FACE"})
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=O1110_BYTES)
    rows = _steps(Session(), wp_id)
    assert [ws.operation_type for ws in rows] == ["FACE"] * 10 + [None] * 22
    assert {ws.feature_type for ws in rows} == {None}
    assert {ws.removed_volume for ws in rows} == {None} and {ws.radial_cutting_depth for ws in rows} == {None}
    assert [ws.feature_no for ws in rows] == [1] * 10 + [2] * 11 + [3] * 11
    assert {tp["stage"] for ws in rows for tp in ws.toolpaths} == {"rough"}


def test_operation_written_by_the_nc_is_cleared_when_it_can_no_longer_be_judged(Session):
    """예전에 NC 가 적은 ISO 공정 이름은 소재 크기를 지우는 등으로 다시 정하지 못하면 지운다 (사람이 적은 값은 둔다)."""
    nc = "\n".join(["M6T6", "G90G00G54X30Y-15", "S3800M3", "G43H6Z5", "G1Z-2F710", "Y115", "G0Z5", "M30"])
    db = Session()
    wp_id = _workplan(db, nc_hash="0000cccc", program="O0003.nc").workplan_id
    db.add(Workingstep(workplan_id=wp_id, step_order=1, tool_number=6, xml_tool_code="T06",
                       operation_type="bottom_and_side_rough_milling", feature_type="slot"))
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=nc)
    row = _steps(Session(), wp_id)[0]
    assert (row.operation_type, row.feature_type, row.feature_geometry) == (None, None, None)


def test_saving_a_stock_size_re_judges_the_parts_workingsteps(Session):
    """업로드 화면에서 부품의 소재 크기를 넣으면(job_manager.set_part_stock) 이미 들어간 워킹스텝을 다시 판단한다."""
    from job_manager import part_stock, resync_part_workingsteps, set_part_stock

    db = Session()
    wp = _workplan(db, nc_hash="00183ad8", program="O0911.nc")
    db.add(WorkplanFileArchive(workplan_id=wp.workplan_id, nc_file_content=O0911_BYTES))
    db.commit()
    assert sync_workplan_nc_cutting_conditions(db, wp.workplan_id)
    assert [ws.feature_type for ws in _steps(Session(), wp.workplan_id)] == [None, None]

    db = Session()
    part, changed = set_part_stock(db, "Proj", "Part", 121, 101, 28)
    assert changed and part_stock(part) == (121.0, 101.0, 28.0, "corner")
    assert resync_part_workingsteps(db, part) == 1
    db.commit()
    rows = _steps(Session(), wp.workplan_id)
    assert [(ws.feature_type, ws.feature_geometry["width"], ws.removed_volume) for ws in rows] == [
        ("slot", 20.0, 4040.0), ("slot", 20.0, 4040.0)]
    assert set_part_stock(Session(), "Proj", "Part", 121, 101, 28)[1] is False      # 같은 값이면 바뀐 것 없음
    assert set_part_stock(Session(), "Proj", "Part", 121, 0, 28) == (None, False)   # 크기가 0 이면 모름


def test_sync_twice_updates_in_place_and_keeps_step_ids(Session):
    db = Session()
    wp_id = _workplan(db).workplan_id
    db.commit()
    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_file_path=O1110_PATH)
    first = [(ws.step_id, ws.step_order) for ws in _steps(Session(), wp_id)]

    db2 = Session()
    assert sync_workplan_nc_cutting_conditions(db2, wp_id, nc_file_path=O1110_PATH)
    rows = _steps(Session(), wp_id)
    assert [(ws.step_id, ws.step_order) for ws in rows] == first
    assert _as_parsed(rows) == EXPECTED


def test_sync_same_layout_fills_new_fields_and_keeps_the_row(Session):
    """예전 판에서 만든 한 행(T6)이 NC 의 한 스텝과 모양이 같으면 지우지 않고 값만 채운다 (step_id 유지)."""
    nc = "\n".join(["M6T6", "G90G00G54X30Y-15", "S3800M3", "G43H6Z5", "G1Z-2F710", "Y115",
                    "G0Z5", "M30"])
    db = Session()
    wp_id = _workplan(db, nc_hash="0000aaaa", program="O0001.nc", stock=LAB_STOCK).workplan_id
    db.add(Workingstep(workplan_id=wp_id, step_order=1, tool_number=6, xml_tool_code="T06",
                       spindle_speed=3800.0, feed_rate=710.0, operation_type="SLOT"))
    db.commit()
    before = _steps(Session(), wp_id)[0].step_id

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=nc)
    rows = _steps(Session(), wp_id)
    assert len(rows) == 1
    assert rows[0].step_id == before
    assert rows[0].operation_type == "bottom_and_side_rough_milling"
    assert (rows[0].spindle_speed, rows[0].feed_rate, rows[0].depth_of_cut) == (3800.0, 710.0, 2.0)
    assert (rows[0].pass_count, rows[0].nc_line_start, rows[0].nc_line_end) == (1, 5, 6)
    assert rows[0].start_position == {"X": 30.0, "Y": -15.0, "Z": 5.0}
    assert rows[0].end_position == {"X": 30.0, "Y": 115.0, "Z": -2.0}
    assert rows[0].cutting_distance == 137.0
    assert (rows[0].feature_no, rows[0].feature_type, rows[0].radial_cutting_depth) == (1, "slot", 12.0)
    assert (rows[0].feature_geometry["width"], rows[0].feature_geometry["ends"]) == (12.0, ["open", "open"])
    assert rows[0].strategy == "unidirectional"
    assert [tp["nc_lines"] for tp in rows[0].toolpaths] == [[5, 6]]


def test_sync_keeps_a_manual_operation_where_the_nc_cannot_tell(Session):
    """절삭 없이 호출만 된 공구(접촉식 프로브 등)는 NC 가 공정을 정하지 못해 사람이 적은 값을 둔다."""
    nc = "\n".join(["M6 T5", "S1000 M3", "G0 X0 Y0 Z50", "M5",
                    "M6 T6", "S3800 M3", "G0 X0 Y0 Z5", "G1 Z-2 F710", "Y100", "G0 Z5", "M30"])
    db = Session()
    wp_id = _workplan(db, nc_hash="0000bbbb", program="O0002.nc", stock=LAB_STOCK).workplan_id
    db.add(Workingstep(workplan_id=wp_id, step_order=1, tool_number=5, xml_tool_code="T05", operation_type="PROBE"))
    db.add(Workingstep(workplan_id=wp_id, step_order=2, tool_number=6, xml_tool_code="T06", operation_type="SLOT"))
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=nc)
    assert [ws.operation_type for ws in _steps(Session(), wp_id)] == ["PROBE", "bottom_and_side_rough_milling"]


def test_sync_regroups_the_v330_row_of_o0911_into_two_slot_steps(Session):
    """V3.3.0 까지 같은 S·F 라고 한 행(패스 4개)으로 합쳤던 Job 1(O0911)이 슬롯마다 한 행이 된다.
    공구 마스터의 T06(Ø12)으로 깎인 자리를 따라가 X26·X34 / X87·X95 가 각각 폭 20 슬롯 하나다."""
    db = Session()
    wp_id = _workplan(db, nc_hash="00183ad8", program="O0911.nc", stock=LAB_STOCK).workplan_id
    db.add(Workingstep(workplan_id=wp_id, step_order=1, tool_number=6, xml_tool_code="T06",
                       spindle_speed=3800.0, feed_rate=710.0, depth_of_cut=2.0, operation_type="SLOT"))
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=O0911_BYTES)
    rows = _steps(Session(), wp_id)
    assert _as_parsed(rows) == [(1, 6, 3800.0, 710.0, 2.0), (2, 6, 3800.0, 710.0, 2.0)]
    assert [ws.tool_id for ws in rows] == [16, 16]                    # 공구 마스터와 번호로 다시 잇는다
    assert [(ws.feature_no, ws.feature_type) for ws in rows] == [(1, "slot"), (2, "slot")]
    assert [(ws.feature_geometry["width"], ws.feature_geometry["length"]) for ws in rows] == [(20.0, 101.0)] * 2
    assert [ws.operation_type for ws in rows] == ["bottom_and_side_rough_milling"] * 2
    assert [ws.pass_count for ws in rows] == [2, 2]
    assert [(ws.nc_line_start, ws.nc_line_end) for ws in rows] == [(11, 14), (16, 18)]
    assert [[tp["ae"] for tp in ws.toolpaths] for ws in rows] == [[12.0, 8.0], [12.0, 8.0]]
    assert [ws.cutting_distance for ws in rows] == [267.0, 260.0]


def test_job_tool_assignment_diameter_wins_over_the_tool_master(Session):
    """그 Job 에서 실제로 쓴 공구(Job 공구 지정 스냅샷)가 Ø6 이었다면 네 줄은 서로 떨어진 홈 네 개다."""
    from DB.models import JobToolAssignment

    db = Session()
    wp = _workplan(db, nc_hash="00183ad8", program="O0911.nc")
    job = Job(workplan_id=wp.workplan_id, work_id="260911164215", source_folder="Proj/Part/2")
    db.add(job)
    db.flush()
    db.add(JobToolAssignment(job_id=job.job_id, tool_number=6, tool_id=None, source="manual",
                             tool_snapshot={"tool_code": "T06", "cutter_diameter": 6.0, "iso14649_class": "ENDMILL"}))
    db.commit()

    assert nc_parser.tool_specs_for_workplan(Session(), wp.workplan_id)[6]["source"] == "Job 공구 지정"
    assert sync_workplan_nc_cutting_conditions(db, wp.workplan_id, nc_content=O0911_BYTES)
    assert [ws.pass_count for ws in _steps(Session(), wp.workplan_id)] == [1, 1, 1, 1]


def test_sync_fills_new_fields_of_v330_condition_rows_in_place(Session):
    """V3.3.0 에서 조건마다 나눈 O1110 의 32행은 모양이 같아 행을 두고 새 칸만 채운다."""
    db = Session()
    wp_id = _workplan(db, stock=LAB_STOCK).workplan_id
    for p in parse_nc_cutting_conditions(O1110_PATH):
        db.add(Workingstep(workplan_id=wp_id, step_order=p["step_order"], tool_number=p["tool_number"],
                           xml_tool_code=p["xml_tool_code"], spindle_speed=p["spindle_speed"],
                           feed_rate=p["feed_rate"], depth_of_cut=p["depth_of_cut"]))
    db.commit()
    ids = [ws.step_id for ws in _steps(Session(), wp_id)]

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=O1110_BYTES)
    rows = _steps(Session(), wp_id)
    assert [ws.step_id for ws in rows] == ids
    assert all(ws.pass_count == 1 and ws.nc_line_start and ws.cutting_distance for ws in rows)
    assert (rows[0].nc_line_start, rows[0].nc_line_end) == (8, 9)
    assert [ws.radial_cutting_depth for ws in rows[:3]] == [2.0, 1.5, 1.5]     # Ø2 를 1.5mm 씩 옆으로


def test_sync_without_nc_changes_nothing(Session):
    db = Session()
    wp_id = _workplan(db).workplan_id
    _old_rule_steps(db, wp_id)
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id) is False
    assert len(_steps(Session(), wp_id)) == 3


def test_sync_reads_nc_from_the_archive_blob(Session):
    db = Session()
    wp_id = _workplan(db).workplan_id
    db.add(WorkplanFileArchive(workplan_id=wp_id, nc_file_content=O1110_BYTES))
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id)
    assert _as_parsed(_steps(Session(), wp_id)) == EXPECTED


def test_sync_commit_false_leaves_the_transaction_to_the_caller(Session):
    db = Session()
    wp_id = _workplan(db).workplan_id
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_file_path=O1110_PATH, commit=False)
    assert len(_steps(db, wp_id)) == 32          # 같은 트랜잭션 안에서는 보인다
    db.rollback()
    assert _steps(Session(), wp_id) == []        # 커밋하지 않았으므로 남지 않는다


def test_sync_keeps_rows_when_the_nc_cannot_be_parsed(Session, monkeypatch):
    db = Session()
    wp_id = _workplan(db).workplan_id
    _old_rule_steps(db, wp_id)
    db.commit()

    def broken(_source):
        raise ValueError("깨진 NC")
    monkeypatch.setattr(nc_parser, "parse_nc_cutting_conditions", broken)
    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_file_path=O1110_PATH, commit=False) is False
    db.commit()     # 호출한 쪽 트랜잭션은 그대로 쓸 수 있다
    assert len(_steps(Session(), wp_id)) == 3


# ---------------------------------------------------------------------------
# 수집 흐름: XML · NC 도착 순서
# ---------------------------------------------------------------------------

def test_xml_with_nc_in_the_job_folder_builds_condition_steps(Session, tmp_path):
    xml_path, _ = _job_folder(tmp_path, with_nc=True)
    xml_parser.parse_xml(xml_path, FOLDER_ID)

    db = Session()
    wp = db.query(Workplan).one()
    assert wp.nc_hash == O1110_HASH
    assert _as_parsed(_steps(db, wp.workplan_id)) == EXPECTED     # ToolNumbers 3행이 아니다
    job = db.query(Job).one()
    assert job.workplan_id == wp.workplan_id
    assert [c["tool_code"] for c in job.tool_conditions] == ["T01", "T02", "T03"]


def test_xml_alone_uses_tool_numbers_then_nc_arrival_rebuilds(Session, tmp_path):
    xml_path, nc_path = _job_folder(tmp_path, with_nc=False)
    xml_parser.parse_xml(xml_path, FOLDER_ID)

    db = Session()
    wp = db.query(Workplan).one()
    assert wp.nc_hash == "NOHASH"
    rows = _steps(db, wp.workplan_id)
    assert [(ws.tool_number, ws.spindle_speed) for ws in rows] == [(1, None), (2, None), (3, None)]
    db.close()

    os.makedirs(os.path.dirname(nc_path))
    shutil.copyfile(O1110_PATH, nc_path)
    assert nc_parser.parse_nc(nc_path, FOLDER_ID)

    db = Session()
    wp = db.query(Workplan).one()
    assert wp.nc_hash == O1110_HASH
    assert _as_parsed(_steps(db, wp.workplan_id)) == EXPECTED


def test_reingesting_old_data_replaces_tool_level_steps(Session, tmp_path):
    """이미 예전 규칙으로 들어간 Job 을 XML 로 다시 넣으면 같은 Workplan 을 쓰면서 스텝만 바뀐다."""
    db = Session()
    wp = _workplan(db, program="O1110.NC")
    _old_rule_steps(db, wp.workplan_id)
    db.add(Job(workplan_id=wp.workplan_id, work_id="261111185522", source_folder=FOLDER_ID))
    db.commit()
    wp_id = wp.workplan_id

    xml_path, _ = _job_folder(tmp_path, with_nc=True)
    xml_parser.parse_xml(xml_path, FOLDER_ID)

    db = Session()
    assert [w.workplan_id for w in db.query(Workplan).all()] == [wp_id]
    assert db.query(Job).count() == 1
    assert _as_parsed(_steps(db, wp_id)) == EXPECTED


def test_folder_name_containing_old_does_not_hide_the_nc(Session, tmp_path):
    """예전에는 경로 전체에서 'old' 를 찾아, MoldBase 같은 부품 폴더의 NC 를 백업으로 보고 버렸다."""
    xml_path, _ = _job_folder(tmp_path, with_nc=True, part="MoldBase")
    xml_parser.parse_xml(xml_path, "Proj/MoldBase/1")

    db = Session()
    wp = db.query(Workplan).one()
    assert wp.nc_hash == O1110_HASH
    assert _as_parsed(_steps(db, wp.workplan_id)) == EXPECTED


def test_backup_named_nc_is_not_used(Session, tmp_path):
    xml_path, _ = _job_folder(tmp_path, with_nc=True, nc_name="O1110_old.nc")
    xml_parser.parse_xml(xml_path, FOLDER_ID)

    db = Session()
    wp = db.query(Workplan).one()
    assert wp.nc_hash == "NOHASH"
    assert [ws.tool_number for ws in _steps(db, wp.workplan_id)] == [1, 2, 3]   # ToolNumbers 로만
