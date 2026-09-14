import os
import csv
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
import job_layout
from DB.database import engine, SessionLocal
from DB.models import SurfaceRoughness, Job, SurfaceRoughnessArchive, Inspection
from vault_manager import save_to_vault

from job_manager import get_or_create_job

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "processed_data")
os.makedirs(PROCESSED_DIR, exist_ok=True)


# 측정기에서 내보낸 평가곡선 CSV 를 알아보는 표식.
# 파일명은 측정 담당자/장비 설정에 따라 제각각이라("0715표면조도평가곡선파일_S2500_F950.CSV",
# "260911_1.CSV" 등) 이름이 아니라 헤더 내용으로 판별한다.
CURVE_MARKERS = ("DATAAXIS:", "XPITCH:", "PROFILENO:")


def _is_curve_csv(path, max_head_lines=80):
    """Mitutoyo 계열 평가곡선 CSV 인지 헤더를 보고 판단한다."""
    try:
        with open(path, "r", encoding="euc-kr", errors="ignore") as f:
            head = [next(f, "") for _ in range(max_head_lines)]
    except Exception:
        return False
    text = "".join(head).upper()
    # DATANUM 은 데이터 시작 위치를 알려주는 필수 표식이고,
    # 나머지 중 하나만 더 있으면 평가곡선 파일로 본다.
    if "DATANUM:" not in text:
        return False
    return any(m in text for m in CURVE_MARKERS)


def _find_curve_files(target_dir):
    """Surface_Roughness 폴더의 CSV 중 평가곡선 파일만 골라 이름순으로 돌려준다."""
    files = []
    try:
        names = sorted(os.listdir(target_dir))
    except OSError:
        return files
    for name in names:
        full = os.path.join(target_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() == ".csv" and _is_curve_csv(full):
            files.append(full)
    return files


def _measure_name_from(path):
    """파일명에서 측정 조건 이름을 뽑는다 (예: ..._S2800_F1050 -> S2800_F1050)."""
    base = os.path.splitext(os.path.basename(path))[0]
    parts = base.split('_')
    if len(parts) >= 3:
        return f"{parts[-2]}_{parts[-1]}"
    return base


def parse_roughness(job_folder_path, job_id=None):
    """
    job_folder_path (예: machining_raw_data/Alchemist_Test_250917) 내의 
    Surface_Roughness 폴더를 탐색하여 DB 및 Parquet으로 변환하고, Vault에 저장합니다.
    """
    target_dir = os.path.join(job_folder_path, job_layout.ROUGHNESS_DIR)
    if not os.path.exists(target_dir):
        return False
        
    folder_name = os.path.basename(os.path.normpath(job_folder_path))
    
    session = SessionLocal()
    try:
        # Job 가져오기 또는 생성 (XML 독립)
        actual_source_folder = job_id if job_id else folder_name
        job = get_or_create_job(session, actual_source_folder)
        job_pk = job.job_id
            
        # 1. 통계 파일 파싱 의존성 제거 (직접 계산으로 대체됨)
                
        # 2. 곡선 파일 파싱 및 Parquet 저장
        curve_files = _find_curve_files(target_dir)
        if not curve_files:
            csv_count = len([f for f in os.listdir(target_dir)
                             if os.path.splitext(f)[1].lower() == ".csv"])
            print(f"[Roughness Parser] {target_dir} 에서 평가곡선 CSV 를 찾지 못했습니다 "
                  f"(CSV {csv_count}개 확인)")
        else:
            print(f"[Roughness Parser] 평가곡선 CSV {len(curve_files)}개 확인: "
                  + ", ".join(os.path.basename(c) for c in curve_files))
        
        for cf in curve_files:
            try:
                measure_name = _measure_name_from(cf)
                    
                parquet_path = os.path.join(PROCESSED_DIR, f"roughness_job_{job_pk}_{measure_name}.parquet")
                
                # Parsing specific mitutoyo curve CSV
                # We skip lines until DATANUM: is found
                with open(cf, 'r', encoding='euc-kr', errors='ignore') as f:
                    lines = f.readlines()
                    
                data_start_idx = -1
                for i, line in enumerate(lines):
                    if line.startswith('DATANUM:'):
                        data_start_idx = i + 1
                        break
                        
                if data_start_idx != -1:
                    data_lines = lines[data_start_idx:]
                    parsed_data = []
                    for dl in data_lines:
                        # format: ,x_val, z_val, dummy
                        parts = dl.strip().split(',')
                        if len(parts) >= 3:
                            try:
                                x_val = float(parts[1])
                                z_val = float(parts[2])
                                parsed_data.append((x_val, z_val))
                            except ValueError:
                                pass
                                
                    if parsed_data:
                        import numpy as np
                        df = pd.DataFrame(parsed_data, columns=['X', 'Z'])
                        df.to_parquet(parquet_path, engine='pyarrow')
                        
                        # Calculate Ra, Rq, Rz directly from Z data
                        z_arr = df['Z'].values
                        mean_z = np.mean(z_arr)
                        z_centered = z_arr - mean_z
                        
                        ra_val = float(np.mean(np.abs(z_centered)))
                        rq_val = float(np.sqrt(np.mean(z_centered**2)))
                        
                        # Standard Rz Calculation (split into 5 segments, average max-min)
                        segments = np.array_split(z_centered, 5)
                        rz_vals = []
                        for seg in segments:
                            if len(seg) > 0:
                                rz_vals.append(np.max(seg) - np.min(seg))
                        rz_val = float(np.mean(rz_vals)) if rz_vals else 0.0
                        
                        # Copy to machining_raw_data
                        from vault_manager import get_rel_raw_data_path
                        from DB.models import Job
                        raw_parquet_path = parquet_path
                        try:
                            job_record = session.query(Job).filter_by(job_id=job_pk).first()
                            if job_record and job_record.source_folder:
                                import shutil
                                raw_job_dir = os.path.join(os.path.dirname(PROCESSED_DIR), "machining_raw_data", *job_record.source_folder.split('/'), job_layout.PARQUET_DIR)
                                os.makedirs(raw_job_dir, exist_ok=True)
                                raw_parquet_path = os.path.join(raw_job_dir, os.path.basename(parquet_path))
                                shutil.copy2(parquet_path, raw_parquet_path)
                        except Exception as e:
                            print(f"Failed to copy roughness parquet to raw_data: {e}")
                        
                        rel_parquet_path = get_rel_raw_data_path(raw_parquet_path)
                        
                        # Insert DB record
                        existing = session.query(SurfaceRoughness).filter_by(job_id=job_pk, measure_name=measure_name).first()
                        if existing:
                            existing.profile_parquet_path = rel_parquet_path
                            existing.ra = ra_val
                            existing.rq = rq_val
                            existing.rz = rz_val
                            session.flush()
                            target_sr_id = existing.roughness_id
                        else:
                            sr = SurfaceRoughness(
                                job_id=job_pk,
                                measure_name=measure_name,
                                profile_parquet_path=rel_parquet_path,
                                ra=ra_val,
                                rq=rq_val,
                                rz=rz_val
                            )
                            session.add(sr)
                            session.flush()
                            target_sr_id = sr.roughness_id
                        
                        archive = session.query(SurfaceRoughnessArchive).filter_by(roughness_id=target_sr_id).first()
                        if not archive:
                            archive = SurfaceRoughnessArchive(roughness_id=target_sr_id)
                            session.add(archive)
                            
                        if os.path.exists(parquet_path):
                            profile_vault_path = save_to_vault(parquet_path, "surface_roughness", f"Job_{job_pk}", os.path.basename(parquet_path))
                            archive.profile_parquet_file_path = profile_vault_path
                                
                        if os.path.exists(cf):
                            curve_vault_path = save_to_vault(cf, "surface_roughness", f"Job_{job_pk}", os.path.basename(cf))
                            archive.curve_csv_file_path = curve_vault_path
            except Exception as e:
                print(f"[Roughness Parser] 곡선 파싱 에러 ({cf}): {e}")
                
        # 3. Inspection 테이블에 평균 조도 자동 계산 및 업데이트
        try:
            all_sr = session.query(SurfaceRoughness).filter_by(job_id=job_pk).all()
            if all_sr:
                valid_ra = [sr.ra for sr in all_sr if sr.ra is not None]
                valid_rz = [sr.rz for sr in all_sr if sr.rz is not None]
                
                avg_ra = sum(valid_ra) / len(valid_ra) if valid_ra else None
                avg_rz = sum(valid_rz) / len(valid_rz) if valid_rz else None
                
                if avg_ra is not None or avg_rz is not None:
                    inspection = session.query(Inspection).filter_by(job_id=job_pk).first()
                    if not inspection:
                        inspection = Inspection(job_id=job_pk)
                        session.add(inspection)
                    
                    if avg_ra is not None:
                        inspection.surface_roughness_ra = round(avg_ra, 3)
                    if avg_rz is not None:
                        inspection.surface_roughness_rz = round(avg_rz, 3)
        except Exception as e:
            print(f"[Roughness Parser] Inspection 자동 기록 에러: {e}")
            
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"[Roughness Parser] 치명적 에러: {e}")
        return False
    finally:
        session.close()