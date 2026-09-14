"""
데이터베이스 및 스토리지(파일/백업) 완전 초기화 스크립트

1. MySQL DB ('orcus'):
   - 모든 기존 테이블 및 뷰 완전 DROP (외래키 제약 임시 비활성화)
   - models.py 최신 스키마 기반 전 테이블 새로 생성 (create_all)
   - schema_patch.ensure_schema() 실행
   - 모든 테이블의 레코드 수 0건 검증

2. 파일 스토리지:
   - machining_raw_data/ 내 모든 파일 및 하위 폴더 삭제 (루트 폴더 유지)
   - archive_vault/ 내 모든 파일 및 하위 폴더 삭제 (루트 및 기본 폴더 구조 유지)
   - processed_data/ 내 모든 파일 삭제 (루트 폴더 유지)
   - failed_data/ 내 모든 파일 삭제 (루트 폴더 유지)
"""
import os
import sys
import stat
import shutil

# backend 경로 추가
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(backend_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text, inspect
from DB.database import engine, Base
# 이름을 직접 쓰지는 않지만 반드시 필요하다. 이 import 가 있어야 모든 모델이 Base.metadata 에
# 등록되고, 아래 create_all 이 14개 테이블을 전부 다시 만든다. 지우면 빈 DB 가 된다.
from DB import models  # noqa: F401
from DB.schema_patch import ensure_schema


def _remove_readonly(func, path, exc_info):
    """Windows 파일/폴더 삭제 시 읽기 전용 속성 해제 후 재시도"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"    [경고] {path} 삭제 재시도 실패: {e}")


def clean_directory(dir_path, recreate_subdirs=None):
    """디렉터리 내의 모든 파일과 하위 폴더를 삭제하고 루트는 유지"""
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
        print(f"[스토리지 초기화] 폴더 생성: {dir_path}")
        return

    deleted_files = 0
    deleted_dirs = 0

    for item in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item)
        try:
            if os.path.isdir(item_path):
                # 읽기 전용 해제 후 rmtree
                for root, dirs, files in os.walk(item_path):
                    for fname in files:
                        p = os.path.join(root, fname)
                        try:
                            os.chmod(p, stat.S_IWRITE)
                        except Exception:
                            pass
                    for dname in dirs:
                        p = os.path.join(root, dname)
                        try:
                            os.chmod(p, stat.S_IWRITE)
                        except Exception:
                            pass
                shutil.rmtree(item_path, onerror=_remove_readonly)
                deleted_dirs += 1
            else:
                try:
                    os.chmod(item_path, stat.S_IWRITE)
                except Exception:
                    pass
                os.remove(item_path)
                deleted_files += 1
        except Exception as e:
            print(f"    [오류] {item_path} 삭제 실패: {e}")

    # 기본 하위 디렉터리 재생성 (필요 시)
    if recreate_subdirs:
        for sub in recreate_subdirs:
            sub_path = os.path.join(dir_path, sub)
            os.makedirs(sub_path, exist_ok=True)

    print(f"[스토리지 초기화] {os.path.basename(dir_path)}: 폴더 {deleted_dirs}개, 파일 {deleted_files}개 삭제 완료.")


def reset_storage():
    """모든 데이터 및 백업 디렉터리 초기화"""
    print("\n=== [1/2] 파일 스토리지 및 백업 초기화 시작 ===")

    raw_data_dir = os.path.join(project_dir, "machining_raw_data")
    vault_dir = os.path.join(project_dir, "archive_vault")
    processed_dir = os.path.join(project_dir, "processed_data")
    failed_dir = os.path.join(project_dir, "failed_data")

    # archive_vault 기본 하위 폴더 목록
    vault_subdirs = [
        "cad_models",
        "etc_files",
        "jobs",
        "machine_logs",
        "processed_parquet",
        "surface_roughness",
        "tdms_files",
        "tool_master",
        "workplan_nc"
    ]

    clean_directory(raw_data_dir)
    clean_directory(vault_dir, recreate_subdirs=vault_subdirs)
    clean_directory(processed_dir)
    clean_directory(failed_dir)

    print("=== 파일 스토리지 및 백업 초기화 완료 ===\n")


def reset_database():
    """MySQL 데이터베이스 모든 테이블 DROP 후 재생성"""
    print("=== [2/2] MySQL 데이터베이스 완전 초기화 시작 ===")

    with engine.connect() as conn:
        # 외래키 제약조건 비활성화
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        conn.commit()

        # 현재 DB에 존재하는 모든 테이블 조회
        current_tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
        )).fetchall()

        table_names = [row[0] for row in current_tables]
        print(f"기존 감지된 테이블 목록 ({len(table_names)}개): {table_names}")

        # 모든 테이블 DROP
        for tbl in table_names:
            conn.execute(text(f"DROP TABLE IF EXISTS `{tbl}`"))
            print(f"  - DROP TABLE `{tbl}`")
        conn.commit()

        # 혹시 모를 뷰(Views)도 DROP
        views = conn.execute(text(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = DATABASE()"
        )).fetchall()
        for v in views:
            conn.execute(text(f"DROP VIEW IF EXISTS `{v[0]}`"))
            print(f"  - DROP VIEW `{v[0]}`")
        conn.commit()

        # models.py 기반 모든 테이블 새로 생성
        print("\n최신 모델 정의 기반 테이블 재생성 (Base.metadata.create_all)...")
        Base.metadata.create_all(bind=engine)

        # 외래키 제약조건 복구
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        conn.commit()

    # 스키마 패치 확인
    print("\n스키마 패치 보정 확인...")
    ensure_schema(verbose=True)

    # 테이블 생성 및 레코드 수 확인
    insp = inspect(engine)
    new_tables = insp.get_table_names()
    print(f"\n생성된 테이블 목록 ({len(new_tables)}개):")
    
    with engine.connect() as conn:
        all_empty = True
        for tbl in sorted(new_tables):
            count = conn.execute(text(f"SELECT COUNT(*) FROM `{tbl}`")).scalar()
            print(f"  - {tbl:30s} : {count} rows")
            if count != 0:
                all_empty = False

    if all_empty:
        print("\n=> 모든 테이블이 0건으로 성공적으로 초기화되었습니다.")
    else:
        print("\n=> [주의] 일부 테이블에 데이터가 남아 있습니다.")

    print("=== MySQL 데이터베이스 초기화 완료 ===\n")


def main():
    print("==================================================")
    print("   Orcus Lab Database & Storage Reset Routine     ")
    print("==================================================")
    reset_storage()
    reset_database()
    print("==================================================")
    print("           전체 초기화 작업 완료                  ")
    print("==================================================")


if __name__ == "__main__":
    main()
