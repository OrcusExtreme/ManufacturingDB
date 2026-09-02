import os
import re
import datetime
import pandas as pd
from DB.database import SessionLocal
from DB.models import Job, Workplan
from nptdms import TdmsFile

from job_manager import get_or_create_job

from vault_manager import save_to_vault

def parse_tdms(file_path, job_id):
    """
    TDMS 파일 처리를 담당하는 모듈.
    무거운 파싱을 피하기 위해 원본 대신 read_metadata()를 사용해 
    가공 시작 일시 등 메타데이터만 0.1초만에 스캔하여 DB를 보완하고 파일 경로를 매핑합니다.
    동시에 원본 TDMS 파일을 archive_vault에 안전하게 복사 백업합니다.
    """
    print(f"  -> [TDMS Parser] 파일: {os.path.basename(file_path)} / Job ID: {job_id}")
    
    db = SessionLocal()
    try:
        # Job 가져오기 또는 생성 (XML 독립)
        job = get_or_create_job(db, job_id)

        # 1. 파일명에서 타임스탬프 (YYMMDDHHMMSS) 1차 추출
        fname = os.path.basename(file_path)
        m = re.search(r'__(\d{12})\.tdms', fname, re.IGNORECASE)
        if m:
            ts_str = m.group(1)
            try:
                dt = datetime.datetime.strptime(f"20{ts_str}", "%Y%m%d%H%M%S")
                if job.start_time is None:
                    job.start_time = dt
                    print(f"    - [TDMS Parser] 파일명에서 가공 일시 추출 반영: {dt}")
            except Exception:
                pass

        # 2. 메타데이터 초고속 추출 로직
        try:
            with TdmsFile.read_metadata(file_path) as tdms_file:
                if len(tdms_file.groups()) > 0:
                    cnc_group = tdms_file.groups()[0]
                    target_ch = cnc_group['CNC-ProgramName'] if 'CNC-ProgramName' in cnc_group else (cnc_group.channels()[0] if len(cnc_group.channels()) > 0 else None)
                    
                    if target_ch:
                        props = target_ch.properties
                        
                        # wf_start_time 추출
                        wf_start_time = props.get('wf_start_time')
                        if wf_start_time is not None and job.start_time is None:
                            try:
                                dt = pd.to_datetime(wf_start_time).to_pydatetime()
                                job.start_time = dt
                                print(f"    - [TDMS Parser] wf_start_time 메타데이터 추출 반영: {dt}")
                            except Exception:
                                pass
                                
                        # ProgramName, MaterialCode 추출 및 Workplan/Part 보완
                        prog_name = props.get('ProgramName')
                        mat_code = props.get('MaterialCode')
                        
                        if job.workplan_id is not None:
                            workplan = db.query(Workplan).filter_by(workplan_id=job.workplan_id).first()
                            if workplan:
                                if prog_name and (not workplan.program_code or workplan.program_code == 'Unknown'):
                                    workplan.program_code = prog_name
                                    
                                if mat_code and workplan.part_code:
                                    from DB.models import Part
                                    part = db.query(Part).filter_by(part_code=workplan.part_code).first()
                                    if part and (not part.material_code or part.material_code == 'Unknown'):
                                        part.material_code = mat_code
        except Exception as meta_e:
            print(f"    - [경고] TDMS 메타데이터 고속 스캔 중 오류: {meta_e}")
        # -------------------------------------------

        # 파일 경로 업데이트
        from vault_manager import get_rel_raw_data_path
        job.tdms_file_path = get_rel_raw_data_path(file_path)
        
        # Vault에 TDMS 원본 백업
        tdms_vault_rel = save_to_vault(file_path, "tdms_files", f"Job_{job.job_id}", os.path.basename(file_path))
        
        db.commit()
        print(f"    - TDMS 메타데이터 파싱, Vault 백업({tdms_vault_rel}) 및 파일 참조 매핑 완료 (내부 PK: {job.job_id})")
        
        # Parquet 시각화 데이터 즉시 연동 생성
        try:
            from tdms_visualizer import process_tdms_file
            res = process_tdms_file(job)
            if res and res[0]:
                job.tdms_parquet_path = res[0]
                job.tdms_fft_parquet_path = res[1]
                db.commit()
                print(f"    - TDMS 시각화 Parquet 즉시 생성 완료: {res[0]}")
        except Exception as v_err:
            print(f"    - [Visualizer 즉시 연동 경고]: {v_err}")
            
        return True

    except Exception as e:
        db.rollback()
        print(f"    - [오류] TDMS 참조 업데이트 실패: {e}")
        return False
    finally:
        db.close()
