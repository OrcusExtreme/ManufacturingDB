"""로그인 / 회원가입 신청 화면과 세션 관리.

로그인한 사람의 작업자 이름은 데이터 삽입 시 그 Job 의 `env_memo.worker_name` 으로
자동 기록된다. 그래서 가입할 때 작업자 이름을 필수로 받는다.

회원가입은 **신청까지만** 여기서 한다. 신청은 승인 대기 줄에 들어가고, 관리자가 제조 DB
Controller 의 '유저 관리' 창(backend/user_manager.py)에서 승인해야 계정이 생긴다.
승인 화면을 대시보드에 두지 않는 이유는 auth.py 설명 참고.

로그인 유지
-----------
Streamlit 의 `session_state` 는 웹소켓 세션에 묶여 있어 **새로고침하면 사라진다.**
그래서 로그인 사실을 서버(`app_session`)에 남기고, 그것을 가리키는 난수 토큰을
**브라우저 쿠키**에 둔다. 쿠키는 탭과 창을 가리지 않으므로 새 탭에서 주소만 쳐도 로그인이
유지된다.

쿠키를 굽는 일은 `streamlit-cookies-controller` 컴포넌트가 하고, 읽는 일은 Streamlit 이
기본으로 주는 `st.context.cookies` 가 한다. 읽기를 내장 기능으로 하는 이유는 컴포넌트가
첫 렌더링에서 값을 못 돌려주는 경우가 있어서다 — 그러면 로그인했는데 한 번 튕기는 것처럼
보인다.

쿠키를 못 쓰는 환경을 대비해 URL 질의문자열(`?sid=...`)도 함께 받는다.
"""
import os

import streamlit as st

import auth

from .common import PROJECT_ROOT

SESSION_KEY = "auth_user"
TOKEN_KEY = "auth_token"
# 쿠키 이름 · URL 질의문자열 이름
COOKIE_NAME = "orcus_sid"
QUERY_PARAM = "sid"


def _cookies():
    """쿠키 쓰기용 컨트롤러. 패키지가 없으면 None (URL 방식으로만 동작).

    객체를 session_state 에 캐시하지 않는다. CookieController 는 생성될 때 컴포넌트를
    렌더링하는데, 캐시해 두면 다음 실행에서 렌더링이 일어나지 않아 쿠키 쓰기가 전달되지
    않는다. 매 실행에서 새로 만든다.
    """
    try:
        from streamlit_cookies_controller import CookieController
    except ImportError:
        return None
    try:
        return CookieController(key="orcus_cookies")
    except Exception:
        return None


def _read_cookie_token():
    """브라우저가 보낸 쿠키에서 토큰을 읽는다 (Streamlit 내장, 첫 렌더링부터 보인다)."""
    try:
        return st.context.cookies.get(COOKIE_NAME)
    except Exception:
        return None


def current_user():
    """로그인한 계정 정보 dict. 로그인 전이면 None."""
    return st.session_state.get(SESSION_KEY)


def current_worker_name():
    """로그인한 사람의 작업자 이름. 로그인 전이면 None."""
    user = current_user()
    return user["worker_name"] if user else None


def _begin_session(user):
    """로그인 성공 처리: 서버 세션을 만들고 토큰을 브라우저에 남긴다.

    여기서 쿠키를 굽지 않는다. 쿠키 쓰기는 컴포넌트가 **실제로 렌더링돼야** 브라우저까지
    전달되는데, 로그인 직후에는 곧바로 st.rerun() 으로 화면을 바꿔야 해서 그 렌더링이
    일어나지 않는다. 예전 코드가 여기서 set() 을 부르고 바로 rerun 해서, 쿠키가 한 번도
    구워지지 않았고 새로고침하면 그대로 로그아웃됐다.

    그래서 두 단계로 나눈다.
      1) 지금: URL 질의문자열에 토큰을 넣는다 (동기적이라 rerun 을 견딘다)
      2) 다음 렌더링: sync_browser_token() 이 쿠키를 굽고, 확인되면 URL 을 청소한다
    """
    auth.purge_expired_sessions()
    token = auth.create_session(user["user_id"])
    st.session_state[SESSION_KEY] = user
    if token:
        st.session_state[TOKEN_KEY] = token
        st.query_params[QUERY_PARAM] = token
    st.cache_data.clear()
    st.rerun()


def sync_browser_token():
    """로그인 상태를 브라우저 쿠키에 남긴다. 로그인된 화면을 그릴 때마다 부른다.

    쿠키가 자리잡으면 탭·창을 가리지 않고 로그인이 유지된다. 확인된 뒤에는 주소창의
    토큰을 지워, 링크를 복사해도 세션이 딸려가지 않게 한다.
    """
    token = st.session_state.get(TOKEN_KEY)
    if not token:
        return

    ctrl = _cookies()
    if ctrl is None:
        return      # 패키지가 없으면 URL 토큰만으로 동작한다 (같은 탭 한정)

    if _read_cookie_token() == token:
        if st.query_params.get(QUERY_PARAM):
            st.query_params.pop(QUERY_PARAM, None)
        return

    try:
        ctrl.set(COOKIE_NAME, token,
                 max_age=auth.SESSION_DAYS * 24 * 3600, same_site="lax")
    except Exception:
        pass


def _restore_session():
    """쿠키 또는 URL 토큰으로 로그인을 되살린다.

    새로고침·새 탭은 모두 이 경로로 들어온다. 쿠키를 먼저 보는 이유는 그것이 탭을 가리지
    않기 때문이고, URL 을 뒤에 보는 이유는 쿠키를 못 쓰는 환경의 보루이기 때문이다.
    """
    for token in (_read_cookie_token(), st.query_params.get(QUERY_PARAM)):
        if not token:
            continue
        user = auth.resolve_session(token)
        if user is not None:
            st.session_state[SESSION_KEY] = user
            st.session_state[TOKEN_KEY] = token
            return True
    # 만료됐거나 폐기된 토큰이면 주소창에 남겨 둘 이유가 없다.
    if st.query_params.get(QUERY_PARAM):
        st.query_params.pop(QUERY_PARAM, None)
    return False


def logout():
    auth.revoke_session(st.session_state.get(TOKEN_KEY))
    st.session_state.pop(SESSION_KEY, None)
    st.session_state.pop(TOKEN_KEY, None)
    st.query_params.pop(QUERY_PARAM, None)
    ctrl = _cookies()
    if ctrl is not None:
        try:
            ctrl.remove(COOKIE_NAME)
        except Exception:
            pass
    st.cache_data.clear()


def _login_form():
    with st.form("auth_login_form"):
        username = st.text_input("아이디", key="auth_login_id")
        password = st.text_input("비밀번호", type="password", key="auth_login_pw")
        submitted = st.form_submit_button("로그인", type="primary", width="stretch")

    if submitted:
        user = auth.authenticate(username, password)
        if user is not None:
            _begin_session(user)
        elif auth.is_signup_pending(username, password):
            # 비밀번호까지 맞은 신청자 본인에게만 알린다 (auth.is_signup_pending 참고).
            st.info("가입 승인을 기다리고 있습니다. 관리자가 승인하면 로그인할 수 있습니다.",
                    icon=":material/hourglass_top:")
        else:
            # 아이디가 없는지 비밀번호가 틀린지 구분해 알려주지 않는다.
            # 구분해 주면 어떤 아이디가 존재하는지 확인하는 데 쓰인다.
            st.error("아이디 또는 비밀번호가 올바르지 않습니다.")


SIGNUP_NONCE_KEY = "signup_form_nonce"
SIGNUP_NOTICE_KEY = "signup_notice"


def _signup_form():
    """가입 신청. 계정은 관리자가 제조 DB Controller 에서 승인해야 생긴다."""
    notice = st.session_state.pop(SIGNUP_NOTICE_KEY, None)
    if notice:
        st.success(notice, icon=":material/how_to_reg:")

    # 신청이 접수되면 입력 칸을 비운다. 위젯 값은 그려진 뒤 바꿀 수 없으므로, 폼 key 를
    # 바꿔 새 위젯으로 다시 그린다. (clear_on_submit 은 오류가 나도 지워서 다시 쳐야 한다)
    nonce = st.session_state.get(SIGNUP_NONCE_KEY, 0)
    st.caption("이름은 가공 이력(env_memo)에 작업자로 기록됩니다. 실제 이름을 입력하세요.")
    with st.form(f"auth_signup_form_{nonce}"):
        username = st.text_input("아이디", help="영문·숫자와 _ . - 3~50자")
        worker_name = st.text_input("이름", placeholder="예: 홍길동")
        c1, c2 = st.columns(2)
        with c1:
            password = st.text_input("비밀번호", type="password",
                                     help=f"{auth.MIN_PASSWORD_LEN}자 이상")
        with c2:
            password2 = st.text_input("비밀번호 확인", type="password")
        submitted = st.form_submit_button("가입 신청", width="stretch")
    st.caption("신청 후 관리자가 제조 DB Controller 에서 승인하면 로그인할 수 있습니다.")

    if submitted:
        ok, message = auth.request_signup(username, password, password2, worker_name)
        if not ok:
            st.error(message)
            return
        st.session_state[SIGNUP_NOTICE_KEY] = message
        st.session_state[SIGNUP_NONCE_KEY] = nonce + 1
        st.rerun()


SIGNED_OUT_NOTICE_KEY = "auth_signed_out_notice"


def _session_still_valid():
    """이미 로그인한 화면이 계속 써도 되는지 서버 세션으로 다시 확인한다.

    session_state 의 계정 정보만 믿으면, 관리자가 Controller 의 '유저 관리'에서 멤버를 지워도
    그 사람이 열어 둔 탭은 새로고침 전까지 계속 로그인된 채로 남는다. 그래서 화면을 그릴
    때마다 세션을 조회한다 (색인된 조회 두 번이라 부담이 없다).

    DB 가 잠깐 안 될 때는 유효한 것으로 둔다. 그때마다 모두를 로그아웃시키면 MySQL 이 한 번
    재시작될 때 전원이 다시 로그인해야 한다.
    """
    token = st.session_state.get(TOKEN_KEY)
    if not token:
        return True     # 서버 세션을 못 만든 로그인(DB 오류 등)은 확인할 근거가 없다
    try:
        user = auth.session_account(token)
    except Exception:
        return True
    if user is None:
        return False
    st.session_state[SESSION_KEY] = user
    return True


def render_login_gate():
    """로그인 화면을 그린다. 이미 로그인했으면 True 를 돌려준다."""
    if current_user():
        if _session_still_valid():
            return True
        # 계정이 삭제됐거나 세션이 만료됐다. 화면의 로그인 상태만 비운다 — 서버 세션은 이미
        # 없고, 브라우저에 남은 토큰은 아래 복원 단계에서 무효로 판정돼 치워진다.
        st.session_state.pop(SESSION_KEY, None)
        st.session_state.pop(TOKEN_KEY, None)
        st.session_state[SIGNED_OUT_NOTICE_KEY] = True
        st.cache_data.clear()

    # 새로고침·새 탭은 session_state 가 비어 있다. 쿠키/URL 토큰으로 되살려 본다.
    if _restore_session():
        st.session_state.pop(SIGNED_OUT_NOTICE_KEY, None)
        return True

    logo_path = os.path.join(PROJECT_ROOT, "assets", "logo_card.png")
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        if os.path.exists(logo_path):
            st.markdown(_LOGIN_LOGO_CSS, unsafe_allow_html=True)
            with st.container(key="login_logo"):
                st.image(logo_path, width="stretch")
        st.markdown("### 공작기계지능화실험실 제조 DB")
        st.caption("계속하려면 로그인하세요.")
        if st.session_state.pop(SIGNED_OUT_NOTICE_KEY, False):
            st.warning("로그인이 만료되었거나 계정이 삭제되어 로그아웃되었습니다.",
                       icon=":material/logout:")

        tab_login, tab_signup = st.tabs(["로그인", "회원가입"])
        with tab_login:
            _login_form()
        with tab_signup:
            _signup_form()

        if auth.user_count() == 0:
            st.info("아직 계정이 없습니다. '회원가입' 탭에서 신청한 뒤, 제조 DB Controller 의 "
                    "'유저 관리'에서 승인하세요.", icon=":material/person_add:")

    return False


# 사이드바 계정 표시.
#
# 처음에는 상단 툴바(Deploy 옆)에 얹으려 했지만, Streamlit 에는 헤더에 위젯을 넣는 API 가
# 없어 CSS 로 위치를 고정해야 했다. 그러면 헤더 높이·Deploy 버튼 폭 같은 남의 치수에
# 기대게 되어, 화면 폭이나 Streamlit 버전이 바뀌면 겹치거나 글자가 잘렸다. 실제로 좁은
# 화면에서 Deploy 위로 올라타는 문제가 났다.
#
# 사이드바는 평범한 문서 흐름이라 그런 곡예가 필요 없다. 위치를 남의 치수에 맞추지 않아도
# 되는 자리에 두는 편이 낫다.
_SIDEBAR_ACCOUNT_CSS = """
<style>
/* 로그아웃: 사이드바의 다른 버튼보다 한 단계 조용하게 (주 동작이 아니다) */
.st-key-sb_account .stButton > button {
    font-weight: 400;
    color: inherit;
    opacity: 0.85;
}
.st-key-sb_account .stButton > button:hover {
    opacity: 1;
}
</style>
"""


# 로그인 화면 로고.
#
# st.image(width="stretch") 는 열 너비를 통째로 채워서, 1400px 화면에서 로고가 470px 까지
# 커지고 로그인 폼이 화면 밖으로 밀려났다. 고정 px 로 못 박으면 이번엔 좁은 화면에서
# 상대적으로 커진다. 그래서 창 폭을 따라가되 위아래로 한계를 두는 clamp 를 쓴다.
#
#   최소 88px  — 더 작아지면 로고 형태가 뭉개진다
#   기준 18vw  — 창 폭을 따라간다
#   최대 176px — 폼이 첫 화면 안에 들어오는 선
# !important 가 필요한 이유: st.image 는 img 에 인라인 style 로 폭을 박는다.
# 인라인 스타일은 일반 규칙보다 우선하므로 !important 없이는 이 값이 무시된다.
_LOGIN_LOGO_CSS = """
<style>
.st-key-login_logo [data-testid="stImage"],
.st-key-login_logo [data-testid="stImageContainer"] {
    width: auto !important;
    display: flex;
    justify-content: center;
}
.st-key-login_logo img {
    width: clamp(88px, 18vw, 176px) !important;
    max-width: 100% !important;
    height: auto !important;
    display: block;
    margin: 0 auto 0.25rem auto;
}
</style>
"""


def render_sidebar_account():
    """사이드바 아래쪽에 로그인한 사람과 로그아웃 버튼을 그린다."""
    user = current_user()
    if not user:
        return

    st.markdown(_SIDEBAR_ACCOUNT_CSS, unsafe_allow_html=True)
    # 테두리로 묶어 브랜딩(로고·제목)과 시각적으로 갈라 놓는다.
    with st.container(key="sb_account", border=True):
        st.markdown(
            f"<span style='font-size:12px; opacity:0.6;'>로그인 계정</span>"
            f"<br><b>{user['worker_name']}</b>"
            f"<span style='font-size:12px; opacity:0.6;'> · {user['username']}</span>",
            unsafe_allow_html=True,
        )
        if st.button("로그아웃", key="btn_logout", width="stretch",
                     icon=":material/logout:"):
            logout()
            st.rerun()
