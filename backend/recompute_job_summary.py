"""기존 Job 의 가공 요약을 TDMS(CNC 채널)+NC 코드 기준으로 다시 계산한다 (미리보기 / --apply).

    python backend/recompute_job_summary.py 1 2              # 미리보기: 지금 값 -> 새 값 (DB 는 그대로)
    python backend/recompute_job_summary.py 1 2 --apply      # 반영. 바꾸기 전 값을 backups/ 에 JSON 으로 남긴다
    python backend/recompute_job_summary.py --all [--apply]  # TDMS 가 연결된 모든 Job

다시 계산하는 것 (tdms_visualizer 와 같은 함수 · 같은 규칙)
  가공 시작·종료(실행 사이클), 절삭시간, 이동거리(공구 길이 보정 G43/G49 등 위치 표시 점프 제외),
  절삭 이동거리(NC 코드), 작업 ID, 장비 코드, 완료 여부(와 그 근거),
  가공 프로그램(TDMS CNC-ProgramName), CNC 요약(machine_log 최대·평균, TDMS 기준),
  장비 로그가 있으면 장비 계산값(machine_indicators)과 알람(열이 없으면 '모름').

다시 만들지 않는 것: 그래프용 Parquet. 가공 구간 행이 그대로라 내용이 같고, DAQ 시계 보정값(daq_sync)도
기존 값을 둔다. DAQ 를 다시 읽지 않으므로 수백 MB TDMS 한 건에 수 초면 끝난다.

예전에 XML 로 적힌 Job(summary_source='XML')도 이 스크립트로 반영하면 'TDMS' 가 된다.
"""
import argparse
import json
import os
import sys
from datetime import datetime

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pandas as pd  # noqa: E402

import tdms_visualizer as tv  # noqa: E402
from DB.database import SessionLocal  # noqa: E402
from DB.models import Job, JobFileArchive  # noqa: E402
from file_archive import archive_path  # noqa: E402
from parsers.log_parser import alarm_summary, read_log_rows, summarize_machine_indicators  # noqa: E402
from parsers.nc_parser import nc_program_name  # noqa: E402
from tdms_alignment import (AlignedResult, describe_cycle, detect_machining_window,  # noqa: E402
                            machining_program, open_tdms, parse_nc_blocks, read_cnc_frame)
from vault_manager import PROJECT_ROOT, get_abs_raw_data_path  # noqa: E402

JOB_FIELDS = ("start_time", "end_time", "cutting_seconds", "moving_distance", "cutting_moving_distance",
              "work_id", "machine_code", "is_finish", "is_error", "summary_source")
LOG_FIELDS = ("max_spindle_load", "max_spindle_rpm", "max_feed_rate", "avg_spindle_rpm", "alarm_count",
              "stats_source")
BACKUP_DIR = os.path.join(PROJECT_ROOT, "backups")


def _plain(value):
    return value.isoformat(sep=" ") if isinstance(value, datetime) else value


def snapshot(job):
    """비교·백업용으로 Job 의 가공 요약과 CNC 요약을 평평한 dict 로."""
    out = {field: _plain(getattr(job, field)) for field in JOB_FIELDS}
    out["program_code"] = job.workplan.program_code if job.workplan is not None else None
    m_log = job.machine_log
    for field in LOG_FIELDS:
        out[f"machine_log.{field}"] = _plain(getattr(m_log, field)) if m_log is not None else None
    out["machine_log.machine_indicators"] = m_log.machine_indicators if m_log is not None else None
    out["machining_window"] = job.machining_window
    return out


def _log_path(job):
    """Job 에 연결된 장비 로그 원본 경로. 없으면 None."""
    m_log = job.machine_log
    archive = m_log.file_archive if m_log is not None else None
    path = get_abs_raw_data_path(archive.log_raw_path) if archive is not None and archive.log_raw_path else None
    return path if path and os.path.exists(path) else None


def recompute(db, job, cnc_frame=None, flush=True):
    """job 을 TDMS+NC 기준으로 다시 계산해 세션에 반영한다 (커밋은 부른 쪽이 한다). (전, 후) 스냅샷.

    cnc_frame 을 넘기면 TDMS 를 읽지 않고 그 프레임을 쓴다 (시험용).
    """
    before = snapshot(job)
    tdms_path = tv.find_tdms_path(job)
    if cnc_frame is None:
        if not tdms_path or not os.path.exists(tdms_path):
            raise FileNotFoundError(f"Job {job.job_id}: TDMS 원본을 찾지 못했습니다")
        with open_tdms(tdms_path) as tdms:
            cnc_frame = read_cnc_frame(tdms)

    program = machining_program(cnc_frame)
    nc_data, nc_file = tv.find_nc_source(job, db, prefer_name=program)
    nc = None
    if nc_data is not None:
        nc = parse_nc_blocks(nc_data, name=program or nc_program_name(nc_data) or nc_file)
    # 힌트는 지금 DB 의 시작·종료(예전 XML 값일 수 있다). 같은 프로그램이 여러 번 기록된 TDMS 에서 같은 실행을 고르게 한다.
    window = detect_machining_window(cnc_frame, nc, job.start_time, job.end_time)
    describe_cycle(cnc_frame, window)
    tv.record_nc_program(window, nc_data)         # 변환 때와 같이 NC 의 프로그램 이름·불일치를 남긴다

    old_sync = (job.machining_window or {}).get("daq_sync") or {}
    result = AlignedResult(cnc=pd.DataFrame(), daq=pd.DataFrame(), fft=pd.DataFrame(), window=window, meta={})
    job.summary_source = None          # 예전 XML 값도 새 기준으로 덮어쓴다
    tv._apply_window_to_job(job, result, cnc_frame, db=db, nc_data=nc_data,
                            tdms_path=tdms_path or archive_path(job, "tdms_raw_path"))
    # DAQ 보정값만 기존 값을 둔다 (window 에는 _apply_window_to_job 이 채운 위치 점프 횟수·합도 들어 있다)
    job.machining_window = {**window.to_dict(), "daq_sync": old_sync}

    # 장비 로그: 장비 계산값과 알람을 지금 규칙으로 다시 읽는다 (최대·평균은 위에서 TDMS 로 계산했다).
    log_path = _log_path(job)
    if log_path and job.machine_log is not None:
        fieldnames, rows = read_log_rows(log_path)
        if rows:
            job.machine_log.machine_indicators = summarize_machine_indicators(rows, os.path.basename(log_path))
            job.machine_log.alarm_count, job.machine_log.critical_alarm_msg = alarm_summary(fieldnames, rows)
    if flush:
        db.flush()
    return before, snapshot(job)


def _short(value):
    if isinstance(value, dict):
        if "values" in value:          # 장비 계산값
            return f"장비 계산값 {len(value['values'])}개 (전부 0 {len(value.get('all_zero', []))}개)"
        return "{...}"
    return str(value)


def _same_when_stored(key, old, new):
    """DB 에 적히는 값으로 비교한다. 시작·종료는 초 단위 DATETIME 이라 계산값의 1/1000초는 반올림돼 저장된다
    (16:46:58.716 -> 16:46:59) — 그 차이만 있으면 바뀌지 않는 값이다."""
    if key in ("start_time", "end_time") and old is not None and new is not None:
        return pd.Timestamp(old).round("s") == pd.Timestamp(new).round("s")
    return old == new


def print_diff(job, before, after):
    print(f"\n=== Job {job.job_id} ({job.source_folder}) ===")
    for key in list(after):
        if key == "machining_window":
            continue
        old, new = before.get(key), after.get(key)
        mark = "  " if _same_when_stored(key, old, new) else "* "
        print(f"  {mark}{key:<32} {_short(old):<34} -> {_short(new)}")
    window = after.get("machining_window") or {}
    print(f"    (가공 구간: {window.get('method')} · NC 블록 {window.get('matched_blocks')}/{window.get('program_blocks')}"
          f" · 첫 절삭 {window.get('local_first_cut')} · 완료 근거 {window.get('program_end_basis')}"
          f" · 이동거리에서 뺀 위치 점프 {window.get('position_jumps')}번 {window.get('position_jump_mm')}mm)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="기존 Job 의 가공 요약을 TDMS+NC 기준으로 다시 계산")
    ap.add_argument("job_ids", nargs="*", type=int, help="다시 계산할 Job ID")
    ap.add_argument("--all", action="store_true", help="TDMS 가 연결된 모든 Job")
    ap.add_argument("--apply", action="store_true", help="DB 에 반영 (없으면 미리보기)")
    args = ap.parse_args(argv)
    if not args.job_ids and not args.all:
        ap.error("Job ID 를 적거나 --all 을 주세요")

    from DB.schema_patch import ensure_schema
    ensure_schema(verbose=True)       # summary_source · machine_log 의 새 칸이 있어야 한다

    db = SessionLocal()
    backup = []
    try:
        query = db.query(Job).join(JobFileArchive, JobFileArchive.job_id == Job.job_id) \
            .filter(JobFileArchive.tdms_raw_path.isnot(None))
        if not args.all:
            query = query.filter(Job.job_id.in_(args.job_ids))
        jobs = query.order_by(Job.job_id).all()
        missing = sorted(set(args.job_ids) - {j.job_id for j in jobs})
        if missing:
            print(f"[알림] TDMS 가 연결되지 않았거나 없는 Job 은 건너뜁니다: {missing}")

        for job in jobs:
            try:
                before, after = recompute(db, job, flush=args.apply)
            except Exception as e:
                db.rollback()
                print(f"\n=== Job {job.job_id}: 다시 계산하지 못했습니다 — {e}")
                continue
            print_diff(job, before, after)
            if args.apply:
                db.commit()
                backup.append({"job_id": job.job_id, "before": before, "after": after})
            else:
                db.rollback()
    finally:
        db.close()

    if args.apply and backup:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, f"recompute_job_summary_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[반영 완료] Job {len(backup)}건. 바꾸기 전 값: {path}")
    elif not args.apply:
        print("\n[미리보기] DB 는 바꾸지 않았습니다. 반영하려면 --apply 를 붙이세요. (* 표시가 바뀌는 값)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
