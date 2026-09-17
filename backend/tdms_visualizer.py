"""TDMS -> 시각화용 Parquet 변환 서비스

예전에는 TDMS 전체를 그대로 읽어 CNC 는 단순 다운샘플링, DAQ 는 포락선으로 줄여
가로로 이어 붙였다. 그러면 기록된 12분 전체가 그래프에 들어가고, 행 수가 600배 차이나는
두 그룹이 각자의 행 번호를 x 축으로 쓰는 바람에 같은 시점을 비교할 수 없었다.

지금은 tdms_alignment 모듈로
  1) 업로드된 NC 프로그램과 CNC-CurrentBlock 을 대조해 실제 가공 구간만 잘라내고,
  2) CNC 스핀들 부하 ↔ DAQ 스핀들 전류 상호상관으로 두 계측계의 시계 지연을 보정한 뒤,
  3) 가공 시작을 0초로 하는 공통 경과시간 축(time_s / daq_time_s)을 붙여 저장한다.
판별 근거와 보정값은 job.machining_window(JSON) 및 *_window.json 사이드카에 남는다.
"""
import json
import os
import time
from contextlib import contextmanager

import numpy as np

import job_layout
from DB.database import SessionLocal
from DB.models import Job, JobFileArchive, Workplan, WorkplanFileArchive
from file_archive import archive_path, get_or_create_job_archive
import vault_layout
from vault_manager import (get_abs_raw_data_path, get_abs_vault_path,
                           get_rel_raw_data_path, save_to_vault)

from tdms_alignment import (TARGET_DATAPOINTS, build_aligned_frames,
                            detect_machining_window, merge_for_parquet,
                            open_tdms, parse_nc_blocks, read_cnc_frame,
                            summarize_window)

# Root processed_data directory (independent from machining_raw_data)
from vault_manager import PROCESSED_ROOT, RAW_DATA_ROOT   # 경로 기준은 vault_manager 한 곳
PROCESSED_DIR = PROCESSED_ROOT
os.makedirs(PROCESSED_DIR, exist_ok=True)

# 변환 점유 표시 파일이 이 시간보다 오래되면 죽은 프로세스가 남긴 것으로 보고 회수한다.
# (수백 MB TDMS 한 건이 2분 안팎이므로 1시간이면 충분히 여유 있는 값이다.)
LOCK_STALE_SECONDS = 3600.0


@contextmanager
def job_conversion_lock(job_id):
    """같은 Job 을 두 프로세스가 동시에 변환하지 않도록 잠근다.

    run_system.py 를 실수로 여러 번 띄우면 tdms_visualizer 데몬도 그만큼 떠서, 같은 Job 을
    동시에 집어 같은 Parquet 을 향해 나란히 변환한다(수백 MB 파일에서는 서로 CPU/디스크를
    빼앗아 소요 시간이 눈에 띄게 늘어난다). 파일 생성의 원자성(O_CREAT|O_EXCL)으로
    먼저 잡은 쪽만 진행시키고, 나머지는 건너뛰게 한다.

    얻으면 True, 이미 다른 쪽이 작업 중이면 False 를 넘겨준다.
    """
    lock_path = os.path.join(PROCESSED_DIR, f".job_{job_id}.converting")
    acquired = False
    try:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            acquired = True
        except FileExistsError:
            # 잠금이 남아 있어도 그 주인이 이미 죽었을 수 있으므로 나이를 보고 회수한다.
            try:
                age = time.time() - os.path.getmtime(lock_path)
            except OSError:
                age = 0.0
            if age > LOCK_STALE_SECONDS:
                print(f"  - [알림] Job {job_id} 의 오래된 변환 점유 표시를 회수합니다 ({age/60:.0f}분 경과).")
                try:
                    os.remove(lock_path)
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode())
                    os.close(fd)
                    acquired = True
                except OSError:
                    acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                os.remove(lock_path)
            except OSError:
                pass


def find_tdms_path(job):
    """
    Job의 TDMS 파일 경로를 찾습니다.
    1. job_file_archive.tdms_raw_path 확인
    2. 없으면 archive_vault 내의 백업 TDMS 탐색
    """
    tdms_raw = archive_path(job, "tdms_raw_path")
    abs_tdms_path = get_abs_raw_data_path(tdms_raw)
    if abs_tdms_path and os.path.exists(abs_tdms_path):
        return abs_tdms_path

    # Vault 탐색
    # 보관소 경로는 저장하지 않고 규칙으로 계산한다 (vault_layout).
    found = vault_layout.find_job_tdms(job.job_id, tdms_raw)
    if found:
        return found

    vault_rel_paths = [
        f"jobs/{job.source_folder}/data.tdms" if job.source_folder else None
    ]
    for vp in vault_rel_paths:
        if vp:
            abs_p = get_abs_vault_path(vp)
            if abs_p and os.path.exists(abs_p):
                return abs_p

    return None


def find_nc_program(job, db=None):
    """Job 이 실행한 NC 프로그램 원본을 찾아 NcProgram 으로 돌려준다.

    가공 구간 판별의 기준이 되므로 DB BLOB -> Vault -> raw_data 폴더 순으로 최대한 찾는다.
    (업로드 순서에 따라 TDMS 가 NC 보다 먼저 들어올 수 있어, 못 찾으면 None 을 반환하고
     활동 기반 판별로 넘어간다. 이후 NC 가 들어오면 재처리 대상이 된다.)
    """
    own_session = db is None
    db = db or SessionLocal()
    try:
        workplan = db.query(Workplan).filter_by(workplan_id=job.workplan_id).first() if job.workplan_id else None
        name = (workplan.program_code if workplan and workplan.program_code else None)

        # 1) DB에 보관된 NC 원본 바이너리
        if job.workplan_id:
            arch = db.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
            if arch is not None:
                if arch.nc_file_content:
                    return parse_nc_blocks(arch.nc_file_content, name=name)
                for cand in (arch.nc_raw_path, vault_layout.workplan_nc_rel(job.workplan_id)):
                    for resolver in (get_abs_raw_data_path, get_abs_vault_path, lambda p: p):
                        try:
                            p = resolver(cand) if cand else None
                        except Exception:
                            p = None
                        if p and os.path.exists(p):
                            with open(p, "rb") as f:
                                return parse_nc_blocks(f.read(), name=name or os.path.basename(p))

        # 2) Workplan 아카이브에 기록된 경로
        wp_nc = archive_path(workplan, "nc_raw_path")
        if wp_nc:
            p = get_abs_raw_data_path(wp_nc)
            if p and os.path.exists(p):
                with open(p, "rb") as f:
                    return parse_nc_blocks(f.read(), name=name or os.path.basename(p))

        # 3) Job 원본 폴더 안의 .nc 파일 (NC/ 하위를 먼저 보고, 없으면 예전 구조인 Job 루트도 본다)
        if job.source_folder:
            job_dir = os.path.join(RAW_DATA_ROOT, *job.source_folder.split('/'))
            cands = job_layout.find_files(job_dir, job_layout.NC_DIR, ('.nc',))
            # 프로그램명과 같은 파일을 우선 사용 (프로브 매크로 등 부수 NC 배제)
            if name:
                base = os.path.splitext(name)[0].lower()
                cands.sort(key=lambda p: (base not in os.path.basename(p).lower(), len(os.path.basename(p))))
            if cands:
                p = cands[0]
                with open(p, "rb") as f:
                    return parse_nc_blocks(f.read(), name=name or os.path.basename(p))
    except Exception as e:
        print(f"  - [경고] NC 프로그램 탐색 중 오류: {e}")
    finally:
        if own_session:
            db.close()
    return None


def _apply_window_to_job(job, result, cnc_df):
    """판별된 가공 구간과 CNC 데이터로 Job 메타데이터를 보완한다."""
    window = result.window
    job.machining_window = {**window.to_dict(), "daq_sync": result.sync.to_dict()}

    # TDMS 는 UTC, XML 은 로컬시간이므로 XML 과 같은 기준으로 되돌려 저장한다
    if window.start is not None and job.start_time is None:
        job.start_time = window.local_start
        print(f"  - 가공 구간 기반 start_time 설정: {job.start_time}")
    if window.end is not None and job.end_time is None:
        job.end_time = window.local_end
        print(f"  - 가공 구간 기반 end_time 설정: {job.end_time}")

    if cnc_df is None or cnc_df.empty or "time_s" not in cnc_df.columns:
        return

    dt = cnc_df["time_s"].diff().fillna(0.0)
    dt[dt > 10] = 0.0          # 폴링이 끊긴 구간은 가공시간에서 제외
    rpm = cnc_df["CNC-Z-SpindleSpeed"] if "CNC-Z-SpindleSpeed" in cnc_df.columns else None
    feed = cnc_df["CNC-ActualFeedRate"] if "CNC-ActualFeedRate" in cnc_df.columns else None
    is_cut = None
    if rpm is not None and feed is not None:
        is_cut = (rpm > 100) & (feed > 0)

    if job.cutting_seconds is None:
        job.cutting_seconds = round(float(dt[is_cut].sum() if is_cut is not None and is_cut.any() else dt.sum()), 1)
        print(f"  - 가공 구간 기반 cutting_seconds 설정: {job.cutting_seconds} 초")

    if job.moving_distance is None and {'CNC-X-Position', 'CNC-Y-Position', 'CNC-Z-Position'}.issubset(cnc_df.columns):
        dx = cnc_df['CNC-X-Position'].diff().fillna(0)
        dy = cnc_df['CNC-Y-Position'].diff().fillna(0)
        dz = cnc_df['CNC-Z-Position'].diff().fillna(0)
        dist_3d = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
        job.moving_distance = round(float(dist_3d.sum()), 1)
        print(f"  - 가공 구간 3D 궤적 기반 moving_distance 설정: {job.moving_distance} mm")
        if is_cut is not None and is_cut.any() and job.cutting_moving_distance is None:
            job.cutting_moving_distance = round(float(dist_3d[is_cut].sum()), 1)
            print(f"  - 가공 구간 3D 궤적 기반 cutting_moving_distance 설정: {job.cutting_moving_distance} mm")


def process_tdms_file(job, db=None, target_points=TARGET_DATAPOINTS):
    """Job 의 TDMS 를 가공 구간만 잘라 시각화 Parquet 로 변환한다.

    반환값: (time-domain parquet 경로, FFT parquet 경로) / 실패 시 (None, None)
    """
    print(f"\n[TDMS Visualizer] 작업 시작: Job ID {job.job_id}")
    tdms_path = find_tdms_path(job)

    if not tdms_path or not os.path.exists(tdms_path):
        print(f"  - [오류] TDMS 파일을 찾을 수 없습니다: {archive_path(job, 'tdms_raw_path')}")
        return None, None

    try:
        start_t = time.time()
        print(f"  - TDMS 로딩 중... (경로: {tdms_path}, 크기: {os.path.getsize(tdms_path)/1024/1024:.1f} MB)")

        nc = find_nc_program(job, db)
        if nc is not None and nc.blocks:
            print(f"  - NC 프로그램 참조: {nc.name or '(이름 없음)'} / 동작 블록 {nc.motion_blocks}개")
        else:
            print("  - [알림] NC 프로그램을 찾지 못해 활동 기반으로 가공 구간을 판별합니다.")

        with open_tdms(tdms_path) as tdms:
            if getattr(tdms, "truncated_bytes", 0):
                print(f"  - [알림] 파일 끝 {tdms.truncated_bytes} 바이트가 손상되어 있어 제외하고 읽었습니다.")

            cnc_frame = read_cnc_frame(tdms)
            if cnc_frame.empty and "DAQ" not in tdms:
                print("  - [경고] CNC/DAQ 그룹을 찾을 수 없습니다.")
                return None, None

            window = detect_machining_window(cnc_frame, nc, job.start_time, job.end_time)
            result = build_aligned_frames(tdms, window, cnc_frame, target_points=target_points)

        print(summarize_window(window, result))

        if result.cnc.empty and result.daq.empty:
            print("  - [경고] 가공 구간에서 추출된 데이터가 없습니다.")
            return None, None

        parquet_path = os.path.join(PROCESSED_DIR, f"job_{job.job_id}_viz.parquet")
        fft_parquet_path = os.path.join(PROCESSED_DIR, f"job_{job.job_id}_fft.parquet")
        window_json_path = os.path.join(PROCESSED_DIR, f"job_{job.job_id}_window.json")

        merged = merge_for_parquet(result.cnc, result.daq)
        merged.to_parquet(parquet_path, engine='pyarrow')

        _apply_window_to_job(job, result, result.cnc)

        if not result.fft.empty:
            result.fft.to_parquet(fft_parquet_path, engine='pyarrow')
        else:
            fft_parquet_path = None

        with open(window_json_path, "w", encoding="utf-8") as f:
            json.dump({"job_id": job.job_id, "tdms_file": os.path.basename(tdms_path),
                       "window": window.to_dict(), "daq_sync": result.sync.to_dict(),
                       "meta": result.meta}, f, ensure_ascii=False, indent=2, default=str)

        print(f"  - Time Domain Parquet 저장 완료: {parquet_path} ({len(merged):,}행)")
        if fft_parquet_path:
            print(f"  - Freq Domain Parquet 저장 완료: {fft_parquet_path}")
        print(f"  - 구간 판별 근거 저장: {window_json_path}")
        print(f"  - 소요 시간: {time.time() - start_t:.2f} 초")

        return parquet_path, fft_parquet_path

    except Exception as e:
        import traceback
        print(f"  - [오류] TDMS 파싱 실패: {e}")
        traceback.print_exc()
        return None, None


def _needs_processing(job):
    """Parquet 가 없거나 유실됐거나, NC 없이 만들어졌는데 이제 NC 가 생긴 경우 재처리 대상."""
    parquet_rel = archive_path(job, "tdms_parquet_raw_path")
    if not parquet_rel:
        return True
    abs_p = get_abs_raw_data_path(parquet_rel)
    if not abs_p or not os.path.exists(abs_p):
        return True

    window = job.machining_window or {}
    if not window:
        return True
    # NC 원본 없이 판별한 구간은, 이후 NC 가 업로드되면 한 번 더 판별한다.
    # (NC 를 참조해서 만든 결과라면 방식이 activity 여도 다시 돌리지 않는다 -> 재처리 루프 방지)
    reference = window.get("nc_reference")
    if not reference and find_nc_program(job) is not None:
        print(f"  - Job {job.job_id}: NC 프로그램이 확보되어 가공 구간을 다시 판별합니다.")
        return True

    # 참조는 했는데 프로그램 이름이 'Unknown' 인 구간은 XML 이 도착하기 전에 만들어진 것이다.
    # TDMS 는 파일 하나만 떨어지면 바로 변환 대상이 되는 반면 program_code 와 가공 시작·종료
    # 시각은 XML 이 들어와야 채워진다. 그 전에 돌면 시간 힌트가 없어 블록 커버리지가 0 이 되고,
    # 'nc_reference 가 있다'는 이유로 다시 판별하지도 않아 activity 구간이 그대로 굳었다.
    # 이름이 Unknown 이 아니게 되는 순간(=XML 반영 완료) 한 번 더 돌면 nc_block 으로 확정된다.
    if reference and str(reference).startswith("Unknown"):
        workplan = getattr(job, "workplan", None)
        program = getattr(workplan, "program_code", None) if workplan else None
        if program and program != "Unknown":
            print(f"  - Job {job.job_id}: XML 메타데이터가 확보되어 가공 구간을 다시 판별합니다.")
            return True
    return False


# 변환에 실패한 Job 을 5초마다 다시 시도하면 로그만 쌓이므로, 재시도 간격을 점점 늘린다.
FAILURE_BACKOFF_START = 60.0      # 첫 실패 후 1분
FAILURE_BACKOFF_MAX = 1800.0      # 최대 30분


def run_visualizer_batch():
    print("========================================")
    print(" TDMS Visualizer 백그라운드 서비스 시작")
    print("========================================")

    retry_after = {}   # job_id -> (다음 시도 시각, 다음 대기 시간)

    while True:
        db = SessionLocal()
        try:
            now = time.time()
            # TDMS 경로는 job_file_archive 에 있으므로 조인해서 고른다.
            candidate_jobs = (db.query(Job)
                              .join(JobFileArchive, JobFileArchive.job_id == Job.job_id)
                              .filter(JobFileArchive.tdms_raw_path.isnot(None))
                              .all())
            jobs = [j for j in candidate_jobs
                    if _needs_processing(j) and retry_after.get(j.job_id, (0.0, 0.0))[0] <= now]

            if jobs:
                print(f"총 {len(jobs)}개의 TDMS Parquet 미생성/재판별 건을 처리합니다.")
                for job in jobs:
                    with job_conversion_lock(job.job_id) as acquired:
                        if not acquired:
                            # 다른 파이프라인 인스턴스가 이미 이 Job 을 변환 중이다.
                            # 다음 폴링에서 결과가 반영됐는지 다시 보면 되므로 조용히 넘어간다.
                            continue
                        parquet_path, fft_parquet_path = process_tdms_file(job, db)
                    if not parquet_path:
                        _, wait = retry_after.get(job.job_id, (0.0, FAILURE_BACKOFF_START))
                        wait = min(max(wait, FAILURE_BACKOFF_START) * 2, FAILURE_BACKOFF_MAX)                             if job.job_id in retry_after else FAILURE_BACKOFF_START
                        retry_after[job.job_id] = (time.time() + wait, wait)
                        print(f"  - 파싱 실패 또는 대상 그룹 없음 (Job ID: {job.job_id}) "
                              f"-> {wait/60:.0f}분 후 재시도")
                        continue
                    retry_after.pop(job.job_id, None)

                    # machining_raw_data 내 processed_parquet 폴더에도 동기화 복사
                    raw_parquet_path = parquet_path
                    raw_fft_path = fft_parquet_path
                    if job.source_folder:
                        raw_job_dir = os.path.join(RAW_DATA_ROOT,
                                                   *job.source_folder.split('/'), job_layout.PARQUET_DIR)
                        os.makedirs(raw_job_dir, exist_ok=True)
                        import shutil
                        raw_parquet_path = os.path.join(raw_job_dir, f"job_{job.job_id}_viz.parquet")
                        shutil.copy2(parquet_path, raw_parquet_path)
                        if fft_parquet_path:
                            raw_fft_path = os.path.join(raw_job_dir, f"job_{job.job_id}_fft.parquet")
                            shutil.copy2(fft_parquet_path, raw_fft_path)

                    # Vault에도 Parquet 이중 백업
                    tdms_parquet_vault = save_to_vault(parquet_path, "processed_parquet", f"job_{job.job_id}_viz.parquet")
                    tdms_fft_vault = save_to_vault(fft_parquet_path, "processed_parquet", f"job_{job.job_id}_fft.parquet") if fft_parquet_path else None

                    # 원본 위치와 보관소 위치를 같은 행에 나란히 적는다.
                    job_archive = get_or_create_job_archive(db, job.job_id)
                    job_archive.tdms_parquet_raw_path = get_rel_raw_data_path(raw_parquet_path)
                    job_archive.tdms_fft_parquet_raw_path = (
                        get_rel_raw_data_path(raw_fft_path) if raw_fft_path else None)
                    # 보관소 경로는 vault_layout 규칙으로 계산되므로 저장하지 않는다.

                    db.commit()
                    print(f"  - DB, Vault 및 raw_data 동기화 완료 (Job ID: {job.job_id})")
        except Exception as e:
            import traceback
            print(f"배치 실행 중 오류 발생: {e}")
            traceback.print_exc()
            db.rollback()
        finally:
            db.close()

        time.sleep(5)  # 5초 간격 폴링


if __name__ == "__main__":
    from DB.schema_patch import ensure_schema
    ensure_schema()
    run_visualizer_batch()
