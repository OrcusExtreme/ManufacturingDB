"""TDMS ↔ NC/XML 시간축 정렬 모듈

TDMS는 가공 한 건만 담고 있지 않다. 장비 모니터링이 켜져 있던 구간 전체(예: 12분)가
그대로 기록되어 있고, 그 안에서 실제 가공은 1분도 되지 않는 경우가 흔하다.
게다가 파일명/속성의 프로그램명이 실제 가공한 NC 프로그램과 다를 수 있어
TDMS 자체만으로는 "어디부터 어디까지가 이번 가공인지"를 알 수 없다.

이 모듈은 그 구간을 찾아내고, 폴링 주기가 완전히 다른 두 그룹을 하나의 시간축에 올린다.

  * CNC 그룹 : 약 27Hz 폴링. 각 행에 그 순간 실행 중이던 NC 블록(CNC-CurrentBlock)이 들어있다.
  * DAQ 그룹 : 12.8kHz 샘플링. 행 수가 CNC의 600배 수준이고 블록 정보가 없다.

판별 근거 (우선순위)
  1. 업로드된 NC 프로그램 원본과 CNC-CurrentBlock 을 대조해, 프로그램이 순서대로
     진행된 구간(run)을 찾는다. 한 파일 안에 같은 프로그램이 여러 번(중단된 시도 포함)
     나타나므로 '실행된 블록 종류 수'로 점수를 매겨 실제 완주한 run 을 고른다.
  2. 고른 run 의 앞뒤에 붙은 대기 구간(프로그램만 띄워놓고 멈춰 있던 시간)은
     스핀들/이송 상태와 블록 전환 간격으로 잘라낸다.
  3. NC 원본이 없으면 스핀들 회전/이송 활동만으로 구간을 잡고,
     그것도 불가능하면 XML(StartTime/FinishTime) 힌트나 전체 구간을 쓴다.
  4. 고른 구간에 운전 상태(CNC-ProgramMode)를 겹쳐 실행 사이클(사이클 시작 ~ 프로그램 끝)을
     찾는다 (describe_cycle). 이것이 DB 의 가공 시작·종료다. TDMS 기록 시작이 아니다.

시각 기준: TDMS 파형 시각은 UTC 이고, DB 에 넣는 시각은 한국 시간(UTC+9)이다 (LOCAL_UTC_OFFSET).

DAQ 매핑은 비율 스케일이 아니라 파형 속성(wf_start_time + wf_start_offset + wf_increment)으로
절대 시각을 복원해 CNC 구간과 같은 시각 범위를 잘라낸다. 파형 속성이 없는 장비 데이터는
"두 그룹의 전체 기록 구간이 같다"는 가정으로 비율 스케일 변환(fallback)을 쓴다.

다만 두 그룹은 수집 경로가 달라(CNC 는 컨트롤러 폴링, DAQ 는 별도 계측 카드) 선언된 시작
시각이 같아도 실제로는 몇 초씩 어긋나 있는 경우가 있다. 그래서 같은 물리량을 보는 두 채널
(CNC 스핀들 부하 ↔ DAQ 스핀들 전류)의 상호상관으로 지연을 실측해 보정한 뒤 잘라낸다.
상관이 충분히 높지 않으면 보정하지 않고 진단값만 남긴다.

단독 실행 (DB 없이 검증 가능)
    python backend/tdms_alignment.py --tdms <file.tdms> --nc <file.nc> [--xml <file.xml>]
                                     [--out <dir>] [--prefix job_7] [--json]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. 손상 내성 TDMS 열기
# ---------------------------------------------------------------------------
# TDMS 세그먼트 lead-in: 태그(4) + ToC(4) + 버전(4) + 다음 세그먼트 오프셋(8) + 원시데이터 오프셋(8)
LEAD_IN_SIZE = 28
TDMS_TAG = b"TDSm"
TDMS_INDEX_TAG = b"TDSh"
_NO_NEXT_SEGMENT = 0xFFFFFFFFFFFFFFFF

# 기본 다운샘플 목표 (그래프 1개당 점 개수)
TARGET_DATAPOINTS = 10000


def find_valid_data_end(path: str) -> Tuple[int, int]:
    """세그먼트 lead-in 을 따라가며 정상적으로 읽을 수 있는 마지막 지점을 찾는다.

    수집 프로그램이 비정상 종료되면 TDMS 끝에 0으로 채워진 조각이 남는데,
    nptdms 는 그 지점에서 ValueError 로 죽는다(같은 이유로 *.tdms_index 도 깨진다).
    반환값은 (유효 끝 오프셋, 전체 파일 크기).
    """
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        return _scan_valid_end(f, size), size


def _scan_valid_end(f, size: int) -> int:
    """열린 파일(디스크 파일이든 메모리든)에서 정상적으로 읽을 수 있는 마지막 오프셋."""
    pos = 0
    while pos + LEAD_IN_SIZE <= size:
        f.seek(pos)
        lead = f.read(LEAD_IN_SIZE)
        if len(lead) < LEAD_IN_SIZE:
            break
        if lead[:4] not in (TDMS_TAG, TDMS_INDEX_TAG):
            return pos
        next_off = struct.unpack("<Q", lead[12:20])[0]
        if next_off == _NO_NEXT_SEGMENT:
            # 마지막 세그먼트가 미완료 상태 -> nptdms 가 경고와 함께 처리해 준다
            return size
        nxt = pos + LEAD_IN_SIZE + next_off
        if nxt > size:
            return size
        pos = nxt
    return pos if pos > 0 else size


class _TruncatedFile(io.RawIOBase):
    """지정한 길이까지만 존재하는 것처럼 보이게 하는 읽기 전용 래퍼.

    읽기 위치를 직접 들고 있는다. 예전에는 읽을 때마다 self._fh.tell() 을 불렀는데,
    nptdms 는 세그먼트마다 잘게 읽기 때문에 이 래퍼의 read 가 464MB 파일 하나에 대해
    400만 번 넘게 호출됐다(실측 cProfile). tell() 한 번의 비용은 작아도 400만 번이면
    전체 변환 시간의 4분의 1이 여기서 나갔다. 위치를 더하기만 하면 되는 값이라 들고 있는다.
    """

    def __init__(self, fh, limit: int):
        self._fh = fh
        self._limit = limit
        self._pos = fh.tell()

    def _remaining(self) -> int:
        return max(0, self._limit - self._pos)

    def read(self, n=-1):
        remain = self._remaining()
        if remain == 0:
            return b""
        if n is None or n < 0:
            n = remain
        chunk = self._fh.read(min(n, remain))
        self._pos += len(chunk)
        return chunk

    def readinto(self, buf):
        remain = self._remaining()
        if remain == 0:
            return 0
        view = memoryview(buf)
        if len(view) > remain:
            view = view[:remain]
        read = self._fh.readinto(view) or 0
        self._pos += read
        return read

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_END:
            self._pos = self._fh.seek(self._limit + offset, os.SEEK_SET)
        else:
            self._pos = self._fh.seek(offset, whence)
        return self._pos

    def tell(self):
        return self._pos

    def readable(self):
        return True

    def seekable(self):
        return True

    def close(self):
        try:
            self._fh.close()
        finally:
            super().close()


def _eager_read_is_affordable(file_size: int) -> bool:
    """파일 전체를 메모리에 올려도 되는지 여유 메모리를 보고 판단한다.

    실측(464MB 파일) 기준 메모리 사용량은 파일 크기의 약 1.3배였다.
    여유 메모리의 절반을 넘지 않을 때만 통째로 읽는다.
    """
    try:
        import psutil
        available = psutil.virtual_memory().available
    except Exception:
        return False
    return file_size * 1.3 < available * 0.5


@contextmanager
def open_tdms(path: str, metadata_only: bool = False, eager: Optional[bool] = None):
    """TDMS 를 여는 컨텍스트 매니저.

    파일 경로 대신 파일 객체를 넘겨 nptdms 가 손상된 *.tdms_index 를 집어들지 않게 하고,
    0-패딩된 꼬리는 잘라낸 상태로 보여준다.

    읽기 방식 (eager)
    -----------------
    TdmsFile.open() 은 채널을 '필요할 때' 읽는다. 채널 하나를 읽을 때마다 파일 전체의
    세그먼트를 훑어야 해서, 채널 수만큼 파일을 다시 스캔한다. CNC 그룹은 채널이 34개라
    464MB 파일을 34번 훑었고, HDD 에서 이것만 45~51초가 걸렸다 (전체 변환 시간의 79%).
    정작 데이터량이 100배인 DAQ 6채널은 0.75초였다. 병목은 데이터량이 아니라 '탐색 횟수'다.

    TdmsFile.read() 는 파일을 순차로 한 번만 훑어 모든 채널을 채운다. 같은 파일이
    51초 -> 15초로 줄었다. 대신 전부 메모리에 올라가므로(464MB 파일 -> 약 592MB)
    여유 메모리가 부족하면 예전처럼 지연 로딩으로 돌아간다.

    eager=None 이면 파일 크기와 여유 메모리를 보고 자동으로 고른다.
    """
    from nptdms import TdmsFile

    valid_end, size = find_valid_data_end(path)

    # 래퍼를 BufferedReader 로 한 번 더 감싼다.
    # nptdms 는 세그먼트마다 잘게 읽는데(이 파일은 세그먼트가 약 56만 개), 그 호출이
    # 전부 파이썬으로 짠 _TruncatedFile 로 들어오면 464MB 하나에 400만 번이 넘는다.
    # 1MB 버퍼를 끼우면 파이썬 쪽 호출이 수백 번으로 줄고 나머지는 C 버퍼가 받아낸다.
    # (실측: 15.0초 -> 9.2초)
    fh = io.BufferedReader(
        _TruncatedFile(open(path, "rb", buffering=0), valid_end),
        buffer_size=1 << 20,
    )
    try:
        if metadata_only:
            tdms = TdmsFile.read_metadata(fh)
        else:
            use_eager = _eager_read_is_affordable(valid_end) if eager is None else eager
            tdms = TdmsFile.read(fh) if use_eager else TdmsFile.open(fh)
            tdms.eager_loaded = bool(use_eager)   # 진단용
        tdms.truncated_bytes = size - valid_end  # 진단용 부가 정보
        yield tdms
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# 2. NC 프로그램 정규화
# ---------------------------------------------------------------------------
_NC_COMMENT_RE = re.compile(r"\(.*?\)")
_NC_WS_RE = re.compile(r"\s+")
# 프로그램 헤더/끝 표식은 가공 동작이 아니므로 진행도 판정에서 제외한다
_NC_MARKER_RE = re.compile(r"^(%|<.*>|O\d+%?|:\d+)$")


def normalize_block(text) -> str:
    """CNC-CurrentBlock 과 NC 원본 한 줄을 같은 형태로 맞춘다."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    s = _NC_COMMENT_RE.sub("", str(text))
    s = _NC_WS_RE.sub("", s)
    return s.strip().rstrip(";").upper()


@dataclass
class NcProgram:
    """NC 프로그램을 블록 순서로 정규화해 담은 참조표."""

    name: str
    blocks: List[str]                       # 정규화된 블록 (원본 순서)
    positions: Dict[str, List[int]] = field(default_factory=dict)
    marker_flags: List[bool] = field(default_factory=list)

    @property
    def motion_blocks(self) -> int:
        """헤더/끝 표식을 뺀 실제 동작 블록 수."""
        return sum(1 for m in self.marker_flags if not m)

    def position_of(self, block: str) -> Optional[int]:
        hit = self.positions.get(block)
        return hit[0] if hit else None

    def is_marker(self, pos: int) -> bool:
        return bool(self.marker_flags[pos]) if 0 <= pos < len(self.marker_flags) else False


def parse_nc_blocks(source, name: Optional[str] = None) -> NcProgram:
    """NC 텍스트(또는 bytes)를 NcProgram 으로 변환."""
    if isinstance(source, bytes):
        source = source.decode("utf-8", errors="replace")
    blocks: List[str] = []
    for raw in str(source).splitlines():
        nb = normalize_block(raw)
        if nb:
            blocks.append(nb)

    positions: Dict[str, List[int]] = {}
    for i, b in enumerate(blocks):
        positions.setdefault(b, []).append(i)
    markers = [bool(_NC_MARKER_RE.match(b)) for b in blocks]
    return NcProgram(name=name or "", blocks=blocks, positions=positions, marker_flags=markers)


def load_nc_program(path: str) -> NcProgram:
    with open(path, "rb") as f:
        data = f.read()
    return parse_nc_blocks(data, name=os.path.basename(path))


# ---------------------------------------------------------------------------
# 3. 가공 구간 판별
# ---------------------------------------------------------------------------
CNC_TIME_CANDIDATES = ("Time Channel CNC", "Timestamp", "time", "Time")
RPM_CANDIDATES = ("CNC-Z-SpindleSpeed", "CNC-W-SpindleSpeed", "CNC-SpindleSpeed")
FEED_CANDIDATES = ("CNC-ActualFeedRate", "CNC-CommandFeedRate")
BLOCK_CANDIDATES = ("CNC-CurrentBlock",)
PROGNAME_CANDIDATES = ("CNC-ProgramName",)
PROGRAM_MODE_CANDIDATES = ("CNC-ProgramMode",)
GMODAL_CANDIDATES = ("CNC-GModal",)

# ---- 시각 기준 ----
# TDMS 파형 시각(wf_start_time · Time Channel CNC)은 UTC 다. 실측 두 건 모두 파일명·XML 의 한국
# 시각보다 정확히 9시간 이르다 (09:55:22 UTC = 18:55:22 KST). 한국은 서머타임이 없어 언제나 +9시간이다.
# 예전에는 XML 시각과 맞대어 보며 차이를 '찾았는데', XML 이 없으면 차이를 0 으로 두어 종료시각이
# 9시간 이르게 저장됐다 (19:06:52 -> 10:06:52). 그래서 고정값으로 바꾼다. DB 에 넣는 시각은 모두 한국 시간이다.
LOCAL_UTC_OFFSET_HOURS = 9.0
LOCAL_UTC_OFFSET = timedelta(hours=LOCAL_UTC_OFFSET_HOURS)


def tdms_to_local(ts) -> Optional[datetime]:
    """TDMS(UTC) 시각 -> 한국 시간 (시간대 없는 datetime). 모르면 None."""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return (t + LOCAL_UTC_OFFSET).to_pydatetime()


def local_to_tdms(ts) -> Optional[datetime]:
    """한국 시간(DB · XML 의 시각) -> TDMS(UTC) 기준. 시간대가 붙은 값은 그 시간대를 따른다."""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    if t.tzinfo is not None:
        return t.tz_convert("UTC").tz_localize(None).to_pydatetime()
    return (t - LOCAL_UTC_OFFSET).to_pydatetime()


# 장비 수집기가 붙이는 이름: {장비}__{프로그램}__{라벨}__{YYMMDDHHMMSS}.tdms
#   예) DNM200__O1110.NC__PROBE START__261111185522.tdms
# 이름의 프로그램과 시각은 '기록을 시작할 때' 의 값이다. 가공 프로그램·가공 시작과 다를 수 있다
# (DB_Test/1: 이름은 O2202.NC 16:42:15, 실제 가공은 O0911.nc 16:46:05).
_TDMS_NAME_RE = re.compile(
    r"^(?P<machine>[^_]+)__(?P<program>.+?)__(?P<label>.*?)__(?P<stamp>\d{12})\.tdms$", re.IGNORECASE)
_TDMS_STAMP_RE = re.compile(r"__(\d{12})\.tdms$", re.IGNORECASE)


def parse_tdms_filename(path) -> dict:
    """{'machine', 'program', 'label', 'recorded_at'(한국 시간)}. 형식이 다르면 아는 것만 채운다."""
    name = os.path.basename(str(path or ""))
    out = {"machine": None, "program": None, "label": None, "recorded_at": None}
    m = _TDMS_NAME_RE.match(name)
    if m:
        out.update(machine=m.group("machine"), program=m.group("program"), label=m.group("label"))
        stamp = m.group("stamp")
    else:
        s = _TDMS_STAMP_RE.search(name)
        stamp = s.group(1) if s else None
    if stamp:
        try:
            out["recorded_at"] = datetime.strptime("20" + stamp, "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return out


def row_seconds(frame: pd.DataFrame) -> pd.Series:
    """행마다 바로 앞 행과의 간격(초). 폴링이 10초 넘게 끊긴 곳은 시간으로 치지 않는다."""
    if frame.empty or "timestamp" not in frame.columns:
        return pd.Series(0.0, index=frame.index)
    dt = frame["timestamp"].diff().dt.total_seconds().fillna(0.0)
    return dt.where((dt > 0) & (dt <= 10.0), 0.0)


# 절삭 이동으로 보는 G 코드 (01 그룹): 직선·원호 절삭과 드릴링 계열 고정 사이클.
CUTTING_G_CODES = frozenset({1, 2, 3, 73, 74, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89})


def cutting_mask(frame: pd.DataFrame) -> pd.Series:
    """행마다 '절삭 중' 인지: 절삭 G 코드(G01~G03 · 고정 사이클) + 실제 이송 > 0 + 주축 회전.

    예전 기준(회전수 > 100 이고 이송 > 0)은 주축이 도는 채로 급속이송(G00)하는 구간까지 절삭으로
    세어, O1110 에서 절삭시간 650.7초 · 절삭이동 9961mm 가 나왔다 (XML 598.2초 · 4327.5mm).
    G 모달로 가르면 598.0초 · 4334.3mm 로 맞는다. 주축 조건은 프로브 측정(주축 정지, G31 이송)을 뺀다.
    """
    if frame.empty:
        return pd.Series(False, index=frame.index)
    feed_col = _pick_column(frame, FEED_CANDIDATES)
    rpm_col = _pick_column(frame, RPM_CANDIDATES)
    gcol = _pick_column(frame, GMODAL_CANDIDATES)
    feeding = (pd.to_numeric(frame[feed_col], errors="coerce").fillna(0) > 0) if feed_col \
        else pd.Series(False, index=frame.index)
    spinning = (pd.to_numeric(frame[rpm_col], errors="coerce").fillna(0) > 0) if rpm_col \
        else pd.Series(True, index=frame.index)
    if gcol:
        codes = pd.to_numeric(frame[gcol].astype(str).str.upper().str.extract(r"G0*(\d+)")[0],
                              errors="coerce")
        cutting_g = codes.isin(CUTTING_G_CODES)
    else:       # G 모달 채널이 없는 장비: 이송·주축만 본다
        cutting_g = pd.Series(True, index=frame.index)
    return cutting_g & feeding & spinning


POSITION_COLUMNS = ("CNC-X-Position", "CNC-Y-Position", "CNC-Z-Position")

# 한 기록 간격 사이의 위치 변화가 이 속도(mm/s)를 넘으면 실제 이동이 아니라 위치 '표시' 가 바뀐 것으로 본다.
# 위치 채널은 공구 길이 보정이 들어간 좌표라, G43 H_(보정 켬)·G49(끔) 블록에서 공구가 움직이지 않아도 값이
# 한 간격 만에 공구 길이만큼 바뀐다. 실측 2건: 점프 7번 125.8~191.1mm = 3,301~5,135 mm/s (모두 Z 만, X·Y 0),
# 실제 이동은 가장 빨라야 380 mm/s (급속이송). 1,500 mm/s(90 m/min)는 실제 이동의 4배, 가장 작은 점프의 1/2.2 이다.
DISPLAY_JUMP_SPEED = 1500.0


def _interval_seconds(values: pd.Series) -> pd.Series:
    """시각 열(datetime 또는 초 단위 숫자) -> 바로 앞 행과의 간격(초)."""
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce").diff()
    return pd.to_datetime(values, errors="coerce").diff().dt.total_seconds()


def travel_steps(frame: pd.DataFrame, time_column: str = "timestamp") -> Optional[Tuple[pd.Series, pd.Series]]:
    """(행마다 바로 앞 행에서 움직인 X·Y·Z 거리 mm, 그 변화가 위치 표시 점프인지). 위치 채널이 없으면 None.

    총 이동거리는 step[~jump] 의 합이다. 점프: 이동량 > DISPLAY_JUMP_SPEED x 기록 간격.
      * 간격은 기록 주기(간격의 중앙값)보다 짧게 치지 않는다 — 시각이 겹치거나 흔들려도 실제 이동을 점프로 보지 않게.
      * 기록이 끊겼던 곳(간격이 긴 곳)의 큰 변화는 그동안 실제로 움직였을 수 있어 점프로 보지 않는다.
      * 점프가 낀 한 간격의 실제 이동(실측 0~2.5mm)도 함께 빠진다.
      * 시각 열이 없으면 점프를 가리지 않는다 (모두 False).
    """
    if frame is None or not set(POSITION_COLUMNS).issubset(frame.columns):
        return None
    delta = frame[list(POSITION_COLUMNS)].apply(pd.to_numeric, errors="coerce").diff().fillna(0.0)
    step = (delta ** 2).sum(axis=1).pow(0.5)
    jump = pd.Series(False, index=frame.index)
    if time_column in frame.columns and len(frame) > 1:
        dt = _interval_seconds(frame[time_column])
        positive = dt[dt > 0]
        if not positive.empty:
            jump = step > DISPLAY_JUMP_SPEED * dt.clip(lower=float(positive.median()))
    return step, jump


def program_key(name) -> str:
    """프로그램 이름 비교용: 확장자·대소문자·공백 차이를 없앤다 ('O1110.NC' == 'o1110')."""
    return normalize_block(os.path.splitext(str(name or ""))[0])


def same_program(a, b) -> Optional[bool]:
    """두 프로그램 이름(NC 첫머리 · TDMS CNC-ProgramName)이 같은 프로그램인지. 한쪽이라도 모르면 None."""
    ka, kb = program_key(a), program_key(b)
    if not ka or not kb:
        return None
    return ka == kb


# 가공 프로그램을 고르는 데 필요한 CNC 채널 (machining_program: 프로그램명 + 시각 + 절삭 판정)
PROGRAM_CHECK_COLUMNS = (*CNC_TIME_CANDIDATES, *PROGNAME_CANDIDATES, *GMODAL_CANDIDATES,
                         *FEED_CANDIDATES, *RPM_CANDIDATES)


def read_tdms_programs(data) -> Tuple[Optional[str], List[str]]:
    """TDMS 내용(바이트열)에서 (가공 프로그램, 기록에 나온 프로그램 이름들) 을 읽는다.

    업로드 화면에서 올리려는 NC 가 TDMS 에서 실제로 깎은 프로그램과 같은지 미리 보려고 쓴다.
    파일로 저장하기 전이라 메모리에서 읽고, 34개 CNC 채널 중 가공 프로그램을 고르는 데 필요한
    몇 개만 지연 로딩한다 (실측 480MB 파일 약 8초 — 대부분 세그먼트 목록을 읽는 시간).
    끝이 0 으로 채워진 파일(수집 프로그램 비정상 종료)은 그 앞까지만 읽는다 (open_tdms 와 같은 처리).
    """
    from nptdms import TdmsFile

    raw = io.BytesIO(data)          # bytes 는 복사하지 않고 그대로 공유한다
    size = len(data)
    end = _scan_valid_end(raw, size)
    if end == 0:
        raise ValueError("TDMS 형식이 아닙니다 (파일 머리에 TDMS 표식이 없습니다)")
    raw.seek(0)
    fh = io.BufferedReader(_TruncatedFile(raw, end), buffer_size=1 << 20)
    try:
        tdms = TdmsFile.open(fh)
        frame = read_cnc_frame(tdms, columns=PROGRAM_CHECK_COLUMNS)
    finally:
        fh.close()
    if frame.empty:
        return None, []
    pcol = _pick_column(frame, PROGNAME_CANDIDATES)
    seen = []
    if pcol is not None:
        names = frame[pcol].astype(str).str.strip()
        seen = [n for n in dict.fromkeys(names) if n and n.lower() != "nan"]
    return machining_program(frame), seen


def machining_program(frame: pd.DataFrame, row_start: int = 0, row_end: Optional[int] = None) -> Optional[str]:
    """CNC-ProgramName 중 [row_start, row_end) 에서 절삭이 가장 오래 일어난 프로그램 이름.

    한 TDMS 에 여러 프로그램이 섞인다. 기록을 시작할 때 띄워 둔 프로그램(DB_Test/1 의 O2202 프로브),
    공구교환 매크로(O9009) 등이다. 파일명·채널 속성의 프로그램명은 기록 시작 때 값이라 가공한
    프로그램과 다를 수 있으므로, 실제로 깎은 시간으로 고른다. 절삭이 없으면 가장 많이 나온 이름.
    """
    pcol = _pick_column(frame, PROGNAME_CANDIDATES)
    if pcol is None or frame.empty:
        return None
    sub = frame.iloc[row_start:row_end]
    names = sub[pcol].astype(str).str.strip()
    valid = (names != "") & (names.str.lower() != "nan")
    if not valid.any():
        return None
    secs = row_seconds(sub)[valid & cutting_mask(sub)]
    if secs.sum() > 0:
        return str(secs.groupby(names[secs.index]).sum().idxmax())
    return str(names[valid].value_counts().idxmax())


@dataclass
class MachiningWindow:
    """판별된 실가공 구간."""

    start: Optional[datetime]
    end: Optional[datetime]
    row_start: int                  # CNC 행 인덱스 (포함)
    row_end: int                    # CNC 행 인덱스 (제외)
    method: str                     # nc_block / activity / hint / full
    total_rows: int = 0
    matched_blocks: int = 0
    program_blocks: int = 0
    hint_start: Optional[datetime] = None       # TDMS(UTC) 기준으로 바꾼 힌트
    hint_end: Optional[datetime] = None
    # TDMS 시각 -> 한국 시간 차이(시). start/end 가 TDMS(UTC) 값이면 LOCAL_UTC_OFFSET_HOURS(9) 이고,
    # TDMS 를 못 읽어 힌트(이미 한국 시간)를 그대로 start/end 로 쓴 경우에만 0 이다.
    hint_shift_hours: float = 0.0
    # 판별에 참조한 NC 프로그램 (없으면 None). 재처리 필요 여부 판단에 쓴다.
    nc_reference: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    # NC 대조에서, 실행이 확인된 가장 뒤 블록 이후 남은 동작 블록 수 (0 = 마지막 블록까지 확인)
    end_gap_blocks: Optional[int] = None
    # ---- 실행 사이클 (describe_cycle 가 채운다) ----
    # 운전 상태(CNC-ProgramMode)가 실행(3)·일시정지(2)로 이어진 구간 중 가공 구간과 가장 많이 겹치는 것.
    # 사이클 시작 버튼을 누른 순간부터 프로그램이 끝날 때까지다.
    program_name: Optional[str] = None
    cycle_start: Optional[datetime] = None
    cycle_end: Optional[datetime] = None
    cycle_row_start: Optional[int] = None
    cycle_row_end: Optional[int] = None
    first_cut: Optional[datetime] = None
    # 프로그램 끝(M02/M30 또는 마지막 동작 블록)까지 실행했는지. 판단할 근거가 없으면 None
    program_end_reached: Optional[bool] = None
    # 완료 여부를 무엇으로 판단했는지 (완료 판정 기준을 실측 Job 으로 다듬을 때 본다 — compare_xml_tdms)
    program_end_basis: Optional[str] = None
    # 참조한 NC 파일 첫머리의 프로그램 이름 (<O1110.NC> -> O1110.NC). program_name(TDMS 에서 실제로 깎은
    # 프로그램)과 다르면 엉뚱한 NC 를 올렸을 수 있다 — Job 화면이 경고한다.
    nc_program: Optional[str] = None
    # 총 이동거리에서 뺀 위치 표시 점프(공구 길이 보정 G43/G49 등, travel_steps): 횟수 · 합(mm).
    # 가공 요약을 계산할 때(tdms_visualizer._apply_window_to_job) 채운다. 위치 채널이 없으면 None.
    position_jumps: Optional[int] = None
    position_jump_mm: Optional[float] = None

    @property
    def program_mismatch(self) -> bool:
        """NC 파일의 프로그램과 TDMS 에서 깎은 프로그램이 다른지 (둘 다 알 때만)."""
        return same_program(self.nc_program, self.program_name) is False

    @property
    def rows(self) -> int:
        return max(0, self.row_end - self.row_start)

    def to_hint_timebase(self, ts):
        """TDMS 기준 시각을 한국 시간(작업자가 보는 시각, DB 기준)으로 바꾼다."""
        if ts is None:
            return None
        return (pd.Timestamp(ts) + pd.to_timedelta(self.hint_shift_hours, unit="h")).to_pydatetime()

    @property
    def local_start(self):
        return self.to_hint_timebase(self.start)

    @property
    def local_end(self):
        return self.to_hint_timebase(self.end)

    @property
    def local_cycle_start(self):
        return self.to_hint_timebase(self.cycle_start)

    @property
    def local_cycle_end(self):
        return self.to_hint_timebase(self.cycle_end)

    @property
    def local_first_cut(self):
        return self.to_hint_timebase(self.first_cut)

    @property
    def machining_start(self):
        """가공 시작 (한국 시간): 실행 사이클 시작, 없으면 가공 구간 시작."""
        return self.local_cycle_start or self.local_start

    @property
    def machining_end(self):
        """가공 종료 (한국 시간): 실행 사이클 끝, 없으면 가공 구간 끝."""
        return self.local_cycle_end or self.local_end

    @property
    def duration_seconds(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return (pd.Timestamp(self.end) - pd.Timestamp(self.start)).total_seconds()

    @property
    def nc_coverage(self) -> float:
        return (self.matched_blocks / self.program_blocks) if self.program_blocks else 0.0

    def to_dict(self) -> dict:
        def iso(value):
            return value.isoformat() if value is not None else None

        d = asdict(self)
        # DB 의 JSON 칸에 그대로 들어가므로 datetime 은 모두 글자로 바꾼다.
        for key in ("start", "end", "hint_start", "hint_end", "cycle_start", "cycle_end", "first_cut"):
            d[key] = iso(getattr(self, key))
        d["rows"] = self.rows
        d["duration_seconds"] = round(self.duration_seconds, 3)
        d["nc_coverage"] = round(self.nc_coverage, 4)
        d["local_start"] = iso(self.local_start)
        d["local_end"] = iso(self.local_end)
        d["local_cycle_start"] = iso(self.local_cycle_start)
        d["local_cycle_end"] = iso(self.local_cycle_end)
        d["local_first_cut"] = iso(self.local_first_cut)
        d["program_mismatch"] = self.program_mismatch
        return d


def align_hint_timebase(rec_start, rec_end, hint_start, hint_end, max_hours: int = 14):
    """XML 가공시간이 TDMS 기록구간과 겹치지 않을 때 정수 시간 단위 시차를 찾아 맞춘다.

    XML 은 +09:00 같은 로컬 오프셋이 붙은 값이고 TDMS 파형 시각은 UTC 라 그대로 비교하면
    9시간이 어긋난다. (shift 된 시작, shift 된 끝, 적용한 시차) 를 돌려준다.
    """
    if hint_start is None:
        return None, None, 0.0
    hs, he = pd.Timestamp(hint_start), pd.Timestamp(hint_end if hint_end is not None else hint_start)
    rs, re_ = pd.Timestamp(rec_start), pd.Timestamp(rec_end)

    def overlap(a, b):
        return (min(b, re_) - max(a, rs)).total_seconds()

    if overlap(hs, he) > 0:
        return hs.to_pydatetime(), (he.to_pydatetime() if hint_end is not None else None), 0.0

    best = None
    for hours in range(-max_hours, max_hours + 1):
        if hours == 0:
            continue
        d = pd.to_timedelta(hours, unit="h")
        ov = overlap(hs - d, he - d)
        if best is None or ov > best[0]:
            best = (ov, hours)
    if best and best[0] > 0:
        d = pd.to_timedelta(best[1], unit="h")
        return ((hs - d).to_pydatetime(),
                ((he - d).to_pydatetime() if hint_end is not None else None),
                float(best[1]))
    return hint_start, hint_end, 0.0


def _pick_column(frame: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for c in candidates:
        if c in frame.columns:
            return c
    return None


def read_cnc_frame(tdms, columns: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """CNC 그룹을 DataFrame 으로 읽고 timestamp 열을 정리한다."""
    if "CNC" not in tdms:
        return pd.DataFrame()
    group = tdms["CNC"]
    names = [ch.name for ch in group.channels()]
    if columns is not None:
        names = [n for n in names if n in set(columns)]
    data = {}
    for name in names:
        try:
            data[name] = group[name][:]
        except Exception:
            continue
    frame = pd.DataFrame(data)
    if frame.empty:
        return frame

    tcol = _pick_column(frame, CNC_TIME_CANDIDATES)
    if tcol is not None:
        ts = pd.to_datetime(frame[tcol], errors="coerce")
        # 폴링 지터로 한두 행이 앞뒤가 바뀌는 경우가 있어 단조 증가로 보정한다
        frame["timestamp"] = ts.cummax().ffill()
    return frame


def map_block_positions(norm_blocks: Sequence[str], nc: NcProgram) -> np.ndarray:
    """각 CNC 행을 NC 프로그램의 몇 번째 블록인지로 바꾼다 (일치 없으면 -1).

    같은 블록 텍스트가 프로그램에 여러 번 나오는 경우(예: G1Y-15 가 2회, G91G28Z0 가 2회)
    항상 첫 위치로 매핑하면 진행도가 뒤로 튀어 한 번의 실행이 여러 개로 쪼개진다.
    그래서 지금까지 진행한 위치 이후의 가장 가까운 후보를 고르고,
    뒤쪽 후보가 하나도 없을 때만 프로그램이 처음부터 다시 돌기 시작한 것으로 본다.
    """
    pos = np.full(len(norm_blocks), -1, dtype=np.int64)
    cursor = 0
    for i, block in enumerate(norm_blocks):
        if not block:
            continue
        cand = nc.positions.get(block)
        if not cand:
            continue
        nxt = next((c for c in cand if c >= cursor), None)
        pos[i] = cand[0] if nxt is None else nxt   # 후보가 없으면 재실행으로 간주
        cursor = int(pos[i])
    return pos


def _candidate_runs(pos: np.ndarray, ts: pd.Series, restart_tolerance: int) -> List[Tuple[int, int]]:
    """NC 위치가 순서대로 진행되는 구간(run)들의 (시작행, 끝행+1) 목록."""
    runs: List[Tuple[int, int]] = []
    matched = np.flatnonzero(pos >= 0)
    if matched.size == 0:
        return runs

    run_start = matched[0]
    run_max = pos[matched[0]]
    prev = matched[0]
    for i in matched[1:]:
        p = pos[i]
        # 프로그램이 처음으로 되돌아가면 새 시도로 본다 (반복 루프는 tolerance 로 허용)
        if p < run_max - restart_tolerance:
            runs.append((run_start, prev + 1))
            run_start, run_max = i, p
        else:
            run_max = max(run_max, p)
        prev = i
    runs.append((run_start, prev + 1))
    return runs


def _score_run(pos: np.ndarray, nc: NcProgram, a: int, b: int) -> Tuple[int, bool]:
    """run 의 (실행된 동작 블록 종류 수, 프로그램 끝까지 도달했는지)."""
    seen = {int(p) for p in pos[a:b] if p >= 0 and not nc.is_marker(int(p))}
    if not seen:
        return 0, False
    last_motion = max((i for i in range(len(nc.blocks)) if not nc.is_marker(i)), default=0)
    return len(seen), (max(seen) >= last_motion)


def _active_mask(frame: pd.DataFrame, pos: np.ndarray, idle_gap: float,
                 rpm_min: float, feed_min: float) -> np.ndarray:
    """행별 '장비가 실제로 일하고 있었는지' 판정.

    스핀들이 돌거나 이송이 걸려 있으면 당연히 작업 중이고, 공구교환/급속이송처럼
    둘 다 0인 구간도 NC 블록이 방금 넘어갔다면 작업 중으로 본다.
    """
    n = len(frame)
    active = np.zeros(n, dtype=bool)

    rpm_col = _pick_column(frame, RPM_CANDIDATES)
    feed_col = _pick_column(frame, FEED_CANDIDATES)
    if rpm_col:
        active |= pd.to_numeric(frame[rpm_col], errors="coerce").fillna(0).abs().to_numpy() > rpm_min
    if feed_col:
        active |= pd.to_numeric(frame[feed_col], errors="coerce").fillna(0).abs().to_numpy() > feed_min

    if "timestamp" in frame.columns and pos is not None:
        t = frame["timestamp"].to_numpy(dtype="datetime64[ns]").astype("int64") / 1e9
        changed = np.zeros(n, dtype=bool)
        prev = -2
        for i in range(n):
            p = pos[i]
            if p >= 0 and p != prev:
                changed[i] = True
                prev = p
        change_times = t[changed]
        if change_times.size:
            # 가장 최근 블록 전환 이후 idle_gap 이내면 작업 중
            idx = np.searchsorted(change_times, t, side="right") - 1
            recent = np.where(idx >= 0, t - change_times[np.clip(idx, 0, None)], np.inf)
            active |= recent <= idle_gap
    return active


def _largest_active_span(active: np.ndarray, ts: pd.Series, merge_gap: float,
                         a: int, b: int) -> Tuple[int, int]:
    """[a, b) 안에서 가장 긴 연속 활동 구간. 짧은 휴지는 이어 붙인다."""
    sub = active[a:b]
    if not sub.any():
        return a, b
    t = ts.iloc[a:b].to_numpy(dtype="datetime64[ns]").astype("int64") / 1e9
    idx = np.flatnonzero(sub)

    spans: List[Tuple[int, int]] = []
    s = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if t[i] - t[prev] > merge_gap:
            spans.append((s, prev + 1))
            s = i
        prev = i
    spans.append((s, prev + 1))

    best = max(spans, key=lambda sp: t[sp[1] - 1] - t[sp[0]])
    return a + int(best[0]), a + int(best[1])


def detect_machining_window(
    frame: pd.DataFrame,
    nc: Optional[NcProgram] = None,
    hint_start: Optional[datetime] = None,
    hint_end: Optional[datetime] = None,
    *,
    idle_gap: float = 15.0,
    merge_gap: float = 5.0,
    rpm_min: float = 1.0,
    feed_min: float = 0.5,
    restart_tolerance: int = 2,
    min_coverage: float = 0.25,
) -> MachiningWindow:
    """CNC 시계열에서 실제 가공 구간을 판별한다.

    frame           : read_cnc_frame() 결과 (timestamp 열 필요, TDMS 시각 = UTC)
    nc              : 업로드된 NC 프로그램 (없으면 활동 기반으로 판별)
    hint_start/end  : 한국 시간으로 적힌 시작/종료 (XML 또는 DB 의 Job 값). 있으면 후보 run 선택과 검증에 쓴다.

    돌려주는 구간의 start/end 는 TDMS(UTC) 시각이고, local_* 는 언제나 한국 시간(+9시간)이다.
    """
    n = len(frame)
    notes: List[str] = []
    hint_start = pd.Timestamp(hint_start).to_pydatetime() if hint_start is not None else None
    hint_end = pd.Timestamp(hint_end).to_pydatetime() if hint_end is not None else None

    nc_ref = ("%s(%d블록)" % (nc.name or "NC", nc.motion_blocks)) if (nc and nc.blocks) else None

    if n == 0 or "timestamp" not in frame.columns:
        # 힌트(이미 한국 시간)를 그대로 구간으로 쓰므로 시차를 더하지 않는다 (hint_shift_hours=0).
        return MachiningWindow(start=hint_start, end=hint_end, row_start=0, row_end=n,
                               method="full", total_rows=n, hint_start=hint_start,
                               hint_end=hint_end, nc_reference=nc_ref, hint_shift_hours=0.0,
                               notes=["CNC 시계열이 없어 구간 판별을 생략했습니다."])

    ts = frame["timestamp"]
    rec_start, rec_end = ts.iloc[0].to_pydatetime(), ts.iloc[-1].to_pydatetime()

    # 힌트는 한국 시간이고 TDMS 파형 시각은 UTC 다. 고정 시차(+9시간)로 TDMS 기준으로 바꿔 비교한다.
    # 그래도 겹치지 않을 때만 예전처럼 정수 시간 차이를 찾아 본다 (다른 시간대로 적힌 힌트 대비).
    raw_hint_start, raw_hint_end = hint_start, hint_end
    shift_hours = LOCAL_UTC_OFFSET_HOURS
    if hint_start is not None:
        hint_start = local_to_tdms(hint_start)
        hint_end = local_to_tdms(hint_end) if hint_end is not None else None
        hint_start, hint_end, residual = align_hint_timebase(rec_start, rec_end, hint_start, hint_end)
        if residual:
            notes.append("힌트 시각을 한국 시간으로 보면 TDMS 기록구간과 %+.0f시간 어긋나 있어 시간대를 "
                         "맞춰 비교했습니다." % residual)

    # 보정해도 겹치지 않으면 (다른 세션의 XML 등) 신뢰하지 않는다
    hint_usable = False
    if hint_start is not None and hint_end is not None:
        overlap = min(hint_end, rec_end) - max(hint_start, rec_start)
        hint_usable = overlap.total_seconds() > 0
        if not hint_usable:
            notes.append(
                "XML 가공시간(%s~%s)이 TDMS 기록구간(%s~%s)과 겹치지 않아 힌트로 쓰지 않았습니다."
                % (raw_hint_start, raw_hint_end, rec_start, rec_end))

    pos = None
    if nc is not None and nc.blocks:
        bcol = _pick_column(frame, BLOCK_CANDIDATES)
        if bcol is None:
            notes.append("CNC-CurrentBlock 채널이 없어 NC 블록 대조를 건너뛰었습니다.")
        else:
            blocks = frame[bcol].map(normalize_block)

            # 다른 프로그램(예: 프로브 매크로 O9009)에서 실행된 행은 대조 대상에서 뺀다
            pcol = _pick_column(frame, PROGNAME_CANDIDATES)
            if pcol is not None and nc.name:
                target = normalize_block(os.path.splitext(nc.name)[0])
                pname = frame[pcol].map(lambda v: normalize_block(os.path.splitext(str(v))[0]))
                foreign = (pname != "") & (pname != target)
                if foreign.any() and (~foreign).sum() > 0:
                    blocks = blocks.where(~foreign, "")

            pos = map_block_positions(blocks.to_numpy(), nc)

            runs = _candidate_runs(pos, ts, restart_tolerance)
            scored = []
            for a, b in runs:
                cov, reached = _score_run(pos, nc, a, b)
                ov = 0.0
                if hint_usable:
                    s, e = ts.iloc[a].to_pydatetime(), ts.iloc[b - 1].to_pydatetime()
                    ov = max(0.0, (min(e, hint_end) - max(s, hint_start)).total_seconds())
                scored.append((ov, cov, reached, b - a, a, b))

            if scored:
                # XML 힌트와 겹치는 run 우선 -> 실행된 블록 종류 수 -> 끝까지 도달 -> 행 수
                ov, cov, reached, _, a, b = max(scored, key=lambda s: (s[0] > 0, s[1], s[2], s[3]))
                coverage = cov / nc.motion_blocks if nc.motion_blocks else 0.0
                if coverage >= min_coverage:
                    active = _active_mask(frame, pos, idle_gap, rpm_min, feed_min)
                    ra, rb = _largest_active_span(active, ts, merge_gap, a, b)
                    if len(runs) > 1:
                        notes.append("같은 프로그램 실행 후보 %d개 중 블록 커버리지가 가장 높은 구간을 선택했습니다."
                                     % len(runs))
                    if (rb - ra) < (b - a):
                        notes.append("선택 구간 앞뒤의 대기 시간 %d행을 잘라냈습니다."
                                     % ((b - a) - (rb - ra)))
                    # 실행이 확인된 가장 뒤 블록 이후로 남은 동작 블록 수. 마지막 한두 블록(원점 복귀,
                    # M30)은 아주 짧아 27Hz 폴링에 안 잡히기도 하므로 0 이 아니어도 끝까지 간 것일 수 있다.
                    seen = [int(p) for p in pos[a:b] if p >= 0 and not nc.is_marker(int(p))]
                    furthest = max(seen) if seen else -1
                    end_gap = sum(1 for i in range(len(nc.blocks)) if i > furthest and not nc.is_marker(i))
                    win = MachiningWindow(
                        start=ts.iloc[ra].to_pydatetime(), end=ts.iloc[rb - 1].to_pydatetime(),
                        row_start=int(ra), row_end=int(rb), method="nc_block", total_rows=n,
                        matched_blocks=int(cov), program_blocks=int(nc.motion_blocks),
                        hint_start=hint_start, hint_end=hint_end,
                        hint_shift_hours=shift_hours, nc_reference=nc_ref, notes=notes,
                        end_gap_blocks=int(end_gap))
                    if hint_usable:
                        win.notes.append("XML 기준 시작/종료와의 차이: %+.2fs / %+.2fs"
                                         % ((win.start - hint_start).total_seconds(),
                                            (win.end - hint_end).total_seconds()))
                    return win
                notes.append("NC 블록 일치율이 낮아(%.0f%%) 활동 기반 판별로 전환했습니다." % (coverage * 100))

    # --- 보조 1: 스핀들/이송 활동 기반 ---
    active = _active_mask(frame, pos if pos is not None else np.full(n, -1, dtype=np.int64),
                          idle_gap, rpm_min, feed_min)
    if active.any():
        ra, rb = _largest_active_span(active, ts, merge_gap, 0, n)
        if rb - ra >= 2:
            return MachiningWindow(
                start=ts.iloc[ra].to_pydatetime(), end=ts.iloc[rb - 1].to_pydatetime(),
                row_start=int(ra), row_end=int(rb), method="activity", total_rows=n,
                program_blocks=int(nc.motion_blocks) if nc else 0,
                hint_start=hint_start, hint_end=hint_end,
                hint_shift_hours=shift_hours, nc_reference=nc_ref,
                notes=notes + ["스핀들 회전/이송 활동이 이어지는 최장 구간을 가공 구간으로 사용했습니다."])

    # --- 보조 2: XML 힌트 ---
    if hint_usable:
        mask = (ts >= pd.Timestamp(hint_start)) & (ts <= pd.Timestamp(hint_end))
        idx = np.flatnonzero(mask.to_numpy())
        if idx.size >= 2:
            return MachiningWindow(
                start=ts.iloc[idx[0]].to_pydatetime(), end=ts.iloc[idx[-1]].to_pydatetime(),
                row_start=int(idx[0]), row_end=int(idx[-1] + 1), method="hint", total_rows=n,
                program_blocks=int(nc.motion_blocks) if nc else 0,
                hint_start=hint_start, hint_end=hint_end,
                hint_shift_hours=shift_hours, nc_reference=nc_ref,
                notes=notes + ["NC/활동 판별이 불가해 XML 가공시간을 그대로 적용했습니다."])

    # --- 최후: 전체 구간 ---
    return MachiningWindow(start=rec_start, end=rec_end, row_start=0, row_end=n,
                           method="full", total_rows=n,
                           program_blocks=int(nc.motion_blocks) if nc else 0,
                           hint_start=hint_start, hint_end=hint_end,
                           hint_shift_hours=shift_hours, nc_reference=nc_ref,
                           notes=notes + ["가공 구간을 좁힐 근거가 없어 기록 전체를 사용했습니다."])


# FANUC 운전 상태(CNC-ProgramMode): 0 리셋, 1 정지, 2 일시정지(HOLD), 3 실행(STaRT).
# 일시정지는 같은 사이클 안의 멈춤이므로 실행과 이어 본다.
RUNNING_STATES = (2, 3)
# 사이클 시작이 가공 구간보다 이만큼 넘게 앞서면 운전 상태를 믿지 않고 가공 구간 시작을 쓴다.
CYCLE_LEAD_LIMIT_SECONDS = 120.0
# 마지막 몇 블록(원점 복귀, M30)은 너무 짧아 폴링에 안 잡힐 수 있다 (O1110: 210블록 중 끝 3개).
END_GAP_TOLERANCE = 3
_PROGRAM_END_RE = r"(?<![A-Z0-9#])M0?(?:2|30)(?![0-9])"


def describe_cycle(frame: pd.DataFrame, window: MachiningWindow) -> MachiningWindow:
    """가공 구간에 실행 사이클(가공 시작 ~ 프로그램 끝)·가공 프로그램·첫 절삭·완료 여부를 채운다.

    가공 시작 = 운전 상태(CNC-ProgramMode)가 실행(3)으로 바뀐 순간 (작업자가 사이클 시작을 누른 때).
    TDMS 기록 시작이 아니다. 같은 파일 안에서 중단된 시도(리셋으로 끝나고 절삭이 없는 실행)는
    가공 구간과 겹치지 않으므로 고르지 않는다 (DB_Test/1: 16:43:12 의 시도는 건너뛰고 16:46:05).
    실측: XML StartTime 과 0.00 · −0.26초, FinishTime 과 −0.22 · −1.91초 차이.

    운전 상태 채널이 없으면 가공 구간(NC 블록 대조 · 활동 기반)을 그대로 사이클로 쓴다.
    window 를 고쳐서 그대로 돌려준다.
    """
    n = len(frame)
    if n == 0 or "timestamp" not in frame.columns or window.rows <= 0:
        return window
    ts = frame["timestamp"]
    a, b = window.row_start, window.row_end

    mcol = _pick_column(frame, PROGRAM_MODE_CANDIDATES)
    if mcol is not None:
        running = pd.to_numeric(frame[mcol], errors="coerce").isin(RUNNING_STATES).to_numpy()
        edges = np.flatnonzero(np.diff(np.concatenate(([0], running.astype(np.int8), [0]))))
        best = None
        for s, e in zip(edges[0::2], edges[1::2]):
            overlap = min(e, b) - max(s, a)
            if overlap > 0 and (best is None or overlap > best[0]):
                best = (overlap, int(s), int(e))
        if best is not None:
            lead = (ts.iloc[a] - ts.iloc[best[1]]).total_seconds()
            if lead > CYCLE_LEAD_LIMIT_SECONDS:
                window.notes.append("운전 상태상 사이클 시작이 가공 구간보다 %.0f초 앞서 있어 가공 구간 시작을 "
                                    "그대로 썼습니다." % lead)
                a, b = a, max(b, best[2])
            else:
                a, b = best[1], best[2]
        else:
            window.notes.append("가공 구간과 겹치는 실행 상태(ProgramMode 2·3)가 없어 가공 구간을 사이클로 썼습니다.")

    window.cycle_row_start, window.cycle_row_end = int(a), int(b)
    window.cycle_start = ts.iloc[a].to_pydatetime()
    window.cycle_end = ts.iloc[b - 1].to_pydatetime()
    window.program_name = machining_program(frame, a, b)

    cut = cutting_mask(frame.iloc[a:b]).to_numpy()
    if cut.any():
        window.first_cut = ts.iloc[a + int(np.flatnonzero(cut)[0])].to_pydatetime()

    # 완료 여부: 사이클 끝 무렵에 프로그램 끝(M02/M30)이 보이거나, NC 대조상 마지막 블록 근처까지 갔으면 완료.
    # 실측 근거 (2026-11-12): DB_Test/1 은 끝에 M2 가 보였고(남은 블록 0), DB_Test2/1 은 M30 이 27Hz 폴링에
    # 안 잡혀 '남은 동작 블록 1개' 로 판단했다. 둘 다 장비 XML 의 IsFinish 와 같다. 중단된 실측 Job 은
    # 아직 없어 허용치(END_GAP_TOLERANCE)는 더 모아서 정한다 — 그래서 무엇으로 판단했는지 함께 남긴다.
    ended, basis = None, None
    gap = window.end_gap_blocks
    gap_text = f"남은 동작 블록 {gap}개" if gap is not None else None
    bcol = _pick_column(frame, BLOCK_CANDIDATES)
    if bcol is not None:
        tail = frame[bcol].iloc[max(a, b - 50):min(n, b + 3)].astype(str).str.upper()
        if tail.str.contains(_PROGRAM_END_RE, regex=True).any():
            ended = True
            basis = "M02/M30 실행 확인" + (f" ({gap_text})" if gap_text else "")
    if ended is None and gap is not None:
        ended = gap <= END_GAP_TOLERANCE
        basis = f"{gap_text} ({'≤' if ended else '>'} 허용 {END_GAP_TOLERANCE}개)"
    window.program_end_reached = ended
    window.program_end_basis = basis
    return window


# ---------------------------------------------------------------------------
# 4. DAQ 시간축 정렬
# ---------------------------------------------------------------------------
@dataclass
class DaqAxis:
    """DAQ 채널의 절대 시간축."""

    start: pd.Timestamp
    increment: float          # 샘플 간격 (초)
    length: int
    source: str               # waveform: 파형 속성 기반 / scaled: 전체 구간 비율 스케일

    @property
    def sample_rate(self) -> float:
        return 1.0 / self.increment if self.increment else 0.0

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.to_timedelta(self.length * self.increment, unit="s")

    def index_of(self, ts) -> int:
        delta = (pd.Timestamp(ts) - self.start).total_seconds()
        return int(round(delta / self.increment)) if self.increment else 0

    def time_of(self, index: int) -> pd.Timestamp:
        return self.start + pd.to_timedelta(index * self.increment, unit="s")

    def clamp(self, index: int) -> int:
        return int(min(max(index, 0), self.length))


def daq_axis(channel, fallback_start=None, fallback_span: Optional[float] = None) -> DaqAxis:
    """채널 파형 속성으로 절대 시간축을 만든다.

    wf_increment 가 없는 장비 데이터는 "DAQ 와 CNC 의 전체 기록 구간이 같다"는 가정으로
    fallback_span(초) / 샘플 수 = 샘플 간격으로 환산한다(비율 스케일 변환).
    """
    props = dict(getattr(channel, "properties", {}) or {})
    length = len(channel)
    inc = props.get("wf_increment")
    start = props.get("wf_start_time")

    if inc and start is not None:
        t0 = pd.Timestamp(start) + pd.to_timedelta(float(props.get("wf_start_offset", 0.0) or 0.0), unit="s")
        return DaqAxis(start=t0, increment=float(inc), length=length, source="waveform")

    if fallback_start is not None and fallback_span and length > 0:
        return DaqAxis(start=pd.Timestamp(fallback_start), increment=float(fallback_span) / length,
                       length=length, source="scaled")

    t0 = pd.Timestamp(start) if start is not None else pd.Timestamp(fallback_start or 0)
    return DaqAxis(start=t0, increment=float(inc or 0.0) or 1.0, length=length, source="unknown")


@dataclass
class DaqSync:
    """CNC 시계 대비 DAQ 시계의 지연 측정 결과."""

    lag_seconds: float = 0.0        # DAQ 시각 + lag = CNC 시각
    correlation: float = 0.0
    zero_lag_correlation: float = 0.0
    reference: str = ""             # 기준으로 쓴 CNC 채널
    signal: str = ""                # 비교한 DAQ 채널
    applied: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lag_seconds"] = round(self.lag_seconds, 3)
        d["correlation"] = round(self.correlation, 4)
        d["zero_lag_correlation"] = round(self.zero_lag_correlation, 4)
        return d


# 지연 추정에 쓸 채널 우선순위 (같은 물리량을 보는 쌍일수록 앞에)
SYNC_REF_CANDIDATES = ("CNC-Z-SpindleLoad", "CNC-W-SpindleLoad", "CNC-Z-Current", "CNC-Z-SpindleSpeed")
SYNC_DAQ_GROUPS = (
    ("DAQ-Spindle-C-R", "DAQ-Spindle-C-S", "DAQ-Spindle-C-T"),
    ("DAQ-ACC-S-Housing-X", "DAQ-ACC-S-Housing-Y"),
    ("SOUND",),
)


def _rms_envelope(values: np.ndarray, samples_per_bin: int) -> np.ndarray:
    n = (len(values) // samples_per_bin) * samples_per_bin
    if n == 0:
        return np.zeros(0)
    return np.sqrt((values[:n].reshape(-1, samples_per_bin) ** 2).mean(axis=1))


def estimate_daq_lag(
    tdms,
    cnc_frame: pd.DataFrame,
    window: "MachiningWindow",
    *,
    search_seconds: float = 10.0,
    resolution: float = 0.05,
    min_correlation: float = 0.6,
    min_gain: float = 0.15,
) -> DaqSync:
    """CNC 부하 신호와 DAQ 신호의 상호상관으로 두 시계의 지연을 측정한다.

    반환값 lag 는 "DAQ 시각에 더하면 CNC 시각이 되는 값"이다.
    상관이 낮거나(min_correlation) 지연 0 대비 이득이 작으면 보정하지 않는다.
    """
    sync = DaqSync()
    if "DAQ" not in tdms or cnc_frame.empty or window.start is None or window.duration_seconds < 2:
        sync.note = "지연 측정에 필요한 CNC/DAQ 구간이 부족합니다."
        return sync

    w = cnc_frame.iloc[window.row_start:window.row_end]
    ref_col = next((c for c in SYNC_REF_CANDIDATES if c in w.columns), None)
    if ref_col is None:
        sync.note = "기준으로 쓸 CNC 부하 채널이 없습니다."
        return sync
    ref = pd.to_numeric(w[ref_col], errors="coerce").fillna(0).to_numpy(dtype=float)
    if ref.size < 8 or float(np.nanstd(ref)) == 0.0:
        sync.note = "CNC %s 값이 일정해 지연을 측정할 수 없습니다." % ref_col
        return sync
    ref_t = (w["timestamp"] - pd.Timestamp(window.start)).dt.total_seconds().to_numpy()

    group = tdms["DAQ"]
    names = {ch.name for ch in group.channels()}
    chosen = next((g for g in SYNC_DAQ_GROUPS if any(n in names for n in g)), None)
    if chosen is None:
        sync.note = "지연 측정에 쓸 DAQ 채널이 없습니다."
        return sync
    chosen = [n for n in chosen if n in names]

    axis = daq_axis(group[chosen[0]])
    if axis.sample_rate <= 0:
        sync.note = "DAQ 샘플링 정보가 없어 지연을 측정할 수 없습니다."
        return sync

    pad = int((search_seconds + 2.0) * axis.sample_rate)
    i0 = axis.clamp(axis.index_of(window.start) - pad)
    i1 = axis.clamp(axis.index_of(window.end) + pad)
    if i1 - i0 < int(axis.sample_rate):
        sync.note = "DAQ 에서 비교할 구간을 확보하지 못했습니다."
        return sync

    # 3상 전류처럼 여러 채널이면 제곱합으로 합성 (채널 길이가 다를 수 있어 짧은 쪽에 맞춘다)
    acc = None
    for name in chosen:
        try:
            v = np.asarray(group[name][i0:i1], dtype=np.float64) ** 2
        except Exception:
            continue
        if acc is None:
            acc = v
        else:
            n = min(len(acc), len(v))
            acc = acc[:n] + v[:n]
    if acc is None or len(acc) == 0:
        sync.note = "DAQ 채널을 읽지 못했습니다."
        return sync

    spb = max(1, int(axis.sample_rate * resolution))
    env = _rms_envelope(np.sqrt(acc), spb)
    if env.size < 8:
        sync.note = "DAQ 포락선이 너무 짧습니다."
        return sync
    env_t = (np.arange(env.size) + 0.5) * resolution         + (axis.time_of(i0) - pd.Timestamp(window.start)).total_seconds()

    # 공통 격자 위에서 정규화 상호상관
    grid = np.arange(max(ref_t[0], env_t[0] + search_seconds),
                     min(ref_t[-1], env_t[-1] - search_seconds), resolution)
    if grid.size < 8:
        sync.note = "상관 분석 구간이 부족합니다."
        return sync
    a = np.interp(grid, ref_t, ref)
    a = a - a.mean()
    a_norm = float(np.sqrt((a * a).sum()))
    if a_norm == 0:
        sync.note = "CNC 기준 신호의 변화가 없습니다."
        return sync

    best_lag, best_corr, zero_corr = 0.0, -2.0, 0.0
    for lag in np.arange(-search_seconds, search_seconds + 1e-9, resolution):
        b = np.interp(grid + lag, env_t, env)
        b = b - b.mean()
        b_norm = float(np.sqrt((b * b).sum()))
        corr = float((a * b).sum() / (a_norm * b_norm)) if b_norm else 0.0
        if abs(lag) < resolution / 2:
            zero_corr = corr
        if corr > best_corr:
            best_corr, best_lag = corr, float(lag)

    sync.reference = ref_col
    sync.signal = "+".join(chosen)
    sync.correlation = best_corr
    sync.zero_lag_correlation = zero_corr
    # xcorr 에서 env(grid + lag) 가 ref(grid) 와 맞았다는 뜻이므로
    # DAQ 시각 = CNC 시각 + lag -> CNC 시각 = DAQ 시각 - lag
    sync.lag_seconds = -best_lag

    if best_corr >= min_correlation and (best_corr - zero_corr) >= min_gain and abs(best_lag) > resolution:
        sync.applied = True
        sync.note = ("%s ↔ %s 상관 %.3f (지연 0일 때 %.3f) 로 DAQ 를 %+.2fs 이동했습니다."
                     % (ref_col, sync.signal, best_corr, zero_corr, sync.lag_seconds))
    else:
        sync.lag_seconds = 0.0
        sync.note = ("상관이 충분하지 않아(최대 %.3f, 지연 0일 때 %.3f) DAQ 시각을 보정하지 않았습니다."
                     % (best_corr, zero_corr))
    return sync


def _envelope(values: np.ndarray, bins: int) -> Tuple[np.ndarray, np.ndarray]:
    """구간별 최대/최소를 남기는 다운샘플 (진폭 보존).

    수백만 샘플을 파이썬 루프로 돌면 채널당 수십 초가 걸리므로 reduceat 으로 한 번에 줄인다.
    """
    n = len(values)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    bins = max(1, min(bins, n))
    starts = np.linspace(0, n, bins + 1, dtype=np.int64)[:-1]
    # 빈 구간이 생기지 않도록 시작 인덱스를 단조 증가로 보정
    starts = np.maximum.accumulate(np.minimum(starts, n - 1))
    hi = np.maximum.reduceat(values, starts)
    lo = np.minimum.reduceat(values, starts)
    return hi, lo


# ---------------------------------------------------------------------------
# 5. 정렬된 프레임 생성
# ---------------------------------------------------------------------------
@dataclass
class AlignedResult:
    cnc: pd.DataFrame
    daq: pd.DataFrame
    fft: pd.DataFrame
    window: MachiningWindow
    meta: dict
    sync: DaqSync = field(default_factory=DaqSync)


def build_aligned_frames(
    tdms,
    window: MachiningWindow,
    cnc_frame: Optional[pd.DataFrame] = None,
    *,
    target_points: int = TARGET_DATAPOINTS,
    fft_nperseg: int = 4096,
    sync: Optional[DaqSync] = None,
    auto_sync: bool = True,
) -> AlignedResult:
    """가공 구간만 잘라 CNC / DAQ(envelope) / FFT 프레임을 만든다.

    두 프레임 모두 가공 시작시각을 0초로 하는 경과시간 열을 갖는다.
      * CNC : time_s
      * DAQ : daq_time_s
    """
    meta: dict = {"daq_channels": {}, "truncated_bytes": getattr(tdms, "truncated_bytes", 0)}

    # ---- CNC ----
    if cnc_frame is None:
        cnc_frame = read_cnc_frame(tdms)

    # ---- DAQ 시계 지연 실측 ----
    if sync is None:
        sync = estimate_daq_lag(tdms, cnc_frame, window) if auto_sync else DaqSync()
    meta["daq_sync"] = sync.to_dict()
    lag = pd.to_timedelta(sync.lag_seconds, unit="s")
    cnc = cnc_frame.iloc[window.row_start:window.row_end].copy() if not cnc_frame.empty else pd.DataFrame()
    if not cnc.empty:
        if len(cnc) > target_points:
            step = max(1, len(cnc) // target_points)
            cnc = cnc.iloc[::step].copy()
        if "timestamp" in cnc.columns and window.start is not None:
            cnc["time_s"] = (cnc["timestamp"] - pd.Timestamp(window.start)).dt.total_seconds()
        cnc = cnc.reset_index(drop=True)
        meta["cnc_rows_window"] = int(window.rows)
        meta["cnc_rows_output"] = int(len(cnc))

    # ---- DAQ ----
    daq_cols: Dict[str, np.ndarray] = {}
    fft_cols: Dict[str, np.ndarray] = {}
    bins = target_points
    if "DAQ" in tdms:
        group = tdms["DAQ"]
        span = None
        if not cnc_frame.empty and "timestamp" in cnc_frame.columns:
            span = (cnc_frame["timestamp"].iloc[-1] - cnc_frame["timestamp"].iloc[0]).total_seconds()
        fb_start = cnc_frame["timestamp"].iloc[0] if (not cnc_frame.empty and "timestamp" in cnc_frame.columns) else None

        try:
            from scipy.signal import welch
        except Exception:
            welch = None

        freqs = None
        for channel in group.channels():
            if len(channel) == 0:
                continue
            axis = daq_axis(channel, fallback_start=fb_start, fallback_span=span)
            if window.start is not None and window.end is not None:
                # DAQ 시각 = CNC 시각 - lag (lag 만큼 DAQ 가 앞서 있었음)
                i0 = axis.clamp(axis.index_of(pd.Timestamp(window.start) - lag))
                i1 = axis.clamp(axis.index_of(pd.Timestamp(window.end) - lag))
            else:
                i0, i1 = 0, axis.length
            if i1 - i0 < 2:
                i0, i1 = 0, axis.length  # 매핑 실패 시 전체 사용
            values = np.asarray(channel[i0:i1], dtype=np.float64)
            if values.size == 0:
                continue

            hi, lo = _envelope(values, bins)
            daq_cols[channel.name + "_max"] = hi
            daq_cols[channel.name + "_min"] = lo
            meta["daq_channels"][channel.name] = {
                "axis_source": axis.source,
                "sample_rate_hz": round(axis.sample_rate, 3),
                "total_samples": int(axis.length),
                "window_samples": int(i1 - i0),
                "window_index": [int(i0), int(i1)],
            }

            if welch is not None and axis.sample_rate > 0:
                nper = int(min(fft_nperseg, max(256, 2 ** int(np.floor(np.log2(max(values.size, 256)))))))
                f, psd = welch(values, fs=axis.sample_rate, nperseg=nper)
                if freqs is None or len(f) == len(freqs):
                    if freqs is None:
                        freqs = f
                        fft_cols["Frequency"] = freqs
                    fft_cols[channel.name] = psd

    daq = pd.DataFrame(daq_cols)
    if not daq.empty:
        # 모든 채널을 같은 bin 수로 줄였으므로 하나의 경과시간 축을 공유한다
        dur = window.duration_seconds or 0.0
        centers = (np.arange(len(daq)) + 0.5) * (dur / len(daq)) if dur else np.arange(len(daq), dtype=float)
        daq.insert(0, "daq_time_s", centers)
        meta["daq_rows_output"] = int(len(daq))

    fft = pd.DataFrame(fft_cols)
    return AlignedResult(cnc=cnc, daq=daq, fft=fft, window=window, meta=meta, sync=sync)


def merge_for_parquet(cnc: pd.DataFrame, daq: pd.DataFrame) -> pd.DataFrame:
    """CNC/DAQ 를 가로로 합쳐 기존 *_viz.parquet 레이아웃을 유지한다.

    행 수가 다르므로 짧은 쪽은 NaN 으로 남는다. 각 그룹은 자기 시간축
    (time_s / daq_time_s)으로 그리면 되고, 두 축의 0초는 같은 시점이다.
    """
    left = cnc.reset_index(drop=True) if not cnc.empty else pd.DataFrame()
    right = daq.reset_index(drop=True) if not daq.empty else pd.DataFrame()
    if left.empty and right.empty:
        return pd.DataFrame()
    if left.empty:
        return right
    if right.empty:
        return left
    return pd.concat([left, right], axis=1)


def summarize_window(window: MachiningWindow, result: Optional[AlignedResult] = None) -> str:
    ended = {True: "예", False: "아니오", None: "모름"}[window.program_end_reached]
    lines = [
        "가공 구간 판별 결과",
        "  판별 방법   : %s" % window.method,
        "  구간 시작   : %s  (한국 %s)" % (window.start, window.local_start),
        "  구간 종료   : %s  (한국 %s)" % (window.end, window.local_end),
        "  구간 길이   : %.2f 초" % window.duration_seconds,
        "  CNC 행      : %d ~ %d (%d행 / 전체 %d행)" % (
            window.row_start, window.row_end, window.rows, window.total_rows),
    ]
    if window.cycle_start is not None:
        lines += [
            "  가공 프로그램: %s" % (window.program_name or "-"),
            "  가공 시작   : %s  (실행 사이클 시작, 한국 시간)" % window.local_cycle_start,
            "  가공 종료   : %s  (프로그램 끝, 한국 시간)" % window.local_cycle_end,
            "  첫 절삭     : %s" % (window.local_first_cut or "-"),
            "  프로그램 끝 : %s" % ended,
        ]
    if window.program_blocks:
        lines.append("  NC 블록     : %d / %d 종 실행 (커버리지 %.0f%%)" % (
            window.matched_blocks, window.program_blocks, window.nc_coverage * 100))
    if result is not None:
        sync = result.sync
        if sync.reference:
            lines.append("  DAQ 시계 보정: %+.2f s (%s, 상관 %.3f)%s" % (
                sync.lag_seconds, "적용" if sync.applied else "미적용",
                sync.correlation, ""))
        for name, info in result.meta.get("daq_channels", {}).items():
            lines.append("  DAQ %-20s %s축 %.0fHz · 전체 %d → 구간 %d 샘플" % (
                name, info["axis_source"], info["sample_rate_hz"],
                info["total_samples"], info["window_samples"]))
    for note in window.notes:
        lines.append("  · %s" % note)
    if result is not None and result.sync.note:
        lines.append("  · %s" % result.sync.note)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. XML 힌트 읽기 (단독 실행용 - 파이프라인은 DB 의 Job 값을 쓴다)
# ---------------------------------------------------------------------------
def read_xml_times(path: str) -> Tuple[Optional[datetime], Optional[datetime], Optional[str]]:
    """WorkModel XML 에서 StartTime / FinishTime / ProgramName 을 읽는다."""
    import xml.etree.ElementTree as ET

    def _strip_ns(tag: str) -> str:
        return tag.split("}")[-1]

    def _to_naive(text):
        ts = pd.Timestamp(text)
        # XML 은 +09:00 같은 오프셋을 달고 오기도, 없이 오기도 한다. DB 와 같은 기준(한국 시간,
        # 시간대 없음)으로 맞춘다. TDMS(UTC) 와의 비교는 detect_machining_window 가 바꿔서 한다.
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None) + LOCAL_UTC_OFFSET
        return ts.floor("us").to_pydatetime()

    start = end = prog = None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None, None, None
    for child in root:
        tag = _strip_ns(child.tag)
        try:
            if tag == "StartTime" and child.text:
                start = _to_naive(child.text)
            elif tag == "FinishTime" and child.text:
                end = _to_naive(child.text)
        except Exception:
            continue
        if tag in ("ProgramName", "ProgramCode") and child.text and not prog:
            prog = child.text.strip()
    return start, end, prog


# ---------------------------------------------------------------------------
# 7. 단독 실행 CLI
# ---------------------------------------------------------------------------
def process_file(tdms_path: str, nc_path: Optional[str] = None, xml_path: Optional[str] = None,
                 out_dir: Optional[str] = None, prefix: str = "aligned",
                 target_points: int = TARGET_DATAPOINTS,
                 hint_start: Optional[datetime] = None,
                 hint_end: Optional[datetime] = None,
                 utc_offset_hours: Optional[float] = None) -> dict:
    """TDMS + NC(+XML) 한 세트를 가공 구간 정렬 Parquet 로 변환한다."""
    nc = load_nc_program(nc_path) if nc_path else None
    if xml_path and (hint_start is None or hint_end is None):
        xs, xe, xprog = read_xml_times(xml_path)
        hint_start = hint_start or xs
        hint_end = hint_end or xe
        if nc is not None and xprog and not nc.name:
            nc.name = xprog

    with open_tdms(tdms_path) as tdms:
        cnc_frame = read_cnc_frame(tdms)

        # 힌트는 한국 시간으로 넘긴다 (detect_machining_window 가 TDMS(UTC) 로 바꿔 비교한다).
        # --utc-offset 은 '시간대 표시 없이 적힌 XML 시각이 UTC 몇 시간인지' 다. 한국 시간으로 옮긴다.
        hs, he = hint_start, hint_end
        if utc_offset_hours is not None and utc_offset_hours != LOCAL_UTC_OFFSET_HOURS:
            delta = pd.to_timedelta(LOCAL_UTC_OFFSET_HOURS - utc_offset_hours, unit="h")
            hs = (pd.Timestamp(hs) + delta).to_pydatetime() if hs is not None else None
            he = (pd.Timestamp(he) + delta).to_pydatetime() if he is not None else None

        # NC 에 이름이 없으면 TDMS 에서 실제로 깎은 프로그램 이름을 쓴다 (다른 프로그램 행을 거르는 기준).
        if nc is not None and not nc.name:
            nc.name = machining_program(cnc_frame) or ""
        window = detect_machining_window(cnc_frame, nc, hs, he)
        describe_cycle(cnc_frame, window)
        result = build_aligned_frames(tdms, window, cnc_frame, target_points=target_points)

    out = {"window": window.to_dict(), "daq_sync": result.sync.to_dict(),
           "meta": result.meta, "files": {}}
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        merged = merge_for_parquet(result.cnc, result.daq)
        if not merged.empty:
            p = os.path.join(out_dir, f"{prefix}_viz.parquet")
            merged.to_parquet(p, engine="pyarrow")
            out["files"]["viz"] = p
        if not result.fft.empty:
            p = os.path.join(out_dir, f"{prefix}_fft.parquet")
            result.fft.to_parquet(p, engine="pyarrow")
            out["files"]["fft"] = p
        p = os.path.join(out_dir, f"{prefix}_window.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        out["files"]["window"] = p

    out["summary"] = summarize_window(window, result)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="TDMS 가공 구간 판별 및 정렬 Parquet 생성")
    ap.add_argument("--tdms", required=True, help="TDMS 파일 경로")
    ap.add_argument("--nc", help="업로드된 NC 프로그램 경로 (블록 대조 기준)")
    ap.add_argument("--xml", help="WorkModel XML 경로 (가공 시작/종료 힌트)")
    ap.add_argument("--out", help="Parquet 출력 디렉터리 (생략 시 판별 결과만 출력)")
    ap.add_argument("--prefix", default="aligned", help="출력 파일 접두어 (기본 aligned)")
    ap.add_argument("--points", type=int, default=TARGET_DATAPOINTS, help="그래프 목표 점 개수")
    ap.add_argument("--utc-offset", type=float, default=None,
                    help="시간대 표시 없이 적힌 XML 시각의 UTC 오프셋(시). 생략하면 한국 시간(+9)으로 본다")
    ap.add_argument("--json", action="store_true", help="판별 결과를 JSON 으로 출력")
    args = ap.parse_args(argv)

    res = process_file(args.tdms, args.nc, args.xml, args.out, args.prefix,
                       target_points=args.points, utc_offset_hours=args.utc_offset)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        print(res["summary"])
        for k, v in res.get("files", {}).items():
            print("  저장 %-7s %s" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
