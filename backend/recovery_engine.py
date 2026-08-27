import os
import json
import zipfile
import io
import shutil
import tempfile
from sqlalchemy.orm import Session
from DB.database import engine
from DB.models import (
    Job, JobFileArchive, WorkplanFileArchive, 
    LogFileArchive, SurfaceRoughness, SurfaceRoughnessArchive,
    EnvMemo, Inspection
)
from vault_manager import get_abs_vault_path

def _copy_from_vault(relative_vault_path, destination_path):
    if not relative_vault_path:
        return
    src = get_abs_vault_path(relative_vault_path)
    if src and os.path.exists(src):
        shutil.copy2(src, destination_path)

def recover_all_jobs(base_output_dir="recovered_data"):
    """
    DB에 저장된 모든 Job과 연관된 원본 파일(Vault 경로 참조)을 파일 시스템으로 복구하고, 
    수기 입력된 메타데이터를 JSON 형태로 내보내는 모듈입니다.
    """
    if not os.path.exists(base_output_dir):
        os.makedirs(base_output_dir)
        
    recovered_count = 0
    with Session(engine) as session:
        jobs = session.query(Job).all()
        
        for job in jobs:
            folder_name = job.source_folder
            if not folder_name:
                folder_name = f"Job_{job.job_id}"
                
            job_dir = os.path.join(base_output_dir, folder_name)
            os.makedirs(job_dir, exist_ok=True)
            
            # 1. Job Metadata Export (Edited values)
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
                
            # 2. Job File Archive (XML, TDMS Parquets)
            job_archive = session.query(JobFileArchive).filter_by(job_id=job.job_id).first()
            if job_archive:
                _copy_from_vault(job_archive.xml_file_path, os.path.join(job_dir, "metadata.xml"))
                        
                parquet_dir = os.path.join(job_dir, "processed_parquet")
                if job_archive.tdms_parquet_file_path or job_archive.tdms_fft_parquet_file_path:
                    os.makedirs(parquet_dir, exist_ok=True)
                
                _copy_from_vault(job_archive.tdms_parquet_file_path, os.path.join(parquet_dir, "tdms_time.parquet"))
                _copy_from_vault(job_archive.tdms_fft_parquet_file_path, os.path.join(parquet_dir, "tdms_fft.parquet"))
                        
            # 3. Workplan File Archive (NC Code)
            if job.workplan_id:
                wp_archive = session.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
                if wp_archive:
                    _copy_from_vault(wp_archive.nc_file_path, os.path.join(job_dir, f"{job.workplan_id}.nc"))
                        
            # 4. Machine Log Archive
            for mlog in job.machine_logs:
                log_archive = session.query(LogFileArchive).filter_by(log_id=mlog.log_id).first()
                if log_archive:
                    _copy_from_vault(log_archive.log_file_path, os.path.join(job_dir, f"machine_log_{mlog.log_id}.csv"))
                        
            # 5. Surface Roughness Archive
            roughness_records = session.query(SurfaceRoughness).filter_by(job_id=job.job_id).all()
            if roughness_records:
                sr_dir = os.path.join(job_dir, "Surface_Roughness")
                os.makedirs(sr_dir, exist_ok=True)
                
                for rr in roughness_records:
                    sr_archive = session.query(SurfaceRoughnessArchive).filter_by(roughness_id=rr.roughness_id).first()
                    if sr_archive:
                        _copy_from_vault(sr_archive.stat_csv_file_path, os.path.join(sr_dir, f"{rr.measure_name}_stat.csv"))
                        _copy_from_vault(sr_archive.curve_csv_file_path, os.path.join(sr_dir, f"{rr.measure_name}_curve.csv"))
                        _copy_from_vault(sr_archive.profile_parquet_file_path, os.path.join(sr_dir, f"{rr.measure_name}_profile.parquet"))
                                
            recovered_count += 1
            print(f"Recovered Job {job.job_id} ({folder_name})")
            
    return recovered_count

def create_recovery_zip():
    """
    메모리 상에서 복구된 폴더 구조를 임시 파일 ZIP으로 말아서 리턴 (Streamlit 다운로드용)
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

if __name__ == "__main__":
    count = recover_all_jobs()
    print(f"\n총 {count}개의 Job이 복구되었습니다.")
