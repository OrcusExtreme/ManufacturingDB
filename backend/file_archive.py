"""파일 경로를 archive 테이블 한 곳에서만 읽고 쓰기 위한 공용 헬퍼.

경로가 두 벌인 이유
-------------------
같은 파일이 두 군데에 있다.

    *_raw_path    RAW_DATA_ROOT 기준 — 장비가 떨군 원본이 놓인 자리
    *_vault_path  VAULT_ROOT   기준 — 원본이 지워져도 남는 보관소 사본

다운로드와 복원이 '원본 폴더 -> 보관소 -> LONGBLOB' 순으로 찾아 내려가기 때문에 둘 다
필요하다. 예전에는 raw 쪽을 주 테이블(job/workplan/surface_roughness)에, vault 쪽을
archive 테이블에 나눠 두었는데, 이름이 비슷해 같은 값처럼 보이는 데다 한 파일의 위치를
알려면 두 테이블을 봐야 했다. 지금은 둘 다 archive 테이블에 있고 이름으로 기준을 구분한다.

archive 행이 없을 때
--------------------
경로를 기록하려는 시점에 archive 행이 아직 없을 수 있다(파서마다 도착 순서가 다르다).
get_or_create_* 가 없으면 만들어서 돌려주므로 호출부는 순서를 신경 쓰지 않아도 된다.
"""
from DB.models import (JobFileArchive, WorkplanFileArchive,
                       LogFileArchive, SurfaceRoughnessArchive)


def get_or_create_job_archive(db, job_id):
    """job_file_archive 행을 얻는다. 없으면 만든다."""
    row = db.query(JobFileArchive).filter_by(job_id=job_id).first()
    if row is None:
        row = JobFileArchive(job_id=job_id)
        db.add(row)
        db.flush()
    return row


def get_or_create_workplan_archive(db, workplan_id):
    """workplan_file_archive 행을 얻는다. 없으면 만든다."""
    row = db.query(WorkplanFileArchive).filter_by(workplan_id=workplan_id).first()
    if row is None:
        row = WorkplanFileArchive(workplan_id=workplan_id)
        db.add(row)
        db.flush()
    return row


def get_or_create_log_archive(db, log_id):
    """log_file_archive 행을 얻는다. 없으면 만든다."""
    row = db.query(LogFileArchive).filter_by(log_id=log_id).first()
    if row is None:
        row = LogFileArchive(log_id=log_id)
        db.add(row)
        db.flush()
    return row


def get_or_create_roughness_archive(db, roughness_id):
    """surface_roughness_archive 행을 얻는다. 없으면 만든다."""
    row = db.query(SurfaceRoughnessArchive).filter_by(roughness_id=roughness_id).first()
    if row is None:
        row = SurfaceRoughnessArchive(roughness_id=roughness_id)
        db.add(row)
        db.flush()
    return row


def set_job_paths(db, job_id, **paths):
    """job_file_archive 의 경로 칸을 채운다. None 인 값은 건드리지 않는다.

    쓰기 예: set_job_paths(db, job.job_id, tdms_raw_path=rel)
    """
    row = get_or_create_job_archive(db, job_id)
    for key, value in paths.items():
        if value is not None:
            setattr(row, key, value)
    return row


def archive_path(owner, attr):
    """관계를 타고 경로 한 칸을 읽는다. archive 행이 없으면 None.

    읽기 예: archive_path(job, "tdms_raw_path")
    """
    row = getattr(owner, "file_archive", None) if owner is not None else None
    return getattr(row, attr, None) if row is not None else None
