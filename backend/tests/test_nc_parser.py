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
# Job 1(2026-09-11 DB_Test) NC 원본: Ø12 엔드밀 T6 으로 X26·X34 / X87·X95 네 줄 -> 폭 20 오픈 슬롯 두 개
O0911_PATH = os.path.join(FIXTURES, "O0911_20260911.nc")

# 연구실 공구 마스터(2026-11-26)의 같은 번호 공구. 실제 공구 지름으로 가공 형상을 가른다.
TOOL_MASTER = {1: {"diameter": 2.0, "kind": "ENDMILL"}, 2: {"diameter": 4.0, "kind": "ENDMILL"},
               3: {"diameter": 6.0, "kind": "ENDMILL"}, 6: {"diameter": 12.0, "kind": "ENDMILL"},
               9: {"diameter": 12.0, "kind": "ENDMILL"}, 29: {"diameter": 125.0, "kind": "FACEMILL"}}
# 연구실 시험 소재(블록) 121 x 101 x 28 mm. 공작물 원점은 윗면 모서리(X0 Y0, Z0 = 윗면) — O0912 정면 밀링 Y50.5 가 가운데.
STOCK = {"x": 121.0, "y": 101.0, "z": 28.0, "origin": "corner"}
# 아래 합성 프로그램(X10 · Y0~50 홈)용 소재
SMALL_STOCK = {"x": 60.0, "y": 50.0, "z": 20.0}


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


def test_job1_four_passes_make_two_open_slots_and_two_workingsteps():
    """O0911(Job 1): Ø12 엔드밀 T6 하나로 같은 조건(S3800 · F710 · Z-2)으로 네 줄을 깎았다.
    X26·X34 는 8mm 간격이라 깎인 자리가 겹쳐 폭 20 슬롯 하나, X87·X95 가 또 하나 -> 형상 두 개, 워킹스텝 두 개.
    (V3.3.0 까지는 조건이 같다고 1개, 위치만으로 나누면 4개였다.)"""
    with open(O0911_PATH, "rb") as f:
        raw = f.read()
    steps = parse_nc_cutting_conditions(raw, tools=TOOL_MASTER, stock=STOCK)
    assert [s["step_order"] for s in steps] == [1, 2]
    assert _conditions(steps) == [(6, 3800.0, 710.0)] * 2
    # NC 는 T6 으로 적지만 DB 에는 표준 표기(두 자리 0 채움)로 적는다. 매칭은 tool_number 로 한다.
    assert {s["xml_tool_code"] for s in steps} == {"T06"}
    assert [s["feature_no"] for s in steps] == [1, 2]
    assert [s["feature_type"] for s in steps] == ["slot", "slot"]
    geometry = [s["feature_geometry"] for s in steps]
    assert [(g["width"], g["length"], g["depth"], g["x"]) for g in geometry] == [
        (20.0, 101.0, 2.0, [20.0, 40.0]), (20.0, 101.0, 2.0, [81.0, 101.0])]    # 길이는 소재 안(Y0~101)만
    assert [g["ends"] for g in geometry] == [["open", "open"]] * 2                 # 소재 끝까지 뚫린 오픈 슬롯
    assert [g["walls"]["left"] == g["walls"]["right"] == "wall" for g in geometry] == [True, True]
    assert all("Ø12" in g["basis"] for g in geometry)
    assert [s["operation_type"] for s in steps] == ["bottom_and_side_rough_milling"] * 2
    assert [s["strategy"] for s in steps] == ["bidirectional"] * 2          # +Y 로 한 줄, -Y 로 한 줄
    assert [s["depth_of_cut"] for s in steps] == [2.0, 2.0]                   # 축방향 절입 ap
    assert [s["radial_cutting_depth"] for s in steps] == [12.0, 12.0]         # 반경 절입 ae (가장 큰 값)
    assert [s["pass_count"] for s in steps] == [2, 2]
    assert _lines(steps) == [(11, 14), (16, 18)]
    # 패스별 경로: 조도 측정점(260911_1~4)처럼 줄마다 잇는 자료는 여기서 본다
    passes = [tp for s in steps for tp in s["toolpaths"]]
    assert [tp["nc_lines"] for tp in passes] == [[11, 12], [14, 14], [16, 16], [18, 18]]
    assert [tp["ae"] for tp in passes] == [12.0, 8.0, 12.0, 8.0]              # 첫 줄은 공구 지름, 둘째는 넓힌 폭
    assert [tp["ap"] for tp in passes] == [2.0] * 4
    assert [tp["start"]["X"] for tp in passes] == [26.0, 34.0, 87.0, 95.0]
    assert [s["cutting_distance"] for s in steps] == [267.0, 260.0]            # 첫 줄은 진입 7mm 포함
    assert [s["removed_volume"] for s in steps] == [4040.0, 4040.0]           # 20 x 101 x 2 (소재 밖 허공은 빼고)
    assert sum(s["cutting_distance"] for s in steps) == nc_cutting_distance(raw) == 527.0


def test_job1_without_a_stock_size_does_not_record_what_it_cannot_judge():
    """소재 크기를 모르면 형상 둘레가 벽인지 뚫렸는지 알 수 없다: 형상 번호(워킹스텝 분리)와 ap · 황삭/정삭만 적고
    형상 분류 · 크기 · 제거 체적 · 반경 절입 · 밀링 공정 이름은 기록하지 않는다 (None)."""
    with open(O0911_PATH, "rb") as f:
        steps = parse_nc_cutting_conditions(f.read(), tools=TOOL_MASTER)
    assert [s["feature_no"] for s in steps] == [1, 2]                 # 슬롯 두 개로 나누는 것은 그대로
    for key in ("feature_type", "feature_geometry", "removed_volume", "radial_cutting_depth", "operation_type"):
        assert [s[key] for s in steps] == [None, None], key
    assert [s["depth_of_cut"] for s in steps] == [2.0, 2.0]
    assert [tp["stage"] for s in steps for tp in s["toolpaths"]] == ["rough"] * 4
    assert [tp["ae"] for s in steps for tp in s["toolpaths"]] == [None] * 4
    assert [s["strategy"] for s in steps] == ["bidirectional"] * 2


def test_job1_without_a_tool_diameter_falls_back_to_path_centre_lines():
    """공구 지름을 어디서도 모르면 경로 중심선만 깎는다고 본다: 네 줄이 서로 닿지 않아 형상 네 개.
    소재 크기를 알아도 형상 분류 · 크기는 제대로 판단할 수 없어 기록하지 않는다."""
    with open(O0911_PATH, "rb") as f:
        steps = parse_nc_cutting_conditions(f.read(), stock=STOCK)
    assert [s["pass_count"] for s in steps] == [1, 1, 1, 1]
    assert [s["feature_type"] for s in steps] == [None] * 4
    assert [s["feature_geometry"] for s in steps] == [None] * 4
    assert [s["radial_cutting_depth"] for s in steps] == [None] * 4
    assert _paths(steps) == ["X26 Y-15 → X26 Y115", "X34 Y115 → X34 Y-15",
                             "X87 Y-15 → X87 Y115", "X95 Y115 → X95 Y-15"]
    assert steps[0]["start_position"] == {"X": 26.0, "Y": -15.0, "Z": 5.0}     # Z5 에서 진입해 Z-2 로
    assert steps[0]["end_position"] == {"X": 26.0, "Y": 115.0, "Z": -2.0}


def test_tool_diameter_from_an_nc_comment_is_used_when_the_db_has_none():
    with open(O0911_PATH, "rb") as f:
        text_ = f.read().decode("utf-8").replace("M6T6", "M6T6 (T06 ENDMILL D12.0)")
    steps = parse_nc_cutting_conditions(text_, stock=STOCK)
    assert [s["pass_count"] for s in steps] == [2, 2]
    assert "NC 주석" in steps[0]["feature_geometry"]["basis"]


def test_db_tool_diameter_wins_over_the_nc_comment():
    """주석은 CAM 이 가정한 공구, DB(Job 공구 지정 · 공구 마스터)는 실제로 꽂은 공구다."""
    with open(O0911_PATH, "rb") as f:
        text_ = f.read().decode("utf-8").replace("M6T6", "M6T6 (D6)")
    assert [s["pass_count"] for s in parse_nc_cutting_conditions(text_)] == [1, 1, 1, 1]   # Ø6: 8mm 간격은 떨어져 있다
    assert [s["pass_count"] for s in parse_nc_cutting_conditions(text_, tools=TOOL_MASTER)] == [2, 2]


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
# 워킹스텝 분리 규칙: ISO 14649 — 가공 형상 · 공정(황삭/정삭)과 전략 · 절삭조건 · 공구
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


def test_equal_layers_over_the_same_slot_are_one_roughing_step():
    """같은 자리를 고르게 층을 나눠 내려가는 것은 황삭 한 공정이다 (ISO 14649: 한 워킹스텝의 축방향 절입)."""
    steps = parse_nc_cutting_conditions(_layered_slot([1.0, 2.0]), tools=TOOL_MASTER, stock=SMALL_STOCK)
    assert [s["pass_count"] for s in steps] == [2]
    assert steps[0]["operation_type"] == "bottom_and_side_rough_milling"
    assert steps[0]["depth_of_cut"] == 1.0                                    # 한 번에 1mm 씩
    assert steps[0]["feature_geometry"]["depth"] == 2.0                       # 형상은 2mm 깊이
    assert _lines(steps) == [(4, 9)]
    assert steps[0]["cutting_distance"] == (6 + 50) + (7 + 50)


def test_thin_last_layer_on_the_same_place_is_a_separate_finishing_step():
    """같은 자리라도 축방향 절입이 확연히 다르면(1.5mm 황삭 뒤 0.5mm) 황삭 · 정삭 두 워킹스텝이다. 형상은 같다."""
    steps = parse_nc_cutting_conditions(_layered_slot([1.5, 2.0]), tools=TOOL_MASTER, stock=SMALL_STOCK)
    assert [s["operation_type"] for s in steps] == ["bottom_and_side_rough_milling",
                                                    "bottom_and_side_finish_milling"]
    assert [s["depth_of_cut"] for s in steps] == [1.5, 0.5]
    assert [s["feature_no"] for s in steps] == [1, 1]
    assert _lines(steps) == [(4, 5), (8, 9)]


def test_remainder_layer_of_roughing_stays_roughing():
    """2 · 2 · 1mm 처럼 남은 두께로 마무리하는 층은 정삭이 아니다 (정삭은 0.5mm 이하이고 앞 절입의 절반 미만)."""
    steps = parse_nc_cutting_conditions(_layered_slot([2, 4, 5]), tools=TOOL_MASTER)
    assert [s["pass_count"] for s in steps] == [3]
    assert [tp["ap"] for tp in steps[0]["toolpaths"]] == [2.0, 2.0, 1.0]
    assert steps[0]["depth_of_cut"] == 2.0


def test_spring_pass_at_the_same_depth_is_a_finishing_step():
    """같은 경로 · 같은 깊이를 한 번 더 도는 스프링 패스는 새로 깎는 두께가 0 인 정삭이다."""
    steps = parse_nc_cutting_conditions(_layered_slot([1.0, 1.0]), tools=TOOL_MASTER, stock=SMALL_STOCK)
    assert [s["operation_type"] for s in steps] == ["bottom_and_side_rough_milling",
                                                    "bottom_and_side_finish_milling"]
    assert [s["depth_of_cut"] for s in steps] == [1.0, 0.0]


def test_spring_pass_in_the_opposite_direction_is_also_finishing():
    program = "\n".join(["M6T6", "S4000M3", "G90G0X10Y0Z5", "G1Z-1F500", "Y50", "G0Z5",
                         "G1Z-1", "Y0", "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program, tools=TOOL_MASTER, stock=SMALL_STOCK)
    assert [s["operation_type"] for s in steps] == ["bottom_and_side_rough_milling",
                                                    "bottom_and_side_finish_milling"]
    assert [s["feature_no"] for s in steps] == [1, 1]


def test_same_place_with_another_feed_is_a_new_step():
    """다듬질을 다른 F 로 했으면 같은 자리라도 따로다 (조건이 경계)."""
    steps = parse_nc_cutting_conditions(_layered_slot([1.0]).replace("M30", "G1Z-1F300\nY50\nG0Z5\nM30"))
    assert _conditions(steps) == [(6, 4000.0, 500.0), (6, 4000.0, 300.0)]


def _layer_first(depths=(1, 2), xs=(10, 20)):
    """홈들을 Z-1 로 모두 깎은 뒤 다시 Z-2 로 깎는다 (층 우선 순서)."""
    lines = ["M6T6", "S4000M3", "G90G0X10Y0Z5"]
    for depth in depths:
        for x in xs:
            lines += [f"G0X{x}Y0", f"G1Z-{depth}F500", "Y50", "G0Z5"]
    return "\n".join(lines + ["M30"])


def test_layer_first_order_over_one_slot_is_one_step():
    """Ø12 로 10mm 간격 두 줄은 한 슬롯이다. 층 우선으로 깎아도 같은 형상 · 같은 공정이라 한 스텝."""
    steps = parse_nc_cutting_conditions(_layer_first(), tools=TOOL_MASTER)
    assert [s["pass_count"] for s in steps] == [4]
    assert [tp["ap"] for tp in steps[0]["toolpaths"]] == [1.0, 1.0, 1.0, 1.0]


def test_returning_to_another_feature_is_a_new_step():
    """형상이 다르면(사이에 소재가 남음) 가공 순서대로 A -> B -> A -> B 네 스텝이다 (이어진 패스만 묶는다)."""
    steps = parse_nc_cutting_conditions(_layer_first(xs=(10, 40)), tools=TOOL_MASTER)
    assert [s["feature_no"] for s in steps] == [1, 2, 1, 2]
    assert [s["depth_of_cut"] for s in steps] == [1.0, 1.0, 1.0, 1.0]


def test_face_then_slot_with_the_same_tool_are_two_features():
    """같은 공구로 60 x 20 소재 윗면 전체를 0.5mm 깎은 뒤(벽 없음 -> planar_face) 가운데에 슬롯을 판다
    (양옆이 깎인 면보다 높은 벽, 양 끝은 소재 끝 -> slot). 슬롯의 축방향 절입은 깎인 면(Z-0.5)에서 잰다."""
    program = "\n".join(["M6T6", "S4000M3", "G90G0X0Y0Z5", "G1Z-0.5F500", "X60", "Y10", "X0", "Y20", "X60",
                         "G0Z5", "G0X30Y-10", "G1Z-3", "Y40", "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program, tools=TOOL_MASTER, stock={"x": 60, "y": 20, "z": 10})
    assert [s["feature_no"] for s in steps] == [1, 2]
    assert [s["feature_type"] for s in steps] == ["planar_face", "slot"]
    assert [s["operation_type"] for s in steps] == ["plane_rough_milling", "bottom_and_side_rough_milling"]
    assert [s["strategy"] for s in steps] == ["bidirectional", "unidirectional"]   # 지그재그 / 한 줄
    assert [s["depth_of_cut"] for s in steps] == [0.5, 2.5]
    assert steps[1]["feature_geometry"]["depth"] == 2.5                            # 깎인 면에서 잰 슬롯 깊이


def test_region_inside_the_stock_with_walls_all_around_is_a_closed_pocket():
    program = "\n".join(["M6T6", "S4000M3", "G90G0X20Y20Z5", "G1Z-2F500", "X40", "Y25", "X20", "Y30", "X40",
                         "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program, tools=TOOL_MASTER, stock={"x": 60, "y": 60, "z": 20})
    assert [s["feature_type"] for s in steps] == ["closed_pocket"]
    assert set(steps[0]["feature_geometry"]["walls"].values()) == {"wall"}


def test_o1110_t03_reaches_the_stock_edge_and_is_a_step():
    """O1110: 공구마다 홈을 겹쳐 깎아 넓은 홈이 된다. T01 · T02 영역은 양옆에 소재가 남은 slot(양 끝은 소재 끝까지),
    T03 영역은 마지막 홈(X119, Ø6)이 소재 끝 X121 을 넘어 한 옆만 벽인 step 이다."""
    steps = parse_nc_cutting_conditions(O1110_PATH, tools=TOOL_MASTER, stock=STOCK)
    first = {}
    for s in steps:
        first.setdefault(s["feature_no"], s)
    assert {no: s["feature_type"] for no, s in first.items()} == {1: "slot", 2: "slot", 3: "step"}
    assert [first[no]["feature_geometry"]["width"] for no in (1, 2, 3)] == [15.5, 39.0, 60.0]
    assert [first[no]["feature_geometry"]["length"] for no in (1, 2, 3)] == [101.0] * 3
    assert first[1]["feature_geometry"]["ends"] == ["open", "open"]
    assert first[3]["feature_geometry"]["x"] == [61.0, 121.0]
    assert sorted(first[3]["feature_geometry"]["walls"].values()) == ["open", "open", "open", "wall"]


def test_rough_and_finish_with_different_tools_share_the_feature():
    """T6 으로 Z-1.8 까지 황삭하고 T9 로 바닥을 Z-2 까지 0.2mm 정삭: 공구가 달라 두 스텝, 형상은 하나."""
    program = "\n".join(["M6T6", "S4000M3", "G90G0X10Y0Z5", "G1Z-1.8F500", "Y50", "G0Z5",
                         "M6T9", "S6000M3", "G0X10Y50Z5", "G1Z-2F300", "Y0", "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program, tools=TOOL_MASTER, stock=SMALL_STOCK)
    assert [s["tool_number"] for s in steps] == [6, 9]
    assert [s["feature_no"] for s in steps] == [1, 1]
    assert [s["operation_type"] for s in steps] == ["bottom_and_side_rough_milling",
                                                    "bottom_and_side_finish_milling"]
    assert [s["depth_of_cut"] for s in steps] == [1.8, 0.2]


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


def test_real_edgecam_face_and_two_slots():
    """O0912(EDGECAM): T29(Ø125 정면 밀링)으로 Z-2 까지 면을 깎고, T6(Ø12)으로 슬롯 두 개를 줄마다 Z-3 -> Z-4
    두 층씩 윤곽 가공한다. 슬롯 층은 깎인 면(Z-2)에서 1mm · 1mm 라 황삭 한 공정 -> 워킹스텝 3개."""
    with open(O0912_PATH, "rb") as f:
        raw = f.read()            # 주석이 cp949 라 utf-8 로는 읽히지 않는다
    steps = parse_nc_cutting_conditions(raw, tools=TOOL_MASTER, stock=STOCK)
    assert _conditions(steps) == [(29, 900.0, 710.0)] + [(6, 3800.0, 710.0)] * 2
    assert [s["feature_type"] for s in steps] == ["planar_face", "slot", "slot"]
    assert [s["operation_type"] for s in steps] == ["plane_rough_milling"] + ["bottom_and_side_rough_milling"] * 2
    assert [s["strategy"] for s in steps] == ["unidirectional", "contour", "contour"]
    assert [s["pass_count"] for s in steps] == [1, 4, 4]
    assert [s["depth_of_cut"] for s in steps] == [2.0, 1.0, 1.0]
    face, slot = steps[0]["feature_geometry"], steps[1]["feature_geometry"]
    assert (face["length"], face["width"], face["depth"]) == (121.0, 101.0, 2.0)    # 소재 윗면 전체
    assert steps[0]["removed_volume"] == 121 * 101 * 2
    # 소재 밖 진입·후퇴 원호(R12)는 허공이라 슬롯은 X20~40 · Y0~101 이다
    assert (slot["width"], slot["length"], slot["depth"], slot["floor_z"], slot["x"]) == (20.0, 101.0, 2.0, -4.0,
                                                                                          [20.0, 40.0])
    assert slot["ends"] == ["open", "open"]
    assert _lines(steps) == [(11, 14), (24, 45), (48, 69)]
    assert sum(s["cutting_distance"] for s in steps) == pytest.approx(nc_cutting_distance(raw), abs=0.01)


def test_real_edgecam_without_diameters_keeps_slots_by_crossing_lead_in_arcs():
    """지름을 모르면 중심선만 보지만, 두 줄의 진입 원호가 서로 가로질러 슬롯마다 한 형상으로 남는다.
    정면 밀링을 몰라 슬롯 첫 층은 Z0 부터 3mm 로 재지만, 1mm 둘째 층은 정삭(0.5mm 이하)이 아니다."""
    with open(O0912_PATH, "rb") as f:
        steps = parse_nc_cutting_conditions(f.read())
    assert [s["pass_count"] for s in steps] == [1, 4, 4]
    assert [s["depth_of_cut"] for s in steps] == [2.0, 3.0, 3.0]


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
    # 공작물 원점이 소재 윗면 가운데(center)인 100 x 100 x 40 소재
    steps = parse_nc_cutting_conditions(program, stock={"x": 100, "y": 100, "z": 40, "origin": "center"})
    assert _conditions(steps) == [(1, 7960.0, 3184.0)]
    assert steps[0]["pass_count"] == 2
    assert _lines(steps) == [(16, 30)]           # 첫 헬리컬 패스(16줄 진입)부터 둘째 패스 끝(30줄)까지
    # 주석의 공구 지름(D12.0MM)으로 Ø24 구멍을 헬리컬로 두 번 내려간 황삭 (17.167 -> 23.833, 둘째 층 6.666mm)
    assert (steps[0]["feature_type"], steps[0]["strategy"]) == ("round_hole", "helical")
    assert (steps[0]["feature_geometry"]["diameter"], steps[0]["feature_geometry"]["depth"]) == (24.0, 23.833)
    assert [tp["ap"] for tp in steps[0]["toolpaths"]] == [17.167, 6.666]
    assert steps[0]["depth_of_cut"] == 17.167


def test_helixes_around_different_centres_are_separate_steps():
    """서로 떨어진 두 원(중심 X0 · X40)은 다른 형상이다."""
    program = "\n".join(["M6T1", "S8000M3", "G90G0X6Y0Z5",
                         "G1Z-1F200", "G2I-6J0F1000", "G0Z5",
                         "G0X46Y0", "G1Z-1F200", "G2I-6J0F1000", "G0Z5", "M30"])
    for tools in (None, {1: {"diameter": 12.0}}):
        steps = parse_nc_cutting_conditions(program, tools=tools)
        assert [s["feature_no"] for s in steps] == [1, 2]
        assert [s["strategy"] for s in steps] == ["circular", "circular"]


def test_helically_milled_hole_is_a_round_hole():
    program = "\n".join(["M6T9", "S8000M3", "G90G0X30Y30Z5", "G1Z0F200",
                         "G3X34Y30I-4J0Z-2F800", "G3X34Y30I-4J0Z-4", "G3X34Y30I-4J0", "G0Z5", "M30"])
    steps = parse_nc_cutting_conditions(program, tools={9: {"diameter": 8.0, "kind": "ENDMILL"}},
                                        stock={"x": 60, "y": 60, "z": 20})
    assert [(s["feature_type"], s["strategy"]) for s in steps] == [("round_hole", "helical")]
    assert steps[0]["feature_geometry"]["diameter"] == 16.0                   # 원 지름 8 + 공구 8
    assert steps[0]["feature_geometry"]["depth"] == 4.0


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
                                   "cutting_distance", "feature_no", "feature_type", "operation_type",
                                   "strategy", "toolpaths")] == [None] * 10


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
    # 한 사이클의 구멍들이 형상 하나 (ISO 14649 구멍 패턴), 공정은 사이클 G 코드로
    assert (steps[0]["feature_type"], steps[0]["operation_type"], steps[0]["strategy"]) == ("round_hole", "drilling", None)
    assert steps[0]["feature_geometry"]["holes"] == 3
    assert steps[0]["feature_geometry"]["positions"] == [[10.0, 10.0], [20.0, 10.0], [30.0, 10.0]]
    assert "bottom" not in steps[0]["feature_geometry"]          # 관통 여부는 소재 두께를 알 때만


def test_drilled_holes_are_through_or_blind_by_the_stock_thickness():
    program = "\n".join(["M6 T7", "S1200 M3", "G90 G0 X0 Y0 Z5", "G99 G81 X10 Y10 Z-5 R2 F100", "X20", "G80",
                         "G0 Z50", "M30"])
    tools = {7: {"diameter": 6.8}}
    thick = parse_nc_cutting_conditions(program, tools=tools, stock={"x": 40, "y": 20, "z": 20})
    thin = parse_nc_cutting_conditions(program, tools=tools, stock={"x": 40, "y": 20, "z": 4})
    assert thick[0]["feature_geometry"]["bottom"] == "blind"
    assert thin[0]["feature_geometry"]["bottom"] == "through"
    assert thin[0]["depth_of_cut"] == 4.0                       # 소재 바닥 아래는 재료가 없다


def test_tapping_cycle_is_a_tapping_step_after_the_drilling_step():
    program = "\n".join(["M6 T7", "S1200 M3", "G90 G0 X0 Y0 Z5", "G99 G81 X10 Y10 Z-8 R2 F100", "X20", "G80",
                         "G0 Z50", "M6 T8", "S300 M3", "G0 X0 Y0 Z5", "G99 G84 X10 Y10 Z-6 R3 F375", "X20", "G80",
                         "G0 Z50", "M30"])
    steps = parse_nc_cutting_conditions(program, tools={7: {"diameter": 6.8}, 8: {"diameter": 8.0}})
    assert [s["operation_type"] for s in steps] == ["drilling", "tapping"]
    assert [s["feature_type"] for s in steps] == ["round_hole", "round_hole"]


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
