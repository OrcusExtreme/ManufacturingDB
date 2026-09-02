import os
import time
import pandas as pd
import numpy as np
from scipy.signal import welch
from nptdms import TdmsFile
from DB.database import SessionLocal
from DB.models import Job, JobFileArchive
from vault_manager import save_to_vault, get_abs_vault_path

TARGET_DATAPOINTS = 10000

# Root processed_data directory (independent from machining_raw_data)
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "processed_data")
os.makedirs(PROCESSED_DIR, exist_ok=True)

def find_tdms_path(job):
    """
    Job의 TDMS 파일 경로를 찾습니다.
    1. job.tdms_file_path 확인
    2. 없으면 archive_vault 내의 백업 TDMS 탐색
    """
    from vault_manager import get_abs_raw_data_path, get_abs_vault_path
    abs_tdms_path = get_abs_raw_data_path(job.tdms_file_path)
    if abs_tdms_path and os.path.exists(abs_tdms_path):
        return abs_tdms_path
        
    # Vault 탐색
    vault_rel_paths = [
        f"tdms_files/Job_{job.job_id}/{os.path.basename(job.tdms_file_path)}" if job.tdms_file_path else None,
        f"tdms/Job_{job.job_id}/{os.path.basename(job.tdms_file_path)}" if job.tdms_file_path else None,
        f"jobs/{job.source_folder}/data.tdms" if job.source_folder else None
    ]
    for vp in vault_rel_paths:
        if vp:
            abs_p = get_abs_vault_path(vp)
            if abs_p and os.path.exists(abs_p):
                return abs_p
                
    return None

def process_tdms_file(job):
    print(f"\n[TDMS Visualizer] 작업 시작: Job ID {job.job_id}")
    tdms_path = find_tdms_path(job)
    
    if not tdms_path or not os.path.exists(tdms_path):
        print(f"  - [오류] TDMS 파일을 찾을 수 없습니다: {job.tdms_file_path}")
        return False
        
    try:
        start_t = time.time()
        print(f"  - TDMS 로딩 중... (경로: {tdms_path}, 크기: {os.path.getsize(tdms_path)/1024/1024:.1f} MB)")
        
        cnc_df = pd.DataFrame()
        daq_df = pd.DataFrame()
        fft_df = pd.DataFrame()
        
        with TdmsFile.read(tdms_path) as tdms_file:
            # 1. CNC 그룹 처리 (저주파)
            if 'CNC' in tdms_file:
                cnc_group = tdms_file['CNC']
                cnc_data = {}
                for channel in cnc_group.channels():
                    data = channel[:]
                    # CNC는 단순 다운샘플링 (10,000개 수준으로)
                    step = max(1, len(data) // TARGET_DATAPOINTS)
                    cnc_data[channel.name] = data[::step]
                
                cnc_df = pd.DataFrame(cnc_data)
                print(f"  - CNC 그룹 파싱 완료 (샘플링 후 데이터 수: {len(cnc_df)})")
            
            # 2. DAQ 그룹 처리 (고주파 12800Hz - Envelope 및 FFT)
            if 'DAQ' in tdms_file:
                daq_group = tdms_file['DAQ']
                daq_data = {}
                fft_data = {}
                
                # FFT 기본 주파수 축 설정 (최초 1회만 생성)
                fs = 12800  # Sampling frequency
                freqs = None
                
                for channel in daq_group.channels():
                    data = channel[:]
                    
                    if len(data) == 0:
                        continue
                        
                    # 2-1. Frequency Domain (FFT/PSD) 계산
                    # nperseg=4096 => 2049개의 frequency bin 생성 (0~6400Hz)
                    f, psd = welch(data, fs=fs, nperseg=4096)
                    if freqs is None:
                        freqs = f
                        fft_data['Frequency'] = freqs
                    fft_data[channel.name] = psd
                    
                    # 2-2. Time Domain Envelope 다운샘플링
                    step = max(1, len(data) // TARGET_DATAPOINTS)
                    truncated_len = (len(data) // step) * step
                    reshaped = data[:truncated_len].reshape(-1, step)
                    
                    # Max와 Min을 저장하여 진폭 보존
                    daq_data[channel.name + "_max"] = reshaped.max(axis=1)
                    daq_data[channel.name + "_min"] = reshaped.min(axis=1)
                
                daq_df = pd.DataFrame(daq_data)
                fft_df = pd.DataFrame(fft_data)
                print(f"  - DAQ 그룹 파싱 및 주파수 분석 완료 (Time 샘플: {len(daq_df)}, FFT Bin: {len(fft_df)})")
        
        # Parquet 저장 경로를 독립된 processed_data 폴더로 지정
        parquet_path = os.path.join(PROCESSED_DIR, f"job_{job.job_id}_viz.parquet")
        fft_parquet_path = os.path.join(PROCESSED_DIR, f"job_{job.job_id}_fft.parquet")
        
        # CNC와 DAQ 행 길이가 다를 수 있으므로 인덱스를 리셋하고 concat (가로 병합)
        if not cnc_df.empty or not daq_df.empty:
            if not cnc_df.empty:
                cnc_df = cnc_df.reset_index(drop=True)
            if not daq_df.empty:
                daq_df = daq_df.reset_index(drop=True)
                
            merged_df = pd.concat([cnc_df, daq_df], axis=1)
            merged_df.to_parquet(parquet_path, engine='pyarrow')
            
            # 시작/종료 일시 및 가공시간, 이동거리 추출 (job 메타데이터 보완)
            time_col = None
            for col in ['Time Channel CNC', 'Timestamp', 'time']:
                if col in merged_df.columns:
                    time_col = col
                    break
            if time_col is not None:
                try:
                    valid_times = pd.to_datetime(merged_df[time_col]).dropna()
                    if not valid_times.empty:
                        min_t = valid_times.iloc[0].to_pydatetime()
                        max_t = valid_times.iloc[-1].to_pydatetime()
                        if job.start_time is None:
                            job.start_time = min_t
                            print(f"  - [TDMS Visualizer] Parquet 타임스탬프 기반 start_time 설정: {min_t}")
                        if job.end_time is None:
                            job.end_time = max_t
                            print(f"  - [TDMS Visualizer] Parquet 타임스탬프 기반 end_time 설정: {max_t}")
                            
                        # 가공시간(cutting_seconds) 산출
                        if job.cutting_seconds is None:
                            dt_series = pd.to_datetime(merged_df[time_col]).diff().dt.total_seconds().fillna(1.0)
                            dt_series[dt_series > 10] = 1.0
                            rpm = merged_df['CNC-Z-SpindleSpeed'] if 'CNC-Z-SpindleSpeed' in merged_df.columns else 0
                            feed = merged_df['CNC-ActualFeedRate'] if 'CNC-ActualFeedRate' in merged_df.columns else 0
                            is_cut = (rpm > 100) & (feed > 0)
                            if is_cut.any():
                                job.cutting_seconds = round(float(dt_series[is_cut].sum()), 1)
                            else:
                                job.cutting_seconds = round(float(dt_series.sum()), 1)
                            print(f"  - [TDMS Visualizer] Parquet 기반 cutting_seconds 설정: {job.cutting_seconds} 초")
                except Exception as t_err:
                    pass

            # 이동거리(moving_distance, cutting_moving_distance) 산출
            if job.moving_distance is None:
                try:
                    if {'CNC-X-Position', 'CNC-Y-Position', 'CNC-Z-Position'}.issubset(merged_df.columns):
                        dx = merged_df['CNC-X-Position'].diff().fillna(0)
                        dy = merged_df['CNC-Y-Position'].diff().fillna(0)
                        dz = merged_df['CNC-Z-Position'].diff().fillna(0)
                        dist_3d = np.sqrt(dx**2 + dy**2 + dz**2)
                        job.moving_distance = round(float(dist_3d.sum()), 1)
                        print(f"  - [TDMS Visualizer] Parquet 3D 궤적 기반 moving_distance 설정: {job.moving_distance} mm")
                        
                        rpm = merged_df['CNC-Z-SpindleSpeed'] if 'CNC-Z-SpindleSpeed' in merged_df.columns else 0
                        feed = merged_df['CNC-ActualFeedRate'] if 'CNC-ActualFeedRate' in merged_df.columns else 0
                        is_cut = (rpm > 100) & (feed > 0)
                        if is_cut.any() and job.cutting_moving_distance is None:
                            job.cutting_moving_distance = round(float(dist_3d[is_cut].sum()), 1)
                            print(f"  - [TDMS Visualizer] Parquet 3D 궤적 기반 cutting_moving_distance 설정: {job.cutting_moving_distance} mm")
                except Exception as dist_err:
                    pass

            # FFT 데이터 저장
            if not fft_df.empty:
                fft_df.to_parquet(fft_parquet_path, engine='pyarrow')
            else:
                fft_parquet_path = None
            
            print(f"  - Time Domain Parquet 저장 완료: {parquet_path}")
            if fft_parquet_path:
                print(f"  - Freq Domain Parquet 저장 완료: {fft_parquet_path}")
            print(f"  - 소요 시간: {time.time() - start_t:.2f} 초")
            
            return parquet_path, fft_parquet_path
        else:
            print("  - [경고] CNC/DAQ 그룹을 찾을 수 없습니다.")
            return None, None
            
    except Exception as e:
        print(f"  - [오류] TDMS 파싱 실패: {e}")
        return None, None

def run_visualizer_batch():
    print("========================================")
    print(" TDMS Visualizer 백그라운드 서비스 시작")
    print("========================================")
    
    while True:
        db = SessionLocal()
        try:
            # TDMS 파일 경로는 존재하지만 Parquet가 없거나 파일이 유실된 Job 검색
            candidate_jobs = db.query(Job).filter(
                Job.tdms_file_path.isnot(None)
            ).all()
            
            jobs = []
            for j in candidate_jobs:
                if not j.tdms_parquet_path:
                    jobs.append(j)
                elif not os.path.exists(get_abs_raw_data_path(j.tdms_parquet_path)):
                    expected_p = get_abs_raw_data_path(j.tdms_parquet_path)
                    if not expected_p or not os.path.exists(expected_p):
                        jobs.append(j)
            
            if jobs:
                print(f"총 {len(jobs)}개의 TDMS Parquet 미생성/유실 건을 처리합니다.")
                for job in jobs:
                    res = process_tdms_file(job)
                    if res is False or not res:
                        continue
                    
                    parquet_path, fft_parquet_path = res
                    if parquet_path:
                        from vault_manager import get_rel_raw_data_path
                        
                        # machining_raw_data 내 processed_parquet 폴더에도 동기화 복사
                        raw_parquet_path = parquet_path
                        raw_fft_path = fft_parquet_path
                        if job.source_folder:
                            raw_job_dir = os.path.join(PROJECT_ROOT, "machining_raw_data", *job.source_folder.split('/'), "processed_parquet")
                            os.makedirs(raw_job_dir, exist_ok=True)
                            import shutil
                            raw_parquet_path = os.path.join(raw_job_dir, f"job_{job.job_id}_viz.parquet")
                            shutil.copy2(parquet_path, raw_parquet_path)
                            if fft_parquet_path:
                                raw_fft_path = os.path.join(raw_job_dir, f"job_{job.job_id}_fft.parquet")
                                shutil.copy2(fft_parquet_path, raw_fft_path)
                                
                        job.tdms_parquet_path = get_rel_raw_data_path(raw_parquet_path)
                        job.tdms_fft_parquet_path = get_rel_raw_data_path(raw_fft_path)
                        
                        # Vault에도 Parquet 이중 백업
                        tdms_parquet_vault = save_to_vault(parquet_path, "processed_parquet", f"job_{job.job_id}_viz.parquet")
                        tdms_fft_vault = save_to_vault(fft_parquet_path, "processed_parquet", f"job_{job.job_id}_fft.parquet") if fft_parquet_path else None
                        
                        job_archive = db.query(JobFileArchive).filter(JobFileArchive.job_id == job.job_id).first()
                        if not job_archive:
                            job_archive = JobFileArchive(job_id=job.job_id)
                            db.add(job_archive)
                            
                        job_archive.tdms_parquet_file_path = tdms_parquet_vault
                        job_archive.tdms_fft_parquet_file_path = tdms_fft_vault
                        
                        db.commit()
                        print(f"  - DB, Vault 및 raw_data 동기화 완료 (Job ID: {job.job_id})")
                    else:
                        print(f"  - 파싱 실패 또는 대상 그룹 없음 (Job ID: {job.job_id})")
        except Exception as e:
            print(f"배치 실행 중 오류 발생: {e}")
            db.rollback()
        finally:
            db.close()
            
        time.sleep(5)  # 5초 간격 폴링

if __name__ == "__main__":
    run_visualizer_batch()

