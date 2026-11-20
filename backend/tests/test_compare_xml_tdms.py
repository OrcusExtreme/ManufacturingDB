"""장비 XML 값 대 TDMS+NC 계산값 비교표 (backend/compare_xml_tdms.py).

값은 실측 Job 1 (DB_Test/1, 2026-09-11) 의 장비 XML 과 TDMS 재계산 결과를 줄여 쓴 것이다.
실제 MySQL 은 건드리지 않는다 (인메모리 SQLite).
"""
import os
import sys
from datetime import datetime

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import compare_xml_tdms as cmp
from DB.database import Base
from DB.models import Job, JobFileArchive, Part, Workplan

XML = b"""<?xml version="1.0" encoding="utf-8"?>
<WorkModel>
  <WorkID>260911164605</WorkID>
  <MachineCode>DNM200</MachineCode>
  <ProgramCode>O0911.nc</ProgramCode>
  <StartTime>2026-09-11T16:46:05.5700000</StartTime>
  <FinishTime>2026-09-11T16:47:00.6200000</FinishTime>
  <CuttingSeconds>44.800000000000367</CuttingSeconds>
  <MovingDistance>1519.2674302959306</MovingDistance>
  <CuttingMovingDistance>526.78949411893723</CuttingMovingDistance>
  <IsFinish>true</IsFinish>
</WorkModel>
"""

TDMS = {   # TDMS(CNC 채널)+NC 로 다시 계산한 값
    "start_time": datetime(2026, 9, 11, 16, 46, 5, 313064),
    "end_time": datetime(2026, 9, 11, 16, 46, 58, 715858),
    # 총 이동거리는 G43H6 위치 점프(191.1mm)를 뺀 실제 이동 (점프를 세면 1357.0)
    "cutting_seconds": 44.8, "cutting_moving_distance": 527.0, "moving_distance": 1165.9,
    "is_finish": True, "work_id": "260911164605", "machine_code": "DNM200", "program_code": "O0911.NC",
    "program_end_basis": "M02/M30 실행 확인 (남은 동작 블록 0개)", "position_jumps": 1, "position_jump_mm": 191.1,
}
WINDOW_KEYS = ("program_code", "program_end_basis", "position_jumps", "position_jump_mm")   # Job 칸이 아닌 값


def test_xml_reference_reads_the_summary():
    ref = cmp.xml_reference(XML)
    assert ref["start_time"] == datetime(2026, 9, 11, 16, 46, 5, 570000)      # 1/10^7 초는 버린다
    assert ref["cutting_seconds"] == pytest.approx(44.8)
    assert ref["is_finish"] is True and ref["program_code"] == "O0911.nc"
    assert cmp.xml_reference(b"<Other/>") is None


def test_compare_values_judges_each_item():
    rows = {r["item"]: r for r in cmp.compare_values(cmp.xml_reference(XML), TDMS)}
    assert rows["가공 시작"]["verdict"] == "일치" and rows["가공 시작"]["diff"] == "-0.26초"
    assert rows["가공 종료"]["verdict"] == "차이" and rows["가공 종료"]["diff"] == "-1.90초"
    assert rows["절삭시간(초)"]["verdict"] == "일치"
    assert rows["절삭 이동거리(mm)"]["verdict"] == "일치" and rows["절삭 이동거리(mm)"]["diff"] == "+0.2 (+0.04%)"
    # DB 칸은 저장된 값(점프 뺀 실제 이동), 차이는 XML 처럼 점프를 더한 1357.0 으로 — 남은 차이는 장비가
    # 프로그램 끝·리셋 뒤의 점프까지 센 것이다
    moving = rows["총 이동거리(mm)"]
    assert moving["db"] == "1165.9" and moving["diff"] == "-162.3 (-10.68%)" and moving["verdict"] == "차이"
    assert "191.1mm" in moving["note"] and "1357.0" in moving["note"]
    assert rows["완료 여부"]["verdict"] == "일치" and "M02/M30" in rows["완료 여부"]["note"]
    assert rows["프로그램"]["verdict"] == "일치"            # 'O0911.nc' 와 'O0911.NC'
    assert rows["작업 ID"]["verdict"] == rows["장비"]["verdict"] == "일치"


def test_compare_values_with_missing_values():
    rows = {r["item"]: r for r in cmp.compare_values(cmp.xml_reference(XML), {**TDMS, "is_finish": None})}
    assert rows["완료 여부"]["verdict"] == "비교 불가" and rows["완료 여부"]["note"] == "DB 에 없음"


def test_moving_distance_without_a_jump_record_is_compared_as_is():
    # 점프를 빼기 전에 계산한 값(가공 구간에 점프 기록 없음)은 XML 과 같은 정의라 그대로 비교한다
    old = {**TDMS, "moving_distance": 1357.0, "position_jumps": None, "position_jump_mm": None}
    moving = {r["item"]: r for r in cmp.compare_values(cmp.xml_reference(XML), old)}["총 이동거리(mm)"]
    assert moving["diff"] == "-162.3 (-10.68%)" and moving["note"] == ""


def test_collect_reads_only_jobs_with_an_archived_xml():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    db = sessionmaker(bind=eng)()
    part = Part(part_name="DB_Test", project_code="Alchemist")
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code="O0911.nc", nc_hash="00183ad8")
    db.add(wp)
    db.flush()
    values = {k: v for k, v in TDMS.items() if k not in WINDOW_KEYS}
    window = {k: TDMS[k] for k in ("program_end_basis", "position_jumps", "position_jump_mm")}
    with_xml = Job(workplan_id=wp.workplan_id, source_folder="Alchemist/DB_Test/1", summary_source="TDMS",
                   machining_window=window, **values)
    without_xml = Job(workplan_id=wp.workplan_id, source_folder="Alchemist/DB_Test/2", **values)
    db.add_all([with_xml, without_xml])
    db.flush()
    db.add_all([JobFileArchive(job_id=with_xml.job_id, xml_file_content=XML),
                JobFileArchive(job_id=without_xml.job_id, tdms_raw_path="x.tdms")])
    db.commit()

    rows = cmp.collect(db)
    assert {r["job_id"] for r in rows} == {with_xml.job_id}
    assert len(rows) == len(cmp.FIELDS) and rows[0]["summary_source"] == "TDMS"
    assert [r for r in rows if r["verdict"] == "차이"] == [r for r in rows if r["item"] in ("가공 종료", "총 이동거리(mm)")]
    moving = next(r for r in rows if r["item"] == "총 이동거리(mm)")
    assert moving["diff"] == "-162.3 (-10.68%)"           # 가공 구간에 적힌 점프를 읽어 같은 정의로 비교
    assert cmp.collect(db, [without_xml.job_id]) == []
