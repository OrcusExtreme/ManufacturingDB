"""기존 워킹스텝을 지금 규칙으로 다시 나누는 도구 (backend/resync_workingsteps.py).

실제 MySQL 은 건드리지 않는다. 인메모리 SQLite 에 예전 판이 남긴 모양의 행을 만들어 두고
미리보기 · 반영 · 다시 실행을 확인한다.
"""
import json
import os
import sys

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import DB.schema_patch as schema_patch
import resync_workingsteps as rs
from DB.database import Base
from DB.models import Job, Part, Workingstep, Workplan, WorkplanFileArchive
from parsers.nc_parser import parse_nc_cutting_conditions

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
with open(os.path.join(FIXTURES, "O0911_20260911.nc"), "rb") as _f:
    O0911 = _f.read()
with open(os.path.join(FIXTURES, "O1110_20261111_mmas.nc"), "rb") as _f:
    O1110 = _f.read()


@pytest.fixture
def Session(monkeypatch, tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    with eng.begin() as conn:   # 파서가 읽는 두 칸만 (tool 은 MySQL 전용 기본값 때문에 모델로 못 만든다)
        conn.execute(text("CREATE TABLE tool (tool_id INTEGER PRIMARY KEY, tool_code VARCHAR(20))"))
        conn.execute(text("INSERT INTO tool (tool_id, tool_code) VALUES (6, 'T06'), (11, 'T01')"))
    factory = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    monkeypatch.setattr(rs, "SessionLocal", factory)
    monkeypatch.setattr(schema_patch, "ensure_schema", lambda verbose=True: [])   # MySQL 전용
    monkeypatch.setattr(rs, "BACKUP_DIR", str(tmp_path / "backups"))
    return factory


def _workplan(db, program, nc_bytes, job_id):
    part = Part(part_name=f"Part{job_id}", project_code="Proj", material_code="AL6061")
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code=program, nc_hash=f"h{job_id}")
    db.add(wp)
    db.flush()
    if nc_bytes is not None:
        db.add(WorkplanFileArchive(workplan_id=wp.workplan_id, nc_file_content=nc_bytes))
    db.add(Job(job_id=job_id, workplan_id=wp.workplan_id, work_id=f"26000000000{job_id}",
               source_folder=f"Proj/Part{job_id}/1"))
    db.flush()
    return wp.workplan_id


def _old_db(Session):
    """예전 판이 남긴 모양: Job 1(O0911) 은 같은 S·F 네 줄을 합친 1행, Job 2(O1110) 는 조건별 32행인데
    새 칸이 비어 있고, Job 3 은 NC 없이 XML ToolNumbers 로 만든 공구당 한 행."""
    db = Session()
    wp1 = _workplan(db, "O0911.nc", O0911, 1)
    db.add(Workingstep(workplan_id=wp1, tool_id=6, step_order=1, tool_number=6, xml_tool_code="T06",
                       spindle_speed=3800.0, feed_rate=710.0, depth_of_cut=2.0, operation_type="SLOT"))
    wp2 = _workplan(db, "O1110.NC", O1110, 2)
    for p in parse_nc_cutting_conditions(O1110):
        db.add(Workingstep(workplan_id=wp2, step_order=p["step_order"], tool_number=p["tool_number"],
                           xml_tool_code=p["xml_tool_code"], spindle_speed=p["spindle_speed"],
                           feed_rate=p["feed_rate"], depth_of_cut=p["depth_of_cut"]))
    wp3 = _workplan(db, "O7777.NC", None, 3)
    db.add(Workingstep(workplan_id=wp3, step_order=1, tool_number=5, xml_tool_code="T05"))
    db.commit()
    ids = {wp: [ws.step_id for ws in db.query(Workingstep).filter_by(workplan_id=wp).order_by(Workingstep.step_order)]
           for wp in (wp1, wp2, wp3)}
    db.close()
    return (wp1, wp2, wp3), ids


def _rows(Session, wp_id):
    db = Session()
    try:
        return db.query(Workingstep).filter_by(workplan_id=wp_id).order_by(Workingstep.step_order).all()
    finally:
        db.close()


def test_preview_reports_each_workplan_and_changes_nothing(Session, capsys):
    (wp1, wp2, wp3), ids = _old_db(Session)
    assert rs.main([]) == 0
    out = capsys.readouterr().out
    assert "* 다시 나눔: 지금 1행 -> 새 4행" in out
    assert "11~12줄  X26 Y-15 → X26 Y115  137mm" in out
    assert "* 값만 고침: 32행" in out and "패스 수 32행 · NC 시작 줄 32행" in out
    assert "그대로 둠: NC 원본을 찾지 못했습니다 (지금 1행)" in out
    assert "[요약] 다시 나눔 1 · 값만 고침 1 · NC 없음 1" in out
    assert "[미리보기] DB 는 바꾸지 않았습니다" in out
    for wp in (wp1, wp2, wp3):
        assert [ws.step_id for ws in _rows(Session, wp)] == ids[wp]
    assert all(ws.pass_count is None for ws in _rows(Session, wp2))
    assert not os.path.exists(rs.BACKUP_DIR)


def test_apply_resplits_fills_and_backs_up(Session, capsys):
    (wp1, wp2, wp3), ids = _old_db(Session)
    assert rs.main(["--apply"]) == 0
    out = capsys.readouterr().out

    job1 = _rows(Session, wp1)
    assert [(ws.step_order, ws.tool_number, ws.pass_count, ws.nc_line_start) for ws in job1] == [
        (1, 6, 1, 11), (2, 6, 1, 14), (3, 6, 1, 16), (4, 6, 1, 18)]
    assert [ws.operation_type for ws in job1] == ["SLOT"] * 4           # 공구 번호로 옮긴 가공방식
    assert [ws.tool_id for ws in job1] == [6] * 4

    job2 = _rows(Session, wp2)
    assert [ws.step_id for ws in job2] == ids[wp2]                       # 값만 고쳤다
    assert all(ws.pass_count == 1 and ws.cutting_distance for ws in job2)

    assert [ws.step_id for ws in _rows(Session, wp3)] == ids[wp3]        # NC 가 없어 그대로

    files = os.listdir(rs.BACKUP_DIR)
    assert len(files) == 1 and files[0].startswith("resync_workingsteps_")
    with open(os.path.join(rs.BACKUP_DIR, files[0]), encoding="utf-8") as f:
        backup = json.load(f)
    assert [(b["workplan_id"], b["verdict"], len(b["before"]), len(b["after"])) for b in backup] == [
        (wp1, "rebuild", 1, 4), (wp2, "update", 32, 32)]
    assert backup[0]["before"][0]["step_id"] == ids[wp1][0] and backup[0]["jobs"] == [1]
    assert "[반영 완료] Workplan 2개" in out


def test_second_run_finds_nothing_to_change(Session, capsys):
    _old_db(Session)
    rs.main(["--apply"])
    capsys.readouterr()
    rs.main([])
    out = capsys.readouterr().out
    assert "[요약] 그대로 2 · NC 없음 1" in out


def test_installer_counts_workplans_left_on_the_old_rule(Session):
    """설치 확인(setup_target verify)이 알리는 수: NC 가 있는데 패스 수가 빈 행이 남은 Workplan."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(backend_dir)), "installer"))
    import setup_target

    _old_db(Session)
    count = lambda: Session().execute(text(setup_target.STALE_WORKINGSTEP_SQL)).scalar()   # noqa: E731
    assert count() == 2               # Job 1 · Job 2 (Job 3 은 NC 가 없어 다시 나눌 수 없다)
    rs.main(["--apply"])
    assert count() == 0


def test_selected_workplans_only(Session, capsys):
    (wp1, wp2, _), ids = _old_db(Session)
    rs.main([str(wp2), "999", "--apply"])
    out = capsys.readouterr().out
    assert "없는 Workplan 은 건너뜁니다: [999]" in out
    assert [ws.step_id for ws in _rows(Session, wp1)] == ids[wp1]        # 고르지 않은 Job 1 은 그대로
    assert all(ws.pass_count == 1 for ws in _rows(Session, wp2))
