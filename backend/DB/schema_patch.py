"""실행 시점에 빠져 있는 컬럼만 채워 넣는 가벼운 스키마 보정.

migrate_keys_v3.py 처럼 키 구조를 바꾸는 큰 마이그레이션과 달리, 새 기능이 추가될 때
생기는 '컬럼 하나'를 서비스 기동 중에 안전하게 붙이기 위한 용도다.
이미 있으면 아무 것도 하지 않으므로 몇 번을 실행해도 된다.
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.append(_backend)

from sqlalchemy import text  # noqa: E402

# (테이블, 컬럼, ADD COLUMN 정의)
PENDING_COLUMNS = [
    ("job", "machining_window",
     "ADD COLUMN machining_window JSON NULL "
     "COMMENT 'TDMS 실가공 구간 판별 결과 (구간/근거/DAQ 시계 보정값)'"),
]


def _column_exists(conn, table, column):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar())


def ensure_schema(verbose=True):
    """빠진 컬럼을 추가한다. 반환값은 실제로 추가한 (테이블, 컬럼) 목록."""
    from DB.database import engine

    added = []
    try:
        with engine.begin() as conn:
            for table, column, ddl in PENDING_COLUMNS:
                if _column_exists(conn, table, column):
                    continue
                conn.execute(text(f"ALTER TABLE {table} {ddl}"))
                added.append((table, column))
                if verbose:
                    print(f"[스키마 보정] {table}.{column} 컬럼을 추가했습니다.")
    except Exception as e:
        # 권한이 없거나 DB가 아직 준비되지 않은 상황에서 서비스 기동을 막지 않는다
        print(f"[스키마 보정 경고] {e}")
    return added


if __name__ == "__main__":
    changed = ensure_schema()
    print("추가된 컬럼:", changed if changed else "없음 (이미 최신)")
