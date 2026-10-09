"""DB · 스토리지 · 공구 목록 초기화(reset_database_and_storage)를 실제 MySQL 로 검증한다.

실제 DB 는 **읽기만** 한다. 같은 서버에 시험용 스키마(orcus_reset_selftest)를 만들어 models.py 로
테이블을 만들고 실제 DB 내용을 그대로 복사한 뒤, 항목 조합마다 진짜 DROP · create_all · DELETE 를
돌려 결과를 대조한다. 스토리지는 임시 폴더로 시험한다. 끝나면 시험용 스키마와 임시 폴더를 지우고,
실제 DB 의 행 수가 시작 때와 같은지 확인한다.

    python backend/tests/verify_reset.py

검사 항목
  1. 고른 항목이 없거나 모듈이 실행 중이면 아무것도 지우지 않는다 (명령줄도 항목 없이는 중단)
  2. 실행 중인 모듈 감지 — 가짜 수집기 프로세스를 실제로 띄워 본다
  3. 가공 데이터 DB 만 — 가공 테이블은 비고 다시 만들어지며, 공구 · 계정은 내용까지 그대로
  4. 공구 목록만 — tool 만 비고 번호가 1부터, 가공 이력은 남되 공구 연결만 비워짐,
     감시 폴더 루트의 공구 마스터 파일은 지우고 다른 파일은 그대로
  5. DB + 공구 — 계정만 남고, 다시 만든 tool 에 CHECK · UNIQUE · 외래키가 그대로
  6. 스토리지만 — 세 폴더는 비고(루트는 남음) DB 는 그대로, 보관소 안쪽 경로는 지우기 전에 막힘
  7. 세 항목 모두 — 계정만 남고 폴더는 비며, 실제 백업 보관소 파일 수는 그대로
  8. 실제 DB 행 수가 시작 때와 같음
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text

import DB.database as dbmod
from DB.database import Base
import reset_database_and_storage as core

SCRATCH = "orcus_reset_selftest"
live_engine = dbmod.engine
LIVE_DB = live_engine.url.database
assert LIVE_DB and LIVE_DB != SCRATCH

failures = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def counts(engine):
    with engine.connect() as conn:
        return {t: core._count(conn, t) for t in core._tables(conn)}


def guard(engine):
    """파괴적인 호출 직전에, 붙은 DB 가 시험용 스키마인지 확인한다 (실제 DB 를 지우는 사고 방지)."""
    with engine.connect() as conn:
        current = conn.execute(text("SELECT DATABASE()")).scalar()
    assert current == SCRATCH, f"시험용 스키마가 아닙니다: {current}"


def seed(scratch):
    """시험용 스키마를 models.py 로 새로 만들고 실제 DB 내용을 그대로 복사한다 (실제 DB 는 읽기만)."""
    guard(scratch)
    with scratch.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in core._tables(conn):
            conn.execute(text(f"DROP TABLE `{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
    Base.metadata.create_all(bind=scratch)
    with scratch.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in core._tables(conn):
            cols = [r[0] for r in conn.execute(text(
                "SELECT s.column_name FROM information_schema.columns s "
                "JOIN information_schema.columns l ON l.table_name = s.table_name "
                "AND l.column_name = s.column_name AND l.table_schema = :live "
                "WHERE s.table_schema = :scratch AND s.table_name = :t ORDER BY s.ordinal_position"),
                {"live": LIVE_DB, "scratch": SCRATCH, "t": table})]
            if cols:
                col_list = ", ".join(f"`{c}`" for c in cols)
                conn.execute(text(f"INSERT INTO `{table}` ({col_list}) "
                                  f"SELECT {col_list} FROM `{LIVE_DB}`.`{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
    return counts(scratch)


def make_tree(root):
    """스토리지 시험용 폴더: 원본 · 변환 · 실패 폴더에 파일을 몇 개 둔다."""
    roots = []
    for name in ("machining_raw_data", "processed_data", "failed_data"):
        base = os.path.join(root, name)
        os.makedirs(os.path.join(base, "Proj", "Part", "1", "NC"), exist_ok=True)
        with open(os.path.join(base, "Proj", "Part", "1", "NC", "a.nc"), "w") as f:
            f.write("G0X0")
        with open(os.path.join(base, "loose.txt"), "w") as f:
            f.write("x")
        roots.append(base)
    raw = roots[0]
    for name in ("공구 마스터.csv", "실험실 공구 정리.xlsx", "surface_roughness_01.csv"):
        with open(os.path.join(raw, name), "w") as f:
            f.write("x")
    return roots, raw


def tool_constraints(engine):
    with engine.connect() as conn:
        create = conn.execute(text("SHOW CREATE TABLE tool")).fetchone()[1]
        fk = conn.execute(text(
            "SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'workingstep' "
            "AND REFERENCED_TABLE_NAME = 'tool'")).fetchall()
    return ("chk_tool_mounted_complete" in create, "UNIQUE KEY `tool_code`" in create,
            [r[0] for r in fk] == ["SET NULL"])


def main():
    live_before = counts(live_engine)
    vault_before = core.count_files(core.VAULT_ROOT)
    print(f"실제 DB '{LIVE_DB}': 테이블 {len(live_before)}개 · 공구 {live_before.get('tool', 0)}행 · "
          f"계정 {live_before.get('app_user', 0)}개 · 보관소 파일 {vault_before:,}개 (읽기만)")
    data_tables = core.tables_to_reset(list(live_before), keep_tools=True)

    with live_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{SCRATCH}`"))
        conn.execute(text(f"CREATE DATABASE `{SCRATCH}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
    scratch = create_engine(live_engine.url.set(database=SCRATCH), pool_recycle=3600)
    work = tempfile.mkdtemp(prefix="reset_selftest_")
    real_running = core.running_modules
    try:
        print("\n[1] 시작하지 않는 경우")
        before = seed(scratch)
        try:
            core.run_reset(engine=scratch, check_modules=False)
            check("고른 항목이 없으면 ResetError", False)
        except core.ResetError:
            check("고른 항목이 없으면 ResetError", True)
        roots, raw = make_tree(os.path.join(work, "t1"))
        core.running_modules = lambda: [("Watchdog 수집기", 4242)]
        try:
            core.run_reset(db=True, storage=True, tools=True, engine=scratch, roots=roots, raw_root=raw)
            check("모듈이 실행 중이면 ResetError", False)
        except core.ResetError as e:
            check(f"모듈이 실행 중이면 ResetError ({str(e)[:40]}…)", "Watchdog 수집기(PID 4242)" in str(e))
        finally:
            core.running_modules = real_running
        check("그 경우 DB 는 그대로", counts(scratch) == before)
        check("그 경우 폴더도 그대로", all(core.count_files(r) == (5 if r == raw else 2) for r in roots))
        check("명령줄은 항목 없이 실행하면 중단 (코드 2)", core.main([]) == 2)

        print("\n[2] 실행 중인 모듈 감지")
        fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                 os.path.join("backend", "data_insert_recognization.py")])
        try:
            time.sleep(0.8)
            found = core.running_modules()
            check(f"가짜 수집기(PID {fake.pid}) 감지", ("Watchdog 수집기", fake.pid) in found, str(found))
        finally:
            fake.kill()
            fake.wait()
        time.sleep(0.3)
        check("끝낸 뒤에는 감지되지 않음", all(pid != fake.pid for _l, pid in core.running_modules()))
        others = core.running_modules()
        check("지금 실제로 돌고 있는 모듈 없음 (시험 전제)", not others, str(others))

        print("\n[3] 가공 데이터 DB 만")
        before = seed(scratch)
        with scratch.connect() as conn:
            tools_before = conn.execute(text("SELECT * FROM tool ORDER BY tool_id")).fetchall()
        roots, raw = make_tree(os.path.join(work, "t3"))
        guard(scratch)
        summary = core.run_reset(db=True, engine=scratch, roots=roots, raw_root=raw, check_modules=False)
        after = counts(scratch)
        check(f"가공 테이블 {len(data_tables)}개가 모두 비고 다시 만들어짐",
              all(after.get(t) == 0 for t in data_tables), str({t: after.get(t) for t in data_tables}))
        check("models.py 의 테이블이 모두 있음 (빠진 것 · 남은 옛 것 없음)",
              set(after) == set(Base.metadata.tables), str(set(after) ^ set(Base.metadata.tables)))
        with scratch.connect() as conn:
            tools_after = conn.execute(text("SELECT * FROM tool ORDER BY tool_id")).fetchall()
        check(f"공구 {before['tool']}행이 내용까지 그대로", tools_after == tools_before)
        check("DB 만 고르면 폴더는 그대로 (공구 마스터 파일 포함)",
              core.count_files(raw) == 5 and all(core.count_files(r) == 2 for r in roots[1:]))
        check("로그인 계정 · 세션 · 가입 신청 그대로",
              all(after[t] == before[t] for t in core.ACCOUNT_TABLES))
        check("요약: 지운 테이블에 tool · 계정이 없음",
              "tool" not in summary["db"]["dropped"]
              and not set(core.ACCOUNT_TABLES) & set(summary["db"]["dropped"]))
        check("workingstep → tool 외래키 · tool CHECK 그대로", all(tool_constraints(scratch)))

        print("\n[4] 공구 목록만")
        before = seed(scratch)
        roots, raw = make_tree(os.path.join(work, "t4"))
        with scratch.connect() as conn:
            linked = conn.execute(text("SELECT COUNT(*) FROM workingstep WHERE tool_id IS NOT NULL")).scalar()
        guard(scratch)
        summary = core.run_reset(tools=True, engine=scratch, roots=roots, raw_root=raw, check_modules=False)
        after = counts(scratch)
        check(f"공구 {before['tool']}행 삭제", after["tool"] == 0 and summary["tools"]["deleted"] == before["tool"])
        check("공구 외 테이블은 모두 그대로", all(after[t] == before[t] for t in after if t != "tool"))
        with scratch.connect() as conn:
            unlinked = conn.execute(text("SELECT COUNT(*) FROM workingstep WHERE tool_id IS NULL")).scalar()
            numbers = conn.execute(text("SELECT COUNT(*) FROM workingstep WHERE tool_number IS NOT NULL")).scalar()
            conn.execute(text("INSERT INTO tool (memo) VALUES ('번호 확인')"))
            new_id = conn.execute(text("SELECT MAX(tool_id) FROM tool")).scalar()
            conn.rollback()
        check(f"가공 이력 {before['workingstep']}건은 남고 공구 연결만 비워짐 (연결돼 있던 {linked}건)",
              unlinked == before["workingstep"] and numbers == before["workingstep"])
        check("다음 공구 번호가 1부터", new_id == 1, f"tool_id {new_id}")
        check("감시 폴더 루트의 공구 마스터 파일 2개 삭제, 조도 CSV 는 그대로",
              sorted(summary["tools"]["removed_files"]) == ["공구 마스터.csv", "실험실 공구 정리.xlsx"]
              and os.path.exists(os.path.join(raw, "surface_roughness_01.csv")))
        check("스토리지의 다른 파일은 그대로", core.count_files(raw) == 3
              and all(core.count_files(r) == 2 for r in roots[1:]))

        print("\n[5] DB + 공구 목록")
        before = seed(scratch)
        roots, raw = make_tree(os.path.join(work, "t5"))
        guard(scratch)
        summary = core.run_reset(db=True, tools=True, engine=scratch, roots=roots, raw_root=raw,
                                 check_modules=False)
        after = counts(scratch)
        check("계정 테이블만 남고 나머지는 모두 빔",
              all(after[t] == (before[t] if t in core.ACCOUNT_TABLES else 0) for t in after))
        check("다시 만든 tool 에 CHECK · UNIQUE · 외래키 그대로", tool_constraints(scratch) == (True, True, True))
        check(f"요약: 공구 {before['tool']}행 삭제로 보고", summary["tools"]["deleted"] == before["tool"])
        check("감시 폴더의 공구 마스터 파일도 지움 (조도 CSV 는 그대로)",
              sorted(summary["tools"]["removed_files"]) == ["공구 마스터.csv", "실험실 공구 정리.xlsx"]
              and core.count_files(raw) == 3)

        print("\n[6] 스토리지만")
        before = seed(scratch)
        roots, raw = make_tree(os.path.join(work, "t6"))
        guard(scratch)
        summary = core.run_reset(storage=True, engine=scratch, roots=roots, raw_root=raw, check_modules=False)
        check("세 폴더가 비고 루트 폴더는 남음",
              all(os.path.isdir(r) and not os.listdir(r) for r in roots), str(summary["storage"]))
        check("DB 는 그대로", counts(scratch) == before)
        probe = os.path.join(core.VAULT_ROOT, "__reset_selftest_probe__")
        check("보관소 안쪽 경로는 지우기 전에 막힘 (아무것도 만들지 않음)",
              core.clean_directory(probe) == (0, 0) and not os.path.exists(probe))

        print("\n[7] 세 항목 모두")
        before = seed(scratch)
        roots, raw = make_tree(os.path.join(work, "t7"))
        guard(scratch)
        summary = core.run_reset(db=True, storage=True, tools=True, engine=scratch, roots=roots,
                                 raw_root=raw, check_modules=False)
        after = counts(scratch)
        check("계정만 남고 DB 는 모두 빔",
              all(after[t] == (before[t] if t in core.ACCOUNT_TABLES else 0) for t in after))
        check("폴더가 모두 빔", all(not os.listdir(r) for r in roots))
        check(f"실제 백업 보관소 파일 {vault_before:,}개 그대로",
              summary["vault_files"] == vault_before == core.count_files(core.VAULT_ROOT))
    finally:
        core.running_modules = real_running
        scratch.dispose()
        with live_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{SCRATCH}`"))
        shutil.rmtree(work, ignore_errors=True)

    print("\n[8] 실제 DB")
    with live_engine.connect() as conn:
        gone = not conn.execute(text("SELECT COUNT(*) FROM information_schema.schemata "
                                     "WHERE schema_name = :s"), {"s": SCRATCH}).scalar()
    check("시험용 스키마를 지움", gone)
    check(f"실제 DB '{LIVE_DB}' 의 모든 테이블 행 수가 시작 때와 같음", counts(live_engine) == live_before)

    print()
    if failures:
        print(f"실패 {len(failures)}건: {failures}")
        return 1
    print("모두 통과 — 실제 DB 는 읽기만 했고, 시험용 스키마와 임시 폴더는 지웠습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
