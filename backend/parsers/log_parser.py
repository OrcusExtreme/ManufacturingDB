import os
import csv
import re
import datetime
from DB.database import SessionLocal
from DB.models import Job, MachineLog, LogFileArchive
from vault_manager import save_to_vault

# CNC 1Hz 로그를 구분하기 위한 헤더 표식.
# 같은 Job 폴더에는 CNC 로그 말고도 .log 확장자를 쓰는 파일이 떨어질 수 있다
# (실제로 NI TDMS 라이브러리가 "TDS Exception in Initialize..." 오류를 UTF-16 .log 로 남긴다).
# 그런 파일을 그대로 받아들이면 job.log_file_path 가 엉뚱한 파일을 가리키고
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

from job_manager import get_or_create_job

def parse_log(file_path, job_id):
    """
    Log 또는 CSV 파일을 파싱하여 DB에 요약 통계를 저장하는 모듈
    (CSV 포맷으로 가정: cls, crpm, cfr, alarm_msg)
    시계열 데이터를 모두 넣지 않고 요약 통계만 MachineLog에 저장하며,
    원본 파일은 Vault에 아카이빙합니다.
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

        f = None
        try:
            with open(file_path, 'r', encoding='utf-8') as test_f:
                test_f.read(8192) # Test read a larger chunk
            f = open(file_path, 'r', encoding='utf-8', errors='ignore')
        except UnicodeDecodeError:
            f = open(file_path, 'r', encoding='cp949', errors='ignore')
            
        max_load = 0.0
        max_rpm = 0.0
        max_feed = 0.0
        total_rpm = 0.0
        row_count = 0
        alarm_msgs = set()
        
        # 가공 메타데이터 Fallback 산출용 변수
        first_time_str = None
        last_time_str = None
        max_ctime = 0.0
        cutting_row_count = 0
        total_dist_3d = 0.0
        cutting_dist_3d = 0.0
        prev_x = None
        prev_y = None
        prev_z = None

        with f:
            sample = f.read(1024)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters='\t,')
                reader = csv.DictReader(f, dialect=dialect)
            except Exception:
                # Fallback: 탭이 있으면 탭, 없으면 콤마로 설정
                delimiter = '\t' if '\t' in sample else ','
                reader = csv.DictReader(f, delimiter=delimiter)

            if not looks_like_cnc_log(reader.fieldnames):
                preview = ", ".join((reader.fieldnames or [])[:5]) or "(헤더 없음)"
                print(f"    - [건너뜀] CNC 1Hz 로그 형식이 아니라 무시합니다 "
                      f"({os.path.basename(file_path)} / 열: {preview})")
                return False

            for row in reader:
                # 타임스탬프
                row_t = row.get('time', '').strip()
                if row_t:
                    if first_time_str is None:
                        first_time_str = row_t
                    last_time_str = row_t
                    
                # ctime
                ctime_val = safe_float(row.get('ctime', 0))
                if ctime_val > max_ctime:
                    max_ctime = ctime_val
                    
                load = safe_float(row.get('cls', 0))
                rpm = safe_float(row.get('crpm', 0))
                feed = safe_float(row.get('cfr', 0))
                
                # 절삭 상태 판별
                cut_flag = str(row.get('cut', '0')).strip()
                is_cutting = (cut_flag == '1') or (rpm > 100 and feed > 0)
                if is_cutting:
                    cutting_row_count += 1
                
                # 3D 좌표 이동거리 적분 (cpx, cpy, cpz)
                if 'cpx' in row and 'cpy' in row and 'cpz' in row:
                    px = safe_float(row.get('cpx', 0))
                    py = safe_float(row.get('cpy', 0))
                    pz = safe_float(row.get('cpz', 0))
                    if prev_x is not None:
                        d_step = ((px - prev_x)**2 + (py - prev_y)**2 + (pz - prev_z)**2)**0.5
                        total_dist_3d += d_step
                        if is_cutting:
                            cutting_dist_3d += d_step
                    prev_x, prev_y, prev_z = px, py, pz

                # 전류 데이터가 있을 경우 추출 (컬럼명이 curr 또는 current 인 경우)
                current = safe_float(row.get('curr', row.get('current', 0)))
                
                alarm = row.get('alarm_msg', '').strip()
                
                if load > max_load:
                    max_load = load
                if rpm > max_rpm:
                    max_rpm = rpm
                if feed > max_feed:
                    max_feed = feed
                    
                total_rpm += rpm
                row_count += 1
                
                if alarm and alarm.lower() not in ['none', 'null', '0', '']:
                    alarm_msgs.add(alarm)
                    
        avg_rpm = (total_rpm / row_count) if row_count > 0 else 0.0
        alarm_count = len(alarm_msgs)
        critical_alarms = ", ".join(list(alarm_msgs)) if alarm_msgs else None
        
        # 3단계 Fallback: Job 메타데이터 보완 (start_time, end_time, cutting_seconds, moving_distance)
        if job.start_time is None and first_time_str:
            try:
                t_clean = first_time_str.split('.')[0].strip()
                job.start_time = datetime.datetime.strptime(t_clean, "%y%m%d %H:%M:%S")
                print(f"    - [Log Parser] time 컬럼에서 start_time 설정: {job.start_time}")
            except Exception:
                pass
                
        if job.end_time is None and last_time_str:
            try:
                t_clean = last_time_str.split('.')[0].strip()
                job.end_time = datetime.datetime.strptime(t_clean, "%y%m%d %H:%M:%S")
                print(f"    - [Log Parser] time 컬럼에서 end_time 설정: {job.end_time}")
            except Exception:
                pass

        if job.cutting_seconds is None and (max_ctime > 0 or cutting_row_count > 0):
            job.cutting_seconds = round(max_ctime if max_ctime > 0 else cutting_row_count * 0.1, 1)
            print(f"    - [Log Parser] ctime/cut 기반 cutting_seconds 설정: {job.cutting_seconds} 초")
            
        if job.moving_distance is None and total_dist_3d > 0:
            job.moving_distance = round(total_dist_3d, 1)
            print(f"    - [Log Parser] cpx/cpy/cpz 3D 궤적 기반 moving_distance 설정: {job.moving_distance} mm")
            if job.cutting_moving_distance is None and cutting_dist_3d > 0:
                job.cutting_moving_distance = round(cutting_dist_3d, 1)
                print(f"    - [Log Parser] cpx/cpy/cpz 3D 궤적 기반 cutting_moving_distance 설정: {job.cutting_moving_distance} mm")

        # Job 테이블에 원본 파일 경로 매핑
        from vault_manager import get_rel_raw_data_path
        job.log_file_path = get_rel_raw_data_path(file_path)
        
        # MachineLog 요약 정보 삽입 또는 업데이트
        existing_log = db.query(MachineLog).filter(MachineLog.job_id == job.job_id).first()
        if existing_log:
            existing_log.max_spindle_load = max_load
            existing_log.max_spindle_rpm = max_rpm
            existing_log.max_feed_rate = max_feed
            existing_log.avg_spindle_rpm = avg_rpm
            existing_log.alarm_count = alarm_count
            existing_log.critical_alarm_msg = critical_alarms
            db.flush()
            target_log_id = existing_log.log_id
        else:
            m_log = MachineLog(
                job_id=job.job_id,
                max_spindle_load=max_load,
                max_spindle_rpm=max_rpm,
                max_feed_rate=max_feed,
                avg_spindle_rpm=avg_rpm,
                alarm_count=alarm_count,
                critical_alarm_msg=critical_alarms
            )
            db.add(m_log)
            db.flush()
            target_log_id = m_log.log_id
            
        # Save to Vault
        log_vault_path = save_to_vault(file_path, "machine_logs", f"Job_{job.job_id}", os.path.basename(file_path))
            
        archive = db.query(LogFileArchive).filter_by(log_id=target_log_id).first()
        if not archive:
            archive = LogFileArchive(log_id=target_log_id, log_file_path=log_vault_path)
            db.add(archive)
        else:
            archive.log_file_path = log_vault_path
            
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
