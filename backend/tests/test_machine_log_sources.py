"""CNC 요약(machine_log)의 출처와 장비 계산값, 기존 Job 다시 계산.

  * 장비 로그(_ext.log)의 장비 계산값 요약 (parsers/log_parser.summarize_machine_indicators)
  * 알람 열이 없는 로그는 알람 '모름' (0건이 아니다)
  * TDMS 로 계산한 최대·평균 값은 나중에 들어온 로그가 덮어쓰지 않는다
  * backend/recompute_job_summary.py: 예전 XML 기준 Job 을 TDMS+NC 기준으로 다시 계산

실제 MySQL · 보관소는 건드리지 않는다 (인메모리 SQLite, 보관 함수는 바꿔 끼운다).
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tests_dir = os.path.dirname(os.path.abspath(__file__))
for p in (backend_dir, tests_dir):
    if p not in sys.path:
        sys.path.append(p)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from DB.database import Base
from DB.models import Job, JobFileArchive, LogFileArchive, MachineLog, Part, Workplan, WorkplanFileArchive
import parsers.log_parser as log_parser
from test_tdms_cycle import NC_TEXT, at, scenario

HEADER = ["time", "ctime", "afmax", "ektc", "echtlevel", "esound", "cut", "crpm", "cfr", "cls", "cpx", "cpy", "cpz"]


def _log_text(rows):
    lines = ["\t".join(HEADER)]
    for r in rows:
        lines.append("\t".join(str(r.get(h, "")) for h in HEADER))
    return "\n".join(lines) + "\n"


def _log_rows():
    """0.1초 간격 10행: 앞 2행은 대기, 6행 절삭(cut=1), 끝 2행 급속."""
    rows = []
    t0 = datetime(2026, 9, 11, 16, 46, 5, 660000)
    for i in range(10):
        cutting = 2 <= i < 8
        rows.append({
            "time": (t0 + timedelta(seconds=0.1 * i)).strftime("%y%m%d %H:%M:%S.%f")[:-3],
            "ctime": f"{0.1 * (i + 1):.3f}", "afmax": "", "ektc": "0.000",
            "echtlevel": "0.500" if i == 5 else "0.000", "esound": f"{100 + i}.0",
            "cut": "1" if cutting else "0", "crpm": "3800" if cutting else "0",
            "cfr": "710" if cutting else ("12728" if i >= 8 else "0"),
            "cls": "40" if cutting else "1", "cpx": f"{i}", "cpy": "0", "cpz": "0",
        })
    return rows


@pytest.fixture
def Session(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    factory = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    monkeypatch.setattr(log_parser, "SessionLocal", factory)
    monkeypatch.setattr(log_parser, "archive_file", lambda *a, **k: None)   # 실제 보관소에 쓰지 않는다
    return factory


def _job(db, folder="TestProj/TestPart/1", **values):
    part = Part(part_name=folder.split("/")[1], project_code=folder.split("/")[0])
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code=values.pop("program_code", "Unknown"), nc_hash="NOHASH")
    db.add(wp)
    db.flush()
    db.add(WorkplanFileArchive(workplan_id=wp.workplan_id, nc_file_content=NC_TEXT.encode()))
    job = Job(workplan_id=wp.workplan_id, work_id=values.pop("work_id", "UNKNOWN_WORKID"),
              source_folder=folder, **values)
    db.add(job)
    db.flush()
    db.add(JobFileArchive(job_id=job.job_id,
                          tdms_raw_path=f"{folder}/TDMS/DNM200__O2202.NC__PROBE START__260911164215.tdms"))
    db.commit()
    return job


# ---------------------------------------------------------------------------
# 장비 계산값 · 알람
# ---------------------------------------------------------------------------

def test_summarize_machine_indicators():
    summary = log_parser.summarize_machine_indicators(_log_rows(), "x_ext.log")
    assert (summary["rows"], summary["cutting_rows"], summary["basis"]) == (10, 6, "절삭 중(cut=1)")
    # CNC 상태를 옮긴 열(crpm·cfr·cls·위치·시각)은 빼고 장비 계산값만 남긴다
    assert set(summary["values"]) == {"echtlevel", "esound"}
    assert summary["values"]["echtlevel"] == {"mean": pytest.approx(0.5 / 6, abs=1e-5), "max": 0.5, "min": 0.0}
    assert summary["values"]["esound"]["mean"] == pytest.approx(104.5)        # 절삭 행(2~7)만
    assert summary["labels"]["echtlevel"] == "채터 수준 (장비 추정)"
    assert summary["all_zero"] == ["ektc"] and summary["empty"] == ["afmax"]
    assert log_parser.summarize_machine_indicators([]) is None


def test_alarm_is_unknown_without_an_alarm_column():
    assert log_parser.alarm_summary(HEADER, _log_rows()) == (None, None)
    rows = [{"alarm_msg": "SV0401"}, {"alarm_msg": ""}, {"alarm_msg": "SV0401"}, {"alarm_msg": "OT0500"}]
    assert log_parser.alarm_summary(["alarm_msg"], rows) == (2, "OT0500, SV0401")


def test_log_motion_summary_uses_the_cut_flag_not_ctime():
    s = log_parser.log_motion_summary(HEADER, _log_rows())
    assert s["cutting_seconds"] == pytest.approx(0.6)        # 절삭 6행 x 0.1초 (ctime 최댓값은 1.0초)
    assert (s["moving_distance"], s["cutting_moving_distance"]) == (9.0, 6.0)
    assert (s["start_time"], s["end_time"]) == (datetime(2026, 9, 11, 16, 46, 5), datetime(2026, 9, 11, 16, 46, 6))
    assert (s["max_spindle_load"], s["max_spindle_rpm"], s["max_feed_rate"]) == (40.0, 3800.0, 12728.0)
    assert s["avg_spindle_rpm"] == pytest.approx(3800 * 6 / 10)


def test_fill_job_from_log_only_fills_blanks():
    s = log_parser.log_motion_summary(HEADER, _log_rows())
    job = SimpleNamespace(start_time=datetime(2026, 9, 11, 16, 46, 0), end_time=None, cutting_seconds=None,
                          moving_distance=5.0, cutting_moving_distance=None)
    assert log_parser.fill_job_from_log(job, s) == ["end_time", "cutting_seconds"]
    assert job.start_time == datetime(2026, 9, 11, 16, 46, 0)        # 이미 있는 값은 그대로
    # 전체 이동거리가 다른 출처면 절삭 이동거리만 로그로 채우지 않는다 (출처가 섞이지 않게)
    assert job.cutting_moving_distance is None


def test_read_log_rows_refuses_other_files(tmp_path):
    good = tmp_path / "a_ext.log"
    good.write_text(_log_text(_log_rows()), encoding="utf-8")
    fieldnames, rows = log_parser.read_log_rows(str(good))
    assert fieldnames[:3] == ["time", "ctime", "afmax"] and len(rows) == 10
    other = tmp_path / "tdms_error.log"
    other.write_text("TDS Exception in Initialize\n", encoding="utf-8")
    assert log_parser.read_log_rows(str(other))[1] is None


# ---------------------------------------------------------------------------
# 로그 파서: 출처 규칙
# ---------------------------------------------------------------------------

def test_log_parser_fills_stats_and_indicators_when_no_tdms(Session, tmp_path):
    db = Session()
    job = _job(db)
    path = tmp_path / "DNM200__O0911.nc__PROBE START__260911164605_ext.log"
    path.write_text(_log_text(_log_rows()), encoding="utf-8")
    assert log_parser.parse_log(str(path), job.source_folder)

    db = Session()
    job = db.get(Job, job.job_id)
    m_log = job.machine_log
    assert (m_log.stats_source, m_log.max_spindle_load, m_log.max_spindle_rpm, m_log.max_feed_rate) == \
        ("LOG", 40.0, 3800.0, 12728.0)
    assert m_log.alarm_count is None
    assert set(m_log.machine_indicators["values"]) == {"echtlevel", "esound"}
    # 절삭시간 임시값: 절삭 판정 6행 x 0.1초 (ctime 은 경과시간이라 쓰지 않는다)
    assert job.cutting_seconds == pytest.approx(0.6)


def test_log_parser_keeps_tdms_stats(Session, tmp_path):
    db = Session()
    job = _job(db)
    db.add(MachineLog(job_id=job.job_id, max_spindle_load=12.0, max_spindle_rpm=3810.0, max_feed_rate=12728.0,
                      avg_spindle_rpm=3648.3, stats_source="TDMS"))
    db.commit()
    path = tmp_path / "late_ext.log"
    path.write_text(_log_text(_log_rows()), encoding="utf-8")
    assert log_parser.parse_log(str(path), job.source_folder)

    m_log = Session().get(Job, job.job_id).machine_log
    assert (m_log.stats_source, m_log.max_spindle_load, m_log.avg_spindle_rpm) == ("TDMS", 12.0, 3648.3)
    assert m_log.machine_indicators["cutting_rows"] == 6          # 장비 계산값은 로그에서 적는다


# ---------------------------------------------------------------------------
# 기존 Job 다시 계산 (recompute_job_summary)
# ---------------------------------------------------------------------------

def _legacy_job(db, tmp_path):
    """예전 방식으로 XML·로그 값이 적힌 Job (실측 Job 1 과 같은 모양)."""
    job = _job(db, summary_source="XML", program_code="O0911.nc", work_id="260911164605",
               start_time=datetime(2026, 9, 11, 16, 46, 5), end_time=datetime(2026, 9, 11, 16, 47, 0),
               cutting_seconds=44.8, moving_distance=1519.3, cutting_moving_distance=526.8, is_finish=True,
               is_error=False, machining_window={"method": "nc_block", "daq_sync": {"lag_seconds": 0.12}})
    log = tmp_path / "legacy_ext.log"
    log.write_text(_log_text(_log_rows()), encoding="utf-8")
    m_log = MachineLog(job_id=job.job_id, max_spindle_load=192.0, max_spindle_rpm=3808.0, max_feed_rate=12728.0,
                       avg_spindle_rpm=3553.3, alarm_count=0)
    db.add(m_log)
    db.flush()
    db.add(LogFileArchive(log_id=m_log.log_id, log_raw_path=str(log)))
    db.commit()
    return job


def test_recompute_turns_a_legacy_job_into_the_tdms_basis(Session, tmp_path):
    import recompute_job_summary as rjs
    from parsers.nc_parser import nc_cutting_distance

    db = Session()
    job = _legacy_job(db, tmp_path)
    frame, (a, b), first_cut = scenario()
    frame.loc[:first_cut - 2, "CNC-Z-Position"] = 191.06               # G43 을 켜기 전: 공구 길이만큼 위로 보인다
    before, after = rjs.recompute(db, job, cnc_frame=frame)
    db.commit()

    assert before["summary_source"] == "XML" and after["summary_source"] == "TDMS"
    job = Session().get(Job, job.job_id)
    assert job.start_time == at(a) + timedelta(hours=9)                 # 사이클 시작으로 바뀐다
    assert job.cutting_seconds == pytest.approx(100 * 0.04)
    # 총 이동거리는 위치 점프를 뺀 실제 이동. 가공 구간을 다시 적어도(DAQ 보정값 유지) 점프 기록이 남는다
    assert job.moving_distance == pytest.approx(110.0)
    assert (job.machining_window["position_jumps"], job.machining_window["position_jump_mm"]) == (1, 191.1)
    assert job.cutting_moving_distance == pytest.approx(round(nc_cutting_distance(NC_TEXT), 1))
    assert job.work_id == (at(a) + timedelta(hours=9)).strftime("%y%m%d%H%M%S")
    assert job.machine_code == "DNM200"
    assert job.machining_window["daq_sync"] == {"lag_seconds": 0.12}   # DAQ 보정값은 그대로
    assert job.machining_window["program_name"] == "O0911.nc"
    # 변환 때와 같이 NC 첫머리의 프로그램도 남긴다 (Job 화면의 불일치 경고가 이 값을 본다)
    assert (job.machining_window["nc_program"], job.machining_window["program_mismatch"]) == ("O0911.nc", False)
    m_log = job.machine_log
    assert (m_log.stats_source, m_log.max_spindle_load) == ("TDMS", 12.0)
    assert m_log.alarm_count is None                                    # 0건이 아니라 모름
    assert set(m_log.machine_indicators["values"]) == {"echtlevel", "esound"}


def test_recompute_preview_changes_nothing(Session, tmp_path):
    import recompute_job_summary as rjs
    db = Session()
    job = _legacy_job(db, tmp_path)
    frame, _, _ = scenario()
    before, after = rjs.recompute(db, job, cnc_frame=frame, flush=False)
    assert after["summary_source"] == "TDMS"
    db.rollback()

    job = Session().get(Job, job.job_id)
    assert (job.summary_source, job.cutting_seconds, job.start_time) == \
        ("XML", 44.8, datetime(2026, 9, 11, 16, 46, 5))
    assert job.machine_log.alarm_count == 0


def test_recompute_preview_marks_only_values_that_change_when_stored():
    import recompute_job_summary as rjs
    # 시작·종료는 초 단위 DATETIME 으로 저장된다 — 1/1000초만 다르면 저장값은 그대로다 (16:46:58.716 -> 16:46:59)
    assert rjs._same_when_stored("end_time", "2026-09-11 16:46:59", "2026-09-11 16:46:58.715858")
    assert not rjs._same_when_stored("end_time", "2026-09-11 16:47:00", "2026-09-11 16:46:58.715858")
    assert not rjs._same_when_stored("start_time", None, "2026-09-11 16:46:05.313064")
    assert not rjs._same_when_stored("moving_distance", 1357.0, 1165.9)
