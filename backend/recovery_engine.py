import os
import json
import zipfile
import shutil
import tempfile
from sqlalchemy.orm import Session
from DB.database import engine
from DB.models import (
    Job, JobFileArchive, WorkplanFileArchive,
    LogFileArchive, CadFileArchive, Workplan, Part
)
import job_layout
from vault_manager import get_abs_vault_path, get_abs_raw_data_path, get_rel_raw_data_path
import vault_layout
from file_archive import (archive_path, get_or_create_job_archive,
                          get_or_create_log_archive)

from vault_manager import PROCESSED_ROOT, RAW_DATA_ROOT as RAW_DATA_DIR  # noqa: E402  경로 기준은 vault_manager 한 곳

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
        abs_p = vault_layout.find_job_xml(job)
        if abs_p:
            file_map['xml'].append((abs_p, os.path.basename(abs_p),
                                    f"{job_layout.XML_DIR}/{os.path.basename(abs_p)}"))
                
        # 2. NC — 원본 폴더가 지워졌을 수 있으므로 보관소도 본다.
        if job.workplan_id:
            wp_archive = session.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
            raw = wp_archive.nc_raw_path if wp_archive else None
            for abs_p in (get_abs_raw_data_path(raw) if raw else None,
                          vault_layout.find_job_nc(job, raw)):
                if abs_p and os.path.exists(abs_p):
                    nc_name = os.path.basename(abs_p)
                    file_map['nc'].append((abs_p, nc_name, f"{job_layout.NC_DIR}/{nc_name}"))
                    break
                    
        # 3. TDMS — 보관소의 Job 폴더 TDMS 분류
        vault_tdms = vault_layout.list_in_job(job.source_folder, job_layout.TDMS_DIR, (".tdms",))
        if vault_tdms:
            for abs_p in vault_tdms:
                f = os.path.basename(abs_p)
                file_map['tdms'].append((abs_p, f, f"{job_layout.TDMS_DIR}/{f}"))
        elif archive_path(job, "tdms_raw_path"):
            abs_tdms_path = get_abs_raw_data_path(archive_path(job, "tdms_raw_path"))
            if abs_tdms_path and os.path.exists(abs_tdms_path):
                file_map['tdms'].append((abs_tdms_path, os.path.basename(abs_tdms_path),
                                         f"{job_layout.TDMS_DIR}/{os.path.basename(abs_tdms_path)}"))
            
        # 4. Logs (Job과 1:1)
        if job.machine_log:
            log_archive = session.query(LogFileArchive).filter_by(log_id=job.machine_log.log_id).first()
            abs_p = vault_layout.find_job_log(
                job, log_archive.log_raw_path if log_archive else None)
            if abs_p:
                file_map['log'].append((abs_p, os.path.basename(abs_p),
                                        f"{job_layout.LOG_DIR}/{os.path.basename(abs_p)}"))
                    
        # 5. Surface Roughness
        # 조도 원본 CSV 는 보관소 폴더를 훑어 모은다 (파일명이 측정기마다 달라 규칙으로 못 만든다)
        for abs_p in vault_layout.list_job_roughness(job, (".csv", ".txt", ".fpk")):
            file_map['roughness'].append((abs_p, os.path.basename(abs_p),
                                          f"{job_layout.ROUGHNESS_DIR}/{os.path.basename(abs_p)}"))
                            
        # 6. Parquet
        if archive_path(job, "tdms_parquet_raw_path"):
            abs_parquet = get_abs_raw_data_path(archive_path(job, "tdms_parquet_raw_path"))
            if abs_parquet and os.path.exists(abs_parquet):
                file_map['parquet'].append((abs_parquet, os.path.basename(abs_parquet),
                                            f"{job_layout.PARQUET_DIR}/{os.path.basename(abs_parquet)}"))
        if archive_path(job, "tdms_fft_parquet_raw_path"):
            abs_fft = get_abs_raw_data_path(archive_path(job, "tdms_fft_parquet_raw_path"))
            if abs_fft and os.path.exists(abs_fft):
                file_map['parquet'].append((abs_fft, os.path.basename(abs_fft),
                                            f"{job_layout.PARQUET_DIR}/{os.path.basename(abs_fft)}"))
            
        # 6-2. CAD (Part 단위라 Job 이 아니라 부품에 달려 있다)
        # 목록에 'cad' 칸만 있고 채우는 곳이 없어 다운로드 탭의 CAD 분류가 늘 비어 있었다.
        wp_for_cad = session.query(Workplan).filter_by(workplan_id=job.workplan_id).first()             if job.workplan_id else None
        if wp_for_cad:
            part_for_cad = session.query(Part).filter_by(part_code=wp_for_cad.part_code).first()
            for cad_arc in session.query(CadFileArchive).filter_by(
                    part_code=wp_for_cad.part_code).all():
                abs_p = vault_layout.find_part_cad(
                    part_for_cad.project_code if part_for_cad else None,
                    part_for_cad.part_name if part_for_cad else None, cad_arc.file_name)
                if abs_p:
                    file_map['cad'].append((abs_p, os.path.basename(abs_p),
                                            f"{job_layout.CAD_DIR}/{os.path.basename(abs_p)}"))

        # 7. etc (기타 참고용 파일)
        for abs_p in vault_layout.list_job_etc(job):
            f = os.path.basename(abs_p)
            file_map.setdefault('etc', []).append((abs_p, f, f"{job_layout.ETC_DIR}/{f}"))
                
        return file_map

def _already_has(dest_dir, kind, extensions):
    """해당 분류 폴더에 그 종류의 파일이 이미 있으면 True.

    Vault 는 XML 을 항상 'metadata.xml' 이라는 표준 이름으로 보관하므로, 원본이 멀쩡히
    있는 폴더에 복원을 한 번 더 돌리면 원래 파일 옆에 metadata.xml 이 하나 더 생겼다.
    NC 도 program_code 로 이름을 만들어 원본 파일명과 다를 수 있다. 파일 이름이 아니라
    '그 종류가 이미 있는지'로 판단해야 복원이 몇 번을 돌아도 같은 결과가 된다.
    """
    folder = os.path.join(dest_dir, kind)
    if not os.path.isdir(folder):
        return False
    suffixes = tuple(e.lower() for e in extensions)
    return any(n.lower().endswith(suffixes) for n in os.listdir(folder)
               if os.path.isfile(os.path.join(folder, n)))


def _restore_job_files(session, job, dest_dir, raw_root, folder_name):
    """Job 1건의 원본 파일을 dest_dir 아래 새 폴더 구조로 복원하고 복원 개수를 돌려준다.

    단일 Job 복원(raw_data 원상 복구)과 전체 복구 ZIP 이 같은 코드를 쓰도록 떼어낸 함수다.
    예전에는 recover_all_jobs 가 XML/NC/로그/조도만 따로 구현하고 있어서, 전체 복구 ZIP 에는
    TDMS·Parquet·etc·CAD 가 통째로 빠져 있었다.

    raw_root: Part 레벨 CAD_Files 를 놓을 기준 폴더 (원상 복구는 machining_raw_data,
              복구 ZIP 은 임시 출력 폴더).
    """
    restored_files = 0

    # 보관소는 원본 트리를 그대로 비춘다. 그래서 복원은 '보관소의 Job 폴더를 통째로 복사'
    # 한 번이면 끝난다. 예전에는 분류마다 자리와 파일명이 달라(jobs/ · workplan_nc/WP{id}.nc ·
    # tdms_files/Job_{id}/ ...) 종류별로 따로 긁어모으고 이름까지 되돌려야 했다.
    vault_job_dir = vault_layout.job_dir_abs(job.source_folder)
    if vault_job_dir and os.path.isdir(vault_job_dir):
        for root, _dirs, files in os.walk(vault_job_dir):
            rel_dir = os.path.relpath(root, vault_job_dir)
            out_dir = dest_dir if rel_dir == "." else os.path.join(dest_dir, rel_dir)
            for name in files:
                src = os.path.join(root, name)
                dest = os.path.join(out_dir, name)
                if os.path.exists(dest):
                    continue
                os.makedirs(out_dir, exist_ok=True)
                shutil.copy2(src, dest)
                restored_files += 1

    # 보관소에 사본이 없을 때를 위한 LONGBLOB 보루.
    # 15MB 이하 원본만 DB 에 들어 있으므로, 보관소가 비어 있는 XML·NC·CAD 만 여기서 살린다.
    job_archive = session.query(JobFileArchive).filter_by(job_id=job.job_id).first()
    if (job_archive and job_archive.xml_file_content
            and not _already_has(dest_dir, job_layout.XML_DIR, ('.xml',))):
        xml_dest = os.path.join(job_layout.subdir_path(dest_dir, job_layout.XML_DIR, create=True),
                                "metadata.xml")
        if _copy_from_vault_or_blob(None, job_archive.xml_file_content, xml_dest):
            restored_files += 1

    if job.workplan_id and not _already_has(dest_dir, job_layout.NC_DIR, ('.nc',)):
        wp_archive = session.query(WorkplanFileArchive).filter_by(
            workplan_id=job.workplan_id).first()
        if wp_archive and wp_archive.nc_file_content:
            nc_name = (os.path.basename(str(wp_archive.nc_raw_path).replace("\\", "/"))
                       if wp_archive.nc_raw_path else f"WP{job.workplan_id}.nc")
            nc_dest = os.path.join(
                job_layout.subdir_path(dest_dir, job_layout.NC_DIR, create=True), nc_name)
            if _copy_from_vault_or_blob(None, wp_archive.nc_file_content, nc_dest):
                restored_files += 1

    # Part 레벨 CAD 는 Job 폴더의 형제라 위 복사에 딸려 오지 않는다. 따로 챙긴다.
    if job.workplan_id:
        wp = session.query(Workplan).filter_by(workplan_id=job.workplan_id).first()
        if wp and wp.part_code:
            part_folder_parts = folder_name.split('/')[:2]
            part_row = session.query(Part).filter_by(part_code=wp.part_code).first()
            if len(part_folder_parts) == 2:
                cad_dest_dir = os.path.join(raw_root, part_folder_parts[0],
                                            part_folder_parts[1], job_layout.CAD_DIR)
                for cad_archive in session.query(CadFileArchive).filter_by(
                        part_code=wp.part_code).all():
                    cad_fname = cad_archive.file_name or f"Part{wp.part_code}.step"
                    cad_dest = os.path.join(cad_dest_dir, cad_fname)
                    if os.path.exists(cad_dest):
                        continue
                    cad_vault = vault_layout.find_part_cad(
                        part_row.project_code if part_row else None,
                        part_row.part_name if part_row else None, cad_archive.file_name)
                    if _copy_from_vault_or_blob(None if not cad_vault else cad_vault,
                                                cad_archive.file_content, cad_dest):
                        restored_files += 1

    # Parquet 은 보관소에 없으면 작업 폴더(processed_data)에서 가져온다.
    if job.source_folder:
        work_dir = os.path.join(PROCESSED_ROOT, *job.source_folder.split('/'))
        if os.path.isdir(work_dir):
            parquet_dir = os.path.join(dest_dir, job_layout.PARQUET_DIR)
            for name in sorted(os.listdir(work_dir)):
                src_p = os.path.join(work_dir, name)
                if not os.path.isfile(src_p) or not name.lower().endswith(".parquet"):
                    continue
                os.makedirs(parquet_dir, exist_ok=True)
                dest_p = os.path.join(parquet_dir, name)
                if not os.path.exists(dest_p):
                    shutil.copy2(src_p, dest_p)
                    restored_files += 1

    return restored_files


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
            # 프로젝트·부품 이름은 part 테이블 하나에서만 읽는다.
            part_row = job.workplan.part if job.workplan else None
            proj = (part_row.project_code if part_row else None) or "Unknown"
            part = (part_row.part_name if part_row else None) or "Unknown"
            folder_name = f"{proj}/{part}/{job.job_id}"

        dest_dir = os.path.join(RAW_DATA_DIR, *folder_name.split('/'))
        os.makedirs(dest_dir, exist_ok=True)

        restored_files = _restore_job_files(session, job, dest_dir, RAW_DATA_DIR, folder_name)
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
                "research_project": (job.workplan.part.project_code
                                     if job.workplan and job.workplan.part else None),
                "part_name": (job.workplan.part.part_name
                              if job.workplan and job.workplan.part else None),
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
                    "pass_fail": ins.pass_fail
                })
            with open(os.path.join(job_dir, "metadata_export.json"), "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
                
            # 파일 복원은 단일 Job 복원과 같은 함수를 쓴다.
            # (예전에는 여기서 XML/NC/로그/조도만 따로 구현해, 전체 복구 ZIP 에 TDMS·Parquet·
            #  etc·CAD 가 빠져 있었다)
            _restore_job_files(session, job, job_dir, base_output_dir, folder_name)

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
        # 임시 폴더 정리는 실패해도 넘어간다.
        # Vault 원본이 읽기 전용이면 shutil.copy2 가 그 속성까지 복사해 와서 rmtree 가
        # PermissionError(WinError 5) 를 내는데, finally 에서 그대로 터지면 이미 다 만들어 둔
        # ZIP 경로가 반환되지 못하고 백업 자체가 실패한 것처럼 보인다.
        shutil.rmtree(temp_dir, ignore_errors=True)

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
    log_raw_path, tdms_parquet_raw_path 등이 누락된 건을
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
            job_arc = get_or_create_job_archive(session, job.job_id)
            # 원본 폴더 -> 작업 폴더 -> 보관소 순. 어느 쪽도 이름에 job_id 를 쓰지 않는다.
            _sf_parts = job.source_folder.split('/') if job.source_folder else None
            candidate_pqs = [
                job_arc.tdms_parquet_raw_path,
                os.path.join(PROCESSED_ROOT, *_sf_parts, "viz.parquet") if _sf_parts else None,
                vault_layout.find_job_parquet(job, "viz"),
            ]
            
            valid_pq = None
            for cpq in candidate_pqs:
                if cpq and os.path.exists(cpq):
                    valid_pq = cpq
                    break
                    
            if valid_pq:
                if not job_arc.tdms_parquet_raw_path or not os.path.exists(
                        get_abs_raw_data_path(job_arc.tdms_parquet_raw_path)):
                    job_arc.tdms_parquet_raw_path = get_rel_raw_data_path(valid_pq)
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

            # TDMS 원본 파일 탐색 및 경로 보완 (보관소의 Job 폴더 TDMS 분류)
            vault_tdms = vault_layout.list_in_job(job.source_folder,
                                                  job_layout.TDMS_DIR, (".tdms",))
            if vault_tdms:
                abs_tdms = vault_tdms[0]
                if not job_arc.tdms_raw_path:
                    job_arc.tdms_raw_path = get_rel_raw_data_path(abs_tdms)
                    modified = True
                if job.start_time is None:
                    m = re.search(r'__(\d{12})\.tdms', os.path.basename(abs_tdms))
                    if m:
                        try:
                            dt = datetime.datetime.strptime(f"20{m.group(1)}", "%Y%m%d%H%M%S")
                            job.start_time = dt
                            modified = True
                            print(f"  [Backfill] Job {job.job_id}: TDMS 파일명에서 start_time 복구 -> {dt}")
                        except Exception:
                            pass

            # --- 3순위: LOG 파일 기반 Fallback ---
            log_candidates = []
            log_arc_rel = (job.machine_log.file_archive.log_raw_path
                           if job.machine_log and job.machine_log.file_archive else None)
            if log_arc_rel:
                abs_log = get_abs_raw_data_path(log_arc_rel)
                if abs_log and os.path.exists(abs_log):
                    log_candidates.append(abs_log)
            log_candidates.extend(vault_layout.list_in_job(job.source_folder,
                                                           job_layout.LOG_DIR))
                    
            if log_candidates:
                target_log_f = log_candidates[0]
                if job.machine_log and (not log_arc_rel or not os.path.exists(
                        get_abs_raw_data_path(log_arc_rel))):
                    log_arc = get_or_create_log_archive(session, job.machine_log.log_id)
                    log_arc.log_raw_path = get_rel_raw_data_path(target_log_f)
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

