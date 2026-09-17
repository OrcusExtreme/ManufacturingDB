"""파일 경로 컬럼을 전부 *_file_archive 테이블로 모으는 마이그레이션.

왜 옮기나
---------
같은 파일을 가리키는 칸이 주 테이블과 archive 테이블에 나뉘어 있었다. 이름만 보면 중복
같지만 실제로 담긴 값은 서로 달랐다.

    job.tdms_parquet_path            Alchemist/DB_test/1/processed_parquet/job_1_viz.parquet
    job_file_archive.tdms_parquet_*  processed_parquet/job_1_viz.parquet

앞은 RAW_DATA_ROOT 기준(장비가 떨군 원본이 놓인 자리), 뒤는 VAULT_ROOT 기준(원본이 지워져도
남는 보관소 사본)이다. 다운로드와 복원이 '원본 폴더 -> 보관소 -> LONGBLOB' 순으로 찾아
내려가기 때문에 둘 다 필요하다. 그래서 한쪽을 버리는 대신 둘 다 archive 로 옮기고
이름(*_raw_path / *_vault_path)으로 기준을 구분한다.

부품명·프로젝트명은 사정이 다르다. job.custom_part_name / job.research_project 는
part.part_name / part.project_code 와 같은 사실을 두 곳에 적어 둔 진짜 중복이라 제거한다.
조회는 Job -> Workplan -> Part 조인으로 한 곳에서만 읽는다.

workplan.nc_hash 는 경로가 아니라 uq_workplan_identity(part_code, program_code, nc_hash)
를 이루는 신원 값이므로 옮기지 않는다. 옮기면 같은 NC 를 두 번 돌렸을 때 Workplan 을
합치는 로직이 통째로 무너진다.

안전장치
--------
- 기본은 미리보기(dry-run). 실제 적용은 --apply.
- 새 컬럼 추가 -> 값 복사 -> 검증 -> 옛 컬럼 삭제 순서. 검증에 실패하면 삭제하지 않는다.
- 이미 옮긴 DB 에 다시 돌려도 안전하다(있는 컬럼은 건너뛴다).

사용법
------
    python backend/migrate_paths_to_archive.py
    python backend/migrate_paths_to_archive.py --apply
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import text
from DB.database import engine

# (테이블, 컬럼, 정의) - 없으면 추가한다
NEW_COLUMNS = [
    ("workplan_file_archive", "nc_raw_path",
     "VARCHAR(1000) NULL COMMENT 'NC 원본 파일 경로 (RAW_DATA_ROOT 기준)'"),
    ("job_file_archive", "tdms_raw_path",
     "VARCHAR(1000) NULL COMMENT 'TDMS 원본 파일 경로'"),
    ("job_file_archive", "tdms_parquet_raw_path",
     "VARCHAR(1000) NULL COMMENT 'TDMS Time-domain Parquet 원본 경로'"),
    ("job_file_archive", "tdms_fft_parquet_raw_path",
     "VARCHAR(1000) NULL COMMENT 'TDMS FFT Parquet 원본 경로'"),
    ("surface_roughness_archive", "profile_parquet_raw_path",
     "VARCHAR(1000) NULL COMMENT '평가곡선 Parquet 원본 경로'"),
    ("log_file_archive", "log_raw_path",
     "VARCHAR(1000) NULL COMMENT '원시 로그/CSV 원본 경로'"),
]

# (설명, 원본 테이블, 원본 컬럼, 복사 SQL) - 원본 컬럼이 있을 때만 수행
COPIES = [
    ("job.tdms_file_path -> job_file_archive.tdms_raw_path",
     "job", "tdms_file_path",
     "UPDATE job_file_archive a JOIN job j ON j.job_id = a.job_id "
     "SET a.tdms_raw_path = j.tdms_file_path "
     "WHERE a.tdms_raw_path IS NULL AND j.tdms_file_path IS NOT NULL"),

    ("job.tdms_parquet_path -> job_file_archive.tdms_parquet_raw_path",
     "job", "tdms_parquet_path",
     "UPDATE job_file_archive a JOIN job j ON j.job_id = a.job_id "
     "SET a.tdms_parquet_raw_path = j.tdms_parquet_path "
     "WHERE a.tdms_parquet_raw_path IS NULL AND j.tdms_parquet_path IS NOT NULL"),

    ("job.tdms_fft_parquet_path -> job_file_archive.tdms_fft_parquet_raw_path",
     "job", "tdms_fft_parquet_path",
     "UPDATE job_file_archive a JOIN job j ON j.job_id = a.job_id "
     "SET a.tdms_fft_parquet_raw_path = j.tdms_fft_parquet_path "
     "WHERE a.tdms_fft_parquet_raw_path IS NULL AND j.tdms_fft_parquet_path IS NOT NULL"),

    ("job_file_archive.xml_file_path -> xml_vault_path",
     "job_file_archive", "xml_file_path",
     "UPDATE job_file_archive SET xml_vault_path = xml_file_path "
     "WHERE xml_vault_path IS NULL AND xml_file_path IS NOT NULL"),

    ("job_file_archive.tdms_parquet_file_path -> tdms_parquet_vault_path",
     "job_file_archive", "tdms_parquet_file_path",
     "UPDATE job_file_archive SET tdms_parquet_vault_path = tdms_parquet_file_path "
     "WHERE tdms_parquet_vault_path IS NULL AND tdms_parquet_file_path IS NOT NULL"),

    ("job_file_archive.tdms_fft_parquet_file_path -> tdms_fft_parquet_vault_path",
     "job_file_archive", "tdms_fft_parquet_file_path",
     "UPDATE job_file_archive SET tdms_fft_parquet_vault_path = tdms_fft_parquet_file_path "
     "WHERE tdms_fft_parquet_vault_path IS NULL AND tdms_fft_parquet_file_path IS NOT NULL"),

    ("job.log_file_path -> log_file_archive.log_raw_path",
     "job", "log_file_path",
     "UPDATE log_file_archive a JOIN machine_log m ON m.log_id = a.log_id "
     "JOIN job j ON j.job_id = m.job_id "
     "SET a.log_raw_path = j.log_file_path "
     "WHERE a.log_raw_path IS NULL AND j.log_file_path IS NOT NULL"),

    ("log_file_archive.log_file_path -> log_vault_path",
     "log_file_archive", "log_file_path",
     "UPDATE log_file_archive SET log_vault_path = log_file_path "
     "WHERE log_vault_path IS NULL AND log_file_path IS NOT NULL"),

    ("surface_roughness.profile_parquet_path -> archive.profile_parquet_raw_path",
     "surface_roughness", "profile_parquet_path",
     "UPDATE surface_roughness_archive a JOIN surface_roughness s ON s.roughness_id = a.roughness_id "
     "SET a.profile_parquet_raw_path = s.profile_parquet_path "
     "WHERE a.profile_parquet_raw_path IS NULL AND s.profile_parquet_path IS NOT NULL"),

    ("surface_roughness_archive.profile_parquet_file_path -> profile_parquet_vault_path",
     "surface_roughness_archive", "profile_parquet_file_path",
     "UPDATE surface_roughness_archive SET profile_parquet_vault_path = profile_parquet_file_path "
     "WHERE profile_parquet_vault_path IS NULL AND profile_parquet_file_path IS NOT NULL"),

    ("surface_roughness_archive.stat_csv_file_path -> stat_csv_vault_path",
     "surface_roughness_archive", "stat_csv_file_path",
     "UPDATE surface_roughness_archive SET stat_csv_vault_path = stat_csv_file_path "
     "WHERE stat_csv_vault_path IS NULL AND stat_csv_file_path IS NOT NULL"),

    ("surface_roughness_archive.curve_csv_file_path -> curve_csv_vault_path",
     "surface_roughness_archive", "curve_csv_file_path",
     "UPDATE surface_roughness_archive SET curve_csv_vault_path = curve_csv_file_path "
     "WHERE curve_csv_vault_path IS NULL AND curve_csv_file_path IS NOT NULL"),

    # workplan.nc_file_path 는 원본 폴더 기준이었다.
    ("workplan.nc_file_path -> workplan_file_archive.nc_raw_path",
     "workplan", "nc_file_path",
     "UPDATE workplan_file_archive a JOIN workplan w ON w.workplan_id = a.workplan_id "
     "SET a.nc_raw_path = w.nc_file_path "
     "WHERE a.nc_raw_path IS NULL AND w.nc_file_path IS NOT NULL"),

    # 옛 workplan_file_archive.nc_file_path 한 칸에는 xml_parser 가 Vault 경로를,
    # nc_parser 가 원본 경로를 섞어 넣었다. Vault 사본은 항상 workplan_nc/ 아래에 있으므로
    # 그 접두사로 어느 기준인지 갈라 각자의 칸으로 보낸다.
    ("workplan_file_archive.nc_file_path -> nc_vault_path (workplan_nc/ 로 시작하는 값)",
     "workplan_file_archive", "nc_file_path",
     "UPDATE workplan_file_archive SET nc_vault_path = nc_file_path "
     "WHERE nc_vault_path IS NULL AND nc_file_path LIKE 'workplan_nc/%'"),
    ("workplan_file_archive.nc_file_path -> nc_raw_path (그 외)",
     "workplan_file_archive", "nc_file_path",
     "UPDATE workplan_file_archive SET nc_raw_path = nc_file_path "
     "WHERE nc_raw_path IS NULL AND nc_file_path IS NOT NULL "
     "AND nc_file_path NOT LIKE 'workplan_nc/%'"),

    ("cad_file_archive.file_path -> file_vault_path",
     "cad_file_archive", "file_path",
     "UPDATE cad_file_archive SET file_vault_path = file_path "
     "WHERE file_vault_path IS NULL AND file_path IS NOT NULL"),
]

# 값을 다 옮긴 뒤 지울 옛 컬럼
DROP_COLUMNS = [
    ("job", "tdms_file_path"),
    ("job", "log_file_path"),
    ("job", "tdms_parquet_path"),
    ("job", "tdms_fft_parquet_path"),
    ("job", "custom_part_name"),
    ("job", "research_project"),
    ("workplan", "nc_file_path"),
    ("surface_roughness", "profile_parquet_path"),
    ("job_file_archive", "xml_file_path"),
    ("job_file_archive", "tdms_parquet_file_path"),
    ("job_file_archive", "tdms_fft_parquet_file_path"),
    ("surface_roughness_archive", "profile_parquet_file_path"),
    ("surface_roughness_archive", "stat_csv_file_path"),
    ("surface_roughness_archive", "curve_csv_file_path"),
    ("log_file_archive", "log_file_path"),
    ("workplan_file_archive", "nc_file_path"),
    ("cad_file_archive", "file_path"),

    # 보관소 경로는 레코드로부터 계산되는 규칙이라 저장하지 않는다.
    # (규칙은 backend/vault_layout.py 한 곳에만 둔다)
    ("job_file_archive", "xml_vault_path"),
    ("job_file_archive", "tdms_parquet_vault_path"),
    ("job_file_archive", "tdms_fft_parquet_vault_path"),
    ("workplan_file_archive", "nc_vault_path"),
    ("log_file_archive", "log_vault_path"),
    ("surface_roughness_archive", "profile_parquet_vault_path"),
    ("surface_roughness_archive", "stat_csv_vault_path"),
    ("surface_roughness_archive", "curve_csv_vault_path"),
    ("cad_file_archive", "file_vault_path"),
]

# (설명, 테이블, 컬럼, 남은 행 세는 SQL) - 옛 컬럼에만 값이 남았는지 확인
CHECKS = [
    ("job.tdms_file_path", "job", "tdms_file_path",
     "SELECT COUNT(*) FROM job j LEFT JOIN job_file_archive a ON a.job_id = j.job_id "
     "WHERE j.tdms_file_path IS NOT NULL AND (a.job_id IS NULL OR a.tdms_raw_path IS NULL)"),
    ("job.log_file_path", "job", "log_file_path",
     "SELECT COUNT(*) FROM job j JOIN machine_log m ON m.job_id = j.job_id "
     "LEFT JOIN log_file_archive a ON a.log_id = m.log_id "
     "WHERE j.log_file_path IS NOT NULL AND (a.log_id IS NULL OR a.log_raw_path IS NULL)"),
    ("surface_roughness.profile_parquet_path", "surface_roughness", "profile_parquet_path",
     "SELECT COUNT(*) FROM surface_roughness s "
     "LEFT JOIN surface_roughness_archive a ON a.roughness_id = s.roughness_id "
     "WHERE s.profile_parquet_path IS NOT NULL "
     "AND (a.roughness_id IS NULL OR a.profile_parquet_raw_path IS NULL)"),
    ("workplan.nc_file_path", "workplan", "nc_file_path",
     "SELECT COUNT(*) FROM workplan w "
     "LEFT JOIN workplan_file_archive a ON a.workplan_id = w.workplan_id "
     "WHERE w.nc_file_path IS NOT NULL AND (a.workplan_id IS NULL OR a.nc_raw_path IS NULL)"),
]


def _has_column(conn, table, column):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar())


def migrate(apply=False):
    print("=" * 70)
    print(" 파일 경로 컬럼을 archive 테이블로 통합")
    print("=" * 70)

    with engine.begin() as conn:
        print("\n[1/4] 새 컬럼 추가")
        added = 0
        for table, column, ddl in NEW_COLUMNS:
            if _has_column(conn, table, column):
                continue
            print(f"  + {table}.{column}")
            added += 1
            if apply:
                conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}"))
        if not added:
            print("  (이미 모두 있습니다)")

        print("\n[2/4] 값 복사")
        for label, src_table, src_col, sql in COPIES:
            if not _has_column(conn, src_table, src_col):
                print(f"  - {label}  (원본 컬럼 없음, 건너뜀)")
                continue
            if not apply:
                print(f"  ~ {label}")
                continue
            n = conn.execute(text(sql)).rowcount
            print(f"  → {label}  ({n}행)")

        if not apply:
            print("\n[3/4] 검증 · [4/4] 옛 컬럼 삭제는 --apply 에서 수행합니다.")
            print("\n실제 적용: python backend/migrate_paths_to_archive.py --apply")
            return 0

        print("\n[3/4] 검증 — 옛 컬럼에만 남은 값이 없는지 확인")
        problems = []
        for label, table, column, sql in CHECKS:
            if not _has_column(conn, table, column):
                continue
            n = conn.execute(text(sql)).scalar() or 0
            print(f"  {'OK' if n == 0 else '남음':4} {label}: 넘어가지 못한 행 {n}")
            if n:
                problems.append(f"{label} ({n}행)")

        if problems:
            print("\n[중단] archive 행이 없어 값을 옮기지 못한 항목이 있습니다.")
            for p in problems:
                print(f"    {p}")
            print("  옛 컬럼을 지우지 않았습니다. 해당 Job 을 다시 수집하거나 "
                  "archive 행을 만든 뒤 재실행하세요.")
            return 1

        print("\n[4/4] 옛 컬럼 삭제")
        dropped = 0
        for table, column in DROP_COLUMNS:
            if not _has_column(conn, table, column):
                continue
            print(f"  - {table}.{column}")
            conn.execute(text(f"ALTER TABLE `{table}` DROP COLUMN `{column}`"))
            dropped += 1
        if not dropped:
            print("  (이미 정리되어 있습니다)")

    print("\n[완료] 파일 경로가 모두 archive 테이블로 모였습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(migrate(apply="--apply" in sys.argv))
