"""공구 마스터(CSV · 모델 · 실제 tool 테이블)가 서로 맞는지 실제 MySQL 로 검증한다.

docs/tool_iso14649_schema.sql 과 models.Tool 을 각각 **임시 테이블**(CREATE TEMPORARY TABLE)로 만들어
docs/tool_iso14649.csv 를 넣어 보고, 마이그레이션이 끝난 DB 라면 실제 tool 테이블까지 대조한다.
임시 테이블은 연결이 끊기면 사라지고 실제 테이블은 읽기만 하므로 DB 에는 아무것도 남지 않는다.
CSV 를 고친 뒤나 마이그레이션 뒤에 다시 돌리면 된다.

    python backend/tests/verify_tool_csv.py

검사 항목
   1. CSV 열 = DDL 의 테이블 열 (tool_id · created_at · updated_at 제외)
   2. CSV 가 공구 입력기 검사(대시보드 업로드 · 수집기와 같은 코드)를 통과한다
   3. strict 모드에서 모든 행이 경고 없이 들어간다 (타입 · 길이 · DECIMAL 범위)
   4. 넣은 값이 그대로 돌아오고, 빈 칸은 DEFAULT 가 아니라 NULL 로 남는다
   5. tool_code 는 표준 표기(T06), iso14649_class 는 정해 둔 이름만, 분류가 빈 행은 미장착뿐
   6. 장착 공구(tool_code 있음)는 분류·직경·날 수가 비면 CHECK 에 막히고, 미장착은 통과한다
   7. tool_code 중복은 막히고, 미장착(NULL)은 여러 개여도 된다
   8. 파서가 쓰는 T번호 매칭(WHERE tool_code IS NOT NULL)에 장착 공구만 잡힌다
   9. models.Tool 로 만든 테이블 = DDL 파일로 만든 테이블 (열 · 인덱스 · CHECK)
  10. 실제 tool 테이블 = DDL, 내용 = CSV(내보내기가 바이트까지 같음), 외래키 · 가공 이력 연결
"""
import csv
import os
import re
import sys
from decimal import Decimal

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_dir = os.path.dirname(backend_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable

from DB.database import engine
from DB.models import Tool
from tool_codes import canonical_tool_code, index_by_number, tool_code_number
from tool_inserter import (ALLOWED_CLASSES, TOOL_COLUMNS, ToolMasterError, export_tool_master_csv,
                           parse_tool_master)

SCHEMA_PATH = os.path.join(project_dir, "docs", "tool_iso14649_schema.sql")
CSV_PATH = os.path.join(project_dir, "docs", "tool_iso14649.csv")
DDL_TABLE = "tool_csv_check"
MODEL_TABLE = "tool_model_check"

failures = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def as_temp(ddl, table):
    """CREATE TABLE tool (...) 을 임시 테이블 DDL 로 바꾼다. 주석은 걷어낸다.

    CHECK 제약 이름은 스키마 안에서 유일해야 하므로 실제 테이블과 겹치지 않게 바꾼다.
    """
    ddl = re.sub(r"--[^\n]*", "", ddl)
    out = re.sub(r"CREATE TABLE\s+`?tool`?\s*\(", f"CREATE TEMPORARY TABLE {table} (", ddl, count=1)
    assert out != ddl, "DDL 에서 'CREATE TABLE tool (' 를 찾지 못했습니다"
    return out.replace("chk_tool_mounted_complete", f"chk_{table}_mounted").strip().rstrip(";")


def _extra(value):
    """SHOW COLUMNS 의 Extra 에서 테이블 종류에 따라 붙는 표기만 걷어낸다.

    같은 DDL 이라도 MySQL 8.0 은 임시 테이블에 'NULL' · 'on update …' 를, 일반 테이블에
    '' · 'DEFAULT_GENERATED on update …' 를 보고한다(임시 테이블은 데이터 사전을 거치지 않음 —
    실측 확인). 이 둘을 빼면 auto_increment · on update CURRENT_TIMESTAMP 는 그대로 비교된다.
    """
    return " ".join(t for t in (value or "").split() if t not in ("NULL", "DEFAULT_GENERATED"))


def columns_of(conn, table):
    """SHOW COLUMNS 의 (Field, Type, Null, Key, Default, Extra)."""
    return [tuple(r[:5]) + (_extra(r[5]),) for r in conn.execute(text(f"SHOW COLUMNS FROM {table}"))]


def indexes_of(conn, table):
    return sorted((r[2], r[4], r[1]) for r in conn.execute(text(f"SHOW INDEX FROM {table}")))


def check_clause(conn, table):
    """SHOW CREATE TABLE 에서 CHECK 식만 (제약 이름 없이)."""
    create = conn.execute(text(f"SHOW CREATE TABLE {table}")).fetchone()[1]
    m = re.search(r"CONSTRAINT `[^`]+` CHECK (.+?)(?:,\n|\n\))", create, re.S)
    return m.group(1).strip() if m else None


def same(csv_value, db_value):
    if csv_value == "":
        return db_value is None
    if db_value is None:
        return False
    if isinstance(db_value, Decimal):
        return Decimal(csv_value) == db_value
    if isinstance(db_value, int):
        return int(csv_value) == db_value
    return str(db_value) == csv_value


def insert_rows(conn, table, rows):
    conn.execute(text(f"INSERT INTO {table} ({', '.join(TOOL_COLUMNS)}) "
                      f"VALUES ({', '.join(':' + c for c in TOOL_COLUMNS)})"), rows)


def rejected(conn, table, row):
    """이 행이 제약에 막히는지. 막히면 (True, 오류 코드). 들어가도 되돌려 CSV 행만 남긴다."""
    savepoint = conn.begin_nested()
    try:
        insert_rows(conn, table, [row])
    except DBAPIError as e:
        savepoint.rollback()
        return True, e.orig.args[0] if e.orig is not None and e.orig.args else None
    savepoint.rollback()
    return False, None


def live_tool_has_new_schema(conn):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() "
        "AND table_name = 'tool' AND column_name = 'iso14649_class'")).scalar())


def main():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        csv_rows = list(csv.DictReader(f))
    columns = list(csv_rows[0].keys())
    mounted = [r for r in csv_rows if r["tool_code"]]
    unmounted = [r for r in csv_rows if not r["tool_code"]]
    print(f"CSV {len(csv_rows)}행 (장착 {len(mounted)} · 미장착 {len(unmounted)}) · 열 {len(columns)}개")

    with open(SCHEMA_PATH, encoding="utf-8") as f:
        ddl_text = f.read()

    with engine.connect() as conn:
        conn.execute(text("SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,"
                          "NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'"))
        conn.exec_driver_sql(as_temp(ddl_text, DDL_TABLE))

        print("\n[1] 열 구성")
        ddl_cols = [c[0] for c in columns_of(conn, DDL_TABLE)]
        expected = [c for c in ddl_cols if c not in ("tool_id", "created_at", "updated_at")]
        check("CSV 열 = DDL 열 (순서 포함)", columns == expected == list(TOOL_COLUMNS),
              f"CSV에만 {sorted(set(columns) - set(expected))} / DDL에만 {sorted(set(expected) - set(columns))}")

        print("\n[2] 공구 입력기 검사")
        try:
            typed = parse_tool_master(CSV_PATH)
            check(f"입력기 검사 통과 ({len(typed)}행)", len(typed) == len(csv_rows))
        except ToolMasterError as e:
            check("입력기 검사 통과", False, f"{e.summary} {e.problems[:5]}")
            typed = [{c: (r[c] or None) for c in TOOL_COLUMNS} for r in csv_rows]

        print("\n[3] 전 행 넣기 (strict 모드)")
        warned = []
        for row in typed:
            insert_rows(conn, DDL_TABLE, [row])
            w = conn.execute(text("SHOW WARNINGS")).fetchall()
            if w:
                warned.append((row["tool_code"] or row["model_code"] or (row["memo"] or "")[:20], w))
        check(f"{len(typed)}행 모두 경고 없이 들어감", not warned, str(warned[:3]))
        n = conn.execute(text(f"SELECT COUNT(*) FROM {DDL_TABLE}")).scalar()
        check(f"행 수 {n} = CSV {len(csv_rows)}", n == len(csv_rows))

        print("\n[4] 값 왕복 · 빈 칸은 NULL")
        stored = conn.execute(text(f"SELECT {', '.join(TOOL_COLUMNS)} FROM {DDL_TABLE} "
                                   f"ORDER BY tool_id")).fetchall()
        mismatches = [(r["tool_code"] or i, c, r[c], db[j])
                      for i, (r, db) in enumerate(zip(csv_rows, stored))
                      for j, c in enumerate(TOOL_COLUMNS) if not same(r[c], db[j])]
        check("모든 칸이 넣은 그대로 돌아옴", not mismatches, str(mismatches[:5]))
        for col in ("corner_radius", "number_of_teeth", "hand_of_cut", "coolant_through", "stock_count"):
            blank = sum(1 for r in csv_rows if r[col] == "")
            nulls = conn.execute(text(f"SELECT COUNT(*) FROM {DDL_TABLE} WHERE {col} IS NULL")).scalar()
            check(f"비운 {col} {blank}칸이 DEFAULT 가 아니라 NULL", blank == nulls, f"NULL {nulls}칸")

        print("\n[5] 표기 · 분류명")
        bad_codes = [r["tool_code"] for r in mounted if canonical_tool_code(r["tool_code"]) != r["tool_code"]]
        check("tool_code 가 모두 표준 표기(T06)", not bad_codes, str(bad_codes))
        bad_classes = sorted({r["iso14649_class"] for r in csv_rows
                              if r["iso14649_class"] and r["iso14649_class"] not in ALLOWED_CLASSES})
        check("iso14649_class 가 정해 둔 이름만", not bad_classes, str(bad_classes))
        check("분류가 빈 행은 미장착뿐", all(r["tool_code"] == "" for r in csv_rows if not r["iso14649_class"]))

        print("\n[6] 장착 공구 CHECK 제약")
        base = {c: None for c in TOOL_COLUMNS}
        probe = dict(base, tool_code="T98", iso14649_class="ENDMILL", cutter_diameter=Decimal("10"),
                     number_of_teeth=2)
        for missing in ("iso14649_class", "cutter_diameter", "number_of_teeth"):
            hit, code = rejected(conn, DDL_TABLE, dict(probe, **{missing: None}))
            check(f"장착 공구의 {missing} 가 비면 막힘", hit and code == 3819, f"오류 코드 {code}")
        hit, code = rejected(conn, DDL_TABLE, dict(base, memo="정체 불명 재고"))
        check("미장착 재고는 분류·직경·날 수가 비어도 들어감", not hit, f"오류 코드 {code}")

        print("\n[7] tool_code 유일성")
        if mounted:
            hit, code = rejected(conn, DDL_TABLE, dict(probe, tool_code=mounted[0]["tool_code"]))
            check(f"장착 번호 {mounted[0]['tool_code']} 중복은 막힘", hit and code == 1062, f"오류 코드 {code}")
        nulls = conn.execute(text(f"SELECT COUNT(*) FROM {DDL_TABLE} "
                                  f"WHERE tool_code IS NULL")).scalar() or 0
        check(f"미장착(tool_code NULL) {nulls}개가 함께 존재 (CSV {len(unmounted)}개)",
              nulls == len(unmounted) and nulls > 1)

        print("\n[8] 파서의 T번호 매칭")
        pairs = conn.execute(text(f"SELECT tool_code, tool_id FROM {DDL_TABLE} "
                                  f"WHERE tool_code IS NOT NULL")).fetchall()
        by_number = index_by_number(pairs)
        expected_numbers = {int(r["tool_code"][1:]) for r in mounted}
        check(f"매칭 표에 장착 공구 {len(expected_numbers)}개만", set(by_number) == expected_numbers,
              f"{sorted(by_number)}")
        ids = dict(pairs)
        check("NC 의 T6 이 T06 행으로 연결", "T06" not in ids or by_number.get(6) == ids["T06"])

        print("\n[9] models.Tool = DDL 파일")
        model_ddl = str(CreateTable(Tool.__table__).compile(dialect=mysql.dialect()))
        conn.exec_driver_sql(as_temp(model_ddl, MODEL_TABLE))
        model_cols, ddl_cols_full = columns_of(conn, MODEL_TABLE), columns_of(conn, DDL_TABLE)
        diff = [(a, b) for a, b in zip(model_cols, ddl_cols_full) if a != b]
        check("열 이름 · 타입 · NULL · 키 · 기본값 · Extra 가 모두 같음",
              len(model_cols) == len(ddl_cols_full) and not diff, str(diff[:3]))
        check("인덱스가 같음 (PK · tool_code UNIQUE)",
              indexes_of(conn, MODEL_TABLE) == indexes_of(conn, DDL_TABLE))
        check("CHECK 식이 같음",
              check_clause(conn, MODEL_TABLE) is not None
              and check_clause(conn, MODEL_TABLE) == check_clause(conn, DDL_TABLE))

        print("\n[10] 실제 tool 테이블")
        if not live_tool_has_new_schema(conn):
            print("  SKIP 실제 tool 테이블은 아직 옛 구조입니다 (마이그레이션 전)")
        else:
            live_cols = columns_of(conn, "tool")
            diff = [(a, b) for a, b in zip(live_cols, ddl_cols_full) if a != b]
            check("열 구성이 DDL 과 같음", len(live_cols) == len(ddl_cols_full) and not diff, str(diff[:3]))
            check("인덱스가 DDL 과 같음", indexes_of(conn, "tool") == indexes_of(conn, DDL_TABLE))
            check("CHECK 식이 DDL 과 같음", check_clause(conn, "tool") == check_clause(conn, DDL_TABLE))
            fk = conn.execute(text(
                "SELECT rc.DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
                "WHERE rc.CONSTRAINT_SCHEMA = DATABASE() AND rc.TABLE_NAME = 'workingstep' "
                "AND rc.REFERENCED_TABLE_NAME = 'tool'")).fetchall()
            check("workingstep → tool 외래키가 ON DELETE SET NULL 로 있음",
                  [r[0] for r in fk] == ["SET NULL"], str(fk))
            with open(CSV_PATH, "rb") as f:
                csv_bytes = f.read()
            check("DB 를 내보낸 CSV 가 docs/tool_iso14649.csv 와 바이트까지 같음",
                  export_tool_master_csv() == csv_bytes)
            ids = [r[0] for r in conn.execute(text("SELECT tool_id FROM tool ORDER BY tool_id"))]
            check(f"tool_id 가 1~{len(ids)} 빈 번호 없음", ids == list(range(1, len(ids) + 1)))

            live_by_number = index_by_number(conn.execute(text(
                "SELECT tool_code, tool_id FROM tool WHERE tool_code IS NOT NULL")))
            steps = conn.execute(text(
                "SELECT step_id, tool_number, xml_tool_code, tool_id FROM workingstep")).fetchall()
            wrong = []
            for step_id, number, xml_code, tool_id in steps:
                n = number if number else tool_code_number(xml_code)
                if live_by_number.get(n) != tool_id:
                    wrong.append((step_id, n, tool_id, live_by_number.get(n)))
            check(f"가공 이력 {len(steps)}건이 T번호대로 연결됨", not wrong, str(wrong[:5]))

        conn.rollback()

    print()
    if failures:
        print(f"실패 {len(failures)}건: {failures}")
        return 1
    print("모두 통과 — 임시 테이블은 연결 종료와 함께 사라졌습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
