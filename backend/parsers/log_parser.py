import os
import csv
import re
import datetime
from DB.database import SessionLocal
from DB.models import MachineLog, LogFileArchive
from vault_manager import archive_file

# CNC 1Hz 로그를 구분하기 위한 헤더 표식.
# 같은 Job 폴더에는 CNC 로그 말고도 .log 확장자를 쓰는 파일이 떨어질 수 있다
# (실제로 NI TDMS 라이브러리가 "TDS Exception in Initialize..." 오류를 UTF-16 .log 로 남긴다).
# 그런 파일을 그대로 받아들이면 log_file_archive.log_raw_path 가 엉뚱한 파일을 가리키고
# machine_log 통계가 전부 0 으로 덮어써지므로, 헤더를 보고 아니면 건너뛴다.
CNC_LOG_REQUIRED_COLUMNS = {'time'}
CNC_LOG_SIGNATURE_COLUMNS = {'cls', 'crpm', 'cfr', 'ctime', 'cut'}


def looks_like_cnc_log(fieldnames):
    """csv 리더가 읽은 헤더가 CNC 1Hz 로그의 것인지 판단한다."""
    columns = {(name or '').strip().lower() for name in (fieldnames or [])}
    return CNC_LOG_REQUIRED_COLUMNS <= columns and bool(columns & CNC_LOG_SIGNATURE_COLUMNS)


def safe_float(val, default=0.0):
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 장비 계산값 (장비 로그에만 있는 값)
# ---------------------------------------------------------------------------
# 장비 로그(_ext.log, 10Hz) 의 열 중 CNC 상태를 그대로 옮긴 것. TDMS CNC 채널(약 27Hz)과 같은 정보라
# 장비 계산값 요약에서는 뺀다 (회전수 crpm·ccrpm, 이송 cfr·ccfr, 위치 cpx·cpy·cpz, M·T 코드,
# 시퀀스·블록 번호, 부하 cls·clx·cly·clz, 시각 time, 경과시간 ctime).
CNC_MIRROR_COLUMNS = frozenset({'time', 'ctime', 'crpm', 'ccrpm', 'cfr', 'ccfr', 'cpx', 'cpy', 'cpz',
                                'cm', 'ct', 'csn', 'cbn', 'cls', 'clx', 'cly', 'clz', 'cut'})
# 이름으로 뜻을 짐작할 수 있는 것만 적는다. 나머지는 장비 열 이름 그대로 보여 준다.
# ek* 는 장비가 함께 남기는 *_coeff.xml(WorkCuttingCoeffModel) 의 Ktc·Krc·Kac·Kte·Kre·Kae 와 같은 이름이다.
MACHINE_INDICATOR_LABELS = {
    'echtlevel': '채터 수준 (장비 추정)',
    'esound': '소리 크기',
    'esoundfq': '소리 주파수 (Hz)',
    'ektc': '절삭계수 Ktc', 'ekrc': '절삭계수 Krc', 'ekac': '절삭계수 Kac',
    'ekte': '날끝계수 Kte', 'ekre': '날끝계수 Kre', 'ekae': '날끝계수 Kae',
    'ekrate': '절삭계수 KRate',
}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float('inf') else None


def _round(value):
    return float(f"{value:.6g}") if value is not None else None


def summarize_machine_indicators(rows, file_name=None):
    """장비 로그 행(dict 목록)에서 장비 계산값을 열마다 요약한다. machine_log.machine_indicators 에 들어간다.

    장비 판정 절삭 행(cut=1)이 있으면 그 행들로, 없으면 전체 행으로 평균·최대·최소를 낸다.
    기록은 됐지만 값이 전부 0 인 열(장비에서 해당 추정이 꺼져 있었을 수 있다)과 값이 없는 열은
    'all_zero' · 'empty' 로 따로 모은다. 반환 예:
        {"source": "장비 로그", "file": "..._ext.log", "rows": 549, "cutting_rows": 448,
         "basis": "절삭 중(cut=1)", "values": {"echtlevel": {"mean": 0.0558, "max": 1.0, "min": 0.0}, ...},
         "labels": {"echtlevel": "채터 수준 (장비 추정)", ...}, "all_zero": ["ektc", ...], "empty": ["afmax", ...]}
    """
    if not rows:
        return None
    columns = [c for c in rows[0].keys()
               if c and c.strip() and c.strip().lower() not in CNC_MIRROR_COLUMNS]
    cutting = [r for r in rows if _number(r.get('cut')) == 1]
    basis = cutting or rows

    values, all_zero, empty = {}, [], []
    for col in columns:
        every = [v for v in (_number(r.get(col)) for r in rows) if v is not None]
        if not every:
            empty.append(col)
            continue
        if all(v == 0 for v in every):
            all_zero.append(col)
            continue
        picked = [v for v in (_number(r.get(col)) for r in basis) if v is not None]
        if picked:
            values[col] = {"mean": _round(sum(picked) / len(picked)), "max": _round(max(picked)),
                           "min": _round(min(picked))}
    return {
        "source": "장비 로그",
        "file": file_name,
        "rows": len(rows),
        "cutting_rows": len(cutting),
        "basis": "절삭 중(cut=1)" if cutting else "전체",
        "values": values,
        "labels": {c: MACHINE_INDICATOR_LABELS[c.lower()] for c in values if c.lower() in MACHINE_INDICATOR_LABELS},
        "all_zero": all_zero,
        "empty": empty,
    }


def read_log_rows(file_path):
    """장비 로그를 (열 이름 목록, 행 dict 목록) 으로 읽는다. CNC 로그 형식이 아니면 행 대신 None.

    UTF-8 로 읽히지 않으면 CP949 로 읽고, 구분자는 탭·쉼표 중에서 고른다.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as test_f:
            test_f.read(8192)
        encoding = 'utf-8'
    except UnicodeDecodeError:
        encoding = 'cp949'
    with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        sample = f.read(1024)
        f.seek(0)
        try:
            reader = csv.DictReader(f, dialect=csv.Sniffer().sniff(sample, delimiters='\t,'))
        except Exception:
            # Fallback: 탭이 있으면 탭, 없으면 콤마로 설정
            reader = csv.DictReader(f, delimiter='\t' if '\t' in sample else ',')
        fieldnames = list(reader.fieldnames or [])
        if not looks_like_cnc_log(fieldnames):
            return fieldnames, None
        return fieldnames, list(reader)


def alarm_summary(fieldnames, rows):
    """(알람 수, 알람 메시지). 알람 열(alarm_msg)이 없는 로그면 (None, None).

    지금 장비 로그에는 alarm_msg 열이 없어서 예전에는 '알람 0건' 으로 적혔다.
    모르는 것과 없는 것은 다르므로 열이 없으면 비워 둔다.
    """
    if 'alarm_msg' not in {(name or '').strip().lower() for name in fieldnames}:
        return None, None
    messages = set()
    for row in rows:
        alarm = (row.get('alarm_msg') or '').strip()
        if alarm and alarm.lower() not in ('none', 'null', '0'):
            messages.add(alarm)
    return len(messages), (", ".join(sorted(messages)) if messages else None)


def _log_time(text):
    """'260911 16:46:05.660' -> datetime. 못 읽으면 None."""
    text = (text or '').strip()
    for fmt in ("%y%m%d %H:%M:%S.%f", "%y%m%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None

def _whole_seconds(dt):
    return dt.replace(microsecond=0) if dt is not None else None


def log_motion_summary(fieldnames, rows):
    """장비 로그 행으로 낸 요약: 시각 범위, 절삭시간·이동거리(임시값), 회전수·이송·부하의 최대·평균.

    절삭 판정은 장비가 남긴 cut 열을 쓴다 (없는 로그만 회전수 > 100 · 이송 > 0 — 급속이송이 섞인다).
    절삭시간은 절삭 행 수 x 행 간격이다. ctime 은 경과시간이라 절삭시간이 아니다
    (O0911: ctime 54.9초, 실제 절삭 44.8초). 수집기 시작 때의 보정(recovery_engine)도 이 함수를 쓴다.
    이동거리는 공구 길이 보정(G43/G49) 위치 점프를 가리지 않은 값이다 — 10Hz 로그로는 점프(125~191mm/0.1초)와
    급속이송을 속도로 확실히 가를 수 없다. TDMS 를 처리하면 점프를 뺀 값(tdms_alignment.travel_steps)으로 바뀐다.
    """
    has_cut_flag = 'cut' in {(name or '').strip().lower() for name in fieldnames}
    first_time_str = last_time_str = None
    row_count = cutting_row_count = 0
    total_dist_3d = cutting_dist_3d = 0.0
    max_load = max_rpm = max_feed = total_rpm = 0.0
    prev = None

    for row in rows:
        row_t = (row.get('time') or '').strip()
        if row_t:
            if first_time_str is None:
                first_time_str = row_t
            last_time_str = row_t

        load = safe_float(row.get('cls', 0))
        rpm = safe_float(row.get('crpm', 0))
        feed = safe_float(row.get('cfr', 0))
        is_cutting = (_number(row.get('cut')) == 1) if has_cut_flag else (rpm > 100 and feed > 0)
        if is_cutting:
            cutting_row_count += 1

        # 3D 좌표 이동거리 적분 (cpx, cpy, cpz)
        if 'cpx' in row and 'cpy' in row and 'cpz' in row:
            pos = (safe_float(row.get('cpx', 0)), safe_float(row.get('cpy', 0)), safe_float(row.get('cpz', 0)))
            if prev is not None:
                d_step = sum((a - b) ** 2 for a, b in zip(pos, prev)) ** 0.5
                total_dist_3d += d_step
                if is_cutting:
                    cutting_dist_3d += d_step
            prev = pos

        max_load, max_rpm, max_feed = max(max_load, load), max(max_rpm, rpm), max(max_feed, feed)
        total_rpm += rpm
        row_count += 1

    # 한 행의 시간 간격 (장비 로그는 10Hz). 시각 열로 재고, 못 읽으면 0.1초로 본다.
    t_first, t_last = _log_time(first_time_str), _log_time(last_time_str)
    row_interval = ((t_last - t_first).total_seconds() / (row_count - 1)
                    if t_first and t_last and row_count > 1 and t_last > t_first else 0.1)
    return {
        "rows": row_count,
        "start_time": _whole_seconds(t_first),
        "end_time": _whole_seconds(t_last),
        "cutting_seconds": round(cutting_row_count * row_interval, 1) if cutting_row_count else None,
        "moving_distance": round(total_dist_3d, 1) if total_dist_3d > 0 else None,
        "cutting_moving_distance": round(cutting_dist_3d, 1) if cutting_dist_3d > 0 else None,
        "max_spindle_load": max_load,
        "max_spindle_rpm": max_rpm,
        "max_feed_rate": max_feed,
        "avg_spindle_rpm": (total_rpm / row_count) if row_count else 0.0,
    }


def fill_job_from_log(job, summary):
    """로그 요약으로 Job 의 빈 칸만 채운다 (임시값 — TDMS 를 처리하면 tdms_visualizer 가 덮어쓴다).
    채운 칸 이름 목록을 돌려준다."""
    filled = []
    for attr in ("start_time", "end_time", "cutting_seconds"):
        if getattr(job, attr) is None and summary.get(attr) is not None:
            setattr(job, attr, summary[attr])
            filled.append(attr)
    # 절삭 이동거리는 같은 로그의 전체 이동거리를 함께 채울 때만 (출처가 섞이지 않게)
    if job.moving_distance is None and summary.get("moving_distance") is not None:
        job.moving_distance = summary["moving_distance"]
        filled.append("moving_distance")
        if job.cutting_moving_distance is None and summary.get("cutting_moving_distance") is not None:
            job.cutting_moving_distance = summary["cutting_moving_distance"]
            filled.append("cutting_moving_distance")
    return filled


from job_manager import get_or_create_job

def parse_log(file_path, job_id):
    """
    장비 로그(_ext.log, 탭 구분 10Hz)를 읽어 machine_log 에 요약을 저장하는 모듈.

    로그가 주는 것
      - 장비 계산값 (채터 수준·소리·절삭계수 추정 등): 로그에만 있다 -> machine_indicators
      - CNC 상태 (회전수·이송·부하·위치): TDMS CNC 채널과 같은 정보다. 최대·평균 값은 TDMS 로
        계산한 것이 기준이고(stats_source='TDMS'), TDMS 가 없는 Job 에서만 로그로 계산한다('LOG').
    시계열은 DB 에 넣지 않고 요약만 저장하며, 원본 파일은 Vault 에 아카이빙한다.
    """
    print(f"  -> [Log Parser] 파일: {os.path.basename(file_path)} / Folder ID: {job_id}")
    
    db = SessionLocal()
    try:
        # Job 가져오기 또는 생성 (XML 독립)
        job = get_or_create_job(db, job_id)

        # 로그 파일명에서 타임스탬프 (YYMMDDHHMMSS) 추출하여 start_time 보완
        fname = os.path.basename(file_path)
        m = re.search(r'__(\d{12})(?:_ext)?\.log', fname)
        if m:
            ts_str = m.group(1)
            try:
                dt = datetime.datetime.strptime(f"20{ts_str}", "%Y%m%d%H%M%S")
                if job.start_time is None:
                    job.start_time = dt
                    print(f"    - [Log Parser] 파일명에서 가공 일시 추출 반영: {dt}")
            except Exception as dt_err:
                pass

        fieldnames, rows = read_log_rows(file_path)
        if rows is None:
            preview = ", ".join(fieldnames[:5]) or "(헤더 없음)"
            print(f"    - [건너뜀] CNC 1Hz 로그 형식이 아니라 무시합니다 "
                  f"({os.path.basename(file_path)} / 열: {preview})")
            return False

        summary = log_motion_summary(fieldnames, rows)
        alarm_count, critical_alarms = alarm_summary(fieldnames, rows)
        indicators = summarize_machine_indicators(rows, os.path.basename(file_path))

        # Job 의 빈 칸만 로그 값으로 임시로 채운다. TDMS 가 있으면 tdms_visualizer 가 CNC 채널로 다시
        # 계산해 덮어쓴다.
        filled = fill_job_from_log(job, summary)
        if filled:
            print(f"    - [Log Parser] 비어 있던 칸을 로그 값으로 임시 설정: "
                  + ", ".join(f"{name}={getattr(job, name)}" for name in filled))

        # Job 테이블에 원본 파일 경로 매핑
        from vault_manager import get_rel_raw_data_path
        log_raw_rel = get_rel_raw_data_path(file_path)
        
        # MachineLog 요약 정보 삽입 또는 업데이트
        # 최대·평균 값은 TDMS(CNC 채널, 약 27Hz) 가 기준이다. TDMS 로 이미 계산했으면 로그(10Hz) 값으로
        # 덮어쓰지 않고, 로그에만 있는 장비 계산값과 알람만 적는다.
        m_log = db.query(MachineLog).filter(MachineLog.job_id == job.job_id).first()
        if m_log is None:
            m_log = MachineLog(job_id=job.job_id)
            db.add(m_log)
        if m_log.stats_source != "TDMS":
            m_log.max_spindle_load = summary["max_spindle_load"]
            m_log.max_spindle_rpm = summary["max_spindle_rpm"]
            m_log.max_feed_rate = summary["max_feed_rate"]
            m_log.avg_spindle_rpm = summary["avg_spindle_rpm"]
            m_log.stats_source = "LOG"
        m_log.alarm_count = alarm_count
        m_log.critical_alarm_msg = critical_alarms
        m_log.machine_indicators = indicators
        db.flush()
        target_log_id = m_log.log_id
        if indicators:
            print(f"    - [Log Parser] 장비 계산값 {len(indicators['values'])}개 요약 저장 "
                  f"(전부 0: {len(indicators['all_zero'])}개, 값 없음: {len(indicators['empty'])}개)")
            
        # 보관소 사본 (원본과 같은 자리·같은 이름)
        archive_file(file_path)
            
        archive = db.query(LogFileArchive).filter_by(log_id=target_log_id).first()
        if not archive:
            archive = LogFileArchive(log_id=target_log_id, log_raw_path=log_raw_rel)
            db.add(archive)
        else:
            archive.log_raw_path = log_raw_rel
            
        db.commit()
        print(f"    - Log 요약 파싱 완료. 통계 DB 저장 및 파일 매핑 성공 (Job ID: {job.job_id})")
        return True
            
    except Exception as e:
        db.rollback()
        print(f"    - [오류] Log 파싱/저장 실패: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()
