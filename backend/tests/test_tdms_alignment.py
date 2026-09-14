"""tdms_alignment 회귀 테스트 (DB 없이 단독 실행)

합성 TDMS 를 만들어 아래 상황들을 검사한다.
  1. NC 프로그램 대조로 실가공 구간 판별 (중단된 시도 + 완주 시도가 한 파일에 있는 경우)
  2. 같은 블록이 프로그램에 여러 번 나올 때 진행도가 뒤로 튀지 않는지
  3. DAQ 시계가 어긋난 데이터의 지연 자동 실측/보정
  4. 파형 속성(wf_increment)이 없는 DAQ 의 비율 스케일 변환
  5. NC 없음 / DAQ 없음 / 빈 채널 / 손상된 파일 꼬리
  6. XML(로컬시간) 과 TDMS(UTC) 의 시간대 자동 정렬

    python backend/tests/test_tdms_alignment.py
"""
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd
from nptdms import ChannelObject, RootObject, TdmsWriter

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from tdms_alignment import (align_hint_timebase, build_aligned_frames,  # noqa: E402
                            detect_machining_window, find_valid_data_end,
                            map_block_positions, normalize_block, open_tdms,
                            parse_nc_blocks, read_cnc_frame)

FS = 12800.0
CNC_HZ = 27.0

NC_TEXT = """%
<O0001.nc>
G90G80G49G40G0
G91G28Z0
M6T1
G90G00G54X0Y-15
S3000M3
G43H1Z5
G1Z-2F700
Y115
G0X34
G1Y-15
G0X87
G1Y115
G0X95
G1Y-15
G0Z5
G91G28Z0
G91G28X0Y0
M5
M30
%
"""

# (블록, 지속 초, rpm, feed) - 실제 가공 한 사이클
CYCLE = [
    ("G90G80G49G40G0", 0.3, 0, 0),
    ("G91G28Z0", 1.0, 0, 0),
    ("M6T1", 0.5, 0, 0),
    ("G90G00G54X0Y-15", 0.5, 0, 2000),
    ("S3000M3", 0.6, 1500, 0),
    ("G43H1Z5", 0.8, 3000, 500),
    ("G1Z-2F700", 0.8, 3000, 700),
    ("Y115", 6.0, 3000, 700),
    ("G0X34", 0.4, 3000, 4000),
    ("G1Y-15", 6.0, 3000, 700),
    ("G0X87", 0.4, 3000, 4000),
    ("G1Y115", 6.0, 3000, 700),
    ("G0X95", 0.4, 3000, 4000),
    ("G1Y-15", 6.0, 3000, 700),
    ("G0Z5", 0.5, 3000, 2000),
    ("G91G28Z0", 0.8, 3000, 3000),
    ("G91G28X0Y0", 1.0, 2000, 3000),
    ("M5", 0.5, 500, 0),
    ("M30", 0.3, 0, 0),
]

# 중단된 시도: 앞부분만 실행하고 원점 복귀 상태로 오래 머문다
ABORTED = [
    ("<O0001.nc>", 20.0, 0, 0),
    ("G90G80G49G40G0", 0.3, 0, 0),
    ("G91G28Z0", 1.0, 0, 0),
    ("M6T1", 0.5, 0, 0),
    ("G90G00G54X0Y-15", 0.5, 0, 2000),
    ("G91G28X0Y0", 45.0, 0, 0),
]

IDLE = [("", 25.0, 0, 0)]


def _build_cnc(segments, start):
    """블록 시나리오를 CNC 폴링 시계열로 펼친다."""
    rows = []
    t = 0.0
    for block, dur, rpm, feed in segments:
        n = max(1, int(round(dur * CNC_HZ)))
        for _ in range(n):
            rows.append((t, block, rpm, feed))
            t += 1.0 / CNC_HZ
    ts = np.array([pd.Timestamp(start) + pd.to_timedelta(r[0], unit="s") for r in rows])
    return {
        "Time Channel CNC": ts.astype("datetime64[us]"),
        "CNC-CurrentBlock": np.array([r[1] for r in rows], dtype=object),
        "CNC-ProgramName": np.array(["O0001.nc"] * len(rows), dtype=object),
        "CNC-Z-SpindleSpeed": np.array([r[2] for r in rows], dtype=np.float64),
        "CNC-ActualFeedRate": np.array([r[3] for r in rows], dtype=np.float64),
        # 스핀들 부하: 절삭이송(700) 구간에서만 크게 올라간다 -> DAQ 전류와 상관 검증용
        "CNC-Z-SpindleLoad": np.array([40.0 if r[3] == 700 and r[2] > 0 else 3.0 for r in rows],
                                      dtype=np.float64),
        "CNC-X-Position": np.arange(len(rows), dtype=np.float64) * 0.05,
        "CNC-Y-Position": np.arange(len(rows), dtype=np.float64) * 0.03,
        "CNC-Z-Position": np.full(len(rows), -2.0),
    }, t


def _daq_from_cnc(cnc, total_seconds, daq_shift=0.0, seed=1):
    """CNC 부하 패턴과 같은 모양의 DAQ 전류/진동을 만든다.

    daq_shift 초만큼 DAQ 쪽 사건을 앞당겨(=시계가 어긋난 상태) 기록한다.
    """
    rng = np.random.default_rng(seed)
    n = int(total_seconds * FS)
    t = np.arange(n) / FS
    load_t = (cnc["Time Channel CNC"] - cnc["Time Channel CNC"][0]) / np.timedelta64(1, "s")
    load = np.interp(t + daq_shift, load_t, cnc["CNC-Z-SpindleLoad"])
    amp = 0.02 + 0.25 * (load > 20)
    carrier = np.sin(2 * np.pi * 100.0 * t)
    return {
        "DAQ-Spindle-C-R": amp * carrier + 0.004 * rng.standard_normal(n),
        "DAQ-Spindle-C-S": amp * np.sin(2 * np.pi * 100.0 * t + 2.09) + 0.004 * rng.standard_normal(n),
        "DAQ-Spindle-C-T": amp * np.sin(2 * np.pi * 100.0 * t + 4.19) + 0.004 * rng.standard_normal(n),
        "DAQ-ACC-S-Housing-X": amp * rng.standard_normal(n),
        "SOUND": amp * 2 * rng.standard_normal(n),
    }


def write_tdms(path, cnc=None, daq=None, start="2026-01-02T03:04:05",
               waveform=True, truncate_tail=0):
    objs = [RootObject(properties={"name": os.path.basename(path)})]
    if cnc:
        for name, data in cnc.items():
            props = {"wf_start_time": np.datetime64(start), "wf_start_offset": 0.0}
            objs.append(ChannelObject("CNC", name, data, properties=props))
    if daq:
        for name, data in daq.items():
            props = {"wf_start_time": np.datetime64(start)}
            if waveform:
                props["wf_increment"] = 1.0 / FS
                props["wf_start_offset"] = 0.0
            objs.append(ChannelObject("DAQ", name, data, properties=props))
    with TdmsWriter(path) as w:
        w.write_segment(objs)
    # 수집 프로그램 비정상 종료를 모사: 0 으로 채워진 꼬리를 붙인다
    if truncate_tail:
        with open(path, "ab") as f:
            f.write(b"\0" * truncate_tail)
    # nptdms 가 만든 인덱스 파일도 같이 깨뜨린다
    idx = path + "_index"
    if truncate_tail and os.path.exists(idx):
        with open(idx, "ab") as f:
            f.write(b"\0" * truncate_tail)
    return path


# ---------------------------------------------------------------------------
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("  %-4s %s%s" % ("[OK]" if cond else "[NG]", name, ("  -> " + detail) if detail else ""))


def test_nc_block_window(tmp):
    """중단된 시도 + 대기 + 완주 시도가 섞인 파일에서 완주 구간만 뽑아내는지."""
    print("\n[1] NC 블록 대조 기반 가공 구간 판별")
    start = "2026-01-02T03:04:05"
    cnc, total = _build_cnc(ABORTED + IDLE + CYCLE, start)
    daq = _daq_from_cnc(cnc, total + 1, daq_shift=0.0)
    path = write_tdms(os.path.join(tmp, "nc_window.tdms"), cnc, daq, start)

    nc = parse_nc_blocks(NC_TEXT, name="O0001.nc")
    with open_tdms(path) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, nc)
        res = build_aligned_frames(tdms, win, frame, target_points=2000)

    cycle_seconds = sum(s[1] for s in CYCLE)
    aborted_seconds = sum(s[1] for s in ABORTED)
    check("판별 방법이 nc_block", win.method == "nc_block", win.method)
    check("실가공 구간 길이가 완주 사이클과 일치",
          abs(win.duration_seconds - cycle_seconds) < 1.5,
          "판별 %.2fs / 기대 %.2fs" % (win.duration_seconds, cycle_seconds))
    check("중단된 시도를 고르지 않음", win.duration_seconds < aborted_seconds,
          "중단 시도 %.1fs" % aborted_seconds)
    check("NC 커버리지 100%", win.nc_coverage >= 0.99, "%.0f%%" % (win.nc_coverage * 100))
    check("전체 행의 일부만 사용", win.rows < win.total_rows,
          "%d / %d행" % (win.rows, win.total_rows))
    check("CNC time_s 가 0 부터 시작", abs(res.cnc["time_s"].iloc[0]) < 1e-6)
    check("DAQ 와 CNC 시간축 끝이 일치",
          abs(res.daq["daq_time_s"].iloc[-1] - res.cnc["time_s"].iloc[-1]) < 0.2,
          "DAQ %.2f / CNC %.2f" % (res.daq["daq_time_s"].iloc[-1], res.cnc["time_s"].iloc[-1]))
    check("FFT 주파수축 생성", "Frequency" in res.fft.columns and len(res.fft) > 10)


def test_repeated_blocks():
    """같은 블록이 프로그램에 두 번 나올 때 진행도가 뒤로 튀지 않아야 한다."""
    print("\n[2] 중복 블록 단조 진행 매핑")
    nc = parse_nc_blocks(NC_TEXT, name="O0001.nc")
    seq = [normalize_block(b) for b, *_ in CYCLE]
    pos = map_block_positions(seq, nc)
    check("위치가 단조 증가", bool((np.diff(pos[pos >= 0]) >= 0).all()), str(pos.tolist()))
    first_g1y = seq.index("G1Y-15")
    last_g1y = len(seq) - 1 - seq[::-1].index("G1Y-15")
    check("두 번째 G1Y-15 가 뒤쪽 위치로 매핑",
          pos[last_g1y] > pos[first_g1y],
          "%d -> %d" % (pos[first_g1y], pos[last_g1y]))
    # 되돌아가면 재실행으로 인식
    restart = seq + [normalize_block("<O0001.nc>"), normalize_block("G90G80G49G40G0")]
    rpos = map_block_positions(restart, nc)
    check("프로그램 재시작을 앞 위치로 인식", rpos[-1] < rpos[len(seq) - 1],
          "%d < %d" % (rpos[-1], rpos[len(seq) - 1]))


def test_daq_clock_offset(tmp):
    """DAQ 시계가 3초 어긋난 데이터에서 지연을 실측해 보정하는지."""
    print("\n[3] DAQ 시계 지연 자동 실측")
    shift = 3.0
    start = "2026-01-02T03:04:05"
    cnc, total = _build_cnc(IDLE + CYCLE + IDLE, start)
    daq = _daq_from_cnc(cnc, total + 1, daq_shift=shift)
    path = write_tdms(os.path.join(tmp, "daq_shift.tdms"), cnc, daq, start)

    nc = parse_nc_blocks(NC_TEXT, name="O0001.nc")
    with open_tdms(path) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, nc)
        res = build_aligned_frames(tdms, win, frame, target_points=2000)

    check("지연 보정 적용", res.sync.applied, res.sync.note)
    check("측정한 지연이 주입한 값과 일치(±0.3s)",
          abs(res.sync.lag_seconds - shift) < 0.3,
          "측정 %+.2fs / 주입 %+.2fs" % (res.sync.lag_seconds, shift))
    check("상관이 지연 0 보다 뚜렷하게 높음",
          res.sync.correlation > res.sync.zero_lag_correlation + 0.15,
          "%.3f vs %.3f" % (res.sync.correlation, res.sync.zero_lag_correlation))

    # 보정을 끄면 다른 구간을 잘라내는지 (보정이 실제로 효과가 있는지 확인)
    from tdms_alignment import DaqSync
    with open_tdms(path) as tdms:
        raw = build_aligned_frames(tdms, win, frame, target_points=2000, sync=DaqSync())
    a = res.daq["DAQ-Spindle-C-R_max"].to_numpy()
    b = raw.daq["DAQ-Spindle-C-R_max"].to_numpy()
    check("보정 전/후 잘라낸 구간이 다름", float(np.abs(a - b).max()) > 0.05,
          "최대 차이 %.3f" % float(np.abs(a - b).max()))


def test_scaled_daq(tmp):
    """wf_increment 가 없는 DAQ 는 전체 구간 비율로 시간축을 만든다."""
    print("\n[4] 파형 속성 없는 DAQ 비율 스케일 변환")
    start = "2026-01-02T03:04:05"
    cnc, total = _build_cnc(CYCLE, start)
    daq = _daq_from_cnc(cnc, total, daq_shift=0.0)
    path = write_tdms(os.path.join(tmp, "no_waveform.tdms"), cnc, daq, start, waveform=False)

    nc = parse_nc_blocks(NC_TEXT, name="O0001.nc")
    with open_tdms(path) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, nc)
        res = build_aligned_frames(tdms, win, frame, target_points=1000)

    sources = {v["axis_source"] for v in res.meta["daq_channels"].values()}
    check("scaled 축으로 환산", sources == {"scaled"}, str(sources))
    check("DAQ 시간축이 가공 구간을 덮음",
          not res.daq.empty and abs(res.daq["daq_time_s"].iloc[-1] - win.duration_seconds) < 1.0,
          "%.2f / %.2f" % (res.daq["daq_time_s"].iloc[-1] if not res.daq.empty else -1,
                           win.duration_seconds))


def test_fallbacks(tmp):
    """NC 없음 / DAQ 없음 / 빈 채널 같은 예외 상황에서도 죽지 않아야 한다."""
    print("\n[5] 대체 경로 (NC 없음 / DAQ 없음 / 빈 채널)")
    start = "2026-01-02T03:04:05"
    cnc, total = _build_cnc(IDLE + CYCLE + IDLE, start)
    daq = _daq_from_cnc(cnc, total, daq_shift=0.0)

    # NC 없이 활동 기반
    p1 = write_tdms(os.path.join(tmp, "no_nc.tdms"), cnc, daq, start)
    with open_tdms(p1) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, None)
        res = build_aligned_frames(tdms, win, frame, target_points=500)
    check("NC 없으면 활동 기반 판별", win.method == "activity", win.method)
    check("대기 구간을 제외", win.rows < win.total_rows, "%d/%d행" % (win.rows, win.total_rows))
    check("NC 참조 기록 없음", win.nc_reference is None)

    # DAQ 그룹 없음
    p2 = write_tdms(os.path.join(tmp, "cnc_only.tdms"), cnc, None, start)
    nc = parse_nc_blocks(NC_TEXT, name="O0001.nc")
    with open_tdms(p2) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, nc)
        res = build_aligned_frames(tdms, win, frame, target_points=500)
    check("DAQ 없어도 CNC 프레임 생성", not res.cnc.empty and res.daq.empty)
    check("DAQ 없을 때 FFT 비어 있음", res.fft.empty)

    # CNC 채널이 비어 있는 파일 (메타데이터만 기록된 경우)
    empty = {k: np.array([], dtype=np.float64) for k in cnc}
    p3 = write_tdms(os.path.join(tmp, "empty.tdms"), empty, None, start)
    with open_tdms(p3) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, nc, pd.Timestamp(start).to_pydatetime(), None)
        res = build_aligned_frames(tdms, win, frame, target_points=500)
    check("빈 채널이면 full 로 표시하고 예외 없음", win.method == "full", win.method)
    check("빈 채널이면 결과 프레임도 빈 상태", res.cnc.empty and res.daq.empty)


def test_corrupt_tail(tmp):
    """0 으로 채워진 꼬리 + 손상된 *_index 가 있어도 읽혀야 한다."""
    print("\n[6] 손상된 파일 꼬리 / 인덱스 우회")
    start = "2026-01-02T03:04:05"
    cnc, total = _build_cnc(CYCLE, start)
    daq = _daq_from_cnc(cnc, total, daq_shift=0.0)
    path = write_tdms(os.path.join(tmp, "corrupt.tdms"), cnc, daq, start, truncate_tail=277)

    valid_end, size = find_valid_data_end(path)
    check("손상 구간 검출", size - valid_end == 277, "%d 바이트" % (size - valid_end))

    from nptdms import TdmsFile
    naive_failed = False
    try:
        TdmsFile.read_metadata(path)
    except Exception:
        naive_failed = True
    check("일반 방식은 실패(=우회가 필요한 상황)", naive_failed)

    nc = parse_nc_blocks(NC_TEXT, name="O0001.nc")
    with open_tdms(path) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, nc)
        res = build_aligned_frames(tdms, win, frame, target_points=500)
    check("open_tdms 로는 정상 판별", win.method == "nc_block" and not res.cnc.empty, win.method)


def test_hint_timebase(tmp):
    """XML(로컬시간)과 TDMS(UTC)의 9시간 차이를 자동으로 맞추는지."""
    print("\n[7] XML/TDMS 시간대 자동 정렬")
    rec_s = pd.Timestamp("2026-01-02T03:04:05").to_pydatetime()
    rec_e = pd.Timestamp("2026-01-02T03:10:05").to_pydatetime()
    local_s = pd.Timestamp("2026-01-02T12:05:00").to_pydatetime()   # +09:00 기준
    local_e = pd.Timestamp("2026-01-02T12:06:00").to_pydatetime()
    s, e, shift = align_hint_timebase(rec_s, rec_e, local_s, local_e)
    check("9시간 차이 검출", shift == 9.0, "shift=%s" % shift)
    check("보정 후 기록 구간 안에 들어옴", rec_s <= s <= rec_e, str(s))

    # 같은 기준이면 건드리지 않는다
    s2, e2, shift2 = align_hint_timebase(rec_s, rec_e,
                                         pd.Timestamp("2026-01-02T03:05:00").to_pydatetime(),
                                         pd.Timestamp("2026-01-02T03:06:00").to_pydatetime())
    check("이미 맞으면 보정 안 함", shift2 == 0.0, "shift=%s" % shift2)

    # 전혀 다른 세션의 XML 이면 힌트를 쓰지 않는다
    start = "2026-01-02T03:04:05"
    cnc, total = _build_cnc(CYCLE, start)
    path = write_tdms(os.path.join(tmp, "hint.tdms"), cnc, None, start)
    with open_tdms(path) as tdms:
        frame = read_cnc_frame(tdms)
        win = detect_machining_window(frame, None,
                                      pd.Timestamp("2025-05-05T01:00:00").to_pydatetime(),
                                      pd.Timestamp("2025-05-05T01:01:00").to_pydatetime())
    check("무관한 XML 힌트는 무시", any("겹치지 않아" in n for n in win.notes),
          " / ".join(win.notes))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_nc_block_window, test_daq_clock_offset, test_scaled_daq,
                   test_fallbacks, test_corrupt_tail, test_hint_timebase):
            try:
                fn(tmp)
            except Exception:
                traceback.print_exc()
                check(fn.__name__ + " 실행", False, "예외 발생")
        try:
            test_repeated_blocks()
        except Exception:
            traceback.print_exc()
            check("test_repeated_blocks 실행", False, "예외 발생")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 60)
    print("통과 %d / %d" % (passed, len(RESULTS)))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  실패: %s  %s" % (name, detail))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
