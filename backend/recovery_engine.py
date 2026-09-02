import os
import json
import zipfile
import io
import shutil
import tempfile
from sqlalchemy.orm import Session
from DB.database import SessionLocal, engine
from DB.models import (
    Job, JobFileArchive, WorkplanFileArchive, 
    LogFileArchive, SurfaceRoughness, SurfaceRoughnessArchive,
    EnvMemo, Inspection, CadFileArchive, Part, Workplan
)
from vault_manager import get_abs_vault_path, VAULT_ROOT, get_abs_raw_data_path, get_rel_raw_data_path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "machining_raw_data")

def _copy_from_vault_or_blob(vault_path, blob_content, destination_path):
    """
    Vault 경로에서 파일을 복사하거나, 없으면 DB BLOB 바이너리에서 복원합니다.
    """
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    
    # 1. Vault 시도
    if vault_path:
        src = get_abs_vault_path(vault_path)
        if src and os.path.exists(src):
            shutil.copy2(src, destination_path)
            return True
            
    # 2. BLOB 시도
    if blob_content:
        with open(destination_path, "wb") as f:
            f.write(blob_content)
        return True
        
    return False

def get_job_archive_files(job_id):
    """
    특정 Job ID에 대해 Vault 및 DB에 보관된 모든 아카이브 파일 목록 및 경로를 반환합니다.
    (사용자 대시보드 다운로드 탭에서 실시간 접근용)
    """
    with Session(engine) as session:
        job = session.query(Job).filter_by(job_id=job_id).first()
        if not job:
            return {}
            
        file_map = {
            'xml': [],
            'nc': [],
            'tdms': [],
            'log': [],
            'roughness': [],
            'parquet': [],
            'cad': []
        }
        
        # 1. XML
        job_archive = session.query(JobFileArchive).filter_by(job_id=job.job_id).first()
        if job_archive and job_archive.xml_file_path:
            abs_p = get_abs_vault_path(job_archive.xml_file_path)
            if abs_p and os.path.exists(abs_p):
                file_map['xml'].append((abs_p, os.path.basename(abs_p), "metadata.xml"))
                
        # 2. NC
        if job.workplan_id:
            wp_archive = session.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
            if wp_archive and wp_archive.nc_file_path:
                abs_p = get_abs_vault_path(wp_archive.nc_file_path)
                if abs_p and os.path.exists(abs_p):
                    file_map['nc'].append((abs_p, os.path.basename(abs_p), f"{job.workplan_id}.nc"))
                    
        # 3. TDMS
        # Vault 내 tdms_files/Job_{id} 탐색
        tdms_vault_dir = os.path.join(VAULT_ROOT, "tdms_files", f"Job_{job.job_id}")
        if os.path.exists(tdms_vault_dir):
            for f in os.listdir(tdms_vault_dir):
                if f.endswith('.tdms'):
                    abs_p = os.path.join(tdms_vault_dir, f)
                    file_map['tdms'].append((abs_p, f, f))
        elif job.tdms_file_path:
            abs_tdms_path = get_abs_raw_data_path(job.tdms_file_path)
            if abs_tdms_path and os.path.exists(abs_tdms_path):
                file_map['tdms'].append((abs_tdms_path, os.path.basename(abs_tdms_path), os.path.basename(abs_tdms_path)))
            
        # 4. Logs
        for mlog in job.machine_logs:
            log_archive = session.query(LogFileArchive).filter_by(log_id=mlog.log_id).first()
            if log_archive and log_archive.log_file_path:
                abs_p = get_abs_vault_path(log_archive.log_file_path)
                if abs_p and os.path.exists(abs_p):
                    file_map['log'].append((abs_p, os.path.basename(abs_p), os.path.basename(abs_p)))
                    
        # 5. Surface Roughness
        for rr in job.surface_roughnesses:
            sr_archive = session.query(SurfaceRoughnessArchive).filter_by(roughness_id=rr.roughness_id).first()
            if sr_archive:
                for vp, default_name in [
                    (sr_archive.stat_csv_file_path, f"{rr.measure_name}_stat.csv"),
                    (sr_archive.curve_csv_file_path, f"{rr.measure_name}_curve.csv")
                ]:
                    if vp:
                        abs_p = get_abs_vault_path(vp)
                        if abs_p and os.path.exists(abs_p):
                            file_map['roughness'].append((abs_p, os.path.basename(abs_p), f"Surface_Roughness/{os.path.basename(abs_p)}"))
                            
        # 6. Parquet
        if job.tdms_parquet_path:
            abs_parquet = get_abs_raw_data_path(job.tdms_parquet_path)
            if abs_parquet and os.path.exists(abs_parquet):
                file_map['parquet'].append((abs_parquet, os.path.basename(abs_parquet), os.path.basename(abs_parquet)))
        if job.tdms_fft_parquet_path:
            abs_fft = get_abs_raw_data_path(job.tdms_fft_parquet_path)
            if abs_fft and os.path.exists(abs_fft):
                file_map['parquet'].append((abs_fft, os.path.basename(abs_fft), os.path.basename(abs_fft)))
            
        # 7. etc (기타 참고용 파일)
        etc_vault_dir = os.path.join(VAULT_ROOT, "etc_files", f"Job_{job.job_id}")
        if os.path.exists(etc_vault_dir):
            for f in os.listdir(etc_vault_dir):
                abs_p = os.path.join(etc_vault_dir, f)
                file_map.setdefault('etc', []).append((abs_p, f, f"etc/{f}"))
                
        return file_map

def restore_single_job_to_raw_data(job_id_or_source_folder):
    """
    삭제된 단일 Job 폴더를 machining_raw_data 디렉터리에 자동으로 원상 복구합니다.
    (XML, NC, TDMS, Log, 표면 조도 CSV, etc 참고용 파일, CAD 파일 일괄 복구)
    """
    with Session(engine) as session:
        if isinstance(job_id_or_source_folder, int) or (isinstance(job_id_or_source_folder, str) and job_id_or_source_folder.isdigit()):
            job = session.query(Job).filter_by(job_id=int(job_id_or_source_folder)).first()
        else:
            job = session.query(Job).filter_by(source_folder=str(job_id_or_source_folder)).first()
            
        if not job:
            return None, 0
            
        folder_name = job.source_folder
        if not folder_name:
            proj = job.research_project or "Unknown"
            part = job.custom_part_name or "Unknown"
            folder_name = f"{proj}/{part}/{job.job_id}"
            
        dest_dir = os.path.join(RAW_DATA_DIR, *folder_name.split('/'))
        os.makedirs(dest_dir, exist_ok=True)
        
        restored_files = 0
        
        # 1. XML 복원
        job_archive = session.query(JobFileArchive).filter_by(job_id=job.job_id).first()
        if job_archive:
            xml_dest = os.path.join(dest_dir, "metadata.xml")
            if _copy_from_vault_or_blob(job_archive.xml_file_path, job_archive.xml_file_content, xml_dest):
                restored_files += 1
                
        # 2. NC 복원
        if job.workplan_id:
            wp_archive = session.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
            if wp_archive:
                nc_name = os.path.basename(wp_archive.nc_file_path) if wp_archive.nc_file_path else f"{job.workplan_id}.nc"
                nc_dest = os.path.join(dest_dir, nc_name)
                if _copy_from_vault_or_blob(wp_archive.nc_file_path, wp_archive.nc_file_content, nc_dest):
                    restored_files += 1
                    
        # 3. TDMS 복원
        tdms_vault_dir = os.path.join(VAULT_ROOT, "tdms_files", f"Job_{job.job_id}")
        if os.path.exists(tdms_vault_dir):
            for f in os.listdir(tdms_vault_dir):
                src = os.path.join(tdms_vault_dir, f)
                dest = os.path.join(dest_dir, f)
                if not os.path.exists(dest):
                    shutil.copy2(src, dest)
                    restored_files += 1
                
        # 4. Logs 복원
        for mlog in job.machine_logs:
            log_archive = session.query(LogFileArchive).filter_by(log_id=mlog.log_id).first()
            if log_archive and log_archive.log_file_path:
                src = get_abs_vault_path(log_archive.log_file_path)
                if src and os.path.exists(src):
                    dest = os.path.join(dest_dir, os.path.basename(src))
                    if not os.path.exists(dest):
                        shutil.copy2(src, dest)
                        restored_files += 1
                    
        # 5. Surface Roughness 복원 (DB 아카이브 + Vault 폴더 전체)
        sr_dir = os.path.join(dest_dir, "Surface_Roughness")
        roughness_records = session.query(SurfaceRoughness).filter_by(job_id=job.job_id).all()
        if roughness_records:
            os.makedirs(sr_dir, exist_ok=True)
            for rr in roughness_records:
                sr_archive = session.query(SurfaceRoughnessArchive).filter_by(roughness_id=rr.roughness_id).first()
                if sr_archive:
                    for vp in [sr_archive.stat_csv_file_path, sr_archive.curve_csv_file_path]:
                        if vp:
                            src = get_abs_vault_path(vp)
                            if src and os.path.exists(src):
                                dest = os.path.join(sr_dir, os.path.basename(src))
                                if not os.path.exists(dest):
                                    shutil.copy2(src, dest)
                                    restored_files += 1
                                    
        sr_vault_dir = os.path.join(VAULT_ROOT, "surface_roughness", f"Job_{job.job_id}")
        if os.path.exists(sr_vault_dir):
            os.makedirs(sr_dir, exist_ok=True)
            for f in os.listdir(sr_vault_dir):
                if f.lower().endswith(('.csv', '.fpk', '.txt')):
                    src = os.path.join(sr_vault_dir, f)
                    dest = os.path.join(sr_dir, f)
                    if not os.path.exists(dest):
                        shutil.copy2(src, dest)
                        restored_files += 1
                        
        # 6. etc (기타 참고용 파일) 복원
        etc_vault_dir = os.path.join(VAULT_ROOT, "etc_files", f"Job_{job.job_id}")
        etc_job_dir = os.path.join(VAULT_ROOT, "jobs", folder_name, "etc")
        etc_dest_dir = os.path.join(dest_dir, "etc")
        
        for v_etc in [etc_vault_dir, etc_job_dir]:
            if os.path.exists(v_etc):
                for f in os.listdir(v_etc):
                    src = os.path.join(v_etc, f)
                    if os.path.isfile(src):
                        os.makedirs(etc_dest_dir, exist_ok=True)
                        dest = os.path.join(etc_dest_dir, f)
                        if not os.path.exists(dest):
                            shutil.copy2(src, dest)
                            restored_files += 1

        # 7. Part 레벨 CAD 파일 복원 (CAD_Files가 유실된 경우)
        if job.workplan_id:
            wp = session.query(Workplan).filter_by(workplan_id=job.workplan_id).first()
            if wp and wp.part_code:
                cad_archive = session.query(CadFileArchive).filter_by(part_code=wp.part_code).first()
                if cad_archive:
                    part_folder_parts = folder_name.split('/')[:2]
                    if len(part_folder_parts) == 2:
                        cad_dest_dir = os.path.join(RAW_DATA_DIR, part_folder_parts[0], part_folder_parts[1], "CAD_Files")
                        cad_fname = cad_archive.file_name or f"{wp.part_code}.step"
                        cad_dest = os.path.join(cad_dest_dir, cad_fname)
                        if not os.path.exists(cad_dest):
                            if _copy_from_vault_or_blob(cad_archive.file_path, cad_archive.file_content, cad_dest):
                                restored_files += 1

        # 8. Parquet 시각화 데이터 복원 (processed_parquet)
        parquet_dir = os.path.join(dest_dir, "processed_parquet")
        for p_name in [f"job_{job.job_id}_viz.parquet", f"job_{job.job_id}_fft.parquet"]:
            v_p = os.path.join(VAULT_ROOT, "processed_parquet", p_name)
            p_p = os.path.join(PROJECT_ROOT, "processed_data", p_name)
            src_p = v_p if os.path.exists(v_p) else (p_p if os.path.exists(p_p) else None)
            if src_p:
                os.makedirs(parquet_dir, exist_ok=True)
                dest_p = os.path.join(parquet_dir, p_name)
                if not os.path.exists(dest_p):
                    shutil.copy2(src_p, dest_p)
                    restored_files += 1
                                
        print(f"[Recovery Engine] Job {job.job_id} ({dest_dir})에 총 {restored_files}개 파일 자동 복원 완료.")
        return dest_dir, restored_files

def recover_all_jobs(base_output_dir="recovered_data"):
    """
    DB에 저장된 모든 Job과 연관된 원본 파일을 지정된 디렉토리로 일괄 복구합니다.
    """
    if not os.path.exists(base_output_dir):
        os.makedirs(base_output_dir)
        
    recovered_count = 0
    with Session(engine) as session:
        jobs = session.query(Job).all()
        for job in jobs:
            folder_name = job.source_folder or f"Job_{job.job_id}"
            job_dir = os.path.join(base_output_dir, folder_name)
            os.makedirs(job_dir, exist_ok=True)
            
            # Metadata export
            metadata = {
                "job_id": job.job_id,
                "research_project": job.research_project,
                "custom_part_name": job.custom_part_name,
                "machining_type": job.machining_type,
                "env_memos": [],
                "inspections": []
            }
            if job.env_memo:
                memo = job.env_memo
                metadata["env_memos"].append({
                    "worker_name": memo.worker_name,
                    "temperature": memo.temperature,
                    "humidity": memo.humidity,
                    "day_of_week": memo.day_of_week,
                    "chip_shape": memo.chip_shape,
                    "abnormal_noise": memo.abnormal_noise,
                    "free_memo": memo.free_memo
                })
            if job.inspection:
                ins = job.inspection
                metadata["inspections"].append({
                    "dimension_tolerance": ins.dimension_tolerance,
                    "surface_roughness_ra": ins.surface_roughness_ra,
                    "surface_roughness_rz": ins.surface_roughness_rz,
                    "shape_accuracy": ins.shape_accuracy,
                    "pass_fail": ins.pass_fail
                })
            with open(os.path.join(job_dir, "metadata_export.json"), "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
                
            # Files
            job_archive = session.query(JobFileArchive).filter_by(job_id=job.job_id).first()
            if job_archive:
                _copy_from_vault_or_blob(job_archive.xml_file_path, job_archive.xml_file_content, os.path.join(job_dir, "metadata.xml"))
            if job.workplan_id:
                wp_archive = session.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
                if wp_archive:
                    _copy_from_vault_or_blob(wp_archive.nc_file_path, wp_archive.nc_file_content, os.path.join(job_dir, f"{job.workplan_id}.nc"))
            for mlog in job.machine_logs:
                log_archive = session.query(LogFileArchive).filter_by(log_id=mlog.log_id).first()
                if log_archive and log_archive.log_file_path:
                    src = get_abs_vault_path(log_archive.log_file_path)
                    if src and os.path.exists(src):
                        shutil.copy2(src, os.path.join(job_dir, os.path.basename(src)))
            for rr in job.surface_roughnesses:
                sr_archive = session.query(SurfaceRoughnessArchive).filter_by(roughness_id=rr.roughness_id).first()
                if sr_archive:
                    sr_dir = os.path.join(job_dir, "Surface_Roughness")
                    os.makedirs(sr_dir, exist_ok=True)
                    for vp in [sr_archive.stat_csv_file_path, sr_archive.curve_csv_file_path]:
                        if vp:
                            src = get_abs_vault_path(vp)
                            if src and os.path.exists(src):
                                shutil.copy2(src, os.path.join(sr_dir, os.path.basename(src)))
            recovered_count += 1
            
    return recovered_count

def create_recovery_zip():
    """
    전체 시스템 복구 ZIP 생성 (Streamlit 다운로드용)
    """
    temp_dir = tempfile.mkdtemp()
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_zip_path = temp_zip.name
    temp_zip.close()
    
    try:
        recover_all_jobs(base_output_dir=temp_dir)
        with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, temp_dir)
                    zf.write(abs_path, rel_path)
        return temp_zip_path
    finally:
        shutil.rmtree(temp_dir)

def create_single_job_zip(job_id, categories=None):
    """
    특정 Job ID에 대해 Vault/DB 아카이브로부터 즉석에서 ZIP 파일을 생성합니다.
    (machining_raw_data 폴더가 없어도 100% 정상 작동)
    """
    file_map = get_job_archive_files(job_id)
    if not file_map:
        return None
        
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_zip_path = temp_zip.name
    temp_zip.close()
    
    cat_keys = []
    if not categories or '전체' in categories:
        cat_keys = list(file_map.keys())
    else:
        for cat in categories:
            if '표면 조도' in cat or 'roughness' in cat.lower(): cat_keys.append('roughness')
            elif '고주파' in cat or 'tdms' in cat.lower(): cat_keys.append('tdms')
            elif 'NC' in cat or 'nc' in cat.lower(): cat_keys.append('nc')
            elif '메타데이터' in cat or 'xml' in cat.lower(): cat_keys.append('xml')
            elif 'Parquet' in cat or 'parquet' in cat.lower(): cat_keys.append('parquet')
            elif '로그' in cat or 'log' in cat.lower(): cat_keys.append('log')
            
    with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        added = 0
        for ck in cat_keys:
            items = file_map.get(ck, [])
            for abs_path, base_name, arcname in items:
                if os.path.exists(abs_path):
                    try:
                        zf.write(abs_path, arcname=arcname)
                        added += 1
                    except Exception as we:
                        print(f"[Warning] Failed to write {abs_path} into zip: {we}")
                    
    if added == 0:
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
        return None
        
    return temp_zip_path

def backfill_missing_job_metadata():
    """
    DB에 등록된 모든 Job 중 start_time, end_time, cutting_seconds, moving_distance,
    log_file_path, tdms_parquet_path 등이 누락된 건을
    1) XML(기존 DB) -> 2) TDMS/Parquet -> 3) LOG 순서의 3단계 Fallback으로 자동 역추적 및 보정(Backfill)합니다.
    """
    import re
    import csv
    import datetime
    import pandas as pd
    import numpy as np
    
    updated_count = 0
    with Session(engine) as session:
        jobs = session.query(Job).all()
        for job in jobs:
            modified = False
            
            # --- 2순위: TDMS / Parquet 파일 기반 Fallback ---
            candidate_pqs = [
                job.tdms_parquet_path,
                os.path.join(PROJECT_ROOT, "processed_data", f"job_{job.job_id}_viz.parquet"),
                get_abs_vault_path(f"processed_parquet/job_{job.job_id}_viz.parquet")
            ]
            
            valid_pq = None
            for cpq in candidate_pqs:
                if cpq and os.path.exists(cpq):
                    valid_pq = cpq
                    break
                    
            if valid_pq:
                if not job.tdms_parquet_path or not os.path.exists(get_abs_raw_data_path(job.tdms_parquet_path)):
                    job.tdms_parquet_path = get_rel_raw_data_path(valid_pq)
                    modified = True
                    
                try:
                    df = pd.read_parquet(valid_pq)
                    
                    # 1. 일시 (start_time, end_time)
                    time_col = None
                    for col in ['Time Channel CNC', 'Timestamp', 'time']:
                        if col in df.columns:
                            time_col = col
                            break
                    if time_col is not None:
                        valid_times = pd.to_datetime(df[time_col]).dropna()
                        if not valid_times.empty:
                            if job.start_time is None:
                                job.start_time = valid_times.iloc[0].to_pydatetime()
                                modified = True
                                print(f"  [Backfill] Job {job.job_id}: Parquet에서 start_time 복구 -> {job.start_time}")
                            if job.end_time is None:
                                job.end_time = valid_times.iloc[-1].to_pydatetime()
                                modified = True
                                print(f"  [Backfill] Job {job.job_id}: Parquet에서 end_time 복구 -> {job.end_time}")
                                
                            # 2. 가공 시간 (cutting_seconds)
                            if job.cutting_seconds is None:
                                dt_series = pd.to_datetime(df[time_col]).diff().dt.total_seconds().fillna(1.0)
                                dt_series[dt_series > 10] = 1.0
                                rpm = df['CNC-Z-SpindleSpeed'] if 'CNC-Z-SpindleSpeed' in df.columns else 0
                                feed = df['CNC-ActualFeedRate'] if 'CNC-ActualFeedRate' in df.columns else 0
                                is_cut = (rpm > 100) & (feed > 0)
                                if is_cut.any():
                                    job.cutting_seconds = round(float(dt_series[is_cut].sum()), 1)
                                else:
                                    job.cutting_seconds = round(float(dt_series.sum()), 1)
                                modified = True
                                print(f"  [Backfill] Job {job.job_id}: Parquet에서 cutting_seconds 복구 -> {job.cutting_seconds} 초")
                                
                    # 3. 이동 거리 (moving_distance, cutting_moving_distance)
                    if job.moving_distance is None:
                        if {'CNC-X-Position', 'CNC-Y-Position', 'CNC-Z-Position'}.issubset(df.columns):
                            dx = df['CNC-X-Position'].diff().fillna(0)
                            dy = df['CNC-Y-Position'].diff().fillna(0)
                            dz = df['CNC-Z-Position'].diff().fillna(0)
                            dist_3d = np.sqrt(dx**2 + dy**2 + dz**2)
                            job.moving_distance = round(float(dist_3d.sum()), 1)
                            modified = True
                            print(f"  [Backfill] Job {job.job_id}: Parquet에서 moving_distance 복구 -> {job.moving_distance} mm")
                            
                            rpm = df['CNC-Z-SpindleSpeed'] if 'CNC-Z-SpindleSpeed' in df.columns else 0
                            feed = df['CNC-ActualFeedRate'] if 'CNC-ActualFeedRate' in df.columns else 0
                            is_cut = (rpm > 100) & (feed > 0)
                            if is_cut.any() and job.cutting_moving_distance is None:
                                job.cutting_moving_distance = round(float(dist_3d[is_cut].sum()), 1)
                                print(f"  [Backfill] Job {job.job_id}: Parquet에서 cutting_moving_distance 복구 -> {job.cutting_moving_distance} mm")
                except Exception as pe:
                    print(f"  [Backfill 경고] Job {job.job_id} Parquet 읽기 오류: {pe}")

            # TDMS 원본 파일 탐색 및 경로 보완
            tdms_vault_dir = os.path.join(VAULT_ROOT, "tdms_files", f"Job_{job.job_id}")
            if os.path.exists(tdms_vault_dir):
                for f in os.listdir(tdms_vault_dir):
                    if f.endswith('.tdms'):
                        abs_tdms = os.path.join(tdms_vault_dir, f)
                        if not job.tdms_file_path:
                            job.tdms_file_path = get_rel_raw_data_path(abs_tdms)
                            modified = True
                        if job.start_time is None:
                            m = re.search(r'__(\d{12})\.tdms', f)
                            if m:
                                ts_str = m.group(1)
                                try:
                                    dt = datetime.datetime.strptime(f"20{ts_str}", "%Y%m%d%H%M%S")
                                    job.start_time = dt
                                    modified = True
                                    print(f"  [Backfill] Job {job.job_id}: TDMS 파일명에서 start_time 복구 -> {dt}")
                                except Exception:
                                    pass
                        break

            # --- 3순위: LOG 파일 기반 Fallback ---
            vault_log_dir = os.path.join(VAULT_ROOT, "machine_logs", f"Job_{job.job_id}")
            log_candidates = []
            if job.log_file_path:
                abs_log = get_abs_raw_data_path(job.log_file_path)
                if abs_log and os.path.exists(abs_log):
                    log_candidates.append(abs_log)
            if os.path.exists(vault_log_dir):
                for f in os.listdir(vault_log_dir):
                    log_candidates.append(os.path.join(vault_log_dir, f))
                    
            if log_candidates:
                target_log_f = log_candidates[0]
                if not job.log_file_path or not os.path.exists(get_abs_raw_data_path(job.log_file_path)):
                    job.log_file_path = get_rel_raw_data_path(target_log_f)
                    modified = True
                    
                # 파일명 타임스탬프 (KST)
                if job.start_time is None:
                    for lf in log_candidates:
                        m = re.search(r'__(\d{12})(?:_ext)?\.log', os.path.basename(lf))
                        if m:
                            ts_str = m.group(1)
                            try:
                                dt = datetime.datetime.strptime(f"20{ts_str}", "%Y%m%d%H%M%S")
                                job.start_time = dt
                                modified = True
                                print(f"  [Backfill] Job {job.job_id}: 로그 파일명에서 start_time 복구 -> {dt}")
                                break
                            except Exception:
                                pass
                                
                # 로그 내용 파싱 (moving_distance / cutting_seconds / start_time / end_time 누락 시)
                if (job.moving_distance is None or job.cutting_seconds is None or 
                    job.start_time is None or job.end_time is None) and os.path.exists(target_log_f):
                    try:
                        f_enc = 'utf-8'
                        try:
                            with open(target_log_f, 'r', encoding='utf-8') as tf:
                                tf.read(2048)
                        except UnicodeDecodeError:
                            f_enc = 'cp949'
                            
                        first_t = None
                        last_t = None
                        max_ctime = 0.0
                        cutting_rows = 0
                        total_log_dist = 0.0
                        cutting_log_dist = 0.0
                        px_prev, py_prev, pz_prev = None, None, None
                        
                        with open(target_log_f, 'r', encoding=f_enc, errors='ignore') as lf_in:
                            sample = lf_in.read(1024)
                            lf_in.seek(0)
                            delim = '\t' if '\t' in sample else ','
                            reader = csv.DictReader(lf_in, delimiter=delim)
                            
                            for row in reader:
                                row_t = row.get('time', '').strip()
                                if row_t:
                                    if first_t is None: first_t = row_t
                                    last_t = row_t
                                    
                                ctime_val = float(row.get('ctime', 0) or 0)
                                if ctime_val > max_ctime: max_ctime = ctime_val
                                
                                rpm = float(row.get('crpm', 0) or 0)
                                feed = float(row.get('cfr', 0) or 0)
                                is_cut = (str(row.get('cut', '0')).strip() == '1') or (rpm > 100 and feed > 0)
                                if is_cut: cutting_rows += 1
                                
                                if 'cpx' in row and 'cpy' in row and 'cpz' in row:
                                    x_cur = float(row.get('cpx', 0) or 0)
                                    y_cur = float(row.get('cpy', 0) or 0)
                                    z_cur = float(row.get('cpz', 0) or 0)
                                    if px_prev is not None:
                                        step_d = ((x_cur - px_prev)**2 + (y_cur - py_prev)**2 + (z_cur - pz_prev)**2)**0.5
                                        total_log_dist += step_d
                                        if is_cut: cutting_log_dist += step_d
                                    px_prev, py_prev, pz_prev = x_cur, y_cur, z_cur
                                    
                        if job.start_time is None and first_t:
                            try:
                                job.start_time = datetime.datetime.strptime(first_t.split('.')[0].strip(), "%y%m%d %H:%M:%S")
                                modified = True
                            except Exception: pass
                            
                        if job.end_time is None and last_t:
                            try:
                                job.end_time = datetime.datetime.strptime(last_t.split('.')[0].strip(), "%y%m%d %H:%M:%S")
                                modified = True
                            except Exception: pass
                            
                        if job.cutting_seconds is None and (max_ctime > 0 or cutting_rows > 0):
                            job.cutting_seconds = round(max_ctime if max_ctime > 0 else cutting_rows * 0.1, 1)
                            modified = True
                            print(f"  [Backfill] Job {job.job_id}: 로그에서 cutting_seconds 복구 -> {job.cutting_seconds} 초")
                            
                        if job.moving_distance is None and total_log_dist > 0:
                            job.moving_distance = round(total_log_dist, 1)
                            modified = True
                            print(f"  [Backfill] Job {job.job_id}: 로그에서 moving_distance 복구 -> {job.moving_distance} mm")
                            if job.cutting_moving_distance is None and cutting_log_dist > 0:
                                job.cutting_moving_distance = round(cutting_log_dist, 1)
                    except Exception as le:
                        print(f"  [Backfill 경고] Job {job.job_id} Log 파싱 오류: {le}")

            if modified:
                updated_count += 1
                
        session.commit()
    print(f"[Backfill 완료] 총 {updated_count}개의 Job 메타데이터가 보정되었습니다.")
    return updated_count

if __name__ == "__main__":
    backfill_missing_job_metadata()
    count = recover_all_jobs()
    print(f"\n총 {count}개의 Job이 복구되었습니다.")

