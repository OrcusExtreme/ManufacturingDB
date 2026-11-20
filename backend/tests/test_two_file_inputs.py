"""입력을 TDMS + NC 두 개로 (P05).

  * 업로드 화면이 쓰는 TDMS 가공 프로그램 읽기 (tdms_alignment.read_tdms_programs) — 파일로 저장하기 전
    바이트열에서, 끝이 0 으로 채워진(수집 비정상 종료) 파일도
  * NC 와 TDMS 프로그램 이름 비교 (program_key / same_program / MachiningWindow.program_mismatch)
  * 가공 구간 재판별 조건 (tdms_visualizer._needs_processing): NC 가 늦게 오면 다시 판별하고, XML 은 기다리지 않는다
  * 수집기 시작 보정 (recovery_engine.backfill_missing_job_metadata) 이 로그 파서와 같은 절삭 규칙을 쓴다

실제 MySQL · 보관소 · data 폴더는 건드리지 않는다.
"""
import io
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from nptdms import ChannelObject, RootObject, TdmsWriter

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tests_dir = os.path.dirname(os.path.abspath(__file__))
for p in (backend_dir, tests_dir):
    if p not in sys.path:
        sys.path.append(p)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import job_layout
import recovery_engine
import tdms_alignment as tda
import tdms_visualizer as tv
from DB.database import Base
from DB.models import Job, Part, Workplan
from test_machine_log_sources import _log_rows, _log_text


# ---------------------------------------------------------------------------
# TDMS 가공 프로그램 읽기 (업로드 화면)
# ---------------------------------------------------------------------------

# DB_Test/1 과 같은 모양: 기록을 시작할 때 띄운 프로브(O2202.NC, 주축 정지) -> 가공(O0911.nc)
# -> 공구교환 매크로(O9009) -> 가공 프로그램의 급속 복귀
ROWS = ([("O2202.NC", "G31", 300.0, 0.0)] * 30
        + [("O0911.nc", "G01", 700.0, 3800.0)] * 60
        + [("O9009", "G00", 0.0, 0.0)] * 20
        + [("O0911.nc", "G00", 12000.0, 3800.0)] * 10)


def _tdms_bytes(rows=ROWS, tail=0, group="CNC"):
    t0 = pd.Timestamp("2026-09-11T07:42:15")
    ts = np.array([t0 + pd.to_timedelta(i * 0.037, unit="s") for i in range(len(rows))]).astype("datetime64[us]")
    channels = {
        "Time Channel CNC": ts,
        "CNC-ProgramName": np.array([r[0] for r in rows], dtype=object),
        "CNC-GModal": np.array([r[1] for r in rows], dtype=object),
        "CNC-ActualFeedRate": np.array([r[2] for r in rows], dtype=np.float64),
        "CNC-Z-SpindleSpeed": np.array([r[3] for r in rows], dtype=np.float64),
        "CNC-Z-SpindleLoad": np.full(len(rows), 12.0),       # 읽지 않아도 되는 채널
    }
    buf = io.BytesIO()
    with TdmsWriter(buf) as writer:
        writer.write_segment([RootObject(), *[ChannelObject(group, name, data) for name, data in channels.items()]])
    return buf.getvalue() + b"\0" * tail


def test_read_tdms_programs_picks_the_program_that_cut():
    program, seen = tda.read_tdms_programs(_tdms_bytes())
    assert program == "O0911.nc"                        # 파일명·첫 행의 O2202.NC 가 아니다
    assert seen == ["O2202.NC", "O0911.nc", "O9009"]


def test_read_tdms_programs_survives_a_zero_padded_tail():
    assert tda.read_tdms_programs(_tdms_bytes(tail=4096)) == tda.read_tdms_programs(_tdms_bytes())


def test_read_tdms_programs_without_cnc_group():
    assert tda.read_tdms_programs(_tdms_bytes(group="DAQ")) == (None, [])


def test_read_tdms_programs_rejects_other_files():
    with pytest.raises(Exception):
        tda.read_tdms_programs(b"this is not a tdms file" * 10)


# ---------------------------------------------------------------------------
# 프로그램 이름 비교
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ("O1110.NC", "o1110", True),            # 확장자·대소문자 차이는 같은 프로그램
    ("O0911.nc", "O0911.NC", True),
    ("O1110.NC", "O1111.NC", False),
    (None, "O1110.NC", None),               # 한쪽을 모르면 판단하지 않는다
    ("", "O1110.NC", None),
])
def test_same_program(a, b, expected):
    assert tda.same_program(a, b) is expected


def test_window_reports_program_mismatch():
    win = tda.MachiningWindow(start=None, end=None, row_start=0, row_end=0, method="nc_block",
                              program_name="O1110.NC", nc_program="O1110")
    assert win.program_mismatch is False and win.to_dict()["program_mismatch"] is False
    win.nc_program = "O2202"
    assert win.program_mismatch is True
    assert win.to_dict()["nc_program"] == "O2202" and win.to_dict()["program_mismatch"] is True
    win.nc_program = None                   # NC 를 못 찾은 판별은 경고하지 않는다
    assert win.program_mismatch is False


# ---------------------------------------------------------------------------
# 가공 구간 재판별 조건
# ---------------------------------------------------------------------------

@pytest.fixture
def needs(monkeypatch, tmp_path):
    parquet = tmp_path / "viz.parquet"
    parquet.write_bytes(b"x")
    state = {"nc": None, "parquet": str(parquet)}
    monkeypatch.setattr(tv, "archive_path", lambda job, key: state["parquet"])
    monkeypatch.setattr(tv, "get_abs_raw_data_path", lambda p: p)
    monkeypatch.setattr(tv, "find_nc_program", lambda job, *a, **k: state["nc"])

    def check(window, nc=None, parquet_present=True):
        state["nc"] = nc
        state["parquet"] = str(parquet) if parquet_present else str(tmp_path / "missing.parquet")
        job = SimpleNamespace(job_id=1, machining_window=window,
                              workplan=SimpleNamespace(program_code="O1110.NC"))
        return tv._needs_processing(job)
    return check


def test_needs_processing_when_parquet_or_window_is_missing(needs):
    assert needs({"method": "nc_block", "nc_reference": "O1110.NC(57종)"}, parquet_present=False)
    assert needs({})


def test_needs_processing_again_when_nc_arrives_late(needs):
    activity = {"method": "activity", "nc_reference": None}
    assert needs(activity, nc=object()) is True          # TDMS 먼저 -> NC 도착 -> 다시 판별
    assert needs(activity, nc=None) is False             # NC 가 아직 없으면 그대로


def test_needs_processing_does_not_wait_for_xml(needs):
    # 예전에는 참조 이름이 'Unknown…' 이고 Workplan 프로그램을 알게 되면(=XML 도착) 다시 판별했다
    assert needs({"method": "activity", "nc_reference": "Unknown(0종)"}, nc=object()) is False
    assert needs({"method": "nc_block", "nc_reference": "O1110.NC(57종)"}, nc=object()) is False


# ---------------------------------------------------------------------------
# 수집기 시작 보정: 로그 규칙
# ---------------------------------------------------------------------------

def test_backfill_uses_the_log_parser_rules(monkeypatch, tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    Session = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = Session()
    part = Part(part_name="TestPart", project_code="TestProj")
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code="Unknown", nc_hash="NOHASH")
    db.add(wp)
    db.flush()
    db.add(Job(workplan_id=wp.workplan_id, work_id="UNKNOWN_WORKID", source_folder="TestProj/TestPart/1"))
    db.commit()
    db.close()

    log = tmp_path / "DNM200__O0911.nc__PROBE START__260911164605_ext.log"
    log.write_text(_log_text(_log_rows()), encoding="utf-8")
    monkeypatch.setattr(recovery_engine, "engine", eng)
    monkeypatch.setattr(recovery_engine, "PROCESSED_ROOT", str(tmp_path))
    monkeypatch.setattr(recovery_engine.vault_layout, "find_job_parquet", lambda *a, **k: None)
    monkeypatch.setattr(recovery_engine.vault_layout, "list_in_job",
                        lambda folder, kind, *a, **k: [str(log)] if kind == job_layout.LOG_DIR else [])

    assert recovery_engine.backfill_missing_job_metadata() == 1
    job = Session().query(Job).one()
    # 절삭시간은 절삭 판정 6행 x 0.1초. 예전 규칙은 ctime(경과시간) 최댓값 1.0초를 넣었다.
    assert job.cutting_seconds == pytest.approx(0.6)
    assert (job.moving_distance, job.cutting_moving_distance) == (9.0, 6.0)
    assert job.start_time == datetime(2026, 9, 11, 16, 46, 5)        # 파일명 시각
    assert job.end_time == datetime(2026, 9, 11, 16, 46, 6)          # 마지막 행 (초 단위)
