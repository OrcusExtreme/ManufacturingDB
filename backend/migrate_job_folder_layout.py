"""Job 폴더 바로 아래 섞여 있던 원본 파일을 자료 종류별 하위 폴더로 옮긴다.

    {JobID}/xxx.xml   ->  {JobID}/XML/xxx.xml
    {JobID}/xxx.log   ->  {JobID}/Log/xxx.log
    {JobID}/xxx.tdms  ->  {JobID}/TDMS/xxx.tdms
    {JobID}/xxx.nc    ->  {JobID}/NC/xxx.nc

DB에 저장된 경로 참조도 옮긴 위치로 함께 고친다. 경로는 전부 *_file_archive 테이블의
*_raw_path 칸에 있다 (job_file_archive.tdms_raw_path / log_file_archive.log_raw_path /
workplan_file_archive.nc_raw_path / surface_roughness_archive.profile_parquet_raw_path).

    python backend/migrate_job_folder_layout.py            # 무엇을 옮길지 확인만
    python backend/migrate_job_folder_layout.py --apply    # 실제 이동 + DB 갱신

수집 파이프라인이 돌고 있으면 폴더 삭제를 유실로 보고 자동 복원할 수 있으므로,
파이프라인을 멈춘 상태에서 실행할 것.
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.append(_here)

import job_layout  # noqa: E402
from DB.database import SessionLocal  # noqa: E402
from DB.models import (Job, Workplan, WorkplanFileArchive, JobFileArchive,  # noqa: E402
                       LogFileArchive, SurfaceRoughnessArchive)
from vault_manager import get_abs_raw_data_path, get_rel_raw_data_path  # noqa: E402

from vault_manager import PROJECT_ROOT, RAW_DATA_ROOT as RAW_DATA_DIR  # noqa: E402


def _job_dirs():
    """machining_raw_data/{Project}/{Part}/{JobID} 형태의 폴더를 모두 찾는다."""
    if not os.path.isdir(RAW_DATA_DIR):
        return
    for project in sorted(os.listdir(RAW_DATA_DIR)):
        project_dir = os.path.join(RAW_DATA_DIR, project)
        if not os.path.isdir(project_dir):
            continue
        for part in sorted(os.listdir(project_dir)):
            part_dir = os.path.join(project_dir, part)
            if not os.path.isdir(part_dir):
                continue
            for job in sorted(os.listdir(part_dir)):
                job_dir = os.path.join(part_dir, job)
                # CAD_Files 는 Job 이 아니라 Part 레벨 폴더다
                if os.path.isdir(job_dir) and job != job_layout.CAD_DIR:
                    yield job_dir


def plan_moves():
    """(원본 경로, 이동할 경로) 목록. Job 루트에 남아 있는 파일만 대상."""
    moves = []
    for job_dir in _job_dirs():
        for name in sorted(os.listdir(job_dir)):
            src = os.path.join(job_dir, name)
            if not os.path.isfile(src):
                continue
            kind = job_layout.subdir_for_file(name)
            if not kind:
                continue    # 규칙에 없는 확장자는 그대로 둔다
            moves.append((src, os.path.join(job_dir, kind, name)))
    return moves


def _remap(stored, moved_rel):
    """DB에 저장된 경로가 옮긴 파일을 가리키면 새 상대경로로 바꿔 돌려준다."""
    if not stored:
        return None
    abs_stored = get_abs_raw_data_path(stored)
    if not abs_stored:
        return None
    return moved_rel.get(os.path.normpath(abs_stored))


def update_db_paths(moved_rel, apply_changes):
    """옮긴 파일을 가리키던 DB 경로 참조를 새 위치로 고친다."""
    db = SessionLocal()
    changes = []
    try:
        # 원본 폴더 기준 경로(*_raw_path)만 옮긴 위치로 고친다.
        # 보관소 기준 경로(*_vault_path)는 Vault 안에서 움직이지 않으므로 그대로 둔다.
        TARGETS = [
            (JobFileArchive, "job_id", ["tdms_raw_path",
                                        "tdms_parquet_raw_path",
                                        "tdms_fft_parquet_raw_path"]),
            (WorkplanFileArchive, "workplan_id", ["nc_raw_path"]),
            (LogFileArchive, "log_id", ["log_raw_path"]),
            (SurfaceRoughnessArchive, "roughness_id", ["profile_parquet_raw_path"]),
        ]
        for model, pk, attrs in TARGETS:
            for arch in db.query(model).all():
                for attr in attrs:
                    old = getattr(arch, attr)
                    new_rel = _remap(old, moved_rel)
                    if new_rel and new_rel != old:
                        label = f"{model.__tablename__}[{getattr(arch, pk)}].{attr}"
                        changes.append((label, old, new_rel))
                        if apply_changes:
                            setattr(arch, attr, new_rel)

        if apply_changes and changes:
            db.commit()
    finally:
        db.close()
    return changes


def main():
    apply_changes = "--apply" in sys.argv
    moves = plan_moves()

    if not moves:
        print("Job 루트에 남아 있는 파일이 없습니다. 이미 새 구조입니다.")
        return

    print(f"이동 대상 {len(moves)}건:")
    for src, dest in moves:
        print(f"   {os.path.relpath(src, RAW_DATA_DIR)}"
              f"  ->  {os.path.relpath(dest, RAW_DATA_DIR)}")

    # 이동 후 경로를 미리 계산해 DB 변경분을 함께 보여준다.
    moved_rel = {os.path.normpath(src): get_rel_raw_data_path(dest) for src, dest in moves}
    changes = update_db_paths(moved_rel, apply_changes=False)
    print(f"\n갱신할 DB 경로 참조 {len(changes)}건:")
    for field, old, new in changes:
        print(f"   {field}\n      {old}\n      -> {new}")

    if not apply_changes:
        print("\n(확인 모드) 실제로 옮기려면 --apply 를 붙여 다시 실행하세요.")
        return

    for src, dest in moves:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            os.remove(dest)
        os.replace(src, dest)
    print(f"\n파일 {len(moves)}건 이동 완료")

    applied = update_db_paths(moved_rel, apply_changes=True)
    print(f"DB 경로 참조 {len(applied)}건 갱신 완료")


if __name__ == "__main__":
    main()
