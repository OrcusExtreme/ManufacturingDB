import os
import sys

# Ensure backend is in path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

import pytest

from parsers.nc_parser import (nc_cutting_distance, parse_nc_cutting_conditions, step_lines_text,
                               step_path_text)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
# 2026-11-11 가공에서 MMAS 가 Job 폴더에 남긴 NC 사본 그대로 (<O1110.NC> 머리줄, M30% 꼬리 포함)
O1110_PATH = os.path.join(FIXTURES, "O1110_20261111_mmas.nc")
# EDGECAM 출력. 주석이 cp949 이고, T6 은 같은 S·F 로 네 줄을 줄마다 Z-3 / Z-4 두 층으로 깎는다.
O0912_PATH = os.path.join(FIXTURES, "O0912_edgecam.nc")


def _conditions(steps):
    return [(s["tool_number"], s["spindle_speed"], s["feed_rate"]) for s in steps]


def _lines(steps):
    return [(s["nc_line_start"], s["nc_line_end"]) for s in steps]


def _paths(steps):
    return [step_path_text(s["start_position"], s["end_position"]) for s in steps]


def _slot_program(tools):
    """조건마다 홈 하나를 깎고 G0 로 빠지는 실험 프로그램을 만든다 (O1105 · O1110 과 같은 꼴).

    tools: [(공구 번호, [(S, F, 깊이), ...]), ...]
    """
    lines = ["G90G80G49G40G0", "G91G28Z0"]
    x = 0.0
    for tool, conditions in tools:
        lines += [f"M6T{tool}", f"G90G00G54X{x:.1f}Y115.0"]
        for i, (s, f, depth) in enumerate(conditions):
            if i == 0:
                lines += [f"S{s}M3", f"G43H{tool}Z5.0", "M8"]
            else:
                lines += ["Y115.0", f"X{x:.1f}", f"S{s}"]
            lines += [f"G1Z-{depth}F{f}", "Y-15.0", "G0Z5.0"]
            x += 5.0
        lines += ["M9", "G49G91G28Z0", "M5"]
    lines += ["G91G28X0Y0", "M30"]
    return "\n".join(lines)


def test_parse_o0911_compact():
    """
    O0911.nc (Job 1 원본 그대로, 컴팩트 포맷: M6T6 · S3800M3 · G1Z-2F710):
    T6(Ø12) 하나로 같은 조건(S3800 · F710 · Z-2)으로 X26 · X34 · X87 · X95 네 줄을 깎는다.
    줄마다 맞물림(폭 12 / 8 / 12 / 8mm)과 방향(+Y / -Y)이 다르고 조도도 줄마다 쟀다(260911_1~4).
    V3.3.0 까지는 조건이 같다고 한 스텝(패스 4개)으로 합쳤다 -> 네 스텝.
    """
    nc_text = """%
<O0911.nc>
G90G80G49G40G0
G91G28Z0
M6T6
G90G00G54X0Y-15
S3800M3
G00X26
G43H6Z5
M8
G1Z-2F710
Y115
G0X34
G1Y-15
G0X87
G1Y115
G0X95
G1Y-15
G0Z5
M9
G91G28Z0
G91G28X0Y0
M5
M2
%"""
    steps = parse_nc_cutting_conditions(nc_text)
    assert [s["step_order"] for s in steps] == [1, 2, 3, 4]
    assert _conditions(steps) == [(6, 3800.0, 710.0)] * 4
    # NC 는 T6 으로 적지만 DB 에는 표준 표기(두 자리 0 채움)로 적는다.
    # 매칭은 tool_number 로 하므로 표기가 달라도 공구 마스터와 연결된다.
    assert {s["xml_tool_code"] for s in steps} == {"T06"}
    assert [s["depth_of_cut"] for s in steps] == [2.0] * 4
    assert [s["pass_count"] for s in steps] == [1] * 4
    # 같은 조건의 스텝을 구분하는 칸: NC 줄(파일 줄 번호, % 줄 포함), 시작 -> 끝, 절삭 길이
    assert _lines(steps) == [(11, 12), (14, 14), (16, 16), (18, 18)]
    assert _paths(steps) == ["X26 Y-15 → X26 Y115", "X34 Y115 → X34 Y-15",
                             "X87 Y-15 → X87 Y115", "X95 Y115 → X95 Y-15"]
    assert steps[0]["start_position"] == {"X": 26.0, "Y": -15.0, "Z": 5.0}     # Z5 에서 진입해 Z-2 로
    assert steps[0]["end_position"] == {"X": 26.0, "Y": 115.0, "Z": -2.0}
    assert [s["cutting_distance"] for s in steps] == [137.0, 130.0, 130.0, 130.0]   # 첫 줄은 진입 7mm 포함
    assert sum(s["cutting_distance"] for s in steps) == nc_cutting_distance(nc_text) == 527.0


def test_parse_o0911_spaced():
    """
    O0911 (공백 구분 포맷):
    M6 T6;
    S3800 M3;
    G1 Z-2 F710;
    """
    nc_text = """G40 G49 G80 G30 G91 Z0;
M6 T6;
G90 G00 G54 X0 Y-15;
S3800 M3;
G00 X26;
G43 H6 Z5;
M8;
G1 Z-2 F710;
Y115;
M9;
M2;
"""
    steps = parse_nc_cutting_conditions(nc_text)
    assert len(steps) == 1
    assert steps[0]["tool_number"] == 6
    assert steps[0]["spindle_speed"] == 3800.0
    assert steps[0]["feed_rate"] == 710.0
    assert steps[0]["depth_of_cut"] == 2.0


def test_parse_multi_step_edgecam():
    """
    O0912(EDGECAM).nc (다공구 복합 가공):
    Step 1: T29 M06, S900 M3, G1 Z-2.0 F710.0
    Step 2: T06 M06, S3800 M3, G1 Z-3.0 F710.0
    """
    nc_text = """%
O0911
G40 G49 G80
G30 G91 Z0.
T29 M06
S900 M3
G90 G54 G0  X171.5 Y50.5
G43 Z5.0 H29 M8
Z4.0
G1 Z-2.0 F710.0
X121.5
G0 Z5.0
M5
M9
G30 G91 Z0.
T06 M06
S3800 M3
G90 G54 G0  X22.0 Y-12.0
G43 Z5.0 H06 M8
G1 Z-3.0 F710.0
G3 X34.0 Y0.0 R12.0 F532.5
G1 Y101.0 F710.0
G0 Z5.0
M5
M2
%"""
    steps = parse_nc_cutting_conditions(nc_text)
    assert len(steps) == 2

    # Step 1
    assert steps[0]["step_order"] == 1
    assert steps[0]["tool_number"] == 29
    assert steps[0]["spindle_speed"] == 900.0
    assert steps[0]["feed_rate"] == 710.0
    assert steps[0]["depth_of_cut"] == 2.0

    # Step 2 — 모서리 원호(F532.5, 약 18.8mm)보다 직선(F710, 101mm)을 길게 깎았다
    assert steps[1]["step_order"] == 2
    assert steps[1]["tool_number"] == 6
    assert steps[1]["spindle_speed"] == 3800.0
    assert steps[1]["feed_rate"] == 710.0
    assert steps[1]["depth_of_cut"] == 3.0


def test_parse_block_numbers_and_decimals():
    """
    블록 번호(Nxx) 및 소수점(F500., S8000) 테스트
    """
    nc_text = """N10 G21 G90 G17 G40 G80 G49
N20 G54 X0. Y0.
N30 T01 M06
N40 S8000 M03
N50 G43 H01 Z50. M08
N60 G00 X-60. Y-60.
N70 G01 Z-5. F500.
N80 G41 D01 X-50. Y-50. F1200.
N90 Y50.
N100 M30
"""
    steps = parse_nc_cutting_conditions(nc_text)
    assert len(steps) == 1
    assert steps[0]["tool_number"] == 1
    assert steps[0]["spindle_speed"] == 8000.0
    # F500 은 Z 로만 내려간 진입 이송이고, XY 로 깎은 것은 전부 F1200 이다.
    assert steps[0]["feed_rate"] == 1200.0
    assert steps[0]["depth_of_cut"] == 5.0


# ---------------------------------------------------------------------------
# 워킹스텝 분리 규칙: 공구 교환 + 절삭 패스 (같은 자리를 같은 조건으로 이어 깎은 패스만 합침)
# ---------------------------------------------------------------------------

O1110_EXPECTED = (
    [(1, s, f) for s, f in [(5000, 150), (5500, 180), (6000, 220), (6500, 260), (7000, 300),
                            (7500, 350), (8000, 400), (8500, 450), (9000, 500), (9500, 570)]]
    + [(2, s, f) for s, f in [(4500, 300), (4800, 350), (5100, 400), (5400, 450), (5700, 500),
                              (6000, 560), (6300, 620), (6600, 680), (6900, 750), (7200, 820),
                              (7500, 900)]]
    + [(3, s, f) for s, f in [(3500, 400), (3700, 450), (4000, 520), (4200, 580), (4500, 650),
                              (4800, 730), (5000, 800), (5300, 880), (5500, 950), (5800, 1050),
                              (6000, 1150)]]
)


def test_real_o1110_program_splits_every_condition():
    """2026-11-11 가공 원본: 공구 3개로 조건 32개. 예전에는 공구당 한 행(3행)으로 뭉치고
    T1 이 S9500 / F150 처럼 실제로 깎은 적 없는 조합이 됐다."""
    steps = parse_nc_cutting_conditions(O1110_PATH)

    assert _conditions(steps) == [(t, float(s), float(f)) for t, s, f in O1110_EXPECTED]
    assert [s["step_order"] for s in steps] == list(range(1, 33))
    assert {s["xml_tool_code"] for s in steps} == {"T01", "T02", "T03"}
    assert all(s["depth_of_cut"] == 0.5 for s in steps)
    assert all(s["pass_count"] == 1 for s in steps)
    # 조건마다 홈 하나: 8~9줄(X1.5) 부터 1.5mm 씩 옆으로. 스텝 길이의 합은 NC 절삭 경로 길이와 같다.
    assert _lines(steps)[:2] == [(8, 9), (14, 15)]
    assert _paths(steps)[:2] == ["X1.5 Y115 → X1.5 Y-15", "X3 Y115 → X3 Y-15"]
    starts = [s["nc_line_start"] for s in steps]
    assert starts == sorted(starts) and len(set(starts)) == 32
    assert sum(s["cutting_distance"] for s in steps) == pytest.approx(nc_cutting_distance(O1110_PATH))


def test_path_bytes_and_text_inputs_give_the_same_steps():
    with open(O1110_PATH, "rb") as f:
        raw = f.read()
    by_path = parse_nc_cutting_conditions(O1110_PATH)
    assert parse_nc_cutting_conditions(raw) == by_path
    assert parse_nc_cutting_conditions(raw.decode("utf-8")) == by_path


def test_feed_change_alone_splits():
    program = _slot_program([(6, [(4000, 300, 1.0), (4000, 400, 1.0), (4000, 500, 1.0)])])
    assert _conditions(parse_nc_cutting_conditions(program)) == [
        (6, 4000.0, 300.0), (6, 4000.0, 400.0), (6, 4000.0, 500.0)]


def test_spindle_change_alone_splits():
    program = _slot_program([(6, [(3000, 500, 1.0), (3500, 500, 1.0), (4000, 500, 1.0)])])
    assert _conditions(parse_nc_cutting_conditions(program)) == [
        (6, 3000.0, 500.0), (6, 3500.0, 500.0), (6, 4000.0, 500.0)]


def test_same_condition_on_different_slots_are_separate_steps():
    """조건이 같아도 다른 자리(홈)를 깎았으면 따로다. V3.3.0 까지는 [3, 2] 패스의 두 스텝이었다."""
    program = _slot_program([(6, [(4000, 500, 1.0)] * 3), (9, [(5000, 600, 1.0)] * 2)])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(6, 4000.0, 500.0)] * 3 + [(9, 5000.0, 600.0)] * 2
    assert [s["pass_count"] for s in steps] == [1] * 5
    assert [s["depth_of_cut"] for s in steps] == [1.0] * 5
    assert _paths(steps) == [f"X{x} Y115 → X{x} Y-15" for x in (0, 5, 10, 15, 20)]


def test_returning_to_an_earlier_condition_is_a_new_step():
    """가공 순서가 곧 스텝 순서다. A -> B -> A 는 세 스텝이다 (TDMS 와 시간 순으로 맞춘다)."""
    program = _slot_program([(6, [(4000, 500, 1.0), (4500, 600, 1.0), (4000, 500, 1.0)])])
    assert _conditions(parse_nc_cutting_conditions(program)) == [
        (6, 4000.0, 500.0), (6, 4500.0, 600.0), (6, 4000.0, 500.0)]


def test_slots_at_different_depths_are_separate_steps_with_their_own_depth():
    program = _slot_program([(6, [(4000, 500, 0.5), (4000, 500, 1.0), (4000, 500, 1.5)])])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(6, 4000.0, 500.0)] * 3
    assert [s["depth_of_cut"] for s in steps] == [0.5, 1.0, 1.5]


def _layered_slot(depths, x=10.0, feed=500):
    """같은 홈(X{x} Y0 -> Y50)을 depths 의 층마다 다시 깎는다. 층 사이에는 G0 로 들어 올려 처음으로 돌아간다."""
    lines = ["M6T6", "S4000M3", f"G90G0X{x}Y0Z5"]
    for depth in depths:
        lines += [f"G1Z-{depth}F{feed}", "Y50", "G0Z5", "Y0"]
    lines += ["M30"]
    return "\n".join(lines)


def test_layers_over_the_same_path_merge_and_leave_depth_blank():
    """깊이만으로는 나누지 않는다. 같은 자리를 같은 S·F 로 층마다 내려가면 한 스텝, 깊이는 섞여서 비운다."""
    steps = parse_nc_cutting_conditions(_layered_slot([0.5, 1.0, 1.5]))
    assert _conditions(steps) == [(6, 4000.0, 500.0)]
    assert steps[0]["pass_count"] == 3
    assert steps[0]["depth_of_cut"] is None
    assert _lines(steps) == [(4, 13)]                 # 첫 층 진입부터 마지막 층 끝까지
    assert _paths(steps) == ["X10 Y0 → X10 Y50"]
    assert steps[0]["end_position"] == {"X": 10.0, "Y": 50.0, "Z": -1.5}
    assert steps[0]["cutting_distance"] == (5.5 + 50) + (6 + 50) + (6.5 + 50)


def test_spring_pass_over_the_same_path_and_depth_merges():
    """같은 경로 · 같은 깊이를 한 번 더 도는 다듬질(스프링) 패스는 깊이가 그대로 남는다."""
    steps = parse_nc_cutting_conditions(_layered_slot([1.0, 1.0]))
    assert [s["pass_count"] for s in steps] == [2]
    assert steps[0]["depth_of_cut"] == 1.0


def test_same_path_cut_in_the_opposite_direction_merges():
    """올라가며 한 번, 내려오며 한 번 같은 줄을 깎아도 같은 자리다 (순서 · 방향은 보지 않는다)."""
    program = "\n".join(["M6T6", "S4000M3", "G90G0X10Y0Z5", "G1Z-1F500", "Y50", "G0Z5",
                         "G1Z-1", "Y0", "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program)
    assert [s["pass_count"] for s in steps] == [2]
    assert _paths(steps) == ["X10 Y0 → X10 Y0"]


def test_same_place_with_another_feed_is_a_new_step():
    """다듬질을 다른 F 로 했으면 같은 자리라도 따로다 (조건이 경계)."""
    steps = parse_nc_cutting_conditions(_layered_slot([1.0]).replace("M30", "G1Z-1F300\nY50\nG0Z5\nM30"))
    assert _conditions(steps) == [(6, 4000.0, 500.0), (6, 4000.0, 300.0)]


def test_layer_first_order_gives_a_step_per_layer_pass():
    """앞 패스와만 견준다. 두 홈을 Z-1 로 다 깎고 다시 Z-2 로 깎으면 네 스텝이다 (가공 순서대로 이어진 구간)."""
    lines = ["M6T6", "S4000M3", "G90G0X10Y0Z5"]
    for depth in (1, 2):
        for x in (10, 20):
            lines += [f"G0X{x}Y0", f"G1Z-{depth}F500", "Y50", "G0Z5"]
    steps = parse_nc_cutting_conditions("\n".join(lines + ["M30"]))
    assert [s["pass_count"] for s in steps] == [1, 1, 1, 1]
    assert [s["depth_of_cut"] for s in steps] == [1.0, 1.0, 2.0, 2.0]


def test_unknown_positions_are_never_the_same_place():
    """G91 증분 지령으로만 쓴 프로그램은 공작물 좌표를 모른다. 모양이 같아도 같은 자리라고 하지 않는다."""
    program = "\n".join(["M6T6", "S4000M3", "G91",
                         "G1Z-6F500", "Y50", "G0Z6", "X10Y-50",
                         "G1Z-6", "Y50", "G0Z6", "M30"])
    steps = parse_nc_cutting_conditions(program)
    assert [s["pass_count"] for s in steps] == [1, 1]
    assert _paths(steps) == ["X? Y? → X? Y?"] * 2
    assert [s["cutting_distance"] for s in steps] == [56.0, 56.0]   # 길이는 증분으로도 안다


def test_nc_lines_are_file_lines_including_comments_and_blank_lines():
    program = "\n".join(["%", "(O0001 TEST)", "M6T1", "", "S5000M3 (spindle)", "G90G0X0Y0Z5",
                         "; plunge next", "G1Z-1F300", "X40", "G0Z5", "M30", "%"])
    steps = parse_nc_cutting_conditions(program)
    assert _lines(steps) == [(8, 9)]
    assert step_lines_text(8, 9) == "8~9" and step_lines_text(14, 14) == "14"


def test_real_edgecam_layers_merge_per_line():
    """O0912(EDGECAM) T6 은 같은 조건으로 네 줄을 줄마다 Z-3 -> Z-4 두 층으로 깎는다.
    층은 합치고(줄마다 패스 2개) 줄은 나눈다. V3.3.0 까지는 T6 전체가 한 스텝(패스 8개)이었다."""
    with open(O0912_PATH, "rb") as f:
        raw = f.read()            # 주석이 cp949 라 utf-8 로는 읽히지 않는다
    steps = parse_nc_cutting_conditions(raw)
    assert _conditions(steps) == [(29, 900.0, 710.0)] + [(6, 3800.0, 710.0)] * 4
    assert [s["pass_count"] for s in steps] == [1, 2, 2, 2, 2]
    assert [s["depth_of_cut"] for s in steps] == [2.0, None, None, None, None]   # 3.0 과 4.0 이 섞였다
    assert _lines(steps) == [(11, 14), (24, 33), (36, 45), (48, 57), (60, 69)]
    assert _paths(steps) == ["X171.5 Y50.5 → X-50.5 Y50.5", "X22 Y-12 → X22 Y113", "X38 Y113 → X38 Y-12",
                             "X83 Y-12 → X83 Y113", "X99 Y113 → X99 Y-12"]
    assert sum(s["cutting_distance"] for s in steps) == pytest.approx(nc_cutting_distance(raw), abs=0.01)


def test_plunge_feed_is_not_the_representative_feed():
    """진입 이송(F100)과 절삭 이송(F500)이 한 줄씩이면 예전 규칙(최빈값)은 먼저 나온 F100 을 골랐다."""
    program = "\n".join([
        "M6 T1", "S5000 M3", "G0 X0 Y0 Z5",
        "G1 Z-1 F100",
        "Y100 F500",
        "G0 Z5", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(1, 5000.0, 500.0)]
    assert steps[0]["depth_of_cut"] == 1.0


def test_plunge_only_and_air_passes_do_not_split_steps():
    """CAM 출력(milling.nc 꼴): 수직 진입만 하는 F400 패스와 헬리컬 F3184 패스가 번갈아 나오고,
    소재 위(Z5)에서 이송으로만 움직이는 패스도 낀다. 경계는 헬리컬 패스만 만들고, 두 헬리컬은 같은 자리
    (중심 X0 Y0 의 R6 원, 두 번째는 한 바퀴 더 내려감)라 한 스텝이다."""
    program = """%
O0001
G90 G54 G17 G21 G40 G49 G80 G94
G91 G28 Z0.
G90
T1 M6 (T01_ENDMILL_D12.0MM)
G54 G90 G17
M03 S7960
G43 H1
G00 Z10.000
X0.000 Y0.000
Z5.000
G01 Z-8.500 F400 (Plunge)
G00 (Clearance)
X6.000 (Entry XY)
G01 Z-10.500 (Plunge)
G02 Z-17.167 I-6.000 J0.000 F3184 (Helix)
I-6.000 J0.000 (Finish circle)
G01 X0.000
G00 Z5.000
Z10.000
Z5.000
G01 Z-8.500 F400 (Plunge)
G00 (Clearance)
X6.000
G01 Z-10.500
G02 Z-17.167 I-6.000 J0.000 F3184
Z-23.833 I-6.000 J0.000
I-6.000 J0.000
G01 X0.000
G00 Z5.000
G01 X50.000 F2000 (feed move above the stock)
G00 Z10.000
M30
%"""
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(1, 7960.0, 3184.0)]
    assert steps[0]["pass_count"] == 2
    assert steps[0]["depth_of_cut"] is None      # 17.167 과 23.833 이 섞였다
    assert _lines(steps) == [(16, 30)]           # 첫 헬리컬 패스(16줄 진입)부터 둘째 패스 끝(30줄)까지


def test_helixes_around_different_centres_are_separate_steps():
    """같은 시작점이라도 원호 중심이 다르면(다른 구멍) 다른 자리다."""
    program = "\n".join(["M6T1", "S8000M3", "G90G0X6Y0Z5",
                         "G1Z-1F200", "G2I-6J0F1000", "G0Z5",
                         "G1Z-1F200", "G2I6J0F1000", "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program)
    assert [s["pass_count"] for s in steps] == [1, 1]


def test_comment_tool_names_are_not_tool_numbers():
    program = "\n".join([
        "T01 M6 (T13_FACEMILL_50MM)", "M03 S954", "G00 X0 Y-27 Z9.5",
        "G01 Z-0.5 F400 (Plunge)", "X75 F500", "G00 Z9.5", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(1, 954.0, 500.0)]
    assert steps[0]["depth_of_cut"] == 0.5


def test_tool_preselected_before_m6_does_not_split_early():
    """T2 를 미리 불러 둬도 M6 전까지 깎는 공구는 T1 이다."""
    program = "\n".join([
        "T1 M6", "S5000 M3", "G0 X0 Y0 Z5", "G1 Z-1 F300", "X50",
        "T2 (next tool)",
        "Y10", "G0 Z5",
        "M6", "S4000 M3", "G0 X0 Y20", "G1 Z-1 F200", "X50", "G0 Z5", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(1, 5000.0, 300.0), (2, 4000.0, 200.0)]


def test_program_without_m6_switches_tool_on_t():
    program = "\n".join([
        "T1", "S5000 M3", "G0 X0 Y0 Z5", "G1 Z-1 F300", "X50", "G0 Z5",
        "T2", "S4000 M3", "G0 X0 Y20", "G1 Z-1 F200", "X50", "G0 Z5", "M30",
    ])
    assert _conditions(parse_nc_cutting_conditions(program)) == [
        (1, 5000.0, 300.0), (2, 4000.0, 200.0)]


def test_tool_called_without_cutting_keeps_one_step():
    """절삭 없이 호출만 된 공구도 XML 공구 목록과 맞추려고 한 줄 남긴다 (패스 0개)."""
    program = "\n".join([
        "M6 T5", "S1000 M3", "G0 X0 Y0 Z50", "M5",
        "M6 T6", "S3800 M3", "G0 X0 Y0 Z5", "G1 Z-2 F710", "Y100", "G0 Z5", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(5, 1000.0, None), (6, 3800.0, 710.0)]
    assert [s["pass_count"] for s in steps] == [0, 1]
    assert [s["depth_of_cut"] for s in steps] == [None, 2.0]
    tool_only = steps[0]
    assert [tool_only[k] for k in ("nc_line_start", "nc_line_end", "start_position", "end_position",
                                   "cutting_distance")] == [None] * 5


def test_canned_cycle_depth_is_hole_bottom():
    program = "\n".join([
        "M6 T7", "S1200 M3", "G90 G0 X0 Y0 Z5",
        "G99 G81 X10 Y10 Z-5 R2 F100", "X20", "X30", "G80",
        "G0 Z50", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(7, 1200.0, 100.0)]
    assert steps[0]["depth_of_cut"] == 5.0
    # 구멍 위치가 자리다: 첫 구멍 -> 마지막 구멍, 길이는 구멍마다 R 점 -> 바닥 7mm
    assert _lines(steps) == [(4, 6)]
    assert _paths(steps) == ["X10 Y10 → X30 Y10"]
    assert steps[0]["end_position"] == {"X": 30.0, "Y": 10.0, "Z": -5.0}
    assert steps[0]["cutting_distance"] == 21.0


def test_same_holes_drilled_again_merge_other_holes_do_not():
    cycle = lambda x0: [f"G99 G81 X{x0} Y10 Z-5 R2 F100", f"X{x0 + 10}", "G80", "G0 Z50"]   # noqa: E731
    program = "\n".join(["M6 T7", "S1200 M3", "G90 G0 X0 Y0 Z50", *cycle(10), *cycle(10), *cycle(40), "M30"])
    steps = parse_nc_cutting_conditions(program)
    assert [s["pass_count"] for s in steps] == [2, 1]


def test_step_text_helpers_read_db_values():
    """화면 · 미리보기용 글: DB 에서 읽은 JSON 문자열과 pandas 의 빈 값(NaN)도 받는다."""
    assert step_path_text('{"X": 26.0, "Y": -15.0, "Z": 5.0}', {"X": 26.0, "Y": 115.5, "Z": -2.0}) \
        == "X26 Y-15 → X26 Y115.5"
    assert step_path_text({"X": -0.0, "Y": 0.00049, "Z": None}, {"X": None, "Y": 1.0, "Z": 0.0}) \
        == "X0 Y0.0005 → X? Y1"
    assert step_path_text(None, None) == "" and step_path_text(float("nan"), float("nan")) == ""
    assert step_lines_text(float("nan"), float("nan")) == "" and step_lines_text(11.0, 12.0) == "11~12"


def test_incremental_moves_track_depth():
    program = "\n".join([
        "M6 T1", "S5000 M3", "G90 G0 X0 Y0 Z5",
        "G91 G1 Z-6 F200", "Y50",
        "G90 G0 Z5", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(1, 5000.0, 200.0)]
    assert steps[0]["depth_of_cut"] == 1.0


def test_depth_unknown_after_reference_return_is_blank_not_guessed():
    """G28 로 원점 복귀한 뒤 Z 를 다시 지령하지 않고 깎으면 깊이를 알 수 없다."""
    program = "\n".join([
        "M6 T1", "S5000 M3", "G91 G28 Z0", "G90 G0 X0 Y0",
        "G1 X10 F300", "G0 X0", "M30",
    ])
    steps = parse_nc_cutting_conditions(program)
    assert _conditions(steps) == [(1, 5000.0, 300.0)]
    assert steps[0]["depth_of_cut"] is None


def test_tool_change_macro_without_cutting_gives_no_steps():
    """장비의 공구 교환 매크로(O9009 꼴): M06 은 있지만 T 도 절삭도 없다."""
    program = "\n".join([
        "%", "O9009(MACRO)", "IF[#1007EQ1]GOTO600", "#14=#4114", "G31",
        "G53G90Z#100", "N40G91G30Z0.0M19", "N50M06", "N60G90", "G04",
        "N#14G#15G#16", "M99", "%",
    ])
    assert parse_nc_cutting_conditions(program) == []


def test_empty_input_gives_no_steps():
    assert parse_nc_cutting_conditions("") == []
    assert parse_nc_cutting_conditions(None) == []
    assert parse_nc_cutting_conditions(b"") == []
