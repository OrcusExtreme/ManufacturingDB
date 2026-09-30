"""대시보드 로그인 계정 — 비밀번호 해싱과 인증.

비밀번호는 평문으로 저장하지 않는다. PBKDF2-HMAC-SHA256 에 계정마다 다른 소금(salt)을
붙여 해싱한 문자열만 DB 에 담는다. 저장 형식은 자체 구분자를 쓴다.

    pbkdf2_sha256$600000$<salt_hex>$<hash_hex>

반복 횟수를 문자열에 함께 적어 두는 이유는, 나중에 횟수를 올리더라도 **이미 가입한 계정이
그대로 로그인되게** 하기 위해서다. 검증은 저장된 문자열이 적어 둔 횟수로 하고, 로그인에
성공하면 현재 기준으로 다시 해싱해 갱신한다.

이 인증의 한계를 분명히 해 둔다
------------------------------
실험실 내부망의 localhost 대시보드를 전제로 한 **앱 수준 인증**이다.

  - Streamlit 은 HTTP 로 서비스되므로, 다른 PC 에서 접속하면 비밀번호가 평문으로 오간다.
    외부에 열어야 한다면 리버스 프록시로 HTTPS 를 씌워야 한다.
  - 로그인 상태는 URL 질의문자열의 세션 토큰으로 유지된다. 그 URL 을 복사해 넘기면
    만료(SESSION_DAYS) 전까지 남이 쓸 수 있다. 로그아웃하면 즉시 무효가 된다.
  - 대시보드 안에는 권한 구분(관리자/일반)이 없다. 로그인한 사람은 모두 같은 일을 할 수 있다.
  - 가입은 관리자가 승인해야 끝난다. 가입 승인과 멤버 조회·삭제는 대시보드가 아니라 서버 PC 의
    제조 DB Controller ('유저 관리' 창, backend/user_manager.py)에서만 할 수 있다. 대시보드에
    두면 로그인한 누구나 남을 들이거나 지울 수 있기 때문이다. 즉 관리 권한 = 서버 PC 접근 권한이다.

즉 '누가 넣었는지 기록하고 아무나 못 들어오게' 하는 용도이지, 적대적 침입을 막는 장치가
아니다.
"""
import base64
import datetime
import hashlib
import hmac
import os
import re
import secrets
import sys

from sqlalchemy.exc import IntegrityError

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from DB.database import SessionLocal
from DB.models import AppSession, PendingSignup, User

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000        # OWASP 2023 권고치 기준
SALT_BYTES = 16

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
MIN_PASSWORD_LEN = 8

# 로그인 세션 유지 기간.
SESSION_DAYS = 7
SESSION_TOKEN_BYTES = 32        # 256비트 난수


# ---------------------------------------------------------------------------
# 해싱
# ---------------------------------------------------------------------------
def hash_password(password, iterations=ITERATIONS, salt=None):
    """비밀번호를 저장 가능한 해시 문자열로."""
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """비밀번호가 맞는지. (맞음 여부, 해시를 갱신해야 하는지) 를 돌려준다."""
    if not stored:
        return False, False
    try:
        algorithm, iter_text, salt_hex, digest_hex = stored.split("$")
        iterations = int(iter_text)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False, False
    if algorithm != ALGORITHM:
        return False, False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    # 타이밍 공격을 피하려면 바이트를 하나씩 비교하면 안 된다.
    ok = hmac.compare_digest(candidate.hex(), digest_hex)
    return ok, (ok and iterations < ITERATIONS)


# ---------------------------------------------------------------------------
# 입력 검증
# ---------------------------------------------------------------------------
def validate_signup(username, password, password2, worker_name):
    """가입 입력을 검사해 사람이 읽을 오류 문구를 돌려준다. 문제 없으면 None."""
    username = (username or "").strip()
    worker_name = (worker_name or "").strip()

    if not username:
        return "아이디를 입력하세요."
    if not USERNAME_RE.match(username):
        return "아이디는 영문·숫자와 _ . - 만 쓸 수 있고 3~50자여야 합니다."
    if not worker_name:
        return "작업자 이름을 입력하세요. (가공 이력에 이 이름이 기록됩니다)"
    if len(worker_name) > 50:
        return "작업자 이름은 50자 이내로 입력하세요."
    if not password or len(password) < MIN_PASSWORD_LEN:
        return f"비밀번호는 {MIN_PASSWORD_LEN}자 이상이어야 합니다."
    if password != password2:
        return "비밀번호 확인이 일치하지 않습니다."
    return None


# ---------------------------------------------------------------------------
# 계정
# ---------------------------------------------------------------------------
def user_count():
    db = SessionLocal()
    try:
        return db.query(User).count()
    finally:
        db.close()


def authenticate(username, password):
    """로그인. 성공하면 계정 정보 dict, 실패하면 None."""
    if not username or not password:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username.strip()).first()
        if user is None:
            # 아이디가 없을 때도 해싱을 한 번 돌려 응답 시간을 맞춘다.
            # 그러지 않으면 응답이 빠른 것만으로 '없는 아이디'임이 드러난다.
            hash_password(password)
            return None

        ok, needs_rehash = verify_password(password, user.password_hash)
        if not ok:
            return None

        if needs_rehash:
            user.password_hash = hash_password(password)
        user.last_login_at = datetime.datetime.now()
        db.commit()
        return {
            "user_id": user.user_id,
            "username": user.username,
            "worker_name": user.worker_name,
        }
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()


def change_worker_name(user_id, worker_name):
    """작업자 이름을 바꾼다. (성공 여부, 메시지)"""
    worker_name = (worker_name or "").strip()
    if not worker_name:
        return False, "작업자 이름을 입력하세요."
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user is None:
            return False, "계정을 찾을 수 없습니다."
        user.worker_name = worker_name
        db.commit()
        return True, "작업자 이름을 변경했습니다."
    except Exception as e:
        db.rollback()
        return False, f"변경하지 못했습니다: {e}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 로그인 세션 (새로고침해도 풀리지 않게)
# ---------------------------------------------------------------------------
# Streamlit 의 session_state 는 웹소켓 세션에 묶여 있어 **새로고침하면 사라진다.**
# 그래서 로그인 사실을 서버에 남기고, 그것을 가리키는 난수 토큰만 URL 로 주고받는다.
#
# 토큰이 주소창에 보인다는 점은 분명히 해 둔다. 그 URL 을 그대로 복사해 넘기면 만료 전까지
# 남이 쓸 수 있다. 그래서 (1) 기간을 SESSION_DAYS 로 제한하고 (2) 로그아웃하면 서버에서
# 지워 즉시 무효화한다. 내부망 도구를 전제로 한 절충이다.
def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id, days=SESSION_DAYS):
    """로그인 세션을 만들고 토큰을 돌려준다. 토큰은 이때만 평문으로 존재한다."""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    now = datetime.datetime.now()
    db = SessionLocal()
    try:
        db.add(AppSession(
            token_hash=_token_hash(token),
            user_id=user_id,
            created_at=now,
            expires_at=now + datetime.timedelta(days=days),
        ))
        db.commit()
        return token
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()


def session_account(token):
    """토큰으로 계정을 찾는다. 세션이 없거나(로그아웃·계정 삭제) 만료됐으면 None.

    DB 오류는 삼키지 않고 올린다. 이미 로그인한 화면을 다시 확인할 때, '세션이 정말 사라졌다'와
    'DB 가 잠깐 안 된다'를 구분해야 하기 때문이다. 둘을 같이 다루면 MySQL 이 한 번 재시작될
    때마다 모든 사람이 로그아웃된다 (auth_view._session_still_valid 참고).
    """
    if not token:
        return None
    db = SessionLocal()
    try:
        row = db.query(AppSession).filter(
            AppSession.token_hash == _token_hash(token)).first()
        if row is None:
            return None
        if row.expires_at and row.expires_at < datetime.datetime.now():
            db.delete(row)      # 만료된 것은 그 자리에서 치운다
            db.commit()
            return None
        user = db.query(User).filter(User.user_id == row.user_id).first()
        if user is None:
            return None
        return {
            "user_id": user.user_id,
            "username": user.username,
            "worker_name": user.worker_name,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def resolve_session(token):
    """session_account 와 같되, DB 오류도 None (로그인 복원처럼 실패하면 그냥 로그인 화면으로 갈 때)."""
    try:
        return session_account(token)
    except Exception:
        return None


def revoke_session(token):
    """로그아웃. 서버에서 지워 그 토큰이 든 URL 을 즉시 무효로 만든다."""
    if not token:
        return
    db = SessionLocal()
    try:
        db.query(AppSession).filter(
            AppSession.token_hash == _token_hash(token)).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def purge_expired_sessions():
    """만료된 세션을 청소한다. 로그인할 때 겸사겸사 부른다."""
    db = SessionLocal()
    try:
        n = db.query(AppSession).filter(
            AppSession.expires_at < datetime.datetime.now()).delete()
        db.commit()
        return n
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 가입 신청과 관리자 승인
# ---------------------------------------------------------------------------
# 승인하기 전까지는 계정을 만들지 않는다. 미승인 계정을 먼저 만들어 두면 '계정은 있는데
# 로그인은 막힌' 상태가 생기고, 그 상태를 코드 곳곳에서 계속 구분해 다뤄야 한다.
# 대기 줄(app_pending_signup)을 따로 두면 승인을 통과한 것만 계정이 된다.
#
# 이 함수들은 두 곳에서 불린다. 신청은 대시보드(Streamlit)가, 목록·승인·거절은 제조 DB
# Controller 가 띄우는 '유저 관리' 창(user_manager.py)이 부른다. 신청·승인·거절 때 한 줄씩
# 찍는 로그는 두 프로세스 모두 컨트롤러의 실시간 로그 패널로 들어가므로, 관리자는 거기서
# 새 신청이 들어온 것을 본다. (Streamlit 의 표준 출력은 파이프라 flush 해야 바로 보인다.)
def request_signup(username, password, password2, worker_name):
    """가입 신청을 대기 줄에 넣는다. (성공 여부, 메시지)

    같은 아이디로 들어온 신청을 덮어쓰지 않는다. 덮어쓰게 두면 남의 신청을 가로채 자기
    비밀번호로 바꿔 놓고 승인을 기다릴 수 있다. 잘못 신청했다면 관리자가 거절한 뒤 다시 한다.
    """
    problem = validate_signup(username, password, password2, worker_name)
    if problem:
        return False, problem

    username = username.strip()
    worker_name = worker_name.strip()

    db = SessionLocal()
    try:
        # 아이디 비교는 대소문자를 구분하지 않는다 (DB 콜레이션이 ai_ci 라 실제로도 그렇다).
        if db.query(User).filter(User.username == username).first():
            return False, "이미 사용 중인 아이디입니다."
        if db.query(PendingSignup).filter(PendingSignup.username == username).first():
            return False, "이미 가입 신청된 아이디입니다. 관리자 승인을 기다려 주세요."
        db.add(PendingSignup(
            username=username,
            worker_name=worker_name,
            password_hash=hash_password(password),
            created_at=datetime.datetime.now(),
        ))
        db.commit()
    except IntegrityError:
        # 같은 아이디가 동시에 두 번 들어와 유일 제약에 걸린 경우
        db.rollback()
        return False, "이미 가입 신청된 아이디입니다. 관리자 승인을 기다려 주세요."
    except Exception as e:
        db.rollback()
        return False, f"가입 신청을 저장하지 못했습니다: {e}"
    finally:
        db.close()

    print(f"[가입 신청] {username} ({worker_name}) — 제조 DB Controller 의 "
          f"'유저 관리'에서 처리하세요.", flush=True)
    return True, ("가입 신청을 접수했습니다. 관리자가 승인하면 '로그인' 탭에서 "
                  "로그인할 수 있습니다.")


def is_signup_pending(username, password):
    """로그인에 실패한 사람이 승인을 기다리는 신청자 본인인지.

    비밀번호까지 맞아야 True 다. 아이디만 보고 알려 주면 누가 신청해 두었는지 캐낼 수 있다.
    """
    if not username or not password:
        return False
    db = SessionLocal()
    try:
        pending = db.query(PendingSignup).filter(
            PendingSignup.username == username.strip()).first()
        if pending is None:
            return False
        ok, _ = verify_password(password, pending.password_hash)
        return ok
    except Exception:
        return False
    finally:
        db.close()


def list_signup_requests():
    """승인을 기다리는 신청 목록. 먼저 들어온 것부터.

    DB 오류는 삼키지 않고 그대로 올린다. 승인 창이 '신청 없음'과 'DB 에 못 붙음'을
    구분해 보여줘야 하기 때문이다.
    """
    db = SessionLocal()
    try:
        rows = db.query(PendingSignup).order_by(
            PendingSignup.created_at, PendingSignup.pending_id).all()
        return [{
            "request_id": r.pending_id,
            "username": r.username,
            "worker_name": r.worker_name,
            "requested_at": r.created_at,
        } for r in rows]
    finally:
        db.close()


def approve_signup(request_id):
    """신청을 승인해 계정을 만든다. (성공 여부, 메시지)

    계정 생성과 신청 삭제를 한 트랜잭션에서 한다. 따로 하면 중간에 실패했을 때 계정만
    생기고 신청이 남거나, 신청만 지워지고 계정이 없는 상태가 된다.
    """
    db = SessionLocal()
    try:
        pending = db.query(PendingSignup).filter(
            PendingSignup.pending_id == request_id).first()
        if pending is None:
            return False, "이미 처리된 신청입니다."
        username, worker_name = pending.username, pending.worker_name
        if db.query(User).filter(User.username == username).first():
            return False, f"'{username}' 은(는) 이미 계정이 있습니다. 이 신청은 거절하세요."
        db.add(User(
            username=username,
            password_hash=pending.password_hash,     # 신청 때 해싱해 둔 값을 그대로 옮긴다
            worker_name=worker_name,
            created_at=datetime.datetime.now(),
        ))
        db.delete(pending)
        db.commit()
    except IntegrityError:
        db.rollback()
        return False, "이미 같은 아이디의 계정이 있습니다. 이 신청은 거절하세요."
    except Exception as e:
        db.rollback()
        return False, f"승인하지 못했습니다: {e}"
    finally:
        db.close()

    print(f"[가입 승인] {username} ({worker_name})", flush=True)
    return True, f"'{username}' ({worker_name}) 을(를) 승인했습니다."


def reject_signup(request_id):
    """신청을 거절한다(대기 줄에서 지운다). (성공 여부, 메시지)"""
    db = SessionLocal()
    try:
        pending = db.query(PendingSignup).filter(
            PendingSignup.pending_id == request_id).first()
        if pending is None:
            return False, "이미 처리된 신청입니다."
        username, worker_name = pending.username, pending.worker_name
        db.delete(pending)
        db.commit()
    except Exception as e:
        db.rollback()
        return False, f"거절하지 못했습니다: {e}"
    finally:
        db.close()

    print(f"[가입 거절] {username} ({worker_name})", flush=True)
    return True, f"'{username}' ({worker_name}) 의 신청을 거절했습니다."


# ---------------------------------------------------------------------------
# 가입 멤버 관리
# ---------------------------------------------------------------------------
# 제조 DB Controller 의 '유저 관리' 창(user_manager.py)에서만 쓴다. 대시보드에 두지 않는
# 이유는 가입 승인과 같다 (모듈 설명 참고).
def list_users():
    """가입한 멤버 목록. 먼저 가입한 사람부터.

    DB 오류는 올린다. 관리 창이 '멤버 없음'과 'DB 에 못 붙음'을 구분해 보여줘야 한다.
    """
    db = SessionLocal()
    try:
        rows = db.query(User).order_by(User.created_at, User.user_id).all()
        return [{
            "user_id": u.user_id,
            "username": u.username,
            "worker_name": u.worker_name,
            "created_at": u.created_at,
            "last_login_at": u.last_login_at,
        } for u in rows]
    finally:
        db.close()


def delete_user(user_id):
    """멤버를 지운다. (성공 여부, 메시지)

    로그인 세션도 같은 트랜잭션에서 지운다. MySQL 은 외래키 CASCADE 로 어차피 지우지만 그
    동작에 기대지 않고 여기서 분명히 한다. 세션이 사라지면 그 사람이 열어 둔 대시보드도 다음
    동작에서 로그인 화면으로 돌아간다 (대시보드는 그릴 때마다 세션을 다시 확인한다).

    가공 이력의 작업자 이름(env_memo.worker_name)은 계정을 가리키는 키가 아니라 그때 적어 둔
    이름이라, 멤버를 지워도 그대로 남는다. 지운 아이디로는 다시 가입 신청할 수 있다.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user is None:
            return False, "이미 삭제된 멤버입니다."
        username, worker_name = user.username, user.worker_name
        db.query(AppSession).filter(AppSession.user_id == user_id).delete(
            synchronize_session=False)
        db.delete(user)
        db.commit()
    except Exception as e:
        db.rollback()
        return False, f"삭제하지 못했습니다: {e}"
    finally:
        db.close()

    print(f"[멤버 삭제] {username} ({worker_name})", flush=True)
    return True, f"'{username}' ({worker_name}) 을(를) 삭제했습니다."
