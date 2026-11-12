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

# 공구 마스터: T01·T02·T03 이 tool_id 11·12·13 (번호로 연결되는지 보려고 일부러 어긋나게 둔다)
TOOL_IDS = {1: 11, 2: 12, 3: 13}

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
        conn.execute(text("CREATE TABLE tool (tool_id INTEGER PRIMARY KEY, tool_code VARCHAR(20))"))
        for number, tool_id in TOOL_IDS.items():
            conn.execute(text("INSERT INTO tool (tool_id, tool_code) VALUES (:i, :c)"),
                         {"i": tool_id, "c": f"T{number:02d}"})
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


def _workplan(db, nc_hash=O1110_HASH, program="O1110.NC"):
    part = Part(part_name="Part", project_code="Proj", material_code="Inconel")
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
    assert {ws.tool_number: ws.tool_id for ws in rows} == TOOL_IDS   # 번호로 공구 마스터와 연결
    assert {ws.xml_tool_code for ws in rows} == {"T01", "T02", "T03"}


def test_sync_rebuilds_steps_made_by_the_old_rule(Session):
    db = Session()
    wp_id = _workplan(db).workplan_id
    _old_rule_steps(db, wp_id, operation_types={1: "FACE"})
    db.commit()

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=O1110_BYTES)
    rows = _steps(Session(), wp_id)
    assert _as_parsed(rows) == EXPECTED
    # 실제로 깎은 적 없는 조합(T1 S9500 / F150)은 남지 않는다
    assert (1, 9500.0, 150.0) not in [(ws.tool_number, ws.spindle_speed, ws.feed_rate) for ws in rows]
    # 사람이 T1 에 적어 둔 가공방식은 T1 의 새 스텝 10개로 옮겨 간다
    assert [ws.operation_type for ws in rows if ws.tool_number == 1] == ["FACE"] * 10
    assert all(ws.operation_type is None for ws in rows if ws.tool_number != 1)


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


def test_sync_same_layout_fills_depth_and_keeps_manual_fields(Session):
    """예전 규칙으로 만든 O0911 의 한 행(T6)은 모양이 같으므로 지우지 않고 깊이만 채운다."""
    nc = "\n".join(["M6T6", "G90G00G54X0Y-15", "S3800M3", "G43H6Z5", "G1Z-2F710", "Y115",
                    "G0X34", "G1Y-15", "G0Z5", "M30"])
    db = Session()
    wp_id = _workplan(db, nc_hash="00183ad8", program="O0911.nc").workplan_id
    db.add(Workingstep(workplan_id=wp_id, step_order=1, tool_number=6, xml_tool_code="T06",
                       spindle_speed=3800.0, feed_rate=710.0, operation_type="SLOT"))
    db.commit()
    before = _steps(Session(), wp_id)[0].step_id

    assert sync_workplan_nc_cutting_conditions(db, wp_id, nc_content=nc)
    rows = _steps(Session(), wp_id)
    assert len(rows) == 1
    assert rows[0].step_id == before
    assert rows[0].operation_type == "SLOT"
    assert (rows[0].spindle_speed, rows[0].feed_rate, rows[0].depth_of_cut) == (3800.0, 710.0, 2.0)


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
