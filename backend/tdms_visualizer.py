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

import pandas as pd

import job_layout
from DB.database import SessionLocal
from DB.models import Job, JobFileArchive, MachineLog, Workplan, WorkplanFileArchive
from file_archive import archive_path, get_or_create_job_archive
import vault_layout
from vault_manager import archive_file, get_abs_raw_data_path, get_rel_raw_data_path

from tdms_alignment import (TARGET_DATAPOINTS, build_aligned_frames, cutting_mask,
                            describe_cycle, detect_machining_window, machining_program,
                            merge_for_parquet, open_tdms, parse_nc_blocks,
                            parse_tdms_filename, read_cnc_frame, row_seconds, summarize_window,
                            travel_steps)
from parsers.nc_parser import nc_cutting_distance, nc_program_name

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

    # 보관소 탐색. 보관소는 원본 트리를 그대로 비추므로 Job 폴더의 TDMS 분류를 본다.
    return vault_layout.find_job_tdms(job, tdms_raw)


def _usable_program(code):
    """임시값('Unknown')이 아닌 프로그램 코드. 없으면 None."""
    return code if code and str(code).strip().lower() != "unknown" else None


def find_nc_source(job, db=None, prefer_name=None):
    """Job 이 실행한 NC 원본을 (bytes, 파일 이름) 으로 찾는다. 못 찾으면 (None, None).

    가공 구간 판별의 기준이 되므로 DB BLOB -> Vault -> raw_data 폴더 순으로 최대한 찾는다.
    (업로드 순서에 따라 TDMS 가 NC 보다 먼저 들어올 수 있어, 못 찾으면 활동 기반 판별로 넘어간다.
     이후 NC 가 들어오면 재처리 대상이 된다.)
    prefer_name: Job 폴더에 NC 가 여럿일 때 고를 프로그램 이름 (TDMS 의 가공 프로그램).
    """
    own_session = db is None
    db = db or SessionLocal()
    try:
        workplan = db.query(Workplan).filter_by(workplan_id=job.workplan_id).first() if job.workplan_id else None
        name = prefer_name or _usable_program(workplan.program_code if workplan else None)

        # 1) DB에 보관된 NC 원본 바이너리
        if job.workplan_id:
            arch = db.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
            if arch is not None:
                if arch.nc_file_content:
                    return bytes(arch.nc_file_content), (os.path.basename(arch.nc_raw_path)
                                                         if arch.nc_raw_path else None)
                # 원본 폴더 -> 보관소 순으로 찾아 내려간다. 보관소는 원본 트리를 그대로
                # 비추므로 Job 폴더의 NC 분류를 보면 된다 (원래 파일명 그대로).
                for p in (get_abs_raw_data_path(arch.nc_raw_path) if arch.nc_raw_path else None,
                          vault_layout.find_job_nc(job, arch.nc_raw_path)):
                    if p and os.path.exists(p):
                        with open(p, "rb") as f:
                            return f.read(), os.path.basename(p)

        # 2) Workplan 아카이브에 기록된 경로
        wp_nc = archive_path(workplan, "nc_raw_path")
        if wp_nc:
            p = get_abs_raw_data_path(wp_nc)
            if p and os.path.exists(p):
                with open(p, "rb") as f:
                    return f.read(), os.path.basename(p)

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
                    return f.read(), os.path.basename(p)
    except Exception as e:
        print(f"  - [경고] NC 프로그램 탐색 중 오류: {e}")
    finally:
        if own_session:
            db.close()
    return None, None


def find_nc_program(job, db=None, name=None):
    """Job 이 실행한 NC 를 블록 대조용 NcProgram 으로. 못 찾으면 None.

    이름(다른 프로그램 행을 거르는 기준): 넘겨받은 이름(TDMS 가공 프로그램) > Workplan 의 프로그램 코드 >
    NC 첫머리(<O1110.NC>) > 파일 이름. 임시값 'Unknown' 은 쓰지 않는다.
    """
    data, file_name = find_nc_source(job, db, prefer_name=name)
    if data is None:
        return None
    if not name:
        workplan = getattr(job, "workplan", None)
        name = (_usable_program(getattr(workplan, "program_code", None))
                or nc_program_name(data) or file_name)
    return parse_nc_blocks(data, name=name)


# machine_log 의 최대·평균 값에 쓰는 채널 (장비 로그의 cls · crpm · cfr 와 같은 뜻)
_SPINDLE_LOAD_COLUMNS = ('CNC-Z-SpindleLoad', 'CNC-SpindleLoad', 'CNC-W-SpindleLoad')
_SPINDLE_RPM_COLUMNS = ('CNC-Z-SpindleSpeed', 'CNC-SpindleSpeed', 'CNC-W-SpindleSpeed')
_FEED_COLUMNS = ('CNC-ActualFeedRate',)


def _tdms_machine_stats(rows):
    """실행 사이클의 CNC 채널로 machine_log 의 최대·평균 값을 낸다. 채널이 없으면 그 값은 뺀다."""
    def series(candidates):
        column = next((c for c in candidates if c in rows.columns), None)
        return pd.to_numeric(rows[column], errors="coerce").dropna() if column else pd.Series(dtype=float)

    load, rpm, feed = series(_SPINDLE_LOAD_COLUMNS), series(_SPINDLE_RPM_COLUMNS), series(_FEED_COLUMNS)
    stats = {}
    if len(load):
        stats["max_spindle_load"] = round(float(load.max()), 1)
    if len(rpm):
        stats["max_spindle_rpm"] = round(float(rpm.max()), 1)
        stats["avg_spindle_rpm"] = round(float(rpm.mean()), 2)
    if len(feed):
        stats["max_feed_rate"] = round(float(feed.max()), 1)
    return stats


def _cycle_rows(cnc_frame, window):
    """실행 사이클의 CNC 행 (원본 해상도). 사이클을 모르면 가공 구간."""
    if cnc_frame is None or cnc_frame.empty:
        return None
    if window.cycle_row_start is not None:
        return cnc_frame.iloc[window.cycle_row_start:window.cycle_row_end]
    return cnc_frame.iloc[window.row_start:window.row_end]


def _adopt_tdms_program(job, program, db):
    """Workplan 의 프로그램 코드를 TDMS 의 가공 프로그램(CNC-ProgramName)으로 맞춘다.

    같은 부품에 같은 이름·같은 NC 해시의 Workplan 이 이미 있으면 그쪽으로 합친다
    (adopt_workplan_identity — 같은 NC 를 다시 돌린 것이므로 Workplan 하나에 Job 여럿이 맞다).
    """
    if not program or db is None or job.workplan is None:
        return
    program = str(program).strip()[:50]
    current = job.workplan.program_code
    if current == program:
        return
    from job_manager import adopt_workplan_identity
    adopt_workplan_identity(db, job, program_code=program)
    print(f"  - 가공 프로그램을 TDMS 기준으로 맞췄습니다: {current} -> {program}")


def record_nc_program(window, nc_data):
    """NC 첫머리의 프로그램 이름을 가공 구간에 남긴다 (변환과 recompute_job_summary 가 함께 쓴다).

    TDMS 에서 깎은 프로그램과 다르면 Job 화면이 경고하고, 구간 메모에도 적는다
    (업로드 화면도 올리기 전에 같은 규칙으로 비교한다).
    """
    window.nc_program = nc_program_name(nc_data) if nc_data is not None else None
    if window.program_mismatch:
        note = (f"업로드한 NC 의 프로그램({window.nc_program})과 TDMS 에서 가공한 프로그램"
                f"({window.program_name})이 다릅니다. NC 파일을 확인하세요.")
        window.notes.append(note)
        print(f"  - [경고] {note}")


def _apply_window_to_job(job, result, cnc_frame, db=None, nc_data=None, tdms_path=None):
    """판별된 실행 사이클과 CNC 채널(+NC 코드)로 Job 의 가공 요약을 채운다.

    출처 규칙 (job.summary_source)
      XML  TDMS 기준으로 바꾸기 전에 XML 로 적힌 예전 Job -> 빈 칸만 채운다 (쌓인 기록을 바꾸지 않는다).
      그 밖 TDMS 계산값으로 덮어쓰고 'TDMS' 로 표시한다. 파일명에서 얻은 임시 시작 시각이나 TDMS 보다
           먼저 도착한 XML 값도 여기서 바뀐다.

    계산 기준 (원본 해상도의 CNC 행으로 계산한다. 예전에는 그래프용으로 최대 1만 점까지 줄인 행으로
    계산해 긴 가공에서 이동거리가 짧게 나왔다)
      시작·종료   실행 사이클: 운전 상태가 실행으로 바뀐 순간 ~ 프로그램 끝 (한국 시간)
      절삭시간    사이클 안에서 절삭 G 코드(G01~G03·고정 사이클) + 실제 이송 > 0 + 주축 회전인 시간
      이동거리    사이클 안의 X·Y·Z 실제 이동 합. 공구 길이 보정(G43/G49)을 켜고 끌 때 위치 표시만 한 번에
                  공구 길이만큼 바뀌는 점프는 뺀다 (tdms_alignment.travel_steps). 뺀 횟수·합은 가공 구간의
                  position_jumps · position_jump_mm (장비 XML 의 MovingDistance 는 점프도 이동으로 센다)
      절삭이동    NC 코드의 절삭 경로 길이. NC 블록 대조로 같은 프로그램임을 확인했을 때만 쓰고,
                  아니면 TDMS 절삭 구간의 실제 이동 합을 쓴다 (점프는 여기서도 뺀다)
      완료 여부   프로그램 끝(M02/M30 또는 마지막 블록 근처)까지 실행했는지. 모르면 두고, 에러 여부는
                  TDMS 로 알 수 없어 건드리지 않는다
      작업 ID     가공 시작 시각 YYMMDDHHMMSS (장비 XML 의 WorkID 와 같은 규칙)
      장비 코드   TDMS 파일명 앞부분 (DNM200)
      프로그램    TDMS CNC-ProgramName 에서 절삭이 일어난 프로그램
    """
    window = result.window
    from_tdms = job.summary_source != "XML"

    def put(attr, value):
        if value is not None and (from_tdms or getattr(job, attr) is None):
            setattr(job, attr, value)

    start, end = window.machining_start, window.machining_end
    put("start_time", start)
    put("end_time", end)

    rows = _cycle_rows(cnc_frame, window)
    tdms_cut_distance = None
    if rows is not None and len(rows) > 1:
        cut = cutting_mask(rows)
        put("cutting_seconds", round(float(row_seconds(rows)[cut].sum()), 1))
        travel = travel_steps(rows)
        if travel is not None:
            step, jump = travel
            window.position_jumps = int(jump.sum())
            window.position_jump_mm = round(float(step[jump].sum()), 1)
            put("moving_distance", round(float(step[~jump].sum()), 1))
            tdms_cut_distance = round(float(step[cut & ~jump].sum()), 1)

        # CNC 요약(machine_log): 장비 로그(10Hz)보다 촘촘한 TDMS CNC 채널(약 27Hz)로 계산한다.
        # 알람은 TDMS 에 정보가 없으므로 건드리지 않는다. 장비 계산값은 로그 파서가 따로 적는다.
        stats = _tdms_machine_stats(rows)
        if stats:
            m_log = job.machine_log
            if m_log is None:
                m_log = MachineLog()
                job.machine_log = m_log
            if from_tdms or m_log.max_spindle_load is None:
                for key, value in stats.items():
                    setattr(m_log, key, value)
                m_log.stats_source = "TDMS"

    # 점프 횟수·합까지 채운 뒤에 적는다 (JSON 칸은 새 dict 를 넣어야 바뀐 것으로 친다)
    job.machining_window = {**window.to_dict(), "daq_sync": result.sync.to_dict()}

    nc_distance = None
    if nc_data is not None and window.method == "nc_block":
        try:
            nc_distance = nc_cutting_distance(nc_data)
        except Exception as e:
            print(f"  - [경고] NC 절삭 경로 길이를 계산하지 못했습니다: {e}")
    put("cutting_moving_distance", round(nc_distance, 1) if nc_distance is not None else tdms_cut_distance)

    put("machine_code", parse_tdms_filename(tdms_path)["machine"] if tdms_path else None)
    if start is not None and (from_tdms or job.work_id in (None, "", "UNKNOWN_WORKID")):
        job.work_id = start.strftime("%y%m%d%H%M%S")
    put("is_finish", window.program_end_reached)

    if from_tdms:
        job.summary_source = "TDMS"
        _adopt_tdms_program(job, window.program_name, db)

    jumps = (f"(위치 점프 {window.position_jumps}번 {window.position_jump_mm}mm 제외)"
             if window.position_jumps else "")
    print(f"  - 가공 요약({'TDMS 기준' if from_tdms else 'XML 유지, 빈 칸만'}): "
          f"시작 {job.start_time} · 종료 {job.end_time} · 절삭 {job.cutting_seconds}s · "
          f"이동 {job.moving_distance}mm{jumps} · 절삭이동 {job.cutting_moving_distance}mm"
          f"{'(NC)' if nc_distance is not None and from_tdms else ''} · 완료 {job.is_finish}")


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

        with open_tdms(tdms_path) as tdms:
            if getattr(tdms, "truncated_bytes", 0):
                print(f"  - [알림] 파일 끝 {tdms.truncated_bytes} 바이트가 손상되어 있어 제외하고 읽었습니다.")

            cnc_frame = read_cnc_frame(tdms)
            if cnc_frame.empty and "DAQ" not in tdms:
                print("  - [경고] CNC/DAQ 그룹을 찾을 수 없습니다.")
                return None, None

            # 가공 프로그램은 TDMS 의 CNC-ProgramName 중 실제로 절삭이 일어난 프로그램이다.
            # 파일명·채널 속성의 이름은 기록을 시작할 때 값이라 다를 수 있다 (O2202.NC 프로브 -> O0911.nc).
            # NC 블록 대조도 이 이름으로 다른 프로그램(프로브, 공구교환 매크로 O9009)의 행을 거른다.
            program = machining_program(cnc_frame)
            nc_data, nc_file = find_nc_source(job, db, prefer_name=program)
            nc = None
            if nc_data is not None:
                workplan = getattr(job, "workplan", None)
                nc_name = (program or _usable_program(getattr(workplan, "program_code", None))
                           or nc_program_name(nc_data) or nc_file)
                nc = parse_nc_blocks(nc_data, name=nc_name)
                print(f"  - NC 프로그램 참조: {nc.name or '(이름 없음)'} / 동작 블록 {nc.motion_blocks}개 "
                      f"(TDMS 가공 프로그램: {program or '모름'})")
            else:
                print("  - [알림] NC 프로그램을 찾지 못해 활동 기반으로 가공 구간을 판별합니다.")

            window = detect_machining_window(cnc_frame, nc, job.start_time, job.end_time)
            describe_cycle(cnc_frame, window)
            record_nc_program(window, nc_data)
            result = build_aligned_frames(tdms, window, cnc_frame, target_points=target_points)

        print(summarize_window(window, result))

        if result.cnc.empty and result.daq.empty:
            print("  - [경고] 가공 구간에서 추출된 데이터가 없습니다.")
            return None, None

        # 작업 폴더도 Job 계층으로 나눈다. 파일 이름에서 job_id 를 뺐기 때문에
        # 평면 폴더에 그대로 두면 Job 끼리 서로 덮어쓴다.
        work_dir = os.path.join(PROCESSED_DIR, *job.source_folder.split('/')) \
            if job.source_folder else os.path.join(PROCESSED_DIR, f"job_{job.job_id}")
        os.makedirs(work_dir, exist_ok=True)
        parquet_path = os.path.join(work_dir, "viz.parquet")
        fft_parquet_path = os.path.join(work_dir, "fft.parquet")
        window_json_path = os.path.join(work_dir, "window.json")

        merged = merge_for_parquet(result.cnc, result.daq)
        merged.to_parquet(parquet_path, engine='pyarrow')

        _apply_window_to_job(job, result, cnc_frame, db=db, nc_data=nc_data, tdms_path=tdms_path)

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
    """Parquet 가 없거나 유실됐거나, NC 없이 만들어졌는데 이제 NC 가 생긴 경우 재처리 대상.

    TDMS 와 NC 는 어느 쪽이 먼저 도착해도 된다. TDMS 를 NC 없이 먼저 판별했다면(활동 기반 구간,
    절삭 이동거리는 TDMS 실측) NC 가 들어온 뒤 한 번 더 판별해 NC 블록 대조·NC 절삭 경로 길이로 바꾼다.
    예전에는 'XML 이 도착해 프로그램 이름을 알게 되면 다시 판별' 하는 규칙도 있었다. 지금은 가공
    프로그램을 TDMS 의 CNC-ProgramName 에서 바로 읽으므로 XML 을 기다릴 일이 없다.
    """
    parquet_rel = archive_path(job, "tdms_parquet_raw_path")
    if not parquet_rel:
        return True
    abs_p = get_abs_raw_data_path(parquet_rel)
    if not abs_p or not os.path.exists(abs_p):
        return True

    window = job.machining_window or {}
    if not window:
        return True
    # NC 를 참조해서 만든 결과라면 방식이 activity 여도 다시 돌리지 않는다 -> 재처리 루프 방지
    if not window.get("nc_reference") and find_nc_program(job) is not None:
        print(f"  - Job {job.job_id}: NC 프로그램이 확보되어 가공 구간을 다시 판별합니다.")
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
                        # 파일 이름에 job_id 를 쓰지 않는다. Job 폴더 안에 있으므로 이름으로
                        # 구분할 필요가 없고, 재수집으로 id 가 바뀌어도 어긋나지 않는다.
                        raw_parquet_path = os.path.join(raw_job_dir, "viz.parquet")
                        shutil.copy2(parquet_path, raw_parquet_path)
                        if fft_parquet_path:
                            raw_fft_path = os.path.join(raw_job_dir, "fft.parquet")
                            shutil.copy2(fft_parquet_path, raw_fft_path)

                    # 보관소에도 이중 백업 (원본과 같은 자리)
                    archive_file(raw_parquet_path)
                    if raw_fft_path:
                        archive_file(raw_fft_path)

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
