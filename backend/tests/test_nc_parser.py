import os
import sys
import pytest

# Ensure backend is in path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from parsers.nc_parser import parse_nc_cutting_conditions


def test_parse_o0911_compact():
    """
    O0911.nc (컴팩트 포맷):
    M6T6
    S3800M3
    G1Z-2F710
    검증: Feed rate 710, Spindle Speed 3800, Tool 6
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
    assert len(steps) == 1
    step = steps[0]
    assert step["step_order"] == 1
    assert step["tool_number"] == 6
    assert step["xml_tool_code"] == "T6"
    assert step["spindle_speed"] == 3800.0
    assert step["feed_rate"] == 710.0


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

    # Step 2
    assert steps[1]["step_order"] == 2
    assert steps[1]["tool_number"] == 6
    assert steps[1]["spindle_speed"] == 3800.0
    assert steps[1]["feed_rate"] == 710.0


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
    assert steps[0]["feed_rate"] in (500.0, 1200.0)
