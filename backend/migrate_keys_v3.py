"""스키마 정규화 마이그레이션 (데이터 보존)

바꾸는 것
  1. part.part_code : 이름 문자열 PK  ->  1부터 증가하는 숫자 PK, 이름은 part_name 컬럼으로 분리
  2. workplan.workplan_id : "부품명_프로그램_해시" 문자열 PK -> 숫자 PK
     (기존 조합은 (part_code, program_code, nc_hash) 유일 제약으로 이동해 중복 차단 규칙 유지)
  3. machine_log : job과 1:1 이 되도록 job_id 유일 제약 추가
  4. tool : 사용하지 않는 is_mounted / photo_filename / photo_content 제거 (memo는 유지)

기존 행은 모두 보존하며, 자식 테이블(job, workingstep, 각종 아카이브)의 참조 값을 새 숫자 키로 다시 씁니다.

사용법
    python backend/migrate_keys_v3.py --check     # 현재 상태 점검만
    python backend/migrate_keys_v3.py --run       # 실제 적용
"""
import argparse
import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text  # noqa: E402

from DB.database import engine  # noqa: E402


def _column_exists(conn, table, column):
    row = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()
    return bool(row)


def _column_type(conn, table, column):
    return conn.execute(text(
        "SELECT COLUMN_TYPE FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()


def _index_exists(conn, table, index):
    row = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
    ), {"t": table, "i": index}).scalar()
    return bool(row)


def _fk_exists(conn, table, name):
    row = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.table_constraints "
        "WHERE table_schema = DATABASE() AND table_name = :t "
        "AND constraint_name = :n AND constraint_type = 'FOREIGN KEY'"
    ), {"t": table, "n": name}).scalar()
    return bool(row)


def _foreign_keys(conn, table):
    """해당 테이블에 걸린 외래키 이름 목록."""
    rows = conn.execute(text(
        "SELECT CONSTRAINT_NAME FROM information_schema.table_constraints "
        "WHERE table_schema = DATABASE() AND table_name = :t AND constraint_type = 'FOREIGN KEY'"
    ), {"t": table}).fetchall()
    return [r[0] for r in rows]


def check(conn):
    print("=" * 60)
    print(" 현재 스키마 상태 점검")
    print("=" * 60)
    done = {
        "part.part_name 존재": _column_exists(conn, "part", "part_name"),
        "part.part_code 숫자형": "int" in (_column_type(conn, "part", "part_code") or ""),
        "workplan.nc_hash 존재": _column_exists(conn, "workplan", "nc_hash"),
        "workplan.workplan_id 숫자형": "int" in (_column_type(conn, "workplan", "workplan_id") or ""),
        "machine_log.job_id 유일제약": _index_exists(conn, "machine_log", "uq_machine_log_job"),
        "tool.is_mounted 제거됨": not _column_exists(conn, "tool", "is_mounted"),
        "tool.photo_filename 제거됨": not _column_exists(conn, "tool", "photo_filename"),
        "tool.photo_content 제거됨": not _column_exists(conn, "tool", "photo_content"),
        "tool.memo 유지됨": _column_exists(conn, "tool", "memo"),
    }
    for k, v in done.items():
        print(f"  [{'O' if v else ' '}] {k}")

    print("\n 데이터 건수")
    for t in ("part", "workplan", "job", "workingstep", "machine_log", "tool",
              "workplan_file_archive", "cad_file_archive", "inspection", "env_memo", "surface_roughness"):
        try:
            print(f"   {t:24} {conn.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar()}")
        except Exception as e:
            print(f"   {t:24} (조회 실패: {e})")

    dup = conn.execute(text(
        "SELECT job_id, COUNT(*) c FROM machine_log GROUP BY job_id HAVING c > 1"
    )).fetchall()
    if dup:
        print(f"\n  [경고] machine_log가 1:1이 아닌 Job이 있습니다: {dup}")
        print("         가장 최근 log_id만 남기고 정리한 뒤 유일 제약을 겁니다.")
    return all(done.values())


def migrate(conn):
    print("=" * 60)
    print(" 마이그레이션 시작")
    print("=" * 60)

    # ── 1. tool: 사용하지 않는 컬럼 제거 -------------------------------------
    for col in ("is_mounted", "photo_filename", "photo_content"):
        if _column_exists(conn, "tool", col):
            conn.execute(text(f"ALTER TABLE tool DROP COLUMN {col}"))
            print(f"  - tool.{col} 제거")

    # ── 2. machine_log: Job과 1:1 보장 ---------------------------------------
    dups = conn.execute(text(
        "SELECT job_id FROM machine_log GROUP BY job_id HAVING COUNT(*) > 1"
    )).fetchall()
    for (job_id,) in dups:
        # 같은 Job에 로그 요약이 여러 건이면 가장 마지막 것만 남긴다.
        conn.execute(text(
            "DELETE FROM machine_log WHERE job_id = :j AND log_id < "
            "(SELECT * FROM (SELECT MAX(log_id) FROM machine_log WHERE job_id = :j) x)"
        ), {"j": job_id})
        print(f"  - machine_log 중복 정리 (job_id={job_id})")
    if not _index_exists(conn, "machine_log", "uq_machine_log_job"):
        conn.execute(text("ALTER TABLE machine_log ADD CONSTRAINT uq_machine_log_job UNIQUE (job_id)"))
        print("  - machine_log.job_id 유일 제약 추가 (Job과 1:1)")

    # ── 3. part: 이름 컬럼 분리 + 숫자 키 부여 --------------------------------
    if not _column_exists(conn, "part", "part_name"):
        conn.execute(text("ALTER TABLE part ADD COLUMN part_name VARCHAR(100) NULL COMMENT '부품 이름'"))
        conn.execute(text("UPDATE part SET part_name = part_code"))
        print("  - part.part_name 추가 및 기존 이름 복사")

    if "int" not in (_column_type(conn, "part", "part_code") or ""):
        # 이름 -> 새 숫자 키 매핑 (이름 순서대로 1번부터)
        names = [r[0] for r in conn.execute(text(
            "SELECT part_name FROM part ORDER BY part_name"
        )).fetchall()]
        mapping = {name: i for i, name in enumerate(names, start=1)}
        print(f"  - 부품 {len(mapping)}건에 번호 부여: " +
              ", ".join(f"{n}->{i}" for n, i in mapping.items()))

        # 자식 테이블이 참조하는 값을 새 번호로 바꾸기 위해 임시 컬럼을 둔다.
        conn.execute(text("ALTER TABLE part ADD COLUMN part_code_new INT NULL"))
        conn.execute(text("ALTER TABLE workplan ADD COLUMN part_code_new INT NULL"))
        conn.execute(text("ALTER TABLE cad_file_archive ADD COLUMN part_code_new INT NULL"))
        for name, new_id in mapping.items():
            conn.execute(text("UPDATE part SET part_code_new = :i WHERE part_name = :n"),
                         {"i": new_id, "n": name})
            conn.execute(text("UPDATE workplan SET part_code_new = :i WHERE part_code = :n"),
                         {"i": new_id, "n": name})
            conn.execute(text("UPDATE cad_file_archive SET part_code_new = :i WHERE part_code = :n"),
                         {"i": new_id, "n": name})

        # 외래키를 잠시 떼고 컬럼 교체
        for fk in _foreign_keys(conn, "workplan"):
            conn.execute(text(f"ALTER TABLE workplan DROP FOREIGN KEY {fk}"))
        for fk in _foreign_keys(conn, "cad_file_archive"):
            conn.execute(text(f"ALTER TABLE cad_file_archive DROP FOREIGN KEY {fk}"))

        conn.execute(text("ALTER TABLE part DROP PRIMARY KEY"))
        conn.execute(text("ALTER TABLE part DROP COLUMN part_code"))
        conn.execute(text("ALTER TABLE part CHANGE part_code_new part_code INT NOT NULL"))
        conn.execute(text("ALTER TABLE part ADD PRIMARY KEY (part_code)"))
        conn.execute(text("ALTER TABLE part MODIFY part_code INT NOT NULL AUTO_INCREMENT"))
        conn.execute(text("ALTER TABLE part MODIFY part_name VARCHAR(100) NOT NULL"))
        conn.execute(text("ALTER TABLE part ADD UNIQUE KEY uq_part_name (part_name)"))

        conn.execute(text("ALTER TABLE workplan DROP COLUMN part_code"))
        conn.execute(text("ALTER TABLE workplan CHANGE part_code_new part_code INT NOT NULL"))
        conn.execute(text("ALTER TABLE cad_file_archive DROP COLUMN part_code"))
        conn.execute(text("ALTER TABLE cad_file_archive CHANGE part_code_new part_code INT NOT NULL"))
        print("  - part.part_code 를 숫자 PK 로 전환하고 자식 참조 갱신")

    # ── 4. workplan: 문자열 PK -> 숫자 PK ------------------------------------
    if not _column_exists(conn, "workplan", "nc_hash"):
        conn.execute(text("ALTER TABLE workplan ADD COLUMN nc_hash VARCHAR(32) NULL COMMENT 'NC 내용 해시'"))
        # 기존 PK 형식이 "부품명_프로그램_해시" 이므로 마지막 조각이 해시다.
        conn.execute(text(
            "UPDATE workplan SET nc_hash = SUBSTRING_INDEX(workplan_id, '_', -1)"
        ))
        print("  - workplan.nc_hash 추가 및 기존 PK에서 해시 추출")

    if "int" not in (_column_type(conn, "workplan", "workplan_id") or ""):
        old_ids = [r[0] for r in conn.execute(text(
            "SELECT workplan_id FROM workplan ORDER BY part_code, program_code, workplan_id"
        )).fetchall()]
        mapping = {old: i for i, old in enumerate(old_ids, start=1)}
        print(f"  - Workplan {len(mapping)}건에 번호 부여")

        conn.execute(text("ALTER TABLE workplan ADD COLUMN workplan_id_new INT NULL"))
        conn.execute(text("ALTER TABLE job ADD COLUMN workplan_id_new INT NULL"))
        conn.execute(text("ALTER TABLE workingstep ADD COLUMN workplan_id_new INT NULL"))
        conn.execute(text("ALTER TABLE workplan_file_archive ADD COLUMN workplan_id_new INT NULL"))
        for old, new_id in mapping.items():
            conn.execute(text("UPDATE workplan SET workplan_id_new = :i WHERE workplan_id = :o"),
                         {"i": new_id, "o": old})
            conn.execute(text("UPDATE job SET workplan_id_new = :i WHERE workplan_id = :o"),
                         {"i": new_id, "o": old})
            conn.execute(text("UPDATE workingstep SET workplan_id_new = :i WHERE workplan_id = :o"),
                         {"i": new_id, "o": old})
            conn.execute(text("UPDATE workplan_file_archive SET workplan_id_new = :i WHERE workplan_id = :o"),
                         {"i": new_id, "o": old})

        for table in ("job", "workingstep", "workplan_file_archive"):
            for fk in _foreign_keys(conn, table):
                conn.execute(text(f"ALTER TABLE {table} DROP FOREIGN KEY {fk}"))

        conn.execute(text("ALTER TABLE workplan DROP PRIMARY KEY"))
        conn.execute(text("ALTER TABLE workplan DROP COLUMN workplan_id"))
        conn.execute(text("ALTER TABLE workplan CHANGE workplan_id_new workplan_id INT NOT NULL"))
        conn.execute(text("ALTER TABLE workplan ADD PRIMARY KEY (workplan_id)"))
        conn.execute(text("ALTER TABLE workplan MODIFY workplan_id INT NOT NULL AUTO_INCREMENT"))

        for table in ("job", "workingstep"):
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN workplan_id"))
            conn.execute(text(f"ALTER TABLE {table} CHANGE workplan_id_new workplan_id INT NOT NULL"))
        conn.execute(text("ALTER TABLE workplan_file_archive DROP PRIMARY KEY"))
        conn.execute(text("ALTER TABLE workplan_file_archive DROP COLUMN workplan_id"))
        conn.execute(text("ALTER TABLE workplan_file_archive CHANGE workplan_id_new workplan_id INT NOT NULL"))
        conn.execute(text("ALTER TABLE workplan_file_archive ADD PRIMARY KEY (workplan_id)"))
        print("  - workplan.workplan_id 를 숫자 PK 로 전환하고 자식 참조 갱신")

    # ── 5. 외래키/유일 제약 재구성 -------------------------------------------
    if not _index_exists(conn, "workplan", "uq_workplan_identity"):
        conn.execute(text(
            "ALTER TABLE workplan ADD CONSTRAINT uq_workplan_identity "
            "UNIQUE (part_code, program_code, nc_hash)"
        ))
        print("  - workplan (part_code, program_code, nc_hash) 유일 제약 추가")

    fk_defs = [
        ("workplan", "fk_workplan_part", "part_code", "part", "part_code"),
        ("cad_file_archive", "fk_cad_part", "part_code", "part", "part_code"),
        ("job", "fk_job_workplan", "workplan_id", "workplan", "workplan_id"),
        ("workingstep", "fk_step_workplan", "workplan_id", "workplan", "workplan_id"),
        ("workplan_file_archive", "fk_wparchive_workplan", "workplan_id", "workplan", "workplan_id"),
    ]
    for table, name, col, ref_table, ref_col in fk_defs:
        if not _fk_exists(conn, table, name):
            conn.execute(text(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({col}) "
                f"REFERENCES {ref_table}({ref_col}) ON DELETE CASCADE"
            ))
            print(f"  - 외래키 재생성: {table}.{col} -> {ref_table}.{ref_col}")

    # ── 6. PK가 항상 첫 컬럼으로 오도록 물리적 순서 정리 --------------------
    reorder_pk_first(conn)

    print("\n마이그레이션 완료")


def reorder_pk_first(conn):
    """PK 컬럼을 테이블의 첫 번째 위치로 되돌린다.

    키 컬럼을 DROP 후 재생성하면 MySQL이 그 컬럼을 맨 뒤에 붙이기 때문에
    SELECT * 결과에서 PK가 마지막에 나온다. 조회·내보내기 모두에 영향을 주므로
    물리적 순서 자체를 바로잡는다.
    """
    # (테이블, 앞으로 보낼 컬럼 정의) 순서대로 적용하면 두 번째 컬럼이 첫 컬럼 뒤에 붙는다.
    plans = [
        ("part", [("part_code", "INT NOT NULL AUTO_INCREMENT", None),
                  ("part_name", "VARCHAR(100) NOT NULL", "part_code")]),
        ("workplan", [("workplan_id", "INT NOT NULL AUTO_INCREMENT", None),
                      ("part_code", "INT NOT NULL", "workplan_id")]),
        ("workplan_file_archive", [("workplan_id", "INT NOT NULL", None)]),
    ]
    for table, cols in plans:
        order = [r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t ORDER BY ordinal_position"
        ), {"t": table}).fetchall()]
        for col, definition, after in cols:
            if col not in order:
                continue
            position = "FIRST" if after is None else f"AFTER {after}"
            conn.execute(text(f"ALTER TABLE {table} MODIFY {col} {definition} {position}"))
        new_order = [r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t ORDER BY ordinal_position"
        ), {"t": table}).fetchall()]
        print(f"  - {table} 컬럼 순서: {' | '.join(new_order)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="실제 적용")
    ap.add_argument("--check", action="store_true", help="상태 점검만")
    args = ap.parse_args()

    with engine.begin() as conn:
        if args.run:
            migrate(conn)
            print()
            check(conn)
        else:
            check(conn)
            if not args.check:
                print("\n적용하려면 --run 을 붙여 실행하세요.")


if __name__ == "__main__":
    main()
