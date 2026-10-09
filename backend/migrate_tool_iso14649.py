"""tool 테이블을 ISO 14649 구조로 바꾸고 docs/tool_iso14649.csv 로 채운다.

무엇을 하나
-----------
1. docs/tool_iso14649.csv 를 공구 입력기 검사로 확인한다 (틀리면 아무것도 바꾸지 않는다)
2. 옛 tool 테이블과 가공 이력의 공구 연결을 backups/ 에 CSV 로 남긴다
3. 옛 tool 테이블을 지우고 models.Tool(= docs/tool_iso14649_schema.sql) 로 새로 만든다
   - workingstep → tool 외래키를 잠시 풀었다가 같은 이름 · ON DELETE SET NULL 로 다시 건다
4. CSV 를 넣고 가공 이력의 공구 연결을 T번호로 다시 맺는다 (대시보드 업로드와 같은 코드)
5. 보관소에 '공구 마스터.csv' 를 남긴다 (보관소에서 원본 복원 시 이 파일로 다시 채워진다)

왜 ALTER 로 고치지 않고 다시 만드나
-----------------------------------
열이 절반 넘게 바뀌고(company_name→manufacturer, tool_type→iso14649_class, specification 삭제,
DOUBLE→DECIMAL, tool_code NOT NULL→NULL+UNIQUE, CHECK 추가) 내용도 CSV 로 통째로 바뀐다. 고쳐
쓰면 열 순서·기본값을 하나하나 맞춰야 해서 빠뜨리기 쉽다. 새로 만들면 모델과 똑같은 테이블이
보장된다. 가공 이력은 tool_id 가 아니라 tool_number / xml_tool_code 로 공구를 기억하므로 다시
연결할 수 있다.

안전장치
--------
- 기본은 미리보기(dry-run). 실제 적용은 --apply.
- 적용 전에 컨트롤러에서 「전체 중지」 하세요. 옛 코드가 새 테이블을 읽으면 오류가 난다.
- 이미 새 구조면 테이블은 두고 CSV 내용만 다시 넣는다 (여러 번 돌려도 같은 결과).
- MySQL 의 DDL 은 자동 커밋이라 중간에 끊기면 되돌릴 수 없다. 그래서 바꾸기 전에 CSV 를 먼저
  검사하고 옛 내용을 backups/ 에 남긴다. 실패하면 원인을 고치고 다시 돌리면 남은 단계를 마저 한다.
- 끝난 뒤 python backend/tests/verify_tool_csv.py 로 실제 테이블까지 대조한다.

사용법
------
    python backend/migrate_tool_iso14649.py
    python backend/migrate_tool_iso14649.py --apply
"""
import csv
import datetime
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import text

from DB.database import engine
from DB.models import Tool
import tool_inserter

PROJECT_DIR = os.path.dirname(BACKEND_DIR)
CSV_PATH = os.path.join(PROJECT_DIR, "docs", "tool_iso14649.csv")
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")
DEFAULT_FK_NAME = "workingstep_ibfk_2"


def _has_table(conn, table):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table}).scalar())


def _has_column(conn, table, column):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"),
        {"t": table, "c": column}).scalar())


def _tool_foreign_keys(conn):
    """workingstep → tool 외래키의 (이름, ON DELETE 규칙) 목록."""
    return [tuple(r) for r in conn.execute(text(
        "SELECT CONSTRAINT_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'workingstep' "
        "AND REFERENCED_TABLE_NAME = 'tool'"))]


def _dump(conn, query, path):
    result = conn.execute(text(query))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(result.keys())
        writer.writerows(["" if v is None else v for v in row] for row in result)


def _backup(conn, stamp):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    tool_path = os.path.join(BACKUP_DIR, f"tool_before_iso14649_{stamp}.csv")
    link_path = os.path.join(BACKUP_DIR, f"workingstep_tool_links_{stamp}.csv")
    _dump(conn, "SELECT * FROM tool ORDER BY tool_id", tool_path)
    _dump(conn, "SELECT step_id, workplan_id, step_order, tool_number, xml_tool_code, tool_id "
                "FROM workingstep ORDER BY step_id", link_path)
    return tool_path, link_path


def migrate(apply=False):
    print("=" * 70)
    print(" tool 테이블 → ISO 14649 구조 · docs/tool_iso14649.csv 적재")
    print("=" * 70)

    print("\n[1/5] CSV 검사 (공구 입력기와 같은 규칙)")
    try:
        rows = tool_inserter.parse_tool_master(CSV_PATH)
    except tool_inserter.ToolMasterError as e:
        print(f"  [중단] {e.summary} 아무것도 바꾸지 않았습니다.")
        for problem in e.problems:
            print(f"    · {problem}")
        return 1
    mounted = sum(1 for r in rows if r["tool_code"])
    print(f"  통과: {len(rows)}행 (장착 {mounted} · 미장착 {len(rows) - mounted})")

    with engine.connect() as conn:
        exists = _has_table(conn, "tool")
        new_schema = exists and _has_column(conn, "tool", "iso14649_class")
        old_rows = conn.execute(text("SELECT COUNT(*) FROM tool")).scalar() if exists else 0
        fks = _tool_foreign_keys(conn) if _has_table(conn, "workingstep") else []
        linked_now = conn.execute(text(
            "SELECT COUNT(*) FROM workingstep WHERE tool_id IS NOT NULL")).scalar() \
            if _has_table(conn, "workingstep") else 0
    state = "새 구조" if new_schema else ("옛 구조" if exists else "없음")
    print(f"  현재 tool 테이블: {state} ({old_rows}행) · 외래키 {fks or '없음'} · "
          f"공구가 연결된 가공 스텝 {linked_now}건")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("\n[2/5] 옛 내용 백업 (backups/)")
    if new_schema:
        print("  이미 새 구조라 건너뜁니다 (지울 옛 테이블이 없음)")
    elif not exists:
        print("  tool 테이블이 없어 건너뜁니다")
    elif apply:
        with engine.connect() as conn:
            for path in _backup(conn, stamp):
                print(f"  + {os.path.relpath(path, PROJECT_DIR)}")
    else:
        print(f"  + backups/tool_before_iso14649_{stamp}.csv ({old_rows}행)")
        print(f"  + backups/workingstep_tool_links_{stamp}.csv")

    fk_name = fks[0][0] if fks else DEFAULT_FK_NAME
    print("\n[3/5] tool 테이블 다시 만들기")
    if new_schema:
        print("  이미 새 구조입니다")
    else:
        if exists:
            print(f"  - 외래키 {fk_name} 풀기 → 가공 스텝의 tool_id 비우기 → 옛 tool 삭제")
        print("  + models.Tool 로 생성 (= docs/tool_iso14649_schema.sql)")
        print(f"  + 외래키 {fk_name}: workingstep.tool_id → tool.tool_id ON DELETE SET NULL")
        if apply:
            with engine.begin() as conn:
                if exists:
                    for name, _rule in fks:
                        conn.execute(text(f"ALTER TABLE workingstep DROP FOREIGN KEY `{name}`"))
                    conn.execute(text("UPDATE workingstep SET tool_id = NULL"))
                    conn.execute(text("DROP TABLE tool"))
                Tool.__table__.create(bind=conn)
                if _has_table(conn, "workingstep") and not _tool_foreign_keys(conn):
                    conn.execute(text(
                        f"ALTER TABLE workingstep ADD CONSTRAINT `{fk_name}` FOREIGN KEY (tool_id) "
                        f"REFERENCES tool (tool_id) ON DELETE SET NULL"))

    print("\n[4/5] CSV 적재 · 가공 이력 공구 재연결")
    if apply:
        stats = tool_inserter.replace_tool_master(rows)
        print(f"  {stats['inserted']}행 적재 (장착 {stats['mounted']} · "
              f"미장착 {stats['inserted'] - stats['mounted']}, 기존 {stats['deleted']}행 교체)")
        print(f"  가공 스텝 공구 연결 {stats['linked']}건 · 미연결 {stats['unlinked']}건")
    else:
        print(f"  {len(rows)}행을 넣고, 가공 스텝의 T번호로 공구를 다시 연결합니다")

    print("\n[5/5] 보관소에 공구 마스터 보관")
    if apply:
        archived = tool_inserter.archive_tool_master()
        print(f"  + {archived}" if archived else "  [경고] 보관 실패 - 서버 로그 확인")
    else:
        print(f"  + archive_vault/{tool_inserter.TOOL_MASTER_VAULT_FILENAME}")

    print()
    print("=" * 70)
    if apply:
        print(" 적용 완료. 대조: python backend/tests/verify_tool_csv.py")
    else:
        print(" 미리보기입니다. 실제 적용: python backend/migrate_tool_iso14649.py --apply")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(migrate(apply="--apply" in sys.argv))
