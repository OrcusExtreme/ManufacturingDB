import os
import time
import pandas as pd
import numpy as np
from scipy.signal import welch
from nptdms import TdmsFile
from DB.database import SessionLocal
from DB.models import Job, JobFileArchive

TARGET_DATAPOINTS = 10000

def process_tdms_file(job):
    print(f"\n[TDMS Visualizer] 작업 시작: Job ID {job.job_id}")
    tdms_path = job.tdms_file_path
    
    if not os.path.exists(tdms_path):
        print(f"  - [오류] 파일을 찾을 수 없습니다: {tdms_path}")
        return False
        
    try:
        start_t = time.time()
        print(f"  - TDMS 로딩 중... (파일 크기: {os.path.getsize(tdms_path)/1024/1024:.1f} MB)")
        
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
        
        # Parquet 저장 경로를 원본 TDMS 폴더 하위의 'processed_parquet' 폴더로 지정
        tdms_dir = os.path.dirname(tdms_path)
        processed_dir = os.path.join(tdms_dir, 'processed_parquet')
        if not os.path.exists(processed_dir):
            os.makedirs(processed_dir)
            
        parquet_path = os.path.join(processed_dir, f"job_{job.job_id}_viz.parquet")
        fft_parquet_path = os.path.join(processed_dir, f"job_{job.job_id}_fft.parquet")
        
        # CNC와 DAQ 행 길이가 다를 수 있으므로 인덱스를 리셋하고 concat (가로 병합)
        if not cnc_df.empty or not daq_df.empty:
            if not cnc_df.empty:
                cnc_df = cnc_df.reset_index(drop=True)
            if not daq_df.empty:
                daq_df = daq_df.reset_index(drop=True)
                
            merged_df = pd.concat([cnc_df, daq_df], axis=1)
            merged_df.to_parquet(parquet_path, engine='pyarrow')
            
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
            # TDMS 파일 경로는 존재하지만 아직 Parquet 처리가 안 된 Job 검색
            jobs = db.query(Job).filter(
                Job.tdms_file_path.isnot(None),
                Job.tdms_parquet_path.is_(None)
            ).all()
            
            if jobs:
                print(f"총 {len(jobs)}개의 신규 TDMS 파일을 처리합니다.")
                for job in jobs:
                    res = process_tdms_file(job)
                    if res is False:
                        continue
                    
                    parquet_path, fft_parquet_path = res
                    if parquet_path:
                        job.tdms_parquet_path = parquet_path
                        job.tdms_fft_parquet_path = fft_parquet_path
                        
                        # DB에 Parquet 파일 원본(BLOB) 삽입
                        job_archive = db.query(JobFileArchive).filter(JobFileArchive.job_id == job.job_id).first()
                        if not job_archive:
                            job_archive = JobFileArchive(job_id=job.job_id)
                            db.add(job_archive)
                            
                        if os.path.exists(parquet_path):
                            with open(parquet_path, 'rb') as f:
                                job_archive.tdms_parquet_file = f.read()
                        if fft_parquet_path and os.path.exists(fft_parquet_path):
                            with open(fft_parquet_path, 'rb') as f:
                                job_archive.tdms_fft_parquet_file = f.read()
                        
                        db.commit()
                        print(f"  - DB 업데이트 완료 (Job ID: {job.job_id})")
                    else:
                        job.tdms_parquet_path = "FAILED"
                        db.commit()
                        print(f"  - 파싱 실패, Job {job.job_id} 제외 처리")
        except Exception as e:
            print(f"배치 실행 중 오류 발생: {e}")
            db.rollback()
        finally:
            db.close()
            
        time.sleep(5)  # 5초 간격 폴링

if __name__ == "__main__":
    run_visualizer_batch()
