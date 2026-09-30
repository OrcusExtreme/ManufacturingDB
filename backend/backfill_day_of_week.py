"""이미 쌓인 env_memo 의 day_of_week 를 가공 시작시각 기준 영어 요일로 채운다.

지금은 Job 이 flush 될 때마다 DB/models.py 의 `_sync_day_of_week` 가 자동으로 맞추지만,
그 전에 들어온 행은 비어 있거나 로케일에 따라 한국어('금요일')로 적혀 있을 수 있다.
이 스크립트로 한 번 정리한다.

요일은 job.start_time 에서 계산되는 파생값이라 기존 값이 있어도 **날짜 기준으로 덮어쓴다**
(그것이 두 값이 어긋나는 상태를 없애는 유일한 방법이다). start_time 을 모르는 Job 은
건드리지 않고 그대로 둔다.

사용법
------
    python backend/backfill_day_of_week.py            # 미리보기
    python backend/backfill_day_of_week.py --apply
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from DB.database import SessionLocal
from DB.models import DAY_NAMES, EnvMemo, Job, english_day_of_week
from job_manager import ensure_env_memo


def main(argv):
    apply = "--apply" in argv
    print("=" * 70)
    print(" env_memo.day_of_week 채우기 (영어 요일)")
    print("=" * 70)
    print(f"  쓰는 값: {' · '.join(DAY_NAMES)}")
    print()

    db = SessionLocal()
    changed = unknown = already = 0
    try:
        jobs = db.query(Job).order_by(Job.job_id).all()
        if not jobs:
            print("  Job 이 없습니다.")
            return 0

        for job in jobs:
            wanted = english_day_of_week(job.start_time)
            memo = db.query(EnvMemo).filter_by(job_id=job.job_id).first()
            current = memo.day_of_week if memo else None

            if wanted is None:
                print(f"  ?  Job {job.job_id}: 시작시각을 몰라 건너뜁니다 "
                      f"(start_time={job.start_time!r})")
                unknown += 1
                continue
            if current == wanted:
                already += 1
                continue

            print(f"  {'→' if apply else '~'}  Job {job.job_id} ({job.start_time}): "
                  f"{current!r} -> {wanted!r}")
            if apply:
                if memo is None:
                    memo = ensure_env_memo(db, job.job_id)
                memo.day_of_week = wanted
            changed += 1

        if apply:
            db.commit()
    finally:
        db.close()

    print()
    print("=" * 70)
    if apply:
        print(f" 완료: {changed}건 갱신 · {already}건 이미 일치 · {unknown}건 시작시각 불명")
    else:
        print(f" 미리보기: {changed}건 갱신 대상 · {already}건 이미 일치 · {unknown}건 시작시각 불명")
        print(" 실제 적용: python backend/backfill_day_of_week.py --apply")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
