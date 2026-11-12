import math
import os
import sys
import re
from typing import Any, Dict, Optional

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy.orm import Session
from sqlalchemy import text
from DB.database import get_db
from DB.models import Workingstep, WorkplanFileArchive
from job_manager import get_or_create_job, adopt_workplan_identity
from file_archive import get_or_create_workplan_archive
from vault_manager import archive_file
from tool_codes import format_tool_code, index_by_number


# ---------------------------------------------------------------------------
# 워킹스텝 분리 규칙
# ---------------------------------------------------------------------------
# 워킹스텝 하나 = "같은 공구로, 같은 절삭조건(S·F)으로 이어서 깎은 구간".
#
# 예전에는 공구 교환(M6)에서만 끊었다. 그래서 공구 하나로 조건을 바꿔 가며 깎는 실험
# (2026-11-11 O1110: 공구 3개로 조건 32개)이 공구당 한 행으로 뭉쳤고, 그 행에는 마지막 S 와
# 첫 F 가 섞여 실제로 깎은 적 없는 조합이 적혔다(T1: S9500 / F150).
#
# 경계
#   1. 공구 교환. M6 이 있는 프로그램은 M6 에서(T 만 적힌 줄은 다음 공구 예약), 없는
#      프로그램은 T 가 바뀔 때.
#   2. 같은 공구 안에서 절삭 패스의 S 나 대표 F 가 바로 앞 패스와 다를 때.
#
# 절삭 패스  급속 이동(G0)·원점 복귀 사이에 이어지는 절삭 이동(G1/G2/G3, 고정 사이클).
# 대표 F     그 패스에서 XY 로 가장 길게 깎은 이송. CAM 은 Z 진입을 낮은 F 로, 그것도 소재
#            위 높은 곳부터 내리는 일이 많아(AUTOMA 출력: Z49.5 -> Z-2 를 F400, 절삭은 F600)
#            거리나 줄 수로 세면 진입 이송이 대표가 된다.
# 보조 패스  XY 이동 없이 Z 로만 움직인 패스(수직 진입)와, 같은 공구의 다른 패스는 Z0 아래로
#            내려가는데 Z0 위에서만 움직인 패스(공중 이송). 경계를 만들지 않고 버린다.
#            공구 구간 전체가 보조 패스뿐이면(G1 드릴링·고정 사이클 등) 그것들로 스텝을 만든다.
# 절입 깊이  공작물 좌표 Z0 을 소재 윗면으로 보고 패스가 내려간 가장 낮은 Z 의 깊이(mm).
#            Z0 위에서만 깎았으면 모른다(None). 한 스텝의 패스들이 같은 깊이일 때만 적고,
#            층을 나눠 내려가는 가공처럼 깊이가 섞이면 한 값으로 말할 수 없어 비운다(None).
#            깊이는 경계가 아니다. 깊이로 나누면 CAM 의 층 가공이 층마다 쪼개진다
#            (O0912(EDGECAM) T6 은 Z-3 / Z-4 를 번갈아 깎아 8개로 갈라진다).

_COMMENT_RE = re.compile(r'\(.*?\)|<.*?>|;.*$')
# (?<![#A-Z]) 는 G43H6 의 H6 같은 다른 주소나 #100 같은 변수를 오인하지 않기 위한 것이다.
_M6_RE = re.compile(r'(?<![#A-Z])M0?6(?![0-9])')
_PROGRAM_END_RE = re.compile(r'(?<![#A-Z])M(?:0?2|30)(?![0-9])')
_T_RE = re.compile(r'(?<![#A-Z])T(\d+)')
_S_RE = re.compile(r'(?<![#A-Z])S(\d+(?:\.\d*)?)')
_F_RE = re.compile(r'(?<![#A-Z])F(\d+(?:\.\d*)?|\.\d+)')
# G00X26 처럼 붙여 쓴 형태와 G54.1 같은 소수 코드를 모두 받는다 (정수부만 쓴다).
_G_RE = re.compile(r'(?<![#A-Z])G0*(\d{1,2})(?:\.\d+)?(?![0-9])')
_WORD_RE = re.compile(r'(?<![#A-Z])([XYZIJKR])\s*([-+]?(?:\d+\.?\d*|\.\d+))')
# Z#100 처럼 값이 변수인 축: 어디로 가는지 모른다.
_VARIABLE_AXIS_RE = re.compile(r'(?<![#A-Z])([XYZ])\s*[#\[]')

_CANNED_CYCLES = {73, 74, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89}
# 축 글자가 있어도 이동이 아닌 코드 (G04 드웰 시간, G10 오프셋 데이터)
_NON_MOTION_CODES = {4, 10}
# 절삭이 아니고, 끝난 뒤 공작물 좌표상 위치를 알 수 없는 코드
# (G28·G30 원점 복귀, G31 스킵, G52 로컬 좌표, G53 기계 좌표, G65·G66 매크로 호출)
_POSITION_LOST_CODES = {28, 30, 31, 52, 53, 65, 66}
_SET_POSITION_CODE = 92
# 원호 평면별 (가로축, 세로축, 수직축, 가로 중심 오프셋, 세로 중심 오프셋).
# G2(시계)/G3(반시계)는 수직축 + 쪽에서 내려다본 방향이다.
_ARC_PLANES = {17: ('X', 'Y', 'Z', 'I', 'J'),
               18: ('Z', 'X', 'Y', 'K', 'I'),
               19: ('Y', 'Z', 'X', 'J', 'K')}
_AXES = ('X', 'Y', 'Z')
# 깊이를 비교·저장하는 자릿수(0.1 µm). 증분 지령을 더하며 생기는 부동소수 잔차를 지운다.
_DEPTH_DIGITS = 4
_EPS = 1e-9


def _read_nc_text(nc_input):
    """경로 · 바이트열 · 문자열 어느 것으로 와도 NC 본문 문자열을 돌려준다."""
    if not nc_input:
        return ""
    encodings = ('utf-8', 'cp949', 'euc-kr', 'latin1')
    if isinstance(nc_input, bytes):
        for enc in encodings:
            try:
                return nc_input.decode(enc)
            except Exception:
                continue
        return ""
    if os.path.exists(str(nc_input)) and os.path.isfile(str(nc_input)):
        for enc in encodings:
            try:
                with open(nc_input, 'r', encoding=enc) as f:
                    return f.read()
            except Exception:
                continue
        return ""
    return str(nc_input)


def _arc_length(d_a, d_b, words, center_a, center_b, clockwise) -> Optional[float]:
    """원호 평면 안에서의 원호 길이. R 지령과 I/J/K 지령 모두 받는다. 모르면 None.

    I/J/K 는 시작점 기준 중심 위치라(파낙 기본값) 절대 좌표를 몰라도 계산된다.
    시작점과 끝점이 같으면 한 바퀴(2π)다.
    """
    if 'R' in words:
        radius = abs(words['R'])
        if radius <= _EPS:
            return None
        theta = 2 * math.asin(min(1.0, math.hypot(d_a, d_b) / (2 * radius)))
        if words['R'] < 0:          # R 이 음수면 180° 를 넘는 쪽 원호
            theta = 2 * math.pi - theta
        return radius * theta
    if center_a in words or center_b in words:
        start = (-words.get(center_a, 0.0), -words.get(center_b, 0.0))   # 중심 -> 시작점
        end = (start[0] + d_a, start[1] + d_b)                            # 중심 -> 끝점
        ccw = math.atan2(start[0] * end[1] - start[1] * end[0],
                         start[0] * end[0] + start[1] * end[1])
        theta = -ccw if clockwise else ccw
        if theta <= _EPS:
            theta += 2 * math.pi
        return math.hypot(*start) * theta
    return None


def _move_lengths(delta, motion, plane, words):
    """절삭 이동 한 블록의 (XY 길이, 전체 길이). 모르는 값은 None."""
    dx, dy, dz = delta['X'], delta['Y'], delta['Z']
    if motion in (2, 3):
        axis_a, axis_b, axis_n, center_a, center_b = _ARC_PLANES[plane]
        d_a, d_b, d_n = delta[axis_a], delta[axis_b], delta[axis_n]
        if d_a is not None and d_b is not None and d_n is not None:
            arc = _arc_length(d_a, d_b, words, center_a, center_b, clockwise=(motion == 2))
            if arc is not None:
                if plane == 17:
                    xy_len = arc
                elif dx is not None and dy is not None:
                    xy_len = math.hypot(dx, dy)   # XZ·YZ 평면 원호는 XY 로 움직인 양만 대략 본다
                else:
                    xy_len = None
                return xy_len, math.hypot(arc, d_n)
    if dx is None or dy is None:
        return None, None
    xy_len = math.hypot(dx, dy)
    return xy_len, (math.hypot(xy_len, dz) if dz is not None else None)


class _PassScanner:
    """NC 블록을 순서대로 읽어 공구 구간별 절삭 패스를 모은다.

    위치는 공작물 좌표로 추적하고, 알 수 없게 되면(G28 원점 복귀 뒤 등) None 으로 둔다.
    """

    def __init__(self, uses_m6):
        self.uses_m6 = uses_m6
        self.sections = [self._new_section(None)]
        self.pos = {'X': None, 'Y': None, 'Z': None}
        self.absolute = True            # G90 / G91
        self.motion = 0                 # 01 그룹: 0 급속, 1 직선, 2 시계 원호, 3 반시계 원호
        self.plane = 17
        self.in_cycle = False           # 고정 사이클(G81 등) 진행 중
        self.cycle_bottom = None        # 고정 사이클 구멍 바닥 Z (G90 일 때만 안다)
        self.cycle_r = None             # 고정 사이클 R 점
        self.tool = None
        self.pending_tool = None        # T 로 예약만 한 공구 (M6 에서 바뀐다)
        self.spindle = None
        self.feed = None
        self.current = None             # 진행 중인 절삭 패스

    @staticmethod
    def _new_section(tool) -> Dict[str, Any]:
        return {'tool': tool, 'last_s': None, 'first_f': None, 'passes': []}

    def _close_pass(self):
        if self.current is not None:
            self.sections[-1]['passes'].append(self.current)
            self.current = None

    def _change_tool(self, number):
        self._close_pass()
        self.tool = number
        self.sections.append(self._new_section(number))

    def _cut(self, xy_len, length, end_z, moved_xy):
        if self.current is None:
            self.current = {'s': self.spindle, 'feeds': {}, 'min_z': None, 'moved_xy': False}
        # 이송별 [XY 길이, 전체 길이, 블록 수]. 대표 F 는 이 순서로 비교해 고른다.
        acc = self.current['feeds'].setdefault(self.feed, [0.0, 0.0, 0])
        acc[0] += xy_len or 0.0
        acc[1] += length or 0.0
        acc[2] += 1
        if moved_xy:
            self.current['moved_xy'] = True
        if end_z is not None and (self.current['min_z'] is None or end_z < self.current['min_z']):
            self.current['min_z'] = end_z

    def _read_g_codes(self, block):
        """모달 상태를 갱신하고, 이 블록에만 듣는 코드가 있으면 돌려준다."""
        one_shot = None
        for code in (int(g) for g in _G_RE.findall(block)):
            if code in (0, 1, 2, 3):
                self.motion = code
                self.in_cycle = False       # 01 그룹 코드는 고정 사이클을 끝낸다
            elif code in _ARC_PLANES:
                self.plane = code
            elif code == 90:
                self.absolute = True
            elif code == 91:
                self.absolute = False
            elif code == 80:
                self.in_cycle = False
            elif code in _CANNED_CYCLES:
                self.in_cycle = True
            elif (code in _NON_MOTION_CODES or code in _POSITION_LOST_CODES
                  or code == _SET_POSITION_CODE):
                one_shot = code
        return one_shot

    def _target(self, commanded, unknown_axes):
        """이 블록의 축별 이동량과 도착 위치. 모르면 None."""
        delta, target = {}, dict(self.pos)
        for axis in _AXES:
            if axis in unknown_axes:
                delta[axis] = target[axis] = None
            elif axis not in commanded:
                delta[axis] = 0.0
            elif self.absolute:
                delta[axis] = None if self.pos[axis] is None else commanded[axis] - self.pos[axis]
                target[axis] = commanded[axis]
            else:
                delta[axis] = commanded[axis]
                target[axis] = None if self.pos[axis] is None else self.pos[axis] + commanded[axis]
        return delta, target

    def _drill(self, words, target):
        """고정 사이클 블록 하나 = 구멍 하나. 깊이는 구멍 바닥(사이클의 Z)이다."""
        if 'Z' in words:
            self.cycle_bottom = words['Z'] if self.absolute else None
        if 'R' in words:
            self.cycle_r = words['R'] if self.absolute else None
        bottom, r_point = self.cycle_bottom, self.cycle_r
        stroke = abs(r_point - bottom) if (r_point is not None and bottom is not None) else None
        self._cut(0.0, stroke, bottom, moved_xy=False)
        target['Z'] = None      # 구멍마다 R 점이나 시작 높이로 돌아간다 (어느 쪽인지 따지지 않는다)
        self.pos = target

    def read_block(self, block):
        t_match = _T_RE.search(block)
        if t_match:
            self.pending_tool = int(t_match.group(1))
            if not self.uses_m6 and self.pending_tool != self.tool:
                self._change_tool(self.pending_tool)
        if self.uses_m6 and _M6_RE.search(block):
            self._change_tool(self.pending_tool)

        s_match = _S_RE.search(block)
        if s_match:
            value = float(s_match.group(1))
            if value != self.spindle:
                self._close_pass()      # 깎는 도중 회전수가 바뀌면 거기서 패스를 끊는다
            self.spindle = value
            self.sections[-1]['last_s'] = value
        f_match = _F_RE.search(block)
        if f_match:
            self.feed = float(f_match.group(1))
            if self.sections[-1]['first_f'] is None:
                self.sections[-1]['first_f'] = self.feed

        one_shot = self._read_g_codes(block)
        words = {letter: float(value) for letter, value in _WORD_RE.findall(block)}
        commanded = {axis: words[axis] for axis in _AXES if axis in words}
        unknown_axes = set(_VARIABLE_AXIS_RE.findall(block))

        if _PROGRAM_END_RE.search(block):
            self._close_pass()

        # 축 없이 I/J/K 나 R 만 적은 원호 블록은 제자리 한 바퀴(원호 보간)다.
        arc_in_place = (not self.in_cycle and self.motion in (2, 3) and one_shot is None
                        and any(letter in words for letter in 'IJKR'))
        if not (commanded or unknown_axes or arc_in_place or (self.in_cycle and 'R' in words)):
            return
        if one_shot in _NON_MOTION_CODES:
            return
        if one_shot == _SET_POSITION_CODE:
            self.pos.update(commanded)
            for axis in unknown_axes:
                self.pos[axis] = None
            return
        if one_shot in _POSITION_LOST_CODES:
            self._close_pass()
            for axis in set(commanded) | unknown_axes:
                self.pos[axis] = None
            return

        delta, target = self._target(commanded, unknown_axes)
        if self.in_cycle:
            self._drill(words, target)
            return
        if self.motion == 0:
            self._close_pass()
        else:
            xy_len, length = _move_lengths(delta, self.motion, self.plane, words)
            if self.motion in (2, 3) and self.plane == 17:
                moved_xy = True
            else:
                moved_xy = any(delta[axis] is None or abs(delta[axis]) > _EPS for axis in ('X', 'Y'))
            self._cut(xy_len, length, target['Z'], moved_xy)
        self.pos = target

    def finish(self):
        self._close_pass()
        return self.sections


def _pass_depth(min_z):
    """Z0 기준 깊이(mm). Z0 위에서만 움직였거나 Z 를 모르면 None."""
    if min_z is None:
        return None
    z = round(min_z, _DEPTH_DIGITS)
    return abs(z) if z <= 0 else None


def _representative_feed(feeds):
    """XY 로 가장 길게 깎은 이송. XY 이동이 없으면 전체 길이, 그것도 모르면 블록 수로 고른다."""
    return max(feeds.items(), key=lambda item: tuple(item[1]))[0]


def _passes_defining_steps(passes):
    """스텝을 만드는 패스만 고른다 (보조 패스는 경계를 만들지 않는다)."""
    in_material = [p for p in passes
                   if p['moved_xy'] and (p['min_z'] is None or round(p['min_z'], _DEPTH_DIGITS) <= 0)]
    if in_material:
        return in_material
    # Z0 아래로 내려간 패스가 없다: Z0 이 소재 윗면이 아닌 프로그램이거나 드릴링뿐인 공구.
    return [p for p in passes if p['moved_xy']] or passes


def _steps_from_sections(sections):
    steps = []
    for section in sections:
        merged = []
        for p in _passes_defining_steps(section['passes']):
            key = (p['s'], _representative_feed(p['feeds']))
            depth = _pass_depth(p['min_z'])
            if merged and merged[-1]['key'] == key:
                merged[-1]['depths'].append(depth)
            else:
                merged.append({'key': key, 'depths': [depth]})

        for m in merged:
            depths = m['depths']
            steps.append({
                'tool_number': section['tool'],
                'spindle_speed': m['key'][0],
                'feed_rate': m['key'][1],
                'depth_of_cut': depths[0] if len(set(depths)) == 1 else None,
                'pass_count': len(depths),
            })
        if not merged and section['tool'] is not None:
            # 절삭 없이 호출만 된 공구도 XML 의 공구 목록과 맞추려고 한 줄 남긴다 (예전 동작).
            steps.append({
                'tool_number': section['tool'],
                'spindle_speed': section['last_s'],
                'feed_rate': section['first_f'],
                'depth_of_cut': None,
                'pass_count': 0,
            })
    return steps


def parse_nc_cutting_conditions(nc_input):
    """
    NC 프로그램 코드(G-code)를 워킹스텝 단위로 나눠 공구 번호(T), 주축 회전수(S, RPM),
    이송속도(F, mm/min), 절입 깊이(mm)를 뽑는다. 나누는 규칙은 위 "워킹스텝 분리 규칙".

    경로 · 바이트열 · 문자열을 모두 받고, compact 포맷(S3800M3, G1Z-2F710, M6T6)과
    공백 구분 포맷을 모두 지원한다.

    반환: 가공 순서대로 dict 목록
        step_order, tool_number, xml_tool_code, spindle_speed, feed_rate, depth_of_cut,
        pass_count (이 스텝으로 합친 절삭 패스 수. 절삭 없이 호출만 된 공구는 0)
    """
    content = _read_nc_text(nc_input)
    if not content:
        return []

    blocks = []
    for line in content.splitlines():
        block = _COMMENT_RE.sub('', line).strip().upper()
        if block and not block.startswith('%'):
            blocks.append(block)

    scanner = _PassScanner(uses_m6=any(_M6_RE.search(b) for b in blocks))
    for block in blocks:
        scanner.read_block(block)
    steps = _steps_from_sections(scanner.finish())

    for idx, s in enumerate(steps, start=1):
        s['step_order'] = idx
        # 표준 표기로 적는다 (T6 -> T06). 매칭은 tool_number 로 하므로 표기가 바뀌어도 안전하다.
        s['xml_tool_code'] = format_tool_code(s['tool_number'])

    return steps


def _resolve_nc_source(db, workplan_id, nc_file_path, nc_content):
    """파싱할 NC 를 찾는다: 넘겨받은 내용 -> 파일 경로 -> NC 아카이브(BLOB -> 원본 경로)."""
    if nc_content:
        return nc_content
    if nc_file_path and os.path.exists(nc_file_path):
        return nc_file_path
    archive = db.query(WorkplanFileArchive).filter(WorkplanFileArchive.workplan_id == workplan_id).first()
    if archive and archive.nc_file_content:
        return archive.nc_file_content
    if archive and archive.nc_raw_path:
        # 저장된 경로는 RAW_DATA_ROOT 기준 상대경로라 그대로 열 수 없다.
        # (get_abs_raw_data_path 는 절대경로가 들어와도 그대로 돌려주므로 옛 데이터도 안전하다)
        from vault_manager import get_abs_raw_data_path
        abs_nc = get_abs_raw_data_path(archive.nc_raw_path)
        if abs_nc and os.path.exists(abs_nc):
            return abs_nc
    return None


def _stored_tool_number(step):
    # tool_number 는 NOT NULL 이라 공구를 모르는 스텝은 0 으로 적는다 (예전부터 같은 규칙).
    return step['tool_number'] if step['tool_number'] is not None else 0


def _apply_nc_steps(db, workplan_id, parsed_steps):
    """NC 파싱 결과를 Workplan 의 Workingstep 으로 만든다. flush 까지만 하고 커밋하지 않는다."""
    # xml_parser 는 Workingstep 을 db.add() 로 세션에 담아두기만 한 채 이 함수를 부를 수 있다.
    # 세션이 autoflush=False 라(DB/database.py) flush 없이 조회하면 그 행들이 보이지 않아
    # "스텝이 아직 없다"고 판단하고 같은 스텝을 한 번 더 만들어 버린다.
    # 조회 전에 반드시 flush 해서 같은 트랜잭션의 미반영 행까지 보이게 한다.
    db.flush()
    existing = (db.query(Workingstep)
                .filter(Workingstep.workplan_id == workplan_id)
                .order_by(Workingstep.step_order, Workingstep.step_id)
                .all())

    if [ws.tool_number for ws in existing] == [_stored_tool_number(p) for p in parsed_steps]:
        # 공구 순서가 같으면 값만 맞춘다. 행을 지우지 않으므로 step_id 와
        # 사람이 DB 테이블 화면에서 적은 operation_type 이 그대로 남는다.
        for ws, p in zip(existing, parsed_steps):
            ws.step_order = p['step_order']
            ws.spindle_speed = p['spindle_speed']
            ws.feed_rate = p['feed_rate']
            ws.depth_of_cut = p['depth_of_cut']
        db.flush()
        return

    # 모양이 다르다: 예전 규칙(공구당 한 행)이나 XML ToolNumbers 로 만든 스텝이다.
    # 지우고 NC 기준으로 다시 만든다. step_id 를 참조하는 테이블은 없다.
    # 사람이 적은 operation_type 은 공구 번호로 대응이 분명할 때만 옮겨 준다.
    op_by_tool = {}
    for ws in existing:
        op_by_tool.setdefault(ws.tool_number, set()).add(ws.operation_type)
    carried_op = {tool: ops.pop() for tool, ops in op_by_tool.items()
                  if len(ops) == 1 and None not in ops}

    # 공구 마스터를 **번호** 기준으로 색인한다. NC 는 T6 으로 적고 마스터는 T06 으로
    # 저장되므로, 표기를 그대로 비교하면 연결되지 않는다.
    tool_by_number = index_by_number(
        db.execute(text("SELECT tool_code, tool_id FROM tool WHERE tool_code IS NOT NULL"))
    )
    new_rows = [dict(
        workplan_id=workplan_id,
        tool_id=tool_by_number.get(p['tool_number']),
        operation_type=carried_op.get(_stored_tool_number(p)),
        step_order=p['step_order'],
        tool_number=_stored_tool_number(p),
        xml_tool_code=p['xml_tool_code'],
        feed_rate=p['feed_rate'],
        spindle_speed=p['spindle_speed'],
        depth_of_cut=p['depth_of_cut'],
    ) for p in parsed_steps]

    for ws in existing:
        db.delete(ws)
    db.flush()
    db.add_all([Workingstep(**row) for row in new_rows])
    db.flush()
    if existing:
        print(f"[NC Parser] Workplan {workplan_id} 의 워킹스텝을 NC 기준으로 다시 만들었습니다 "
              f"({len(existing)}개 -> {len(new_rows)}개, 공구 교환 · S·F 변화 기준)")


def sync_workplan_nc_cutting_conditions(db: Session, workplan_id, nc_file_path: Optional[str] = None,
                                        nc_content=None, commit: bool = True):
    """
    Workplan 의 Workingstep 을 NC 코드 기준으로 맞춘다. NC 가 있으면 NC 가 스텝 목록의 기준이다.

    - 기존 스텝의 공구 순서가 NC 파싱 결과와 같으면 값(S · F · 절입 깊이)만 고친다.
    - 다르면 지우고 NC 기준으로 다시 만든다. 예전 규칙으로 공구당 한 행씩 만든 스텝이나
      XML 의 ToolNumbers 로 만든 스텝이 여기서 조건별 스텝으로 바뀐다.
    - NC 를 찾지 못했거나 절삭 블록이 하나도 없으면 아무것도 바꾸지 않고 False 를 돌려준다.

    commit=False 는 호출한 쪽(xml_parser)이 트랜잭션을 쥐고 있을 때 쓴다. flush 까지만 하고,
    DB 오류는 그대로 올려 보내 호출한 쪽이 전체를 rollback 하게 한다. (예전에는 이 함수가
    도중에 commit/rollback 해서 xml_parser 가 아직 커밋하지 않은 Part·Workplan 까지 같이
    확정하거나 날렸다.)
    """
    try:
        source = _resolve_nc_source(db, workplan_id, nc_file_path, nc_content)
        parsed_steps = parse_nc_cutting_conditions(source) if source else []
    except Exception as e:
        print(f"[NC Parser] NC 를 읽지 못해 워킹스텝을 그대로 둡니다 ({workplan_id}): {e}")
        return False
    if not parsed_steps:
        return False

    if not commit:
        _apply_nc_steps(db, workplan_id, parsed_steps)
        return True

    try:
        _apply_nc_steps(db, workplan_id, parsed_steps)
        db.commit()
        return True
    except Exception as e:
        print(f"[NC Parser] 가공 조건 동기화 중 오류 ({workplan_id}): {e}")
        db.rollback()
        return False


def parse_nc(file_path, job_id_str=None):
    """
    단독으로 업로드된 NC 파일을 파싱하여 DB에 반영합니다.
    XML 없이 NC 파일만 업로드된 경우에도 Job과 Workplan을 생성/조회하고 nc_file_path를 업데이트하며,
    Workingstep에 feed_rate 및 spindle_speed를 파싱하여 연동합니다.
    """
    if not job_id_str:
        return False

    # 경로가 machining_raw_data/{프로젝트}/{부품}/... 형태인지만 확인한다.
    # (프로젝트·부품 이름 자체는 job_id_str 로 이미 넘어오므로 여기서 쓰지 않는다.
    #  예전에는 parts[raw_idx+2] 를 꺼내 쓰면서 경로가 짧으면 IndexError 로 터졌다.)
    parts = file_path.split(os.sep)
    try:
        raw_idx = parts.index("machining_raw_data")
    except ValueError:
        print(f"[NC Parser] machining_raw_data 아래의 파일이 아닙니다: {file_path}")
        return False
    if len(parts) - raw_idx < 4:
        print(f"[NC Parser] 파일 경로에서 프로젝트/부품 정보를 찾을 수 없습니다: {file_path}")
        return False

    db = next(get_db())
    try:
        # get_or_create_job은 필요시 Part, Workplan, Job을 모두 생성합니다.
        job = get_or_create_job(db, job_id_str)
        if not job:
            print(f"[NC Parser] Job 생성 실패: {job_id_str}")
            return False

        # 해당 Job의 Workplan에 nc_file_path 업데이트
        from vault_manager import get_rel_raw_data_path
        if job.workplan:
            nc_rel_for_workplan = get_rel_raw_data_path(file_path)

            # program_code가 없는 경우 NC 파일명을 프로그램 코드로 임시 사용
            fallback_program = None
            if not job.workplan.program_code or job.workplan.program_code == "UNKNOWN":
                fallback_program = os.path.splitext(os.path.basename(file_path))[0]

            prev_workplan_id = job.workplan_id
            adopt_workplan_identity(db, job, program_code=fallback_program,
                                    nc_file_path=nc_rel_for_workplan)
            # 경로는 아래 workplan_file_archive 기록부에서 한 번만 쓴다.
            # (예전에는 workplan.nc_file_path 에도 같은 값을 또 적었다)

            # NC 파일 바이너리 데이터 읽기 및 저장
            nc_binary_data = None
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    nc_data = f.read()
                    if len(nc_data) <= 15 * 1024 * 1024:
                        nc_binary_data = nc_data
                    else:
                        print(f"    - [알림] NC 파일이 15MB를 초과하여 DB 내부 저장을 생략합니다.")

            # XML 이 NC 보다 먼저 처리되면 Workplan 의 nc_hash 가 'NOHASH' 로 남는다.
            # 이제 NC 내용을 알게 됐으므로 여기서 채워 넣는다. 그러지 않으면 나중에 같은 부품·
            # 프로그램의 XML 이 다시 들어올 때 해시가 다르다고 판단해 Workplan 이 하나 더 생기고,
            # 먼저 만든 쪽이 아무 Job 도 참조하지 않는 고아로 남는다.
            if nc_binary_data and job.workplan.nc_hash in (None, "NOHASH"):
                import hashlib
                real_hash = hashlib.md5(nc_binary_data).hexdigest()[:8]
                # 그냥 대입하면 같은 부품·프로그램의 Workplan 이 이미 있을 때
                # uq_workplan_identity 에 걸려 트랜잭션 전체가 rollback 된다.
                # adopt_workplan_identity 가 충돌 시 기존 Workplan 으로 Job 을 옮겨 준다.
                adopt_workplan_identity(db, job, nc_hash=real_hash,
                                        nc_file_path=nc_rel_for_workplan)
                print(f"    - [NC Parser] Workplan {job.workplan_id} 의 NC 해시를 채웠습니다: {real_hash}")

            from integrity import sha256_bytes
            nc_sha256 = sha256_bytes(nc_binary_data) if nc_binary_data else None

            # 경로는 반드시 RAW_DATA_ROOT 기준 상대경로로 저장한다.
            # 예전에는 이 컬럼만 절대경로였는데, 그러면 프로젝트 폴더를 옮기거나 다른 PC 에서
            # 같은 DB 를 열었을 때 이 한 칸만 깨진다 (다른 경로 컬럼은 전부 상대경로다).
            nc_rel_path = get_rel_raw_data_path(file_path)

            wp_archive = get_or_create_workplan_archive(db, job.workplan_id)
            wp_archive.nc_raw_path = nc_rel_path
            wp_archive.nc_file_content = nc_binary_data
            wp_archive.nc_file_sha256 = nc_sha256

            # 보관소 사본도 여기서 남긴다. 예전에는 xml_parser 가 Workplan 을 새로 만들 때만
            # 넣어서, NC 가 먼저 도착하면 보관소에 NC 가 없는 Job 이 생겼다.
            archive_file(file_path)

            db.commit()

            # NC 가공 조건 (feed_rate, spindle_speed) 파싱 및 Workingstep 동기화
            sync_workplan_nc_cutting_conditions(db, job.workplan_id, nc_file_path=file_path)

            print(f"[NC Parser] NC 파일 파싱 및 가공조건 반영 완료 (Job PK: {job.job_id}): {file_path}")
            return True
        else:
            print(f"[NC Parser] Workplan이 존재하지 않습니다. (Job PK: {job.job_id})")
            return False

    except Exception as e:
        print(f"[NC Parser] 데이터베이스 처리 중 에러 발생: {e}")
        db.rollback()
        return False
    finally:
        db.close()
