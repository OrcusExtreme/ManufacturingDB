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
    ("part", "part_name",
     "ADD COLUMN part_name VARCHAR(100) NULL "
     "COMMENT '부품 이름'"),
    ("workplan", "nc_hash",
     "ADD COLUMN nc_hash VARCHAR(32) NULL "
     "COMMENT 'NC 원본 내용 MD5 앞 8자리'"),
    ("job", "machining_window",
     "ADD COLUMN machining_window JSON NULL "
     "COMMENT 'TDMS 실가공 구간 판별 결과 (구간/근거/DAQ 시계 보정값)'"),
    ("workingstep", "feed_rate",
     "ADD COLUMN feed_rate FLOAT NULL "
     "COMMENT '가공 이송속도 (mm/min, NC코드 파싱)'"),
    ("workingstep", "spindle_speed",
     "ADD COLUMN spindle_speed FLOAT NULL "
     "COMMENT '스핀들 주축 회전수 (RPM, NC코드 파싱)'"),
    ("workplan_file_archive", "nc_file_sha256",
     "ADD COLUMN nc_file_sha256 VARCHAR(64) NULL "
     "COMMENT 'NC 파일 SHA-256 체크섬'"),
    ("job_file_archive", "xml_file_sha256",
     "ADD COLUMN xml_file_sha256 VARCHAR(64) NULL "
     "COMMENT 'XML 파일 SHA-256 체크섬'"),
    ("cad_file_archive", "file_sha256",
     "ADD COLUMN file_sha256 VARCHAR(64) NULL "
     "COMMENT 'CAD 파일 SHA-256 체크섬'"),
]


def _column_exists(conn, table, column):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar())


def _data_type(conn, table, column):
    """information_schema 기준 데이터 타입(예: 'int', 'varchar')을 소문자로 돌려준다."""
    value = conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()
    return (value or "").lower()


def _backfill_part_name(conn, verbose):
    """part_name을 갓 추가한 직후, 이름을 PK로 쓰던 구 스키마에서만 이름을 옮겨온다.

    V2.3.2 이전에는 part.part_code가 사람이 읽는 부품 이름을 담은 VARCHAR였다.
    그 시절 DB에 part_name을 붙였다면 part_code의 값이 곧 이름이므로 그대로 복사하면 된다.
    반면 마이그레이션이 끝난 DB에서 part_code는 숫자 대리키라, 같은 복사를 하면
    '3' 같은 일련번호가 부품 이름으로 박힌다. 그래서 part_code가 문자형일 때만 복사한다.
    """
    if _data_type(conn, "part", "part_code").startswith(("varchar", "char", "text")):
        conn.execute(text("UPDATE part SET part_name = part_code WHERE part_name IS NULL"))
        if verbose:
            print("[스키마 보정] 구 스키마의 part_code(이름)를 part_name으로 옮겼습니다.")
        return

    orphan = conn.execute(text(
        "SELECT COUNT(*) FROM part WHERE part_name IS NULL"
    )).scalar()
    if orphan:
        # 숫자 PK를 이름으로 쓰면 사람이 못 읽는 이름이 생기므로 채우지 않고 알리기만 한다.
        print(f"[스키마 보정 경고] 이름이 비어 있는 part 행이 {orphan}건 있습니다. "
              f"part_code는 숫자 키라 자동으로 채우지 않으니 직접 이름을 지정해 주세요.")


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

            # 이름 이관은 part_name을 '이번에 처음 붙였을 때'만 필요하다.
            # 기동할 때마다 돌리면 이후에 지운 이름이 매번 되살아난다.
            if ("part", "part_name") in added:
                _backfill_part_name(conn, verbose)
    except Exception as e:
        # 권한이 없거나 DB가 아직 준비되지 않은 상황에서 서비스 기동을 막지 않는다
        print(f"[스키마 보정 경고] {e}")
    return added


if __name__ == "__main__":
    changed = ensure_schema()
    print("추가된 컬럼:", changed if changed else "없음 (이미 최신)")
