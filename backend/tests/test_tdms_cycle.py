"""TDMS(CNC 채널)+NC 를 가공 요약의 기준으로 삼는 부분.

  * 시각 기준: TDMS(UTC) -> 한국 시간(+9시간)
  * 가공 프로그램: CNC-ProgramName 중 실제로 절삭한 프로그램
  * 가공 시작·종료: 운전 상태(CNC-ProgramMode)의 실행 사이클 (TDMS 기록 시작이 아니다)
  * 절삭 판정: 절삭 G 코드 + 이송 + 주축
  * Job 요약 기록 규칙 (tdms_visualizer._apply_window_to_job)

실제 TDMS 는 수백 MB 라 여기서는 CNC 행을 합성한다. 흐름은 실측 DB_Test/1 과 같다:
기록 시작(프로그램 없음) -> 중단된 첫 시도(리셋) -> 공구교환 매크로(O9009) -> 일시정지가 낀 본 가공 -> M2 로 끝.
실제 MySQL 은 건드리지 않는다 (요약 기록은 인메모리 SQLite).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

import tdms_alignment as tda

T0 = datetime(2026, 9, 11, 7, 42, 15)        # TDMS(UTC) 기록 시작 = 한국 16:42:15
DT = 0.04                                     # CNC 폴링 간격 (약 25Hz)
MAIN = 100                                    # 본 가공 실행 사이클이 시작하는 행
COLUMNS = ["CNC-ProgramName", "CNC-ProgramMode", "CNC-CurrentBlock", "CNC-GModal",
           "CNC-ActualFeedRate", "CNC-Z-SpindleSpeed"]

NC_TEXT = """%
<O0911.nc>
G90G80G49G40G0
G91G28Z0
M6T6
S3800M3
G43H6Z5
G1Z-2F710
Y115
G0Z5
M2
%
"""


def scenario(end_block="M2", with_mode=True):
    """(CNC 프레임, 본 가공 사이클 [시작, 끝) 행, 첫 절삭 행)."""
    rows, xs = [], []
    x = 0.0

    def add(count, program, mode, block, g, feed, rpm, step=0.0):
        nonlocal x
        for _ in range(count):
            x += step
            rows.append((program, mode, block, g, feed, rpm))
            xs.append(x)

    add(50, "", 1, "O0000%", "G00", 0, 0)                           # 기록만 켜 둔 대기
    add(10, "O0911.nc", 3, "G90G80G49G40G0", "G00", 0, 0)           # 첫 시도 ...
    add(40, "O0911.nc", 0, "G43H6Z5", "G00", 0, 0)                  # ... 절삭 없이 리셋
    add(5, "O0911.nc", 3, "G91G28Z0", "G00", 7500, 0)               # 본 가공: 사이클 시작
    add(1, "O9009", 3, "M6T6", "G00", 0, 0)                         # 공구교환 매크로
    add(5, "O0911.nc", 3, "S3800M3", "G00", 0, 3800)
    first_cut = len(rows)
    add(50, "O0911.nc", 3, "G1Z-2F710", "G01", 710, 3800, step=1.0)
    add(10, "O0911.nc", 2, "G1Z-2F710", "G01", 0, 3800)             # 일시정지(HOLD)
    add(50, "O0911.nc", 3, "Y115", "G01", 710, 3800, step=1.0)
    add(5, "O0911.nc", 3, "G0Z5", "G00", 7500, 3800, step=2.0)      # 급속: 절삭 아님
    add(1, "O0911.nc", 2, end_block, "G00", 0, 0)
    cycle_end = len(rows)
    add(31, "O0911.nc", 0, end_block, "G00", 0, 0)                  # 리셋 후 대기

    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["CNC-X-Position"] = xs
    frame["CNC-Y-Position"] = 0.0
    frame["CNC-Z-Position"] = 0.0
    # 주축 부하: 절삭하는 동안만 12%
    frame["CNC-Z-SpindleLoad"] = (frame["CNC-GModal"].eq("G01") & frame["CNC-ActualFeedRate"].gt(0)) * 12.0
    frame["timestamp"] = pd.to_datetime([T0 + timedelta(seconds=i * DT) for i in range(len(rows))])
    if not with_mode:
        frame = frame.drop(columns=["CNC-ProgramMode"])
    return frame, (MAIN, cycle_end), first_cut


def window_for(frame, a, b, method="nc_block", end_gap=None):
    """NC 블록 대조가 고른 것처럼 가공 구간을 만든다 (판별 자체는 test_tdms_alignment.py 가 본다)."""
    ts = frame["timestamp"]
    return tda.MachiningWindow(start=ts.iloc[a].to_pydatetime(), end=ts.iloc[b - 1].to_pydatetime(),
                               row_start=a, row_end=b, method=method, total_rows=len(frame),
                               hint_shift_hours=tda.LOCAL_UTC_OFFSET_HOURS, end_gap_blocks=end_gap)


def at(i):
    return T0 + timedelta(seconds=i * DT)


# ---------------------------------------------------------------------------
# 시각 기준 · 파일명
# ---------------------------------------------------------------------------

def test_tdms_time_is_utc_and_db_time_is_kst():
    assert tda.tdms_to_local(datetime(2026, 11, 11, 9, 55, 22)) == datetime(2026, 11, 11, 18, 55, 22)
    assert tda.tdms_to_local(datetime(2026, 11, 11, 9, 55, 22, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 11, 18, 55, 22)
    assert tda.local_to_tdms(datetime(2026, 11, 11, 18, 55, 22)) == datetime(2026, 11, 11, 9, 55, 22)
    assert tda.local_to_tdms("2026-09-11T16:46:05+09:00") == datetime(2026, 9, 11, 7, 46, 5)
    assert tda.tdms_to_local(None) is None and tda.tdms_to_local(pd.NaT) is None


def test_parse_tdms_filename():
    info = tda.parse_tdms_filename(r"C:\x\DNM200__O2202.NC__PROBE START__260911164215.tdms")
    assert info == {"machine": "DNM200", "program": "O2202.NC", "label": "PROBE START",
                    "recorded_at": datetime(2026, 9, 11, 16, 42, 15)}
    assert tda.parse_tdms_filename("odd_name__261111185522.TDMS")["recorded_at"] == datetime(2026, 11, 11, 18, 55, 22)
    assert tda.parse_tdms_filename("data.tdms") == {"machine": None, "program": None, "label": None,
                                                    "recorded_at": None}


def test_window_local_times_are_kst_even_without_xml():
    """예전에는 XML 시작·종료가 없으면 시차를 0 으로 두어 종료시각이 9시간 이르게 저장됐다."""
    frame, _, _ = scenario()
    win = tda.detect_machining_window(frame, None)          # 힌트 없음 (XML 없음)
    assert win.hint_shift_hours == 9.0
    assert win.local_start == win.start + timedelta(hours=9)
    assert win.local_end == win.end + timedelta(hours=9)


def test_kst_hint_is_compared_in_utc():
    frame, (a, b), _ = scenario()
    hint_s, hint_e = at(a) + timedelta(hours=9), at(b - 1) + timedelta(hours=9)   # 한국 시간 힌트
    win = tda.detect_machining_window(frame, None, hint_s, hint_e)
    assert win.hint_start == at(a) and win.hint_end == at(b - 1)
    assert not any("어긋나" in n for n in win.notes)
    other = tda.detect_machining_window(frame, None, datetime(2025, 5, 5, 1), datetime(2025, 5, 5, 1, 1))
    assert any("겹치지 않아" in n for n in other.notes)


# ---------------------------------------------------------------------------
# 절삭 판정 · 가공 프로그램
# ---------------------------------------------------------------------------

def test_cutting_mask_needs_cutting_g_feed_and_spindle():
    frame = pd.DataFrame({
        "CNC-GModal": ["G00", "G01", "G01", "G02", "G81", "G1"],
        "CNC-ActualFeedRate": [7500, 710, 300, 500, 200, 100],
        "CNC-Z-SpindleSpeed": [3800, 3800, 0, 3800, 1000, 900],
    })
    # 급속(G00)은 이송이 있어도 절삭이 아니고, 주축이 멈춘 G01 이송은 프로브 측정이다
    assert tda.cutting_mask(frame).tolist() == [False, True, False, True, True, True]


def _positions(z, x=None, seconds=None):
    """위치 프레임. seconds: 행마다 시각(초), 없으면 0.038초 간격 (실측 약 27Hz)."""
    n = len(z)
    seconds = seconds if seconds is not None else [i * 0.038 for i in range(n)]
    return pd.DataFrame({"timestamp": pd.to_datetime([T0 + timedelta(seconds=s) for s in seconds]),
                         "CNC-X-Position": x if x is not None else [0.0] * n,
                         "CNC-Y-Position": [0.0] * n, "CNC-Z-Position": z})


def test_travel_steps_separates_tool_length_jumps_from_real_moves():
    # 실측 DB_Test/1 G43H6Z5: Z 표시가 한 간격(0.038초)에 -191.06mm (5,000 mm/s 넘음), 그 뒤 실제 하강.
    # X 는 급속이송 380 mm/s(실측 최대) — 실제 이동이다.
    frame = _positions(z=[360.28, 360.28, 169.22, 167.24, 163.60, 163.60, 163.60, 163.60],
                       x=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 14.44, 28.88])
    step, jump = tda.travel_steps(frame)
    assert jump.tolist() == [False, False, True, False, False, False, False, False]
    assert step[jump].sum() == pytest.approx(191.06)
    assert step[~jump].sum() == pytest.approx(1.98 + 3.64 + 14.44 + 14.44)


def test_travel_steps_does_not_call_real_moves_jumps():
    # 시각이 겹친 행(간격 0)도 기록 주기로 쳐서, 14mm 이동을 '무한히 빠른' 점프로 보지 않는다
    dup = _positions(z=[0.0, 0.0, 14.0, 28.0], seconds=[0.0, 0.038, 0.038, 0.076])
    assert not tda.travel_steps(dup)[1].any()
    # 기록이 1초 끊긴 사이의 191mm 는 그동안 실제로 움직였을 수 있다 (191 mm/s)
    gap = _positions(z=[0.0, 0.0, 191.0, 191.0], seconds=[0.0, 0.038, 1.038, 1.076])
    assert not tda.travel_steps(gap)[1].any()
    # 시각 열이 숫자(경과 초, 그래프용 Parquet 의 time_s)여도 같다
    elapsed = _positions(z=[360.0, 360.0, 169.0]).assign(time_s=[0.0, 0.038, 0.076])
    assert tda.travel_steps(elapsed, time_column="time_s")[1].tolist() == [False, False, True]
    # 시각을 모르면 점프를 가리지 않고, 위치 채널이 없으면 None
    assert not tda.travel_steps(elapsed.drop(columns=["timestamp"]))[1].any()
    assert tda.travel_steps(elapsed.drop(columns=["CNC-Z-Position"])) is None


def test_machining_program_is_the_one_that_cut():
    frame, _, _ = scenario()
    # 행 수로는 대기·리셋 행이 많아도, 깎은 시간으로 고른다. 공구교환 매크로(O9009)는 아니다.
    assert tda.machining_program(frame) == "O0911.nc"
    probe_only = pd.DataFrame({"CNC-ProgramName": ["O2202.NC"] * 5 + ["O9009"],
                               "CNC-GModal": ["G01"] * 6, "CNC-ActualFeedRate": [100] * 6,
                               "CNC-Z-SpindleSpeed": [0] * 6})
    assert tda.machining_program(probe_only) == "O2202.NC"   # 절삭이 없으면 가장 많이 나온 이름


# ---------------------------------------------------------------------------
# 실행 사이클 (가공 시작·종료)
# ---------------------------------------------------------------------------

def test_cycle_starts_when_the_program_runs_and_skips_the_aborted_attempt():
    frame, (a, b), first_cut = scenario()
    win = tda.describe_cycle(frame, window_for(frame, a + 6, b - 3))   # 앞뒤가 조금 잘린 대조 구간
    assert (win.cycle_row_start, win.cycle_row_end) == (a, b)          # 일시정지(2)도 같은 사이클
    assert win.machining_start == at(a) + timedelta(hours=9)           # 16:42:19, 기록 시작(16:42:15) 아님
    assert win.machining_end == at(b - 1) + timedelta(hours=9)
    assert win.local_first_cut == at(first_cut) + timedelta(hours=9)
    assert win.program_name == "O0911.nc"
    assert win.program_end_reached is True                             # 끝에 M2
    json.dumps(win.to_dict())                                          # DB 의 JSON 칸에 그대로 들어간다


def test_cycle_without_program_mode_uses_the_window():
    frame, (a, b), _ = scenario(with_mode=False)
    win = tda.describe_cycle(frame, window_for(frame, a + 6, b - 3))
    assert (win.cycle_row_start, win.cycle_row_end) == (a + 6, b - 3)


def test_run_state_far_before_the_window_is_not_trusted():
    frame, (a, b), _ = scenario()
    frame.loc[:a, "CNC-ProgramMode"] = 3          # 기록 처음부터 '실행' 으로 찍힌 장비
    frame["timestamp"] = pd.to_datetime([T0 + timedelta(seconds=i * 3.0) for i in range(len(frame))])
    win = tda.describe_cycle(frame, window_for(frame, a, b))
    assert win.cycle_row_start == a               # 300초 앞선 '실행' 은 믿지 않는다
    assert any("앞서" in n for n in win.notes)


@pytest.mark.parametrize("end_block, gap, expected, basis", [
    ("M2", None, True, "M02/M30 실행 확인"),
    ("M30", 0, True, "M02/M30 실행 확인 (남은 동작 블록 0개)"),
    ("G0Z5", 1, True, "남은 동작 블록 1개 (≤ 허용 3개)"),        # DB_Test2/1: M30 이 폴링에 안 잡힘
    ("G0Z5", 10, False, "남은 동작 블록 10개 (> 허용 3개)"),
    ("G0Z5", None, None, None),
])
def test_program_end_reached(end_block, gap, expected, basis):
    frame, (a, b), _ = scenario(end_block=end_block)
    win = tda.describe_cycle(frame, window_for(frame, a, b, end_gap=gap))
    assert win.program_end_reached is expected
    assert win.program_end_basis == basis                   # 무엇으로 판단했는지 남긴다
    assert win.to_dict()["program_end_basis"] == basis


# ---------------------------------------------------------------------------
# Job 요약 기록 (tdms_visualizer._apply_window_to_job)
# ---------------------------------------------------------------------------

@pytest.fixture
def Session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from DB.database import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    return sessionmaker(bind=eng, autocommit=False, autoflush=False)


def _job(db, **values):
    from DB.models import Job, Part, Workplan
    part = Part(part_name="DB_Test", project_code="Alchemist", material_code="AL6061")
    db.add(part)
    db.flush()
    wp = Workplan(part_code=part.part_code, program_code=values.pop("program_code", "Unknown"),
                  nc_hash="NOHASH")
    db.add(wp)
    db.flush()
    job = Job(workplan_id=wp.workplan_id, work_id=values.pop("work_id", "UNKNOWN_WORKID"),
              source_folder="Alchemist/DB_Test/1", **values)
    db.add(job)
    db.commit()
    return job


def _apply(db, job, method="nc_block", frame=None):
    import tdms_visualizer as tv
    base, (a, b), _ = scenario()
    frame = base if frame is None else frame       # 행 구성이 같은 프레임 (값만 바꾼 것)
    win = tda.describe_cycle(frame, window_for(frame, a + 6, b - 3, method=method))
    result = tda.AlignedResult(cnc=pd.DataFrame(), daq=pd.DataFrame(), fft=pd.DataFrame(), window=win, meta={})
    tv._apply_window_to_job(job, result, frame, db=db, nc_data=NC_TEXT.encode(),
                            tdms_path="DNM200__O2202.NC__PROBE START__260911164215.tdms")
    db.commit()
    return win, (a, b)


def test_tdms_summary_replaces_provisional_values(Session):
    from parsers.nc_parser import nc_cutting_distance
    db = Session()
    job = _job(db, start_time=datetime(2026, 9, 11, 16, 42, 15))       # 파일명의 기록 시작 (임시값)
    win, (a, b) = _apply(db, job)
    db.refresh(job)

    assert job.summary_source == "TDMS"
    assert job.start_time == at(a) + timedelta(hours=9)                # 실행 사이클 시작으로 바뀐다
    assert job.end_time == at(b - 1) + timedelta(hours=9)
    assert job.cutting_seconds == pytest.approx(100 * DT)              # 절삭 100행, 일시정지·급속 제외
    assert job.moving_distance == pytest.approx(110.0)                 # 절삭 100mm + 급속 10mm
    assert job.machining_window["position_jumps"] == 0                 # 뺀 위치 점프 없음
    assert job.cutting_moving_distance == pytest.approx(round(nc_cutting_distance(NC_TEXT), 1))  # NC 기준
    assert job.work_id == (at(a) + timedelta(hours=9)).strftime("%y%m%d%H%M%S")
    assert job.machine_code == "DNM200"
    assert job.is_finish is True
    assert job.workplan.program_code == "O0911.nc"                     # 파일명의 O2202.NC 가 아니다
    assert job.machining_window["program_name"] == "O0911.nc"
    # CNC 요약(machine_log)도 TDMS 실행 사이클로 계산한다. 알람은 TDMS 로 알 수 없어 비워 둔다.
    m_log = job.machine_log
    assert (m_log.stats_source, m_log.max_spindle_load, m_log.max_spindle_rpm, m_log.max_feed_rate) == \
        ("TDMS", 12.0, 3800.0, 7500.0)
    assert m_log.alarm_count is None


def test_tool_length_jumps_are_not_counted_as_travel(Session):
    """위치 채널은 공구 길이 보정이 들어간 좌표라 G43 을 켜면 표시만 공구 길이만큼 바뀐다. 이동으로 세지 않는다."""
    db = Session()
    job = _job(db)
    frame, (a, b), first_cut = scenario()
    frame.loc[:first_cut - 2, "CNC-Z-Position"] = 191.06     # 보정을 켜기 전: 공구 길이만큼 위에 보인다
    frame.loc[b + 5:, "CNC-Z-Position"] = 191.06             # 프로그램 끝·리셋 뒤 보정 해제 (사이클 밖)
    win, _ = _apply(db, job, frame=frame)
    db.refresh(job)
    assert job.moving_distance == pytest.approx(110.0)        # 점프가 없을 때와 같다
    assert (job.machining_window["position_jumps"], job.machining_window["position_jump_mm"]) == (1, 191.1)
    assert (win.position_jumps, win.position_jump_mm) == (1, 191.1)


def test_without_nc_block_match_the_cutting_distance_comes_from_tdms(Session):
    db = Session()
    job = _job(db)
    _apply(db, job, method="activity")
    db.refresh(job)
    assert job.cutting_moving_distance == pytest.approx(100.0)


def test_legacy_xml_job_keeps_its_values_and_only_fills_blanks(Session):
    db = Session()
    job = _job(db, summary_source="XML", program_code="O0911.nc", work_id="260911164605",
               start_time=datetime(2026, 9, 11, 16, 46, 5), cutting_seconds=44.8, cutting_moving_distance=526.8)
    win, (a, b) = _apply(db, job)
    db.refresh(job)
    assert job.summary_source == "XML"
    assert (job.start_time, job.cutting_seconds, job.cutting_moving_distance, job.work_id) == \
        (datetime(2026, 9, 11, 16, 46, 5), 44.8, 526.8, "260911164605")
    assert job.end_time == at(b - 1) + timedelta(hours=9)              # 비어 있던 칸만 채운다
    assert job.moving_distance == pytest.approx(110.0)
