"""NC 코드에서 읽는 프로그램 이름과 절삭 경로 길이 (parsers/nc_parser.py)."""
import os
import sys

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from parsers.nc_parser import nc_cutting_distance, nc_program_name

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
O1110_PATH = os.path.join(FIXTURES, "O1110_20261111_mmas.nc")


@pytest.mark.parametrize("text, name", [
    ("%\n<O1110.NC>G91G28Z0 \nM6T1\n", "O1110.NC"),        # 이름형 헤더 뒤에 블록이 붙은 실측 형태
    ("%\n<O0911.nc> \nG90G80G49G40G0\n", "O0911.nc"),       # 대소문자를 그대로 둔다 (TDMS 표기와 같게)
    ("%\nO1234 (FACE MILL)\nG90\n", "O1234"),               # 번호형
    ("%\n(PROGRAM BY CAM)\nO0001\nG90\n", "O0001"),         # 앞에 주석 한 줄
    ("%\nG90G0X0Y0\nG1X10F100\nM30\n", None),               # 이름이 없다
    ("", None),
])
def test_nc_program_name(text, name):
    assert nc_program_name(text) == name
    assert nc_program_name(text.encode("utf-8")) == name


def test_nc_program_name_from_a_file():
    assert nc_program_name(O1110_PATH) == "O1110.NC"


def test_nc_cutting_distance_matches_the_machine_xml():
    """실측 O1110: 장비 XML 의 CuttingMovingDistance 4327.48mm 와 0.5% 안에서 같다."""
    distance = nc_cutting_distance(O1110_PATH)
    assert distance == pytest.approx(4336.0, abs=0.5)
    assert abs(distance / 4327.479 - 1) < 0.005


def test_nc_cutting_distance_counts_only_cutting_moves():
    nc = "%\nG90G0X0Y0Z5\nS1000M3\nG1Z-1F100\nX10\nY10\nG0Z5\nX100\nM30\n%\n"
    assert nc_cutting_distance(nc) == pytest.approx(26.0)      # Z 진입 6 + X 10 + Y 10 (급속 제외)
    assert nc_cutting_distance("%\nG90G0X0Y0\nX10\nM30\n%\n") is None
    assert nc_cutting_distance("") is None
