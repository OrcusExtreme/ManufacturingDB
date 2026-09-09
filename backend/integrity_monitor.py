"""원본 복원 검증 백그라운드 감시자.

사람이 화면에서 눌러 확인하던 SHA-256 복원 검증을, 파이프라인이 백그라운드에서
주기적으로 수행하고 결과는 로그로만 남긴다. 해시 재계산 자체는 integrity.py의
단일 로직(verify_content)을 그대로 쓴다 - 검증 근거가 두 곳으로 갈라지면 안 된다.

판정은 3-상태를 유지한다: 일치 / 불일치 / 검증 불가(기준 해시 미기록).
"불일치"만 실제 사고이며, "검증 불가"는 실패가 아니라 판단 근거가 없는 상태다.
"""
import threading
import time

from sqlalchemy.orm import Session

from DB.database import engine
from DB.models import CadFileArchive, JobFileArchive, Part, WorkplanFileArchive
from integrity import verify_content

VERIFY_INTERVAL_SECONDS = 30 * 60


def _collect_targets(session):
    """(라벨, 원본 바이트, 기준 해시) 목록 - DB에 원본이 보존된 모든 아카이브."""
    targets = []
    for row in session.query(JobFileArchive).all():
        targets.append((f"Job {row.job_id} XML", row.xml_file_content, row.xml_file_sha256))
    for row in session.query(WorkplanFileArchive).all():
        targets.append((f"Workplan {row.workplan_id} NC", row.nc_file_content, row.nc_file_sha256))
    for row in session.query(CadFileArchive).all():
        # part_code는 숫자 키라 로그만 보고 어떤 부품인지 알기 어려우므로 이름을 함께 남긴다.
        part_row = session.query(Part).filter_by(part_code=row.part_code).first()
        part_label = part_row.part_name if part_row else row.part_code
        targets.append((f"Part {part_label} CAD ({row.file_name})", row.file_content, row.file_sha256))
    return targets


def verify_all_archives():
    """DB에 보존된 모든 원본(XML/NC/CAD)을 재검증하고 요약 통계를 반환한다."""
    summary = {"match": 0, "mismatch": 0, "unknown": 0, "no_content": 0}
    mismatches = []

    with Session(engine) as session:
        for label, content, stored_hash in _collect_targets(session):
            result = verify_content(content, stored_hash)
            if result is None:
                summary["no_content"] += 1
            elif result.ok is True:
                summary["match"] += 1
            elif result.ok is False:
                summary["mismatch"] += 1
                mismatches.append((label, result))
            else:
                summary["unknown"] += 1

    print(
        f"[원본 복원 검증] 일치 {summary['match']}건 / 불일치 {summary['mismatch']}건 / "
        f"검증 불가 {summary['unknown']}건 / 원본 미보존 {summary['no_content']}건"
    )
    for label, result in mismatches:
        print(
            f"  [경고] 원본 불일치 감지 - {label}\n"
            f"         기준 해시 : {result.hash_stored}\n"
            f"         재계산 해시: {result.hash_actual}"
        )
    return summary


def start_background_verification(interval_seconds=VERIFY_INTERVAL_SECONDS):
    """파이프라인과 함께 도는 검증 데몬 스레드를 띄운다 (시작 직후 1회 + 주기 반복)."""
    def _loop():
        while True:
            try:
                verify_all_archives()
            except Exception as e:
                print(f"[원본 복원 검증 오류] {e}")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True, name="integrity-verifier")
    thread.start()
    return thread


if __name__ == "__main__":
    verify_all_archives()
