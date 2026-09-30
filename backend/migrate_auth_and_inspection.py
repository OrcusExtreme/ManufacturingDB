"""로그인 계정 테이블 추가 + inspection.shape_accuracy 제거.

무엇을 하나
-----------
1. `app_user` 테이블 생성 — 대시보드 로그인 계정 (아이디/PBKDF2 해시/작업자 이름)
2. `app_session` 테이블 생성 — 새로고침해도 로그인이 유지되게 하는 세션 토큰 해시
3. `app_pending_signup` 테이블 생성 — 관리자 승인을 기다리는 가입 신청
4. `app_user.email` 컬럼 삭제 — 가입 방식을 이메일 인증에서 관리자 승인으로 바꿨다
5. `inspection.shape_accuracy` 컬럼 삭제 — 쓰지 않기로 한 속성

이메일 인증 방식이던 DB 에 돌리면
---------------------------------
- `app_pending_signup` 은 이메일·인증 코드 컬럼을 가진 옛 형식이므로 **지우고 새로 만든다.**
  남아 있던 행은 10분짜리 인증 코드를 기다리던 미완료 신청이라 옮기지 않는다
  (이메일 확인도 안 된 신청을 승인 대기로 올릴 근거가 없다). 필요하면 다시 신청하면 된다.
- `app_user.email` 은 지운다. 계정·비밀번호·작업자 이름은 그대로라 기존 계정은 계속
  로그인된다.

왜 `user` 가 아니라 `app_user` 인가
----------------------------------
`user` 는 MySQL 의 시스템 테이블 이름과 겹치고 예약어처럼 다뤄지는 자리가 많아, 따옴표 없이
쓰면 질의가 깨지기 쉽다. 애플리케이션 계정임을 드러내는 이름을 따로 쓴다.

안전장치
--------
- 기본은 미리보기(dry-run). 실제 적용은 --apply.
- 이미 적용된 DB 에 다시 돌려도 안전하다(있으면 건너뛰고, 없으면 만든다).
- shape_accuracy · email 에 값이 남아 있으면 **몇 건인지 먼저 보고**한다. 지우면 되돌릴 수 없다.
- 적용 전에 대시보드를 멈춘다(컨트롤러 「전체 중지」). 옛 코드가 떠 있는 채로 email 을
  지우면, 옛 코드는 그 컬럼을 읽으려다 실패해 로그인이 막힌다.

사용법
------
    python backend/migrate_auth_and_inspection.py
    python backend/migrate_auth_and_inspection.py --apply
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import text

from DB.database import engine
from DB.models import AppSession, Base, PendingSignup, User


def _has_table(conn, table):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table}).scalar())


def _has_column(conn, table, column):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"),
        {"t": table, "c": column}).scalar())


def migrate(apply=False):
    print("=" * 70)
    print(" 로그인 계정 · 가입 승인 대기 테이블 정리 · shape_accuracy 제거")
    print("=" * 70)

    with engine.begin() as conn:
        print("\n[1/5] app_user 테이블")
        if _has_table(conn, "app_user"):
            n = conn.execute(text("SELECT COUNT(*) FROM app_user")).scalar()
            print(f"  이미 있습니다 (계정 {n}개)")
        else:
            print("  + app_user 생성")
            if apply:
                User.__table__.create(bind=conn)

        print("\n[2/5] app_session 테이블")
        if _has_table(conn, "app_session"):
            n = conn.execute(text("SELECT COUNT(*) FROM app_session")).scalar()
            print(f"  이미 있습니다 (세션 {n}개)")
        else:
            print("  + app_session 생성")
            if apply:
                AppSession.__table__.create(bind=conn)

        print("\n[3/5] app_pending_signup 테이블 (관리자 승인 대기)")
        if not _has_table(conn, "app_pending_signup"):
            print("  + app_pending_signup 생성")
            if apply:
                PendingSignup.__table__.create(bind=conn)
        elif _has_column(conn, "app_pending_signup", "code_hash"):
            # 이메일 인증 시절의 형식. 컬럼 구성이 달라 고쳐 쓰기보다 새로 만드는 편이 명확하다.
            n = conn.execute(text("SELECT COUNT(*) FROM app_pending_signup")).scalar()
            note = f" — 미완료 이메일 인증 신청 {n}건 폐기" if n else " (남은 신청 없음)"
            print(f"  ~ 이메일 인증 형식 -> 승인 대기 형식으로 다시 만듦{note}")
            if apply:
                conn.execute(text("DROP TABLE app_pending_signup"))
                PendingSignup.__table__.create(bind=conn)
        else:
            n = conn.execute(text("SELECT COUNT(*) FROM app_pending_signup")).scalar()
            print(f"  이미 승인 대기 형식입니다 (대기 중인 신청 {n}건)")

        print("\n[4/5] app_user.email 컬럼")
        if not _has_column(conn, "app_user", "email"):
            print("  이미 제거되어 있습니다")
        else:
            left = conn.execute(text(
                "SELECT COUNT(*) FROM app_user WHERE email IS NOT NULL AND email <> '' "
                "AND email NOT LIKE '%@unverified.local'")).scalar()
            if left:
                print(f"  [주의] 실제 이메일이 적힌 계정 {left}개 — 삭제하면 되돌릴 수 없습니다")
            else:
                print("  실제 이메일이 적힌 계정 없음 (자리표시자뿐)")
            # 컬럼을 지우면 그 컬럼만으로 된 UNIQUE 인덱스도 MySQL 이 함께 지운다.
            print("  - app_user.email 삭제 (계정·비밀번호·작업자 이름은 그대로)")
            if apply:
                conn.execute(text("ALTER TABLE app_user DROP COLUMN email"))

        print("\n[5/5] inspection.shape_accuracy")
        if not _has_column(conn, "inspection", "shape_accuracy"):
            print("  이미 제거되어 있습니다")
        else:
            left = conn.execute(text(
                "SELECT COUNT(*) FROM inspection WHERE shape_accuracy IS NOT NULL "
                "AND shape_accuracy <> ''")).scalar()
            if left:
                print(f"  [주의] 값이 남아 있는 행 {left}건 — 삭제하면 되돌릴 수 없습니다")
            else:
                print("  값이 들어 있는 행 없음")
            print("  - inspection.shape_accuracy 삭제")
            if apply:
                conn.execute(text("ALTER TABLE inspection DROP COLUMN shape_accuracy"))

    print()
    print("=" * 70)
    if apply:
        print(" 적용 완료.")
    else:
        print(" 미리보기입니다. 실제 적용: "
              "python backend/migrate_auth_and_inspection.py --apply")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(migrate(apply="--apply" in sys.argv))
