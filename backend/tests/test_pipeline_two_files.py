"""TDMS + NC 두 파일만으로 Job 이 끝까지 완성되는지 — 수집 흐름 전체 회귀 테스트 (합성 TDMS).

실측 TDMS 는 수백 MB 라 저장소에 둘 수 없어서, 같은 모양의 작은 TDMS 를 만들어 실제 수집 함수
(parse_nc · parse_tdms · tdms_visualizer 데몬 한 바퀴 · parse_xml)를 그대로 돌린다.
흐름은 실측 DB_Test/1 과 같다: 기록 시작(대기) -> 프로브(O2202.NC) 실행 후 리셋 -> 본 가공(O1234.nc,
공구교환 매크로 O9009 포함) -> M30 -> 대기. 파일명의 프로그램은 기록 시작 때 띄운 O2202.NC 다.

  * 도착 순서: NC 먼저 / TDMS 먼저(NC 가 변환 뒤에 도착) — 최종 결과가 같아야 한다
  * 중단된 가공: 완료 여부 False 와 그 근거
  * 다른 프로그램 이름의 NC: 불일치 표시, 가공 프로그램은 TDMS 기준
  * 장비 XML 이 나중에 들어와도 TDMS+NC 결과와 Workplan 이 바뀌지 않는다

실제 MySQL · 보관소 · data 폴더는 건드리지 않는다 (인메모리 SQLite, 임시 폴더).
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from nptdms import ChannelObject, RootObject, TdmsWriter

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import DB.database as dbmod
import parsers.nc_parser as nc_parser
import parsers.tdms_parser as tdms_parser
import parsers.xml_parser as xml_parser
import tdms_visualizer as tv
import vault_layout
import vault_manager
from DB.database import Base
from DB.models import EnvMemo, Job, JobFileArchive, Workingstep, Workplan
from file_archive import get_or_create_job_archive
from parsers.nc_parser import nc_cutting_distance

FOLDER = "Synth/Part/1"
T0 = datetime(2026, 9, 11, 7, 42, 15)       # TDMS(UTC) 기록 시작 = 한국 16:42:15
DT = 0.04
TDMS_NAME = "DNM200__O2202.NC__PROBE START__260911164215.tdms"

NC_BODY = """G90G54G0X0Y0
M6T6
S3800M3
G43H6Z5
G1Z-2F710
X100
Y50
X0
G0Z5
G49G91G28Z0
M30
%
"""
NC_TEXT = "%\n<O1234.nc>\n" + NC_BODY
MAIN = 70                                    # 본 가공 실행 사이클이 시작하는 행
X_MEASURED = 100.4                           # TDMS 위치는 NC 지령(X100)과 조금 다르다 -> 출처를 가릴 수 있다
TOOL_LENGTH = 191.0                          # H6 공구 길이 (실측 DB_Test/1 의 G43H6 점프 191.06mm)


def build_rows(interrupt_after=None):
    """CNC 행 목록과 본 가공 사이클 [시작, 끝) 행. interrupt_after: 이 블록 뒤에서 리셋(중단).

    위치 채널은 실측처럼 공구 길이 보정이 들어간 좌표다. 보정을 켜기 전(G43 전, G49 뒤)에는 공구 끝보다
    TOOL_LENGTH 만큼 위로 보이고, G43·G49 블록 첫 행에서 표시만 한 번에 바뀐다 (그 행에서 공구는 그대로).
    """
    rows = []
    pos = [0.0, 0.0, 50.0]                   # 공구 끝
    shown = [TOOL_LENGTH]                    # 위치 표시에 더해지는 값 (보정을 켜면 0)

    def add(count, program, mode, block, g, feed, rpm, target=None, offset=None):
        start = list(pos)
        if offset is not None:
            shown[0] = offset
        for i in range(1, count + 1):
            if target is not None:
                pos[:] = [s + (t - s) * i / count for s, t in zip(start, target)]
            cutting = g == "G01" and feed > 0 and rpm > 0
            rows.append((program, mode, block, g, feed, rpm, pos[0], pos[1], pos[2] + shown[0],
                         12.0 if cutting else 2.0))

    add(30, "", 1, "", "G00", 0, 0)                                      # 기록만 켜 둔 대기
    add(20, "O2202.NC", 3, "G31Z-10.", "G31", 300, 0)                    # 프로브 (주축 정지)
    add(20, "O2202.NC", 0, "M30", "G00", 0, 0)                           # 프로브 끝, 리셋
    main = [
        (5, "O1234.nc", "G90G54G0X0Y0", "G00", 0, 0, None),
        (5, "O9009", "M6T6", "G00", 0, 0, None),                         # 공구교환 매크로
        (5, "O1234.nc", "S3800M3", "G00", 0, 3800, None),
        (1, "O1234.nc", "G43H6Z5", "G00", 0, 3800, None, 0.0),           # 보정 켬: 표시만 공구 길이만큼 내려감
        (5, "O1234.nc", "G43H6Z5", "G00", 7500, 3800, [0.0, 0.0, 5.0]),
        (10, "O1234.nc", "G1Z-2F710", "G01", 710, 3800, [0.0, 0.0, -2.0]),
        (50, "O1234.nc", "X100", "G01", 710, 3800, [X_MEASURED, 0.0, -2.0]),
        (25, "O1234.nc", "Y50", "G01", 710, 3800, [X_MEASURED, 50.0, -2.0]),
        (50, "O1234.nc", "X0", "G01", 710, 3800, [0.0, 50.0, -2.0]),
        (5, "O1234.nc", "G0Z5", "G00", 7500, 3800, [0.0, 50.0, 5.0]),
        (1, "O1234.nc", "G49G91G28Z0", "G00", 0, 0, None, TOOL_LENGTH),  # 보정 끔: 표시만 다시 올라감
        (5, "O1234.nc", "G49G91G28Z0", "G00", 7500, 0, [0.0, 50.0, 50.0]),
        (2, "O1234.nc", "M30", "G00", 0, 0, None),
    ]
    assert len(rows) == MAIN
    last_block = "M30"
    for count, program, block, g, feed, rpm, target, *offset in main:
        add(count, program, 3, block, g, feed, rpm, target, offset[0] if offset else None)
        last_block = block
        if block == interrupt_after:
            break
    cycle_end = len(rows)
    add(30, "O1234.nc", 0, last_block, "G00", 0, 0)                      # 리셋 후 대기
    return rows, (MAIN, cycle_end)


def write_tdms(path, rows):
    names = ["CNC-ProgramName", "CNC-ProgramMode", "CNC-CurrentBlock", "CNC-GModal", "CNC-ActualFeedRate",
             "CNC-Z-SpindleSpeed", "CNC-X-Position", "CNC-Y-Position", "CNC-Z-Position", "CNC-Z-SpindleLoad"]
    columns = list(zip(*rows))
    ts = np.array([np.datetime64(T0 + timedelta(seconds=i * DT), "us") for i in range(len(rows))])
    channels = [ChannelObject("CNC", "Time Channel CNC", ts)]
    for name, values in zip(names, columns):
        dtype = object if isinstance(values[0], str) else np.float64
        channels.append(ChannelObject("CNC", name, np.array(values, dtype=dtype)))
    with TdmsWriter(path) as writer:
        writer.write_segment([RootObject(), *channels])


def at(row):
    """CNC 행의 한국 시각."""
    return T0 + timedelta(seconds=row * DT, hours=9)


@pytest.fixture
def env(monkeypatch, tmp_path):
    raw = tmp_path / "machining_raw_data"
    job_dir = raw.joinpath(*FOLDER.split("/"))
    (job_dir / "TDMS").mkdir(parents=True)
    (job_dir / "NC").mkdir()
    processed = tmp_path / "processed_data"
    processed.mkdir()

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":       # MySQL 전용 기본값이라 모델로 못 만든다
            table.create(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE tool (tool_id INTEGER PRIMARY KEY, tool_code VARCHAR(20))"))
        conn.execute(text("INSERT INTO tool (tool_id, tool_code) VALUES (6, 'T06')"))
    Session = sessionmaker(bind=eng, autocommit=False, autoflush=False)   # 운영과 같은 설정

    monkeypatch.setattr(dbmod, "SessionLocal", Session)        # nc_parser.get_db 가 쓴다
    for mod in (tdms_parser, tv, xml_parser):
        monkeypatch.setattr(mod, "SessionLocal", Session)
    for mod in (nc_parser, tdms_parser, tv, xml_parser):
        monkeypatch.setattr(mod, "archive_file", lambda *a, **k: None)       # 보관소에 쓰지 않는다
    monkeypatch.setattr(vault_manager, "RAW_DATA_ROOT", str(raw))
    monkeypatch.setattr(tv, "RAW_DATA_ROOT", str(raw))
    monkeypatch.setattr(tv, "PROCESSED_DIR", str(processed))
    monkeypatch.setattr(vault_layout, "find_job_nc", lambda *a, **k: None)
    monkeypatch.setattr(vault_layout, "find_job_tdms", lambda *a, **k: None)

    def put_tdms(rows):
        path = job_dir / "TDMS" / TDMS_NAME
        write_tdms(str(path), rows)
        return str(path)

    def put_nc(text_=NC_TEXT, name="O1234.nc"):
        path = job_dir / "NC" / name
        path.write_text(text_, encoding="utf-8")
        return str(path)

    def daemon():
        """tdms_visualizer 데몬 한 바퀴씩, 처리할 Job 이 없을 때까지. 변환 횟수를 돌려준다."""
        conversions = 0
        for _ in range(4):
            db = Session()
            try:
                jobs = (db.query(Job).join(JobFileArchive, JobFileArchive.job_id == Job.job_id)
                        .filter(JobFileArchive.tdms_raw_path.isnot(None)).all())
                todo = [j for j in jobs if tv._needs_processing(j)]
                if not todo:
                    return conversions
                for job in todo:
                    parquet, _ = tv.process_tdms_file(job, db)
                    assert parquet, "TDMS 변환 실패"
                    get_or_create_job_archive(db, job.job_id).tdms_parquet_raw_path = parquet
                    db.commit()
                    conversions += 1
            finally:
                db.close()
        raise AssertionError("재처리가 끝나지 않는다")

    def job():
        db = Session()
        j = db.query(Job).one()
        j.workplan.program_code       # 세션을 닫기 전에 읽어 둔다
        j.machine_log
        return j, db

    return SimpleNamespace(Session=Session, job_dir=job_dir, put_tdms=put_tdms, put_nc=put_nc,
                           daemon=daemon, job=job)


def summary(env):
    j, db = env.job()
    try:
        w = j.machining_window or {}
        return {
            "start_time": j.start_time, "end_time": j.end_time, "cutting_seconds": j.cutting_seconds,
            "moving_distance": j.moving_distance, "cutting_moving_distance": j.cutting_moving_distance,
            "work_id": j.work_id, "machine_code": j.machine_code, "is_finish": j.is_finish,
            "summary_source": j.summary_source, "program_code": j.workplan.program_code,
            "workplans": db.query(Workplan).count(), "workplan_id": j.workplan_id,
            "steps": [(s.tool_number, s.spindle_speed, s.feed_rate) for s in
                      db.query(Workingstep).filter_by(workplan_id=j.workplan_id).order_by(Workingstep.step_order)],
            "env_memo": db.query(EnvMemo).filter_by(job_id=j.job_id).count(),
            "stats": (j.machine_log.stats_source, j.machine_log.max_spindle_rpm, j.machine_log.max_spindle_load)
            if j.machine_log else None,
            "method": w.get("method"), "nc_program": w.get("nc_program"),
            "program_mismatch": w.get("program_mismatch"), "end_basis": w.get("program_end_basis"),
            "jumps": (w.get("position_jumps"), w.get("position_jump_mm")),
        }
    finally:
        db.close()


NC_DISTANCE = round(nc_cutting_distance(NC_TEXT), 1)


def test_nc_cutting_distance_of_the_synthetic_program():
    assert NC_DISTANCE == pytest.approx(257.0)      # Z-2 7 + X100 + Y50 + X0 100


def test_two_files_complete_a_job(env):
    rows, (a, b) = build_rows()
    assert nc_parser.parse_nc(env.put_nc(), FOLDER)          # 업로드 화면은 NC 를 먼저 놓는다
    assert tdms_parser.parse_tdms(env.put_tdms(rows), FOLDER)
    assert env.daemon() == 1                                 # NC 를 바로 찾아 한 번에 끝난다

    s = summary(env)
    assert s["summary_source"] == "TDMS" and s["method"] == "nc_block"
    assert s["start_time"] == at(a)                          # 실행 사이클 시작 (파일명 16:42:15 아님)
    assert s["end_time"] == at(b - 1)
    assert s["cutting_seconds"] == pytest.approx(135 * DT)   # G01 + 이송 + 주축인 135행
    # 실제 이동만: G43·G49 위치 점프(공구 길이 191mm x 2)는 빼고 기록에 남긴다
    assert s["moving_distance"] == pytest.approx(45 + 7 + X_MEASURED + 50 + X_MEASURED + 7 + 45)
    assert s["jumps"] == (2, 2 * TOOL_LENGTH)
    assert s["cutting_moving_distance"] == NC_DISTANCE       # NC 블록 대조가 맞아 NC 경로 길이
    assert s["work_id"] == at(a).strftime("%y%m%d%H%M%S") and s["machine_code"] == "DNM200"
    assert s["is_finish"] is True and s["end_basis"].startswith("M02/M30")
    assert s["program_code"] == "O1234.nc"                   # 파일명의 O2202.NC 가 아니다
    assert s["nc_program"] == "O1234.nc" and s["program_mismatch"] is False
    assert s["steps"] and all(step[0] == 6 for step in s["steps"])
    assert s["stats"] == ("TDMS", 3800.0, 12.0)
    assert s["env_memo"] == 1 and s["workplans"] == 1
    assert env.daemon() == 0                                 # 다시 돌려도 재처리하지 않는다


def test_nc_arriving_after_the_tdms_conversion_gives_the_same_job(env):
    rows, _ = build_rows()
    assert tdms_parser.parse_tdms(env.put_tdms(rows), FOLDER)
    assert env.daemon() == 1
    before = summary(env)
    assert before["method"] == "activity"                    # NC 없이 활동 기반으로 판별
    assert before["cutting_moving_distance"] == pytest.approx(7 + X_MEASURED + 50 + X_MEASURED)  # TDMS 실측
    assert before["steps"] == []

    assert nc_parser.parse_nc(env.put_nc(), FOLDER)
    assert env.daemon() == 1                                 # NC 가 생겨 한 번 더 판별
    after = summary(env)
    assert after["method"] == "nc_block" and after["cutting_moving_distance"] == NC_DISTANCE
    assert after["start_time"] == before["start_time"] and after["cutting_seconds"] == before["cutting_seconds"]
    assert after["moving_distance"] == before["moving_distance"] and after["jumps"] == before["jumps"] == (2, 382.0)
    assert after["is_finish"] is True and after["steps"]
    assert env.daemon() == 0


def test_same_result_whichever_file_arrives_first(env):
    rows, _ = build_rows()
    assert nc_parser.parse_nc(env.put_nc(), FOLDER)
    assert tdms_parser.parse_tdms(env.put_tdms(rows), FOLDER)
    env.daemon()
    nc_first = summary(env)

    # 같은 두 파일을 TDMS 먼저 순서로, 비운 DB 에 다시 넣는다 (Parquet 은 Job 폴더 자리에 덮어쓴다)
    db = env.Session()
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != "tool":
            db.execute(table.delete())
    db.commit()
    db.close()
    assert tdms_parser.parse_tdms(str(env.job_dir / "TDMS" / TDMS_NAME), FOLDER)
    assert nc_parser.parse_nc(str(env.job_dir / "NC" / "O1234.nc"), FOLDER)
    env.daemon()
    tdms_first = summary(env)
    keys = ("start_time", "end_time", "cutting_seconds", "moving_distance", "cutting_moving_distance",
            "work_id", "machine_code", "is_finish", "program_code", "steps", "stats", "method", "end_basis",
            "jumps")
    assert {k: tdms_first[k] for k in keys} == {k: nc_first[k] for k in keys}


def test_interrupted_run_is_not_finished(env):
    rows, (a, b) = build_rows(interrupt_after="X100")         # X100 뒤에서 리셋
    assert nc_parser.parse_nc(env.put_nc(), FOLDER)
    assert tdms_parser.parse_tdms(env.put_tdms(rows), FOLDER)
    env.daemon()
    s = summary(env)
    assert s["method"] == "nc_block"
    assert s["end_time"] == at(b - 1)
    assert s["cutting_seconds"] == pytest.approx(60 * DT)     # G1Z-2 10행 + X100 50행
    assert s["moving_distance"] == pytest.approx(45 + 7 + X_MEASURED) and s["jumps"] == (1, TOOL_LENGTH)  # G49 전
    assert s["is_finish"] is False
    assert s["end_basis"] == "남은 동작 블록 5개 (> 허용 3개)"  # Y50 · X0 · G0Z5 · G49G91G28Z0 · M30


def test_nc_with_another_program_name_is_flagged(env):
    rows, _ = build_rows()
    other = "%\n<O9999.nc>\n" + NC_BODY                        # 내용은 같고 이름만 다른 NC
    assert nc_parser.parse_nc(env.put_nc(other, name="O9999.nc"), FOLDER)
    assert tdms_parser.parse_tdms(env.put_tdms(rows), FOLDER)
    env.daemon()
    s = summary(env)
    assert s["nc_program"] == "O9999.nc" and s["program_mismatch"] is True
    assert s["program_code"] == "O1234.nc"                   # 가공 프로그램은 TDMS 에서 깎은 것
    j, db = env.job()
    try:
        assert any("다릅니다" in note for note in j.machining_window["notes"])
    finally:
        db.close()


XML_LATE = """<?xml version="1.0" encoding="utf-8"?>
<WorkModel>
  <WorkID>260911164217</WorkID>
  <ProjectCode>Synth</ProjectCode>
  <MachineCode>DNM200</MachineCode>
  <MachineIpAddress>10.0.0.5</MachineIpAddress>
  <MaterialCode>AL6061</MaterialCode>
  <PartCode>Part</PartCode>
  <ProgramCode>O1234</ProgramCode>
  <ToolNumbers><int>6</int></ToolNumbers>
  <Tools><ToolModel><Properties>
    <anyType><Name>toolCode</Name><Value>T6_10</Value></anyType>
    <anyType><Name>toolUsedCount</Name><Value>12</Value></anyType>
  </Properties></ToolModel></Tools>
  <CuttingSeconds>99.9</CuttingSeconds>
  <MovingDistance>999.9</MovingDistance>
  <CuttingMovingDistance>999.9</CuttingMovingDistance>
  <IsFinish>false</IsFinish>
  <IsError>false</IsError>
</WorkModel>
"""


def test_machine_xml_arriving_later_only_fills_blanks(env):
    rows, _ = build_rows()
    assert nc_parser.parse_nc(env.put_nc(), FOLDER)
    assert tdms_parser.parse_tdms(env.put_tdms(rows), FOLDER)
    env.daemon()
    before = summary(env)

    xml_dir = env.job_dir / "XML"
    xml_dir.mkdir()
    xml_path = xml_dir / "DNM200__O1234__Part__260911164217_0.xml"
    xml_path.write_text(XML_LATE, encoding="utf-8")
    xml_parser.parse_xml(str(xml_path), FOLDER)

    after = summary(env)
    # 프로그램 표기가 달라도('O1234' / 'O1234.nc') Workplan 을 새로 만들거나 Job 을 옮기지 않는다.
    # 예전에는 새 Workplan 으로 옮기면서 원래 Workplan 과 워킹스텝을 고아로 지웠다.
    assert after == before
    j, db = env.job()
    try:
        assert j.machine_ip == "10.0.0.5"                    # XML 에만 있는 값은 채운다
        assert j.tool_conditions[0]["used_count"] == 12
        assert db.query(JobFileArchive).filter_by(job_id=j.job_id).one().xml_file_content
        assert j.workplan.part.material_code == "AL6061"     # 비어 있던 소재도 채운다
    finally:
        db.close()
