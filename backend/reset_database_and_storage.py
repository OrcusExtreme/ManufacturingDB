"""DB · 스토리지 · 공구 목록 초기화 — 항목을 골라서 지운다.

  db       가공 데이터 DB — 공구 목록과 로그인 계정을 뺀 모든 테이블(과 뷰)을 DROP 하고
           models.py 최신 스키마로 다시 만든다(create_all). 남긴 테이블은 그대로 둔다.
  storage  프로젝트 data/ 의 machining_raw_data · processed_data · failed_data 를 비운다 (루트 폴더 유지)
  tools    공구 목록(tool 테이블)을 비운다. 감시 폴더 루트의 공구 마스터 파일도 지운다
           (남겨 두면 수집기가 다시 켜질 때 그 파일로 공구 목록을 되살린다).

어떤 항목을 골라도 지우지 않는 것
  - 원본 백업 보관소(archive_vault). 원본이 사라져도 복원할 수 있게 해 주는 마지막 방어선이고,
    프로젝트와 함께 지워지지 않도록 일부러 밖으로 빼 둔 폴더다. 대상에서 뺄 뿐 아니라
    clean_directory 가 보관소 안쪽 경로를 받으면 실제 삭제 직전에 한 번 더 막는다(이중 안전장치).
    보관소의 '공구 마스터.csv' 도 남으므로, 보관소에서 복원하면 공구 목록이 다시 채워진다.
  - 로그인 계정(app_user · app_session · app_pending_signup). 계정은 Controller 의 '유저 관리'에서
    관리한다. 초기화마다 계정이 사라지면 모두가 다시 가입 신청·승인을 거쳐야 한다.

실행 전에 수집기 · 웹 대시보드 · TDMS 변환기를 멈춰야 한다(running_modules). 수집기가 켜져 있으면
지운 Job 폴더를 '유실'로 보고 보관소에서 자동 복원해 스토리지 초기화가 무효가 되고, DB 를 다시
만드는 동안 다른 모듈이 행을 쓰면 상태가 뒤엉킨다.

제조 DB Controller 의 'DB · 스토리지 초기화' 버튼은 reset_manager.py 창을 띄우고, 그 창이 이 모듈의
run_reset() 을 부른다. 명령줄에서도 쓸 수 있다 (항목을 하나도 고르지 않으면 아무것도 하지 않는다).

    python backend/reset_database_and_storage.py --db                 # 가공 데이터 DB 만
    python backend/reset_database_and_storage.py --db --storage       # DB + 스토리지
    python backend/reset_database_and_storage.py --all                # DB + 스토리지 + 공구 목록
"""
import argparse
import os
import shutil
import stat
import sys

# backend 경로 추가
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(backend_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text

import DB.database as dbmod
from DB.database import Base
# 이름을 직접 쓰지는 않지만 반드시 필요하다. 이 import 가 있어야 모든 모델이 Base.metadata 에
# 등록되고, 아래 create_all 이 지운 테이블을 다시 만든다. 지우면 빈 DB 가 된다.
from DB import models  # noqa: F401
from DB.schema_patch import ensure_schema
from vault_manager import (RAW_DATA_ROOT, VAULT_ROOT, VAULT_IS_EXTERNAL,
                           PROCESSED_ROOT, FAILED_ROOT, is_inside_vault)

# 어떤 항목을 골라도 지우지 않는 테이블 (로그인 계정)
ACCOUNT_TABLES = ("app_user", "app_session", "app_pending_signup")
TOOL_TABLE = "tool"
STORAGE_ROOTS = (RAW_DATA_ROOT, PROCESSED_ROOT, FAILED_ROOT)

# 초기화 전에 멈춰 있어야 하는 모듈 (실행 스크립트 이름 → 화면 이름)
MODULE_SCRIPTS = {
    "data_insert_recognization.py": "Watchdog 수집기",
    "dashboard.py": "웹 대시보드",
    "tdms_visualizer.py": "TDMS 변환기",
}


class ResetError(Exception):
    """초기화를 시작하지 않았다 (고른 항목 없음 · 모듈 실행 중)."""


# ---------------------------------------------------------------------------
# 사전 점검 · 현재 상태
# ---------------------------------------------------------------------------
def running_modules():
    """초기화 전에 멈춰야 하는 모듈 중 지금 돌고 있는 것 [(화면 이름, PID), ...].

    컨트롤러로 띄웠든 tools/run_system.py 로 띄웠든, 실행 중인 프로세스의 명령줄에 그 스크립트가
    있는지로 판단한다. 권한이 없어 명령줄을 못 읽는 프로세스(시스템 프로세스 등)는 건너뛴다.
    """
    import psutil

    found = []
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid, cmdline = proc.info["pid"], proc.info["cmdline"] or []
        except (psutil.Error, OSError):
            continue
        if pid == me:
            continue
        names = {os.path.basename(str(arg)) for arg in cmdline}
        for script, label in MODULE_SCRIPTS.items():
            if script in names:
                found.append((label, pid))
                break
    return sorted(found)


def _tables(conn):
    return [r[0] for r in conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"))]


def _count(conn, table):
    return conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar() or 0


def tables_to_reset(tables, keep_tools):
    """가공 데이터 DB 초기화가 지울 테이블. 계정은 항상, 공구는 keep_tools 면 남긴다."""
    return [t for t in tables
            if t not in ACCOUNT_TABLES and not (keep_tools and t == TOOL_TABLE)]


def count_files(root):
    total = 0
    if os.path.isdir(root):
        for _root, _dirs, files in os.walk(root):
            total += len(files)
    return total


def snapshot(engine=None, roots=STORAGE_ROOTS):
    """항목마다 지워질 양과 남는 것을 센다 (초기화 창이 보여 준다)."""
    engine = engine or dbmod.engine
    with engine.connect() as conn:
        tables = _tables(conn)
        counts = {t: _count(conn, t) for t in tables}
    data_tables = tables_to_reset(tables, keep_tools=True)
    storage_files = {root: count_files(root) for root in roots}
    return {
        "db_tables": len(data_tables),
        "db_rows": sum(counts[t] for t in data_tables),
        "tool_rows": counts.get(TOOL_TABLE, 0),
        "accounts": counts.get("app_user", 0),
        "storage_total": sum(storage_files.values()),
        "storage_files": storage_files,
    }


# ---------------------------------------------------------------------------
# 스토리지
# ---------------------------------------------------------------------------
def _remove_readonly(func, path, exc_info):
    """Windows 파일/폴더 삭제 시 읽기 전용 속성 해제 후 재시도"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"    [경고] {path} 삭제 재시도 실패: {e}")


def clean_directory(dir_path, recreate_subdirs=None):
    """디렉터리 내의 모든 파일과 하위 폴더를 삭제하고 루트는 유지. (폴더 수, 파일 수) 를 돌려준다.

    백업 보관소(archive_vault)와 그 안쪽은 어떤 경우에도 지우지 않는다.
    호출부에서 이미 보관소를 대상에서 빼 두었지만, 경로 설정이 바뀌거나 호출이 잘못돼도
    원본 백업이 날아가는 일은 없어야 해서 실제 삭제 직전에 한 번 더 막는다.
    """
    if is_inside_vault(dir_path):
        print(f"[보호] 백업 보관소는 초기화 대상이 아닙니다. 건너뜁니다: {dir_path}")
        return 0, 0

    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
        print(f"[스토리지 초기화] 폴더 생성: {dir_path}")
        return 0, 0

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
                # Python 3.12 부터 onerror 는 권장되지 않는다 (같은 함수를 onexc 로 넘긴다)
                if sys.version_info >= (3, 12):
                    shutil.rmtree(item_path, onexc=_remove_readonly)
                else:
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
    return deleted_dirs, deleted_files


def reset_storage(roots=STORAGE_ROOTS):
    """프로젝트 data/ 만 비운다. 백업 보관소는 건드리지 않는다. {폴더: 남은 파일 수}."""
    print("\n=== 스토리지 초기화 (프로젝트 data/) ===")
    # 보관소는 의도적으로 목록에서 뺀다. 원본이 사라져도 복원할 수 있게 해 주는
    # 마지막 방어선이라, 초기화가 닿으면 안 된다.
    print(f"  [보존] 백업 보관소는 지우지 않습니다: {VAULT_ROOT}")
    if VAULT_IS_EXTERNAL:
        print("         (프로젝트 밖에 있어 프로젝트를 지워도 남습니다)")
    for target in roots:
        clean_directory(target)
    left = {root: count_files(root) for root in roots}
    print("=== 스토리지 초기화 완료 ===")
    return left


# ---------------------------------------------------------------------------
# 공구 목록
# ---------------------------------------------------------------------------
def remove_watch_tool_files(raw_root=RAW_DATA_ROOT):
    """감시 폴더 루트의 공구 마스터 파일을 지운다. 지운 파일 이름 목록.

    남겨 두면 수집기가 다시 켜질 때 '지금 DB(빈 공구 목록)와 다른 파일'로 보고 공구 목록을
    되살릴 수 있다. 보관소의 사본은 그대로 남는다.
    """
    from tool_inserter import is_tool_master_file

    removed = []
    if os.path.isdir(raw_root) and not is_inside_vault(raw_root):
        for name in sorted(os.listdir(raw_root)):
            path = os.path.join(raw_root, name)
            if os.path.isfile(path) and is_tool_master_file(name):
                try:
                    os.remove(path)
                    removed.append(name)
                except OSError as e:
                    print(f"    [오류] {path} 삭제 실패: {e}")
    for name in removed:
        print(f"  - 감시 폴더의 공구 마스터 파일 삭제: {name}")
    return removed


def reset_tools(engine=None, raw_root=RAW_DATA_ROOT):
    """공구 목록을 비운다. {"deleted": 지운 행 수, "removed_files": [...]}.

    가공 이력(workingstep)은 남긴다. tool_id 는 외래키(ON DELETE SET NULL)로 비워지지만
    tool_number · xml_tool_code 가 남으므로, 나중에 공구 마스터를 다시 넣으면 다시 연결된다.
    """
    engine = engine or dbmod.engine
    print("\n=== 공구 목록 초기화 ===")
    with engine.begin() as conn:
        if TOOL_TABLE not in _tables(conn):
            print("  tool 테이블이 없어 건너뜁니다")
            deleted = 0
        else:
            deleted = _count(conn, TOOL_TABLE)
            conn.execute(text(f"DELETE FROM `{TOOL_TABLE}`"))
            conn.execute(text(f"ALTER TABLE `{TOOL_TABLE}` AUTO_INCREMENT = 1"))
            print(f"  - tool: {deleted}행 삭제 (가공 이력의 공구 연결은 비워지고 T번호는 남음)")
    removed = remove_watch_tool_files(raw_root)
    print("=== 공구 목록 초기화 완료 ===")
    return {"deleted": deleted, "removed_files": removed}


# ---------------------------------------------------------------------------
# 가공 데이터 DB
# ---------------------------------------------------------------------------
def reset_database(keep_tools=True, engine=None):
    """가공 데이터 테이블을 DROP 하고 models.py 로 다시 만든다.

    로그인 계정은 항상, 공구 목록은 keep_tools 면 남긴다(create_all 은 없는 테이블만 만들므로
    남긴 테이블과 그 내용은 그대로다). {"dropped": {테이블: 지운 행 수}, "preserved": {테이블: 행 수}}.
    """
    engine = engine or dbmod.engine
    print("\n=== 가공 데이터 DB 초기화 ===")
    with engine.connect() as conn:
        tables = _tables(conn)
        targets = tables_to_reset(tables, keep_tools)
        dropped = {t: _count(conn, t) for t in targets}
        preserved = {t: _count(conn, t) for t in tables if t not in targets}
        views = [r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.views WHERE table_schema = DATABASE()"))]

        # 외래키 검사를 끈 채로 지운다 (지우는 순서와 상관없이). 같은 연결에서 반드시 되켠다.
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for tbl in targets:
                conn.execute(text(f"DROP TABLE IF EXISTS `{tbl}`"))
                print(f"  - DROP TABLE `{tbl}` ({dropped[tbl]}행)")
            for view in views:
                conn.execute(text(f"DROP VIEW IF EXISTS `{view}`"))
                print(f"  - DROP VIEW `{view}`")
        finally:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()

    for tbl, rows in sorted(preserved.items()):
        print(f"  [보존] {tbl} ({rows}행)")

    # models.py 기준으로 지운 테이블을 다시 만든다. 남긴 테이블은 건드리지 않는다.
    print("\n  최신 모델 정의로 테이블 다시 만들기 (Base.metadata.create_all)...")
    Base.metadata.create_all(bind=engine)

    # 실제 DB 라면 기동 시와 같은 스키마 보정도 건다 (방금 만든 테이블에는 할 일이 없다).
    if engine is dbmod.engine:
        ensure_schema(verbose=True)

    with engine.connect() as conn:
        left = {t: _count(conn, t) for t in targets if t in _tables(conn)}
        kept = {t: _count(conn, t) for t in preserved}
    not_empty = {t: n for t, n in left.items() if n}
    changed = {t: (preserved[t], kept[t]) for t in preserved if kept[t] != preserved[t]}
    if not_empty:
        print(f"  [주의] 비워지지 않은 테이블: {not_empty}")
    if changed:
        print(f"  [주의] 보존 대상인데 행 수가 바뀐 테이블: {changed}")
    print(f"=== 가공 데이터 DB 초기화 완료: {len(targets)}개 테이블 다시 만듦 ===")
    return {"dropped": dropped, "preserved": preserved}


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------
def run_reset(db=False, storage=False, tools=False, engine=None, roots=STORAGE_ROOTS,
              raw_root=RAW_DATA_ROOT, check_modules=True):
    """고른 항목만 초기화한다. 시작할 수 없으면 ResetError (아무것도 지우지 않은 상태).

    순서는 DB → 공구 목록 → 스토리지. DB 를 먼저 하는 이유는, 도중에 실패해도 원본 파일이
    남아 있으면 수집기로 다시 채울 수 있기 때문이다.
    """
    if not (db or storage or tools):
        raise ResetError("초기화할 항목을 하나 이상 고르세요.")
    if check_modules:
        running = running_modules()
        if running:
            names = ", ".join(f"{label}(PID {pid})" for label, pid in running)
            raise ResetError(f"실행 중인 모듈이 있어 초기화하지 않았습니다: {names}. "
                             f"Controller 에서 「전체 중지」 한 뒤 다시 하세요.")

    chosen = [name for name, on in (("가공 데이터 DB", db), ("스토리지", storage),
                                     ("공구 목록", tools)) if on]
    print("==================================================")
    print("   DB · 스토리지 초기화")
    print("==================================================")
    print(f"  초기화 대상 : {', '.join(chosen)}")
    print(f"  보존 대상   : 백업 보관소 {VAULT_ROOT} · 로그인 계정"
          + ("" if tools else " · 공구 목록"))
    print("==================================================")

    summary: dict = {}
    if db:
        summary["db"] = reset_database(keep_tools=not tools, engine=engine)
    if tools:
        if db:
            # 공구 테이블은 위에서 함께 다시 만들었다. 감시 폴더의 공구 마스터 파일만 치운다.
            print("\n=== 공구 목록 초기화 (가공 데이터 DB 와 함께 다시 만듦) ===")
            removed = remove_watch_tool_files(raw_root)
            summary["tools"] = {"deleted": summary["db"]["dropped"].get(TOOL_TABLE, 0),
                                "removed_files": removed}
        else:
            summary["tools"] = reset_tools(engine=engine, raw_root=raw_root)
    if storage:
        summary["storage"] = reset_storage(roots)

    # 보관소가 초기화를 온전히 견뎠는지 실제 파일 수로 확인해 남긴다.
    survived = count_files(VAULT_ROOT)
    summary["vault_files"] = survived
    print("\n==================================================")
    print("   초기화 완료")
    print(f"  백업 보관소 보존 확인: 파일 {survived:,}개 그대로 남아 있음")
    print("==================================================")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="DB · 스토리지 · 공구 목록 중 고른 항목만 초기화합니다. "
                    "백업 보관소와 로그인 계정은 지우지 않습니다.")
    parser.add_argument("--db", action="store_true", help="가공 데이터 DB (공구 목록 · 계정 제외)")
    parser.add_argument("--storage", action="store_true",
                        help="프로젝트 data/ (machining_raw_data · processed_data · failed_data)")
    parser.add_argument("--tools", action="store_true", help="공구 목록 (tool 테이블)")
    parser.add_argument("--all", action="store_true", help="세 항목 모두")
    args = parser.parse_args(argv)
    db, storage, tools = (args.db or args.all), (args.storage or args.all), (args.tools or args.all)
    if not (db or storage or tools):
        parser.print_help()
        print("\n[중단] 초기화할 항목을 고르세요. 아무것도 지우지 않았습니다.")
        return 2
    try:
        run_reset(db=db, storage=storage, tools=tools)
    except ResetError as e:
        print(f"[중단] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
