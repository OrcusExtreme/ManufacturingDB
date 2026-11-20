"""models.py 의 컬럼 주석을 운영 DB 에 맞춘다 — 미리보기가 기본, --apply 로 반영.

DB 탐색기의 '설명(Comment)' 은 DB 에 저장된 컬럼 주석을 읽는다. 컬럼 주석은 테이블을 만들 때만 들어가므로
models.py 를 고쳐도 이미 있는 DB 에는 반영되지 않는다 (예: 가공 요약이 TDMS+NC 기준으로 바뀐 뒤에도
'XML: StartTime' 으로 보인다). 이 도구는 주석만 바꾼다.

    python backend/DB/sync_column_comments.py           # 바뀔 주석 목록만 본다 (DB 를 바꾸지 않음)
    python backend/DB/sync_column_comments.py --apply   # 반영

반영 방식
    ALTER TABLE t MODIFY COLUMN c <지금과 같은 정의> COMMENT '새 주석', ALGORITHM=INPLACE, LOCK=NONE
    정의는 information_schema 에 적힌 지금 값(형식·NULL 여부·문자셋)을 그대로 다시 적으므로 바뀌는 것은 주석
    뿐이다. 그래도 표 복사(ALGORITHM=COPY)가 필요한 변경이 되면 ALGORITHM=INPLACE 때문에 오류로 멈추고,
    LOCK=NONE 이라 그동안 읽기·쓰기를 막지 않는다. 기본값·자동 증가(EXTRA) 가 있는 칸은 정의를 다시 적다
    틀릴 수 있어 건너뛴다.
    다른 연결이 표를 붙잡고 있으면 ALTER 가 메타데이터 잠금을 기다리며 그 뒤의 조회까지 막을 수 있다 (MySQL
    기본 대기 한도는 1년). 그래서 대기를 LOCK_WAIT_SECONDS 로 줄여, 넘으면 그 표에서 멈춘다 — 다시 실행하면
    남은 것만 반영한다.
    반영 뒤에는 information_schema 를 다시 읽어 바꾼 칸의 정의가 그대로이고 주석이 새 값인지 확인한다.
    바꾸기 전 주석은 미리보기에 그대로 찍히므로 되돌릴 때 쓴다.
"""
import argparse
import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

_STRING_TYPES = ("char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum", "set")
LOCK_WAIT_SECONDS = 10
_COLUMNS_SQL = ("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, "
                "CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_COMMENT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION")
_TABLES_SQL = ("SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
               "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME")


def _metadata(metadata=None):
    if metadata is None:
        from DB.database import Base
        import DB.models  # noqa: F401  (모델을 등록한다)
        metadata = Base.metadata
    return metadata


def model_comments(metadata=None):
    """{(테이블, 컬럼): 주석} — models.py 기준. 주석이 없는 칸은 뺀다."""
    return {(table.name, column.name): column.comment
            for table in _metadata(metadata).sorted_tables for column in table.columns if column.comment}


def model_table_comments(metadata=None):
    """{테이블: 표 설명} — models.py 기준."""
    return {table.name: table.comment for table in _metadata(metadata).sorted_tables if table.comment}


def table_plan(wanted, live_rows):
    """표 설명이 다른 표. live_rows: information_schema.TABLES 의 행(dict)."""
    return [{"table": r["TABLE_NAME"], "old": r.get("TABLE_COMMENT") or "", "new": wanted[r["TABLE_NAME"]]}
            for r in live_rows
            if r["TABLE_NAME"] in wanted and wanted[r["TABLE_NAME"]] != (r.get("TABLE_COMMENT") or "")]


def column_definition(col):
    """information_schema.COLUMNS 한 행 -> MODIFY COLUMN 에 쓸 정의. 다시 적기 어려운 칸이면 None."""
    if (col.get("EXTRA") or "").strip():
        return None                       # auto_increment · DEFAULT_GENERATED · on update …
    if col.get("COLUMN_DEFAULT") is not None:
        return None                       # 기본값이 있는 칸
    parts = [col["COLUMN_TYPE"]]
    if (col.get("DATA_TYPE") or "").lower() in _STRING_TYPES and col.get("CHARACTER_SET_NAME"):
        parts.append(f"CHARACTER SET {col['CHARACTER_SET_NAME']} COLLATE {col['COLLATION_NAME']}")
    parts.append("NULL" if col.get("IS_NULLABLE") == "YES" else "NOT NULL")
    return " ".join(parts)


def plan(wanted, live_rows):
    """바꿀 칸 목록. wanted: model_comments(), live_rows: information_schema.COLUMNS 의 행(dict).

    각 항목: {table, column, old, new, definition(없으면 건너뜀), reason}
    """
    out = []
    for col in live_rows:
        key = (col["TABLE_NAME"], col["COLUMN_NAME"])
        new = wanted.get(key)
        old = col.get("COLUMN_COMMENT") or ""
        if new is None or new == old:
            continue
        definition = column_definition(col)
        out.append({"table": key[0], "column": key[1], "old": old, "new": new, "definition": definition,
                    "reason": None if definition else "기본값·EXTRA 가 있는 칸이라 건너뜀 (수동으로)"})
    return out


def verify(applied, live_columns, live_tables):
    """반영 뒤 확인. 바꾼 칸의 정의가 그대로이고 주석이 새 값인지 — 문제 목록 (없으면 빈 목록)."""
    columns = {(r["TABLE_NAME"], r["COLUMN_NAME"]): r for r in live_columns}
    tables = {r["TABLE_NAME"]: r for r in live_tables}
    problems = []
    for item in applied:
        if "column" not in item:
            row = tables.get(item["table"])
            if row is None or (row.get("TABLE_COMMENT") or "") != item["new"]:
                problems.append(f"{item['table']} (표 설명): 새 설명이 보이지 않습니다")
            continue
        where = f"{item['table']}.{item['column']}"
        row = columns.get((item["table"], item["column"]))
        if row is None:
            problems.append(f"{where}: 칸이 없습니다")
            continue
        if (row.get("COLUMN_COMMENT") or "") != item["new"]:
            problems.append(f"{where}: 새 주석이 보이지 않습니다")
        now = column_definition(row)
        if now != item["definition"]:
            problems.append(f"{where}: 정의가 바뀌었습니다 ({item['definition']} -> {now})")
    return problems


def _quote(text_value):
    return "'" + str(text_value).replace("\\", "\\\\").replace("'", "''") + "'"


def ddl(item):
    """주석만 바꾸는 ALTER 문. 컬럼이면 정의를 다시 적고, 표면 표 설명만."""
    if "column" in item:
        return (f"ALTER TABLE `{item['table']}` MODIFY COLUMN `{item['column']}` {item['definition']} "
                f"COMMENT {_quote(item['new'])}, ALGORITHM=INPLACE, LOCK=NONE")
    return f"ALTER TABLE `{item['table']}` COMMENT={_quote(item['new'])}, ALGORITHM=INPLACE, LOCK=NONE"


def _read_live(conn):
    from sqlalchemy import text
    return ([dict(r) for r in conn.execute(text(_COLUMNS_SQL)).mappings()],
            [dict(r) for r in conn.execute(text(_TABLES_SQL)).mappings()])


def describe(item):
    """'job.start_time' / 'job_file_archive (표 설명)'."""
    return f"{item['table']}.{item['column']}" if "column" in item else f"{item['table']} (표 설명)"


def sync(apply=False, engine=None, verbose=True):
    """models.py 주석과 DB 주석의 차이를 찾고, apply 면 반영한 뒤 확인한다. MySQL 이 아니면 None.

    반환 {"items": 다른 것 전부, "todo": 반영할 것(건너뛸 칸 제외), "applied": 반영한 것, "problems": 반영 뒤 확인 문제}.
    설치 프로그램(installer/setup_target.py 의 schema 단계)도 업그레이드할 때 이 함수로 맞춘다.
    verbose 면 바꿀 주석마다 지금 값과 새 값을 찍는다 — 되돌릴 때 쓴다.
    """
    from sqlalchemy import text
    if engine is None:
        from DB.database import engine

    with engine.connect() as conn:
        if conn.dialect.name != "mysql":
            return None
        rows, tables = _read_live(conn)
    items = table_plan(model_table_comments(), tables) + plan(model_comments(), rows)
    todo = [i for i in items if "column" not in i or i["definition"]]
    result = {"items": items, "todo": todo, "applied": [], "problems": []}
    if verbose:
        for item in items:
            print(f"[{describe(item)}]")
            print(f"  지금: {item['old'] or '(없음)'}")
            print(f"  바꿈: {item['new']}" + (f"   <- {item['reason']}" if item.get("reason") else ""))
    if not apply or not todo:
        return result
    try:
        with engine.begin() as conn:
            conn.execute(text(f"SET SESSION lock_wait_timeout = {int(LOCK_WAIT_SECONDS)}"))
            for item in todo:
                # 주석 안의 ':단어'(예: 'open-meteo:temperature')를 바인드 변수로 읽지 않게 콜론을 이스케이프한다
                conn.execute(text(ddl(item).replace(":", r"\:")))
                result["applied"].append(item)
    finally:        # ALTER 는 하나씩 확정된다 — 중간에 멈춰도 어디까지 됐는지 남긴다
        if verbose:
            print(f"\n반영했습니다: {len(result['applied'])}/{len(todo)}개")
    with engine.connect() as conn:
        result["problems"] = verify(result["applied"], *_read_live(conn))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="models.py 컬럼 주석을 운영 DB 에 맞춘다 (기본은 미리보기)")
    parser.add_argument("--apply", action="store_true", help="실제로 반영한다")
    args = parser.parse_args(argv)

    result = sync(apply=args.apply)
    if result is None:
        print("MySQL 에서만 쓴다.")
        return []
    items, todo = result["items"], result["todo"]
    if not items:
        print("DB 의 주석이 models.py 와 같습니다.")
    elif not args.apply:
        print(f"\n바꿀 주석 {len(todo)}개 (건너뜀 {len(items) - len(todo)}개). 반영하려면 --apply")
    elif result["problems"]:
        print("[확인 실패]\n  " + "\n  ".join(str(p) for p in result["problems"]))
    else:
        print(f"확인: {len(result['applied'])}개 모두 새 주석이 보이고, 바꾼 칸의 정의(형식·NULL 여부·문자셋)는 그대로입니다.")
    return items


if __name__ == "__main__":
    main()
