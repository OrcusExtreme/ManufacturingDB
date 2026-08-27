import os
import csv
from DB.database import SessionLocal
from DB.models import Job, MachineLog, LogFileArchive
from vault_manager import save_to_vault

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
            
            for row in reader:
                load = safe_float(row.get('cls', 0))
                rpm = safe_float(row.get('crpm', 0))
                feed = safe_float(row.get('cfr', 0))
                
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
        
        # Job 테이블에 원본 파일 경로 매핑
        job.log_file_path = file_path
        
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
