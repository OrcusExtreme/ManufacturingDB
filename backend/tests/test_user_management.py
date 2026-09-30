"""유저 관리 (auth.py) — 가입 신청 승인·거절, 멤버 조회·삭제, 세션 확인.

실제 MySQL 은 건드리지 않는다. 인메모리 SQLite 에 계정 관련 테이블만 만들어 auth.SessionLocal 을
바꿔 끼운다. (SQLite 는 아이디 대소문자를 구분하므로 ai_ci 콜레이션 동작은 여기서 보지 않는다)
"""
import os
import sys

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
from DB.models import AppSession, PendingSignup, User

PW = "password1"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    for table in (User.__table__, PendingSignup.__table__, AppSession.__table__):
        table.create(eng)
    monkeypatch.setattr(auth, "SessionLocal", sessionmaker(bind=eng, autoflush=False))
    yield


def _request(username="kim", worker_name="김철수", password=PW):
    return auth.request_signup(username, password, password, worker_name)


def _only_request():
    reqs = auth.list_signup_requests()
    assert len(reqs) == 1
    return reqs[0]


@pytest.mark.parametrize("username, pw1, pw2, name", [
    ("kim", "short", "short", "김철수"),             # 비밀번호 길이
    ("kim", "password1", "password2", "김철수"),     # 확인 불일치
    ("김", "password1", "password1", "김철수"),      # 아이디 형식
    ("kim", "password1", "password1", "   "),        # 이름 누락
])
def test_invalid_input_is_not_queued(username, pw1, pw2, name):
    ok, _ = auth.request_signup(username, pw1, pw2, name)
    assert not ok
    assert auth.list_signup_requests() == []


def test_request_creates_no_account_until_approved():
    ok, _ = _request("  kim  ", " 김철수 ")
    assert ok
    req = _only_request()
    assert (req["username"], req["worker_name"]) == ("kim", "김철수")
    assert auth.user_count() == 0
    assert auth.authenticate("kim", PW) is None


def test_pending_notice_requires_correct_password():
    _request()
    assert auth.is_signup_pending("kim", PW)
    assert not auth.is_signup_pending("kim", "wrongpass")
    assert not auth.is_signup_pending("nobody", PW)
    assert not auth.is_signup_pending("", "")


def test_duplicate_request_does_not_overwrite_original():
    _request()
    ok, _ = _request(worker_name="가로채기", password="otherpass1")
    assert not ok
    assert auth.is_signup_pending("kim", PW)
    assert not auth.is_signup_pending("kim", "otherpass1")


def test_approve_moves_request_to_account():
    _request()
    ok, _ = auth.approve_signup(_only_request()["request_id"])
    assert ok
    assert auth.list_signup_requests() == []
    user = auth.authenticate("kim", PW)       # 신청 때 비밀번호 그대로
    assert user is not None and user["worker_name"] == "김철수"
    assert not auth.is_signup_pending("kim", PW)


def test_approve_twice_is_refused():
    _request()
    rid = _only_request()["request_id"]
    assert auth.approve_signup(rid)[0]
    assert auth.approve_signup(rid) == (False, "이미 처리된 신청입니다.")
    assert auth.user_count() == 1


def test_request_for_existing_account_is_refused():
    _request()
    auth.approve_signup(_only_request()["request_id"])
    ok, _ = _request(password="password9")
    assert not ok


def test_reject_removes_request_and_allows_reapply():
    _request()
    rid = _only_request()["request_id"]
    assert auth.reject_signup(rid)[0]
    assert auth.list_signup_requests() == []
    assert auth.authenticate("kim", PW) is None
    assert not auth.is_signup_pending("kim", PW)
    assert not auth.reject_signup(rid)[0]
    assert _request(password="password2")[0]


def test_approve_refused_when_username_taken_meanwhile():
    _request()
    db = auth.SessionLocal()
    db.add(User(username="kim", password_hash=auth.hash_password("zzzzzzzz"),
                worker_name="선점"))
    db.commit()
    db.close()
    rid = _only_request()["request_id"]
    ok, _ = auth.approve_signup(rid)
    assert not ok
    assert auth.reject_signup(rid)[0]           # 남아 있으니 거절로 정리할 수 있다


def test_requests_listed_oldest_first():
    for name in ("lee", "park", "choi"):
        _request(name)
    assert [r["username"] for r in auth.list_signup_requests()] == ["lee", "park", "choi"]


def test_list_raises_on_db_error(monkeypatch):
    # 승인 창이 '신청 없음'과 'DB 에 못 붙음'을 구분하려면 예외가 올라와야 한다.
    monkeypatch.setattr(auth, "SessionLocal",
                        sessionmaker(bind=create_engine("sqlite:///Z:/no/such/dir/x.db")))
    with pytest.raises(Exception):
        auth.list_signup_requests()
    ok, message = _request()
    assert not ok and "저장하지 못했습니다" in message


# ---------------------------------------------------------------------------
# 멤버 조회 · 삭제
# ---------------------------------------------------------------------------
def _approved(username, worker_name="멤버"):
    _request(username, worker_name)
    rid = [r for r in auth.list_signup_requests() if r["username"] == username][0]["request_id"]
    assert auth.approve_signup(rid)[0]
    return [u for u in auth.list_users() if u["username"] == username][0]


def test_list_users_oldest_first_with_login_time():
    _approved("lee", "이영희")
    _approved("park", "박민수")
    users = auth.list_users()
    assert [u["username"] for u in users] == ["lee", "park"]
    assert all(u["last_login_at"] is None for u in users)
    auth.authenticate("park", PW)
    assert [u for u in auth.list_users() if u["username"] == "park"][0]["last_login_at"]


def test_delete_user_blocks_login_and_kills_sessions():
    user = _approved("kim")
    token = auth.create_session(user["user_id"])
    assert auth.session_account(token)["username"] == "kim"

    ok, _ = auth.delete_user(user["user_id"])
    assert ok
    assert auth.list_users() == []
    assert auth.authenticate("kim", PW) is None
    assert auth.session_account(token) is None      # 열려 있던 대시보드도 다음 동작에서 튕긴다
    db = auth.SessionLocal()
    assert db.query(AppSession).count() == 0         # CASCADE 에 기대지 않고 직접 지웠는지
    db.close()


def test_delete_leaves_other_members_alone():
    kim, lee = _approved("kim"), _approved("lee")
    lee_token = auth.create_session(lee["user_id"])
    auth.delete_user(kim["user_id"])
    assert [u["username"] for u in auth.list_users()] == ["lee"]
    assert auth.session_account(lee_token)["username"] == "lee"


def test_delete_twice_is_refused():
    user = _approved("kim")
    assert auth.delete_user(user["user_id"])[0]
    assert auth.delete_user(user["user_id"]) == (False, "이미 삭제된 멤버입니다.")


def test_deleted_username_can_apply_again():
    user = _approved("kim")
    auth.delete_user(user["user_id"])
    assert _request(password="password2")[0]


def test_session_account_raises_on_db_error_but_resolve_does_not(monkeypatch):
    # 대시보드는 'DB 오류'면 로그인을 유지하고 '세션 없음'일 때만 로그아웃시킨다.
    monkeypatch.setattr(auth, "SessionLocal",
                        sessionmaker(bind=create_engine("sqlite:///Z:/no/such/dir/x.db")))
    with pytest.raises(Exception):
        auth.session_account("some-token")
    assert auth.resolve_session("some-token") is None
    with pytest.raises(Exception):
        auth.list_users()
