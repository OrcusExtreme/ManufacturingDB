"""NC 절삭 패스의 기하 분석 — 실제 공구 지름으로 깎인 자리를 따라가 가공 형상, 절입, 황삭·정삭을 정한다.

워킹스텝 분리(nc_parser 의 "워킹스텝 분리 규칙")가 쓴다. ISO 14649 의 워킹스텝은 가공 형상(feature) ·
가공 공정과 전략(operation · strategy) · 절삭 조건(technology) · 공구가 같은 단위다. 그래서 패스마다
어느 형상을 깎았는지, 황삭인지 정삭인지를 NC 경로와 공구 지름으로 알아낸다.

소재 모델 (Z-map)
  공작물 XY 를 격자로 나누고 칸마다 소재 윗면 높이를 둔다. 공작물 좌표 Z0 이 소재 윗면이다.
    - 소재(블록) 크기를 알면(part.stock_x/y/z, 원점 corner = 윗면의 X·Y 가장 작은 모서리, center = 윗면 가운데)
      그 안에만 재료가 있다. 소재 밖을 지나는 진입·후퇴 경로는 허공이라 깎은 것으로 세지 않는다.
    - 모르면 재료가 Z0 높이로 넓게 깔려 있다고 보고 형상 번호(워킹스텝 분리)와 ap 만 정한다 (아래 '기록하지 않는 값').
  패스를 가공 순서대로 따라가며 평엔드밀 바닥(공구 반지름의 원판)이 지나간 칸을 그 높이까지 깎는다. 그러면 패스마다
    축방향 절입 ap  실제로 깎은 칸의 깎인 두께 (중앙값). 앞 층이나 다른 공구(정면 밀링)가 이미 깎은 높이에서 잰다.
    반경 절입 ae    경로에 수직으로 잰 실제로 깎인 폭 (중앙값). 슬롯을 처음 깎으면 공구 지름, 옆으로 넓히면 넓힌 폭.
    제거 체적       깎인 두께 x 칸 넓이의 합 (mm^3).
가공 형상
  마지막 소재 높이에서 바닥 높이가 같은(WALL_TOL 안) 깎인 칸이 이어진 덩어리 하나가 형상 하나다.
    - 사이에 소재가 남은 두 홈은 다른 형상이다. 겹치거나 맞닿게 깎아 한 덩어리로 파낸 패스들은 한 형상이다
      (O0911: Ø12 로 X26·X34 두 줄 -> 폭 20 슬롯 하나, X87·X95 -> 또 하나).
    - 정면 밀링한 넓은 면과 그 안에 더 깊이 판 슬롯은 바닥 높이가 달라 다른 형상이다.
    - 패스는 자기가 깎은 체적이 가장 많이 든 형상에 속한다. 공구가 달라도 같은 자리를 깎으면 같은 형상이다
      (황삭 공구와 정삭 공구가 다른 경우).
    - 드릴링(고정 사이클, Z 로만 내려간 패스)은 한 패스의 구멍들이 형상 하나다 (ISO 14649 의 구멍 패턴).
형상 분류 (ISO 14649-10) — 소재 크기를 알 때만
  덩어리 둘레의 네 옆(길이 방향 축 기준 좌 · 우 · 양 끝)이 벽인지(바닥보다 높은 재료가 남음) 열렸는지(소재 밖, 소재
  끝까지 깎음)로 가른다. 더 깊게 판 다른 형상(바닥의 구멍)은 벽이 아니다.
    벽 없음                                   planar_face
    한 옆만 벽                                step
    마주 보는 두 옆이 벽 (나머지 열림)          slot (양 끝 열림: through)
    벽 셋                                     길쭉하고(길이 >= 2 x 폭) 긴 두 옆이 벽이면 slot (한 끝 막힘), 아니면 open_pocket
    이웃한 두 옆이 벽 (모서리)                 open_pocket
    벽 넷                                     원(헬리컬 · 원호로 판 둥근 덩어리)이면 round_hole, 길쭉하면 slot (양 끝 막힘),
                                              아니면 closed_pocket
  공구 지름은 깎인 자리를 정할 때만 쓰고 분류에는 쓰지 않는다 (폭 39mm 의 양쪽 벽 통로도 slot).
정삭
  같은 형상에서 앞서 깎은 자리(깎인 칸이 겹치는 앞 패스)를 축방향으로 얇게 다시 깎은 패스:
  ap <= FINISH_MAX_AP 이고 ap < FINISH_RATIO x (겹치는 앞 패스의 가장 큰 ap) 이며, 뒤에 그 자리를 더 깊이 깎는
  패스가 없을 때. 같은 깊이를 한 번 더 도는 스프링 패스(ap 0)도 정삭이다. 층을 고르게 내려가거나 남은 두께로
  마무리하는 황삭(2·2·1 mm 등)은 황삭이다.
기록하지 않는 값 — 제대로 판단할 수 없으면 비워 둔다 (None)
  소재 크기를 모르면     형상 분류 · 형상 크기 · 제거 체적 · 반경 절입, 그리고 형상에 따라 이름이 갈리는 밀링 공정
                         (plane_* / bottom_and_side_*). 황삭/정삭은 패스별 경로(toolpaths 의 stage)에 남는다.
  공구 지름을 모르면     경로 중심선이 지나간 칸만 깎는다고 보고 형상 번호 · ap 만 정한다 (위 값들은 비움).
  드릴링 사이클의 구멍은 G 코드로 분명해 형상(round_hole) · 공정을 적는다. 관통 여부(bottom)는 소재 두께를 알 때만.
"""
import math

import numpy as np

EPS = 1e-4
# 형상 경계: 이웃 칸의 바닥 높이가 이보다 더 다르면 벽(다른 형상)으로 본다 (mm).
WALL_TOL = 0.1
# 정삭: 앞 패스(황삭) 축방향 절입의 이 비율 미만이고, FINISH_MAX_AP 이하로 얇게 깎은 마지막 패스.
FINISH_RATIO = 0.5
FINISH_MAX_AP = 0.5
# 격자 칸 크기: 가장 작은 공구 지름의 1/24 (0.05 ~ 1.0 mm), 칸 수는 MAX_CELLS 를 넘지 않게.
MAX_CELLS = 4_000_000
_CELL_PER_DIAMETER = 24
_MIN_CELL, _MAX_CELL = 0.05, 1.0

# ISO 14649-10 형상
SLOT, STEP, PLANAR_FACE, ROUND_HOLE = 'slot', 'step', 'planar_face', 'round_hole'
CLOSED_POCKET, OPEN_POCKET = 'closed_pocket', 'open_pocket'
# ISO 14649-11 구멍 가공 (고정 사이클 G 코드 -> 공정)
DRILL_OPERATIONS = {73: 'drilling', 81: 'drilling', 82: 'drilling', 83: 'drilling', 74: 'tapping',
                    84: 'tapping', 76: 'boring', 85: 'boring', 86: 'boring', 88: 'boring', 89: 'boring',
                    87: 'back_boring'}
ROUGH, FINISH = 'rough', 'finish'



# ---------------------------------------------------------------------------
# 경로 -> 점
# ---------------------------------------------------------------------------
def _known(*values):
    return all(v is not None for v in values)


def _arc_points(a, b, centre, clockwise, step):
    cx, cy = centre
    r = math.hypot(a['X'] - cx, a['Y'] - cy)
    a0 = math.atan2(a['Y'] - cy, a['X'] - cx)
    a1 = math.atan2(b['Y'] - cy, b['X'] - cx)
    sweep = -((a0 - a1) % (2 * math.pi)) if clockwise else (a1 - a0) % (2 * math.pi)
    if abs(sweep) < 1e-9:                       # 시작점 = 끝점: 한 바퀴
        sweep = -2 * math.pi if clockwise else 2 * math.pi
    n = max(2, int(math.ceil(abs(sweep) * r / step)) + 1)
    out = []
    for i in range(n):
        t = i / (n - 1)
        ang = a0 + sweep * t
        out.append((cx + r * math.cos(ang), cy + r * math.sin(ang), a['Z'] + (b['Z'] - a['Z']) * t))
    return out


def segment_points(seg, step):
    """세그먼트(_PassScanner 가 남긴 것)를 [(x, y, z), ...] 로. 좌표를 하나라도 모르면 None."""
    if seg[0] == 'hole':
        hole = seg[1]
        return [(hole['X'], hole['Y'], hole['Z'])] if _known(hole['X'], hole['Y'], hole['Z']) else None
    a, b = seg[1], seg[2]
    if not _known(a['X'], a['Y'], a['Z'], b['X'], b['Y'], b['Z']):
        return None
    if seg[0] == 'arc' and seg[3] is not None:
        return _arc_points(a, b, seg[3], seg[4], step)
    return [(a['X'], a['Y'], a['Z']), (b['X'], b['Y'], b['Z'])]


def _chords(points):
    """점 목록 -> 일정 높이 선분 (xa, ya, xb, yb, z). 높이는 두 끝 중 낮은 쪽 (깎는 쪽으로 넉넉히)."""
    if len(points) == 1:
        x, y, z = points[0]
        return [(x, y, x, y, z)]
    return [(a[0], a[1], b[0], b[1], min(a[2], b[2])) for a, b in zip(points, points[1:])]


# ---------------------------------------------------------------------------
# 패스 모양 · 전략
# ---------------------------------------------------------------------------
def path_type(p):
    """'drill' · 'helix' · 'circular' · 'contour' · 'line' · 'zigzag'."""
    if p.get('cycle') is not None or not p['moved_xy']:
        return 'drill'
    segs = [s for s in p['segments'] if s[0] in ('line', 'arc')]
    arcs = [s for s in segs if s[0] == 'arc' and s[3] is not None]
    for s in arcs:
        a, b = s[1], s[2]
        if _known(a['Z'], b['Z']) and abs(a['Z'] - b['Z']) > EPS:
            return 'helix'
    if any(_known(s[1]['X'], s[1]['Y'], s[2]['X'], s[2]['Y'])
           and abs(s[1]['X'] - s[2]['X']) < EPS and abs(s[1]['Y'] - s[2]['Y']) < EPS for s in arcs):
        return 'circular'
    if arcs:
        return 'contour'
    lines = []
    for s in segs:
        a, b = s[1], s[2]
        if not _known(a['X'], a['Y'], b['X'], b['Y']):
            continue
        dx, dy = b['X'] - a['X'], b['Y'] - a['Y']
        length = math.hypot(dx, dy)
        if length > EPS:
            lines.append((dx / length, dy / length, length))
    if not lines:
        return 'contour'
    longest = max(length for _, _, length in lines)
    long_lines = [(ux, uy) for ux, uy, length in lines if length >= 0.5 * longest]
    ux0, uy0 = long_lines[0]
    if all(abs(ux * uy0 - uy * ux0) < 0.02 for ux, uy in long_lines):        # 긴 선분이 모두 평행
        return 'line' if all(ux * ux0 + uy * uy0 > 0 for ux, uy in long_lines) else 'zigzag'
    return 'contour'


def main_direction(p):
    """패스에서 가장 길게 깎은 직선의 방향 (단위 벡터). 없으면 None."""
    best = None
    for s in p['segments']:
        if s[0] != 'line':
            continue
        a, b = s[1], s[2]
        if not _known(a['X'], a['Y'], b['X'], b['Y']):
            continue
        dx, dy = b['X'] - a['X'], b['Y'] - a['Y']
        length = math.hypot(dx, dy)
        if length > EPS and (best is None or length > best[2]):
            best = (dx / length, dy / length, length)
    return best[:2] if best else None


PATH_CLASS = {'line': 'linear', 'zigzag': 'linear', 'contour': 'contour', 'helix': 'helix',
              'circular': 'helix', 'drill': 'drill'}


def strategy(passes):
    """워킹스텝 하나의 가공 전략 (ISO 14649 two5D_milling_strategy 에 가까운 이름). 드릴링은 None."""
    types = [p['path_type'] for p in passes]
    if 'drill' in types:
        return None
    if 'helix' in types:
        return 'helical'
    if 'circular' in types:
        return 'circular'
    if 'contour' in types:
        return 'contour'
    if 'zigzag' in types:
        return 'bidirectional'
    dirs = [d for d in (main_direction(p) for p in passes) if d]
    if any(ux * dirs[0][0] + uy * dirs[0][1] < -0.5 for ux, uy in dirs[1:]):
        return 'bidirectional'
    return 'unidirectional'


def operation(feature_type, stage, passes):
    """ISO 14649-11 공정 이름. 밀링은 형상에 따라 이름이 갈려(plane_* / bottom_and_side_*) 형상 분류가 없으면
    (소재 크기를 모름) None — 황삭/정삭은 패스별 경로의 stage 에 남는다."""
    if passes and passes[0]['path_type'] == 'drill':
        return DRILL_OPERATIONS.get(passes[0].get('cycle'), 'drilling')
    if feature_type is None:
        return None
    head = 'plane' if feature_type == PLANAR_FACE else 'bottom_and_side'
    return f"{head}_{stage or ROUGH}_milling"


def stock_box(stock):
    """소재 정보 {'x', 'y', 'z'(mm), 'origin': 'corner'|'center'} -> (x0, x1, y0, y1, 바닥 z). 크기를 모르면 None.

    공작물 좌표 Z0 이 소재 윗면이다. corner 는 윗면의 X·Y 가장 작은 모서리가 원점(X0 Y0), center 는 윗면 가운데.
    """
    if not stock:
        return None
    try:
        sx, sy, sz = (float(stock.get(k)) for k in ('x', 'y', 'z'))
    except (TypeError, ValueError):
        return None
    if not all(v > 0 and v == v for v in (sx, sy, sz)):
        return None
    if str(stock.get('origin') or 'corner').lower() == 'center':
        return (-sx / 2, sx / 2, -sy / 2, sy / 2, -sz)
    return (0.0, sx, 0.0, sy, -sz)


# ---------------------------------------------------------------------------
# 소재 격자
# ---------------------------------------------------------------------------
class _Grid:
    def __init__(self, xmin, xmax, ymin, ymax, h):
        self.h = h
        self.x0, self.y0 = xmin, ymin
        self.nx = max(1, int(math.ceil((xmax - xmin) / h)))
        self.ny = max(1, int(math.ceil((ymax - ymin) / h)))
        self.top = np.zeros((self.ny, self.nx))
        self.inside = None          # 소재 안 칸 (소재 크기를 알 때)
        self.bottom = None          # 소재 바닥 높이

    def set_stock(self, box):
        """소재 밖에는 재료가 없다 (높이 -inf). 소재 안은 윗면 Z0 에서 시작한다."""
        x0, x1, y0, y1, bottom = box
        xs = self.centres(0, self.nx, 'x')
        ys = self.centres(0, self.ny, 'y')
        self.inside = (xs[None, :] >= x0) & (xs[None, :] <= x1) & (ys[:, None] >= y0) & (ys[:, None] <= y1)
        self.top = np.where(self.inside, 0.0, -np.inf)
        self.bottom = bottom

    def span(self, lo, hi, axis):
        origin, n = (self.x0, self.nx) if axis == 'x' else (self.y0, self.ny)
        i0 = max(0, int(math.floor((lo - origin) / self.h)))
        i1 = min(n, int(math.ceil((hi - origin) / self.h)) + 1)
        return i0, i1

    def centres(self, i0, i1, axis):
        origin = self.x0 if axis == 'x' else self.y0
        return origin + (np.arange(i0, i1) + 0.5) * self.h


def _sweep(grid, chords, r):
    """패스 공구 바닥이 지나간 칸의 높이. (iy0, ix0, 창 배열 — 지나지 않은 칸은 inf) 또는 None."""
    xs = [c[0] for c in chords] + [c[2] for c in chords]
    ys = [c[1] for c in chords] + [c[3] for c in chords]
    ix0, ix1 = grid.span(min(xs) - r, max(xs) + r, 'x')
    iy0, iy1 = grid.span(min(ys) - r, max(ys) + r, 'y')
    if ix0 >= ix1 or iy0 >= iy1:
        return None
    zl = np.full((iy1 - iy0, ix1 - ix0), np.inf)
    for xa, ya, xb, yb, z in chords:
        jx0, jx1 = grid.span(min(xa, xb) - r, max(xa, xb) + r, 'x')
        jy0, jy1 = grid.span(min(ya, yb) - r, max(ya, yb) + r, 'y')
        if jx0 >= jx1 or jy0 >= jy1:
            continue
        px = grid.centres(jx0, jx1, 'x')[None, :] - xa
        py = grid.centres(jy0, jy1, 'y')[:, None] - ya
        dx, dy = xb - xa, yb - ya
        length2 = dx * dx + dy * dy
        if length2 > 0:
            t = np.clip((px * dx + py * dy) / length2, 0.0, 1.0)
            ex, ey = px - t * dx, py - t * dy
        else:
            ex, ey = px, py
        inside = ex * ex + ey * ey <= r * r
        sub = zl[jy0 - iy0:jy1 - iy0, jx0 - ix0:jx1 - ix0]
        np.minimum(sub, np.where(inside, z, np.inf), out=sub)
    return iy0, ix0, zl


def _radial_width(grid, chords, r, iy0, ix0, cut):
    """경로에 수직으로 잰 깎인 폭의 중앙값 (mm). 공구 지름을 모르면 None."""
    if r <= 0:
        return None
    k = max(8, int(math.ceil(4 * r / grid.h)))
    offsets = (np.arange(k) + 0.5) * (2 * r / k) - r      # 공구 지름을 k 칸으로 나눈 가운데 점
    spacing = max(grid.h, r / 2)
    widths = []
    for xa, ya, xb, yb, _ in chords:
        dx, dy = xb - xa, yb - ya
        length = math.hypot(dx, dy)
        if length < grid.h:
            continue
        nxv, nyv = -dy / length, dx / length
        m = max(1, int(length // spacing))
        ts = (np.arange(m) + 0.5) / m
        qx = (xa + ts * dx)[:, None] + offsets[None, :] * nxv
        qy = (ya + ts * dy)[:, None] + offsets[None, :] * nyv
        cx = np.floor((qx - grid.x0) / grid.h).astype(np.int64) - ix0
        cy = np.floor((qy - grid.y0) / grid.h).astype(np.int64) - iy0
        ok = (cx >= 0) & (cx < cut.shape[1]) & (cy >= 0) & (cy < cut.shape[0])
        hit = np.zeros(ok.shape, dtype=bool)
        hit[ok] = cut[cy[ok], cx[ok]]
        w = hit.sum(axis=1) * (2 * r / k)
        widths.extend(w[w > 0].tolist())
    return float(np.median(widths)) if widths else 0.0


# ---------------------------------------------------------------------------
# 형상
# ---------------------------------------------------------------------------
def _round(value, digits):
    return None if value is None else round(float(value), digits) + 0.0


def _region_geometry(grid, labels, region):
    from scipy import ndimage

    iy, ix = np.nonzero(labels == region)
    y0, y1, x0, x1 = iy.min(), iy.max() + 1, ix.min(), ix.max() + 1
    mask = labels[y0:y1, x0:x1] == region
    top = grid.top[y0:y1, x0:x1]
    h = grid.h
    xs = grid.x0 + (ix + 0.5) * h
    ys = grid.y0 + (iy + 0.5) * h
    if len(xs) >= 2 and (np.ptp(xs) > 0 or np.ptp(ys) > 0):
        w, v = np.linalg.eigh(np.cov(np.vstack([xs, ys])))
        ux, uy = v[:, int(np.argmax(w))]
    else:
        ux, uy = 1.0, 0.0
    along = xs * ux + ys * uy
    across = -xs * uy + ys * ux
    length = float(np.ptp(along) + h)
    # 폭은 축에 수직으로 자른 단면 폭의 중앙값: 끝의 진입·후퇴 원호처럼 일부만 넓은 곳에 흔들리지 않는다
    bins = np.floor((along - along.min()) / h).astype(np.int64)
    order = np.argsort(bins, kind='stable')
    sorted_bins, sorted_across = bins[order], across[order]
    starts = np.flatnonzero(np.r_[True, sorted_bins[1:] != sorted_bins[:-1]])
    width = float(np.median(np.maximum.reduceat(sorted_across, starts)
                            - np.minimum.reduceat(sorted_across, starts) + h))
    floor = float(np.median(top[mask]))
    # 형상 둘레 바로 바깥 칸 중 바닥보다 높은 칸(벽 위)의 높이 = 형상의 윗면 (정면 밀링한 면 위의 슬롯이면 그 면)
    big = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=bool)
    big[1:-1, 1:-1] = mask
    ring = ndimage.binary_dilation(big) & ~big
    ry, rx = np.nonzero(ring)
    ry, rx = ry - 1 + y0, rx - 1 + x0
    inside = (ry >= 0) & (ry < grid.ny) & (rx >= 0) & (rx < grid.nx)
    walls = grid.top[ry[inside], rx[inside]]
    walls = walls[walls > floor + WALL_TOL]
    top_level = float(np.median(walls)) if walls.size else 0.0
    sides = _sides(grid, labels, region, iy, ix, along, across, ux, uy, floor) if grid.inside is not None else None
    return {
        'length': _round(length, 1), 'width': _round(width, 1),
        'depth': _round(top_level - floor, 3), 'floor_z': _round(floor, 3),
        'x': [_round(grid.x0 + x0 * h, 1), _round(grid.x0 + x1 * h, 1)],
        'y': [_round(grid.y0 + y0 * h, 1), _round(grid.y0 + y1 * h, 1)],
        'area': _round(len(xs) * h * h, 1),
        'axis_deg': _round(math.degrees(math.atan2(uy, ux)) % 180, 1),
        '_fill': len(xs) * h * h / (length * width),
        '_extent_width': float(np.ptp(across) + h),
        '_sides': sides,
    }


def _sides(grid, labels, region, iy, ix, along, across, ux, uy, floor):
    """형상 둘레의 네 옆이 벽인지 열렸는지 {'left', 'right', 'start', 'end'}: 'wall' / 'open'.

    덩어리 밖 이웃 칸이 소재 밖이면 열림, 바닥보다 높은 재료면 벽, 더 깊은 다른 형상이면 세지 않는다.
    한 옆에서 벽 칸이 열린 칸보다 많아야 벽이다 (다른 형상이 벽을 일부 끊어도 흔들리지 않게).
    """
    a0, a1, c0, c1 = along.min(), along.max(), across.min(), across.max()
    counts = {'left': [0, 0], 'right': [0, 0], 'start': [0, 0], 'end': [0, 0]}       # [벽, 열림]
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        ny, nx = iy + dy, ix + dx
        ok = (ny >= 0) & (ny < grid.ny) & (nx >= 0) & (nx < grid.nx)
        ny, nx, sa, sc = ny[ok], nx[ok], along[ok], across[ok]
        other = labels[ny, nx] != region
        ny, nx, sa, sc = ny[other], nx[other], sa[other], sc[other]
        top = grid.top[ny, nx]
        is_open = ~grid.inside[ny, nx]
        is_wall = ~is_open & (top > floor + WALL_TOL)
        keep = is_open | is_wall
        for a, c, wall in zip(sa[keep], sc[keep], is_wall[keep]):
            if min(c - c0, c1 - c) <= min(a - a0, a1 - a):
                side = 'left' if c - c0 < c1 - c else 'right'
            else:
                side = 'start' if a - a0 < a1 - a else 'end'
            counts[side][0 if wall else 1] += 1
    return {side: 'wall' if w > o else 'open' for side, (w, o) in counts.items()}


def _classify(geometry, passes):
    """둘레의 벽 · 열림으로 ISO 14649-10 형상을 정한다 (모듈 설명의 '형상 분류'). 소재 크기를 알 때만 부른다."""
    sides = geometry['_sides']
    walls = {side for side, kind in sides.items() if kind == 'wall'}
    long_pair = {'left', 'right'} <= walls
    end_pair = {'start', 'end'} <= walls
    elongated = geometry['length'] >= 2 * geometry['width']
    if not walls:
        return PLANAR_FACE
    if len(walls) == 1:
        return STEP
    if len(walls) == 2:
        return SLOT if (long_pair or end_pair) else OPEN_POCKET
    if len(walls) == 3:
        return SLOT if (long_pair and elongated) else OPEN_POCKET
    round_shape = (any(p['path_type'] in ('helix', 'circular') for p in passes)
                   and geometry['length'] <= 1.15 * geometry['_extent_width'])
    if round_shape:
        return ROUND_HOLE
    return SLOT if elongated else CLOSED_POCKET


def _finish_geometry(geometry, ftype):
    """분류에 맞춰 크기 칸을 정리한다 (슬롯은 양 끝 조건, 구멍은 지름)."""
    sides = geometry.pop('_sides')
    extent_width = geometry.pop('_extent_width')
    geometry.pop('_fill')
    geometry['walls'] = sides
    if ftype == SLOT:
        if {'start', 'end'} <= {s for s, k in sides.items() if k == 'wall'} and sides['left'] == 'open':
            # 벽이 양 끝에 있다: 통로는 가로 방향이다 -> 폭 · 길이를 바꿔 적는다
            geometry['width'], geometry['length'] = geometry['length'], _round(extent_width, 1)
            geometry['axis_deg'] = _round((geometry['axis_deg'] + 90) % 180, 1)
            geometry['ends'] = [sides['left'], sides['right']]
        else:
            geometry['ends'] = [sides['start'], sides['end']]
    if ftype == ROUND_HOLE:                               # 원은 단면 폭이 자리마다 달라 가장 긴 지름을 쓴다
        geometry['diameter'] = geometry.pop('length')
        for k in ('width', 'axis_deg'):
            geometry.pop(k)
    return geometry


def _basis(passes, specs):
    seen = []
    for p in passes:
        spec = specs.get(p['_tool']) or {}
        d = spec.get('diameter')
        text = (f"T{p['_tool']:02d} Ø{d:g} ({spec.get('source') or '공구 마스터'})" if d and p['_tool'] is not None
                else f"T{p['_tool']} 공구 지름 모름 — 경로 중심선 기준")
        if text not in seen:
            seen.append(text)
    return ', '.join(seen)


# ---------------------------------------------------------------------------
# 분석
# ---------------------------------------------------------------------------
def analyze(sections, specs, defining, stock=None):
    """패스마다 ap · ae · 제거 체적 · 모양 · 형상 번호 · 황삭/정삭을 붙이고, 형상 목록을 돌려준다.

    sections  _PassScanner 의 공구 구간 (패스에 segments 가 있다)
    specs     {공구 번호: {'diameter', 'kind', 'source'}}
    defining  스텝을 만드는 패스의 id 집합 (보조 패스는 소재만 깎고 형상·스텝에는 들지 않는다)
    stock     소재 {'x', 'y', 'z', 'origin'} (stock_box). 없으면 형상 분류 · 크기 · 체적 · ae 를 기록하지 않는다
    반환       {형상 번호: {'type', 'geometry'}}. 패스 dict 에 feature · stage · ap · ae · volume · path_type 을 적는다.
    """
    box = stock_box(stock)
    entries = []
    for section in sections:
        for p in section['passes']:
            p['_tool'] = section['tool']
            p['path_type'] = path_type(p)
            entries.append(p)

    used = {p['_tool'] for p in entries}
    known = [s['diameter'] for tool, s in specs.items() if tool in used and s.get('diameter')]
    h = min(_MAX_CELL, max(_MIN_CELL, min(known) / _CELL_PER_DIAMETER)) if known else 0.25
    xs, ys = [], []
    for p in entries:
        d = (specs.get(p['_tool']) or {}).get('diameter')
        p['_r'] = d / 2 if d else 0.0
        p['_r_eff'] = max(p['_r'], 0.71 * h)
        step = max(h, min(1.0, p['_r_eff'] / 2))
        chords = []
        for seg in p['segments']:
            points = segment_points(seg, step)
            if points is not None:
                chords += _chords(points)
        p['_chords'] = chords
        for c in chords:
            xs += [c[0] - p['_r_eff'], c[2] + p['_r_eff'], c[0] + p['_r_eff'], c[2] - p['_r_eff']]
            ys += [c[1] - p['_r_eff'], c[3] + p['_r_eff'], c[1] + p['_r_eff'], c[3] - p['_r_eff']]

    grid = None
    if xs:
        xmin, xmax, ymin, ymax = min(xs) - 2 * h, max(xs) + 2 * h, min(ys) - 2 * h, max(ys) + 2 * h
        area = (xmax - xmin) * (ymax - ymin)
        if area / (h * h) > MAX_CELLS:
            h = math.sqrt(area / MAX_CELLS)
        grid = _Grid(xmin, xmax, ymin, ymax, h)
        if box is not None:
            grid.set_stock(box)

    for p in entries:
        p.update(ap=None, ae=None, volume=None, _swept=None, _box=None, _cut=None, _cut_depth=None)
        in_material = p['min_z'] is not None and p['min_z'] <= EPS
        if grid is None or not p['_chords']:
            continue
        swept = _sweep(grid, p['_chords'], p['_r_eff'])
        if swept is None:
            continue
        iy0, ix0, zl = swept
        if grid.bottom is not None:
            zl = np.where(np.isfinite(zl), np.maximum(zl, grid.bottom), zl)     # 소재 바닥 아래는 재료가 없다
        win = grid.top[iy0:iy0 + zl.shape[0], ix0:ix0 + zl.shape[1]]
        passed = np.isfinite(zl)
        cut = passed & (zl < win - EPS)
        depth = np.where(cut, win - zl, 0.0)
        rows, cols = np.nonzero(passed)
        p['_swept'] = (rows + iy0) * grid.nx + (cols + ix0)
        p['_box'] = (iy0 + rows.min(), iy0 + rows.max(), ix0 + cols.min(), ix0 + cols.max()) if len(rows) else None
        rows, cols = np.nonzero(cut)
        p['_cut'] = (rows + iy0) * grid.nx + (cols + ix0)
        p['_cut_depth'] = depth[cut]
        p['volume'] = float(depth.sum() * grid.h * grid.h)
        if cut.any():
            p['ap'] = float(np.median(depth[cut]))
        elif in_material:
            p['ap'] = 0.0                                   # 이미 깎인 자리를 같은 깊이로 다시 지나감 (스프링 패스)
        if p['path_type'] != 'drill':
            p['ae'] = _radial_width(grid, p['_chords'], p['_r'], iy0, ix0, cut)
        if grid.inside is None or p['_r'] <= 0:
            p['ae'] = p['volume'] = None        # 소재 크기나 공구 지름을 몰라 제대로 잴 수 없다 (기록하지 않음)
        np.minimum(win, zl, out=win)

    labels = _label_regions(grid) if grid is not None else None
    features = _assign_features(entries, defining, labels, grid, specs, box)
    _assign_stages([p for p in entries if id(p) in defining])
    return features


def _label_regions(grid):
    """바닥 높이가 같은 깎인 칸 덩어리에 번호를 붙인다.

    같은 높이의 칸끼리는 그보다 깊게 깎인 칸을 거쳐서도 이어진다고 본다. 정면 밀링한 면을 나중에 슬롯이
    가로질러 판 경우, 면이 슬롯을 사이에 두고 여러 조각으로 갈려도 한 형상으로 남게 하려는 것이다.
    """
    from scipy import ndimage

    labels = np.zeros(grid.top.shape, dtype=np.int64)
    finite = np.isfinite(grid.top)                      # 소재 밖(-inf)은 형상이 아니다
    cut = finite & (grid.top < -EPS)
    if not cut.any():
        return labels
    levels = np.rint(np.where(finite, grid.top, 0.0) / WALL_TOL).astype(np.int64)
    count = 0
    for level in np.unique(levels[cut]):
        lab, n = ndimage.label(cut & (levels <= level))
        own = cut & (levels == level)
        labels[own] = lab[own] + count
        count += n
    return labels


def _region_of(p, flat_labels):
    if p['_cut'] is not None and len(p['_cut']):
        votes = np.bincount(flat_labels[p['_cut']], weights=p['_cut_depth'])
    elif p['_swept'] is not None and len(p['_swept']):
        votes = np.bincount(flat_labels[p['_swept']]).astype(float)
    else:
        return None
    votes[0] = 0.0
    return int(np.argmax(votes)) if votes.max() > 0 else None


def _assign_features(entries, defining, labels, grid, specs, box=None):
    flat = labels.ravel() if labels is not None else None
    number = {}          # 형상 키 -> 번호
    members = {}         # 번호 -> 패스들
    regions = {}         # 번호 -> 격자 덩어리 번호
    for p in entries:
        if id(p) not in defining:
            continue
        place = frozenset(p['footprint']) if p['footprint'] and None not in p['footprint'] else None
        if p['path_type'] == 'drill':
            key = ('holes', place) if place else ('pass', id(p))
        else:
            region = _region_of(p, flat) if flat is not None else None
            key = ('region', region) if region else (('path', place) if place else ('pass', id(p)))
        if key not in number:
            number[key] = len(number) + 1
            if key[0] == 'region':
                regions[number[key]] = key[1]
        p['feature'] = number[key]
        members.setdefault(number[key], []).append(p)

    features = {}
    for no, passes in members.items():
        basis = _basis(passes, specs)
        diameters_known = all((specs.get(p['_tool']) or {}).get('diameter') for p in passes)
        if passes[0]['path_type'] == 'drill':
            holes = sorted({(round(k[1], 3), round(k[2], 3)) for k in passes[0]['footprint'] if k})
            d = (specs.get(passes[0]['_tool']) or {}).get('diameter')
            geometry = {'holes': len(holes), 'diameter': _round(d, 3), 'depth': _round(passes[0]['ap'], 3),
                        'positions': [list(xy) for xy in holes[:20]], 'basis': basis}
            if box is not None and passes[0]['min_z'] is not None:       # 관통 여부는 소재 두께를 알 때만
                geometry['bottom'] = 'through' if passes[0]['min_z'] <= box[4] + EPS else 'blind'
            features[no] = {'type': ROUND_HOLE, 'geometry': geometry}
        elif no in regions and box is not None and diameters_known:
            geometry = _region_geometry(grid, labels, regions[no])
            ftype = _classify(geometry, passes)
            geometry = _finish_geometry(geometry, ftype)
            geometry['basis'] = basis
            features[no] = {'type': ftype, 'geometry': geometry}
        else:
            # 소재 크기나 공구 지름을 몰라 형상을 제대로 판단할 수 없다: 분류 · 크기를 기록하지 않는다
            features[no] = {'type': None, 'geometry': None}
    return features


def _overlap(a, b):
    """두 패스가 지나간 칸이 겹치는지 (격자가 없으면 같은 자리인지)."""
    if a['_swept'] is None or b['_swept'] is None:
        return bool(a['footprint']) and None not in a['footprint'] and a['footprint'] == b['footprint']
    (ay0, ay1, ax0, ax1), (by0, by1, bx0, bx1) = a['_box'], b['_box']
    if ay1 < by0 or by1 < ay0 or ax1 < bx0 or bx1 < ax0:
        return False
    return np.intersect1d(a['_swept'], b['_swept'], assume_unique=True).size > 0


def _assign_stages(defining):
    """황삭 / 정삭 (드릴링은 None). 규칙은 모듈 설명의 '정삭'."""
    for i, p in enumerate(defining):
        if p['path_type'] == 'drill':
            p['stage'] = None
            continue
        p['stage'] = ROUGH
        if p['ap'] is None or p['ap'] > FINISH_MAX_AP:
            continue
        same = [(j, q) for j, q in enumerate(defining)
                if j != i and q.get('feature') == p['feature'] and q['path_type'] != 'drill']
        reference = max((q['ap'] for j, q in same if j < i and q['ap'] is not None and _overlap(p, q)),
                        default=0.0)
        if reference <= 0 or p['ap'] >= FINISH_RATIO * reference:
            continue
        deeper_later = any(j > i and q['min_z'] is not None and p['min_z'] is not None
                           and q['min_z'] < p['min_z'] - EPS and _overlap(p, q) for j, q in same)
        if not deeper_later:
            p['stage'] = FINISH


# ---------------------------------------------------------------------------
# 표시
# ---------------------------------------------------------------------------
def _num(value):
    text = f"{float(value):.3f}".rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def feature_size_text(geometry):
    """형상 크기 한 줄: 'W20 × L101 × D2', 구멍은 'Ø16 × D4' · '3 × Ø6.8 × D5'. 모르면 '-'."""
    import json

    if isinstance(geometry, str):
        geometry = json.loads(geometry)
    if not isinstance(geometry, dict):
        return "-"
    depth = f" × D{_num(geometry['depth'])}" if geometry.get('depth') else ""
    if geometry.get('diameter'):
        holes = geometry.get('holes') or 1
        return (f"{holes} × " if holes > 1 else "") + f"Ø{_num(geometry['diameter'])}" + depth
    if geometry.get('width') and geometry.get('length'):
        return f"W{_num(geometry['width'])} × L{_num(geometry['length'])}" + depth
    return depth[3:] or "-"
