import os
import streamlit as st
from streamlit_option_menu import option_menu

from components.common import engine
from components.home_view import render_home_view, LOGO_PATH, LOGO_ICO_PATH, LOGO_CARD_PATH
from components.job_workspace import render_job_workspace
from components.master_tree import render_master_tree
from components.data_upload import render_data_upload
from components.db_explorer import render_db_explorer
from components.download_center import render_download_center
from components.recovery import render_recovery
from components.tool_master import render_tool_master
from components.native_picker import render_native_file_picker
from erd_component import render_interactive_erd

# 앱 아이콘 및 페이지 설정
page_icon = LOGO_ICO_PATH if os.path.exists(LOGO_ICO_PATH) else (LOGO_PATH if os.path.exists(LOGO_PATH) else "⚙️")
st.set_page_config(
    page_title="공작기계지능화실험실 제조 DB 대시보드",
    page_icon=page_icon,
    layout="wide",
    initial_sidebar_state="expanded"
)

# 파일 선택창 한글 필터 활성화
render_native_file_picker()

# 기본 내비게이션 상태: 첫 진입 시 '홈'으로 설정
if 'current_nav' not in st.session_state:
    st.session_state.current_nav = "홈"


@st.dialog("데이터베이스 관계도 (ERD)", width="large")
def show_erd_dialog():
    st.caption("테이블을 클릭하면 확대·이동할 수 있으며, 상세 데이터 조회는 'DB 테이블' 메뉴에서 이어집니다.")
    render_interactive_erd(selected_table="part", height=560, key="erd_viewer_sidebar")


def navigate_to(menu_name: str):
    """지정된 메뉴로 화면을 전환합니다."""
    st.session_state.current_nav = menu_name
    st.session_state['nav_menu'] = menu_name
    st.rerun()


# ================= [사이드바 구성] =================
with st.sidebar:
    sidebar_logo = LOGO_CARD_PATH if os.path.exists(LOGO_CARD_PATH) else LOGO_PATH
    if os.path.exists(sidebar_logo):
        st.image(sidebar_logo, width=72)
    st.markdown("**공작기계지능화실험실 제조 DB**<br><span style='font-size:12px; opacity:0.75;'>v3.0.5</span>", unsafe_allow_html=True)

    st.write("")

    if st.button("DB 관계도(ERD) 보기", width="stretch", key="btn_show_erd", icon=":material/schema:"):
        show_erd_dialog()

    st.divider()

    options_list = [
        "홈", "---",
        "Job 워크스페이스", "계층형 마스터 데이터", "공구 마스터", "---",
        "데이터 삽입", "데이터 다운로드", "---",
        "DB 테이블", "시스템 백업/복구"
    ]
    icons_list = [
        "house", None,
        "kanban", "diagram-3", "wrench", None,
        "cloud-upload", "download", None,
        "database", "shield-check"
    ]

    default_idx = options_list.index(st.session_state.current_nav) if st.session_state.current_nav in options_list else 0

    nav_menu = option_menu(
        menu_title=None,
        options=options_list,
        icons=icons_list,
        menu_icon="cast",
        default_index=default_idx,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "gray", "font-size": "17px"},
            "nav-link": {
                "font-size": "15px",
                "text-align": "left",
                "margin": "2px 0px",
                "--hover-color": "rgba(26, 115, 232, 0.1)",
                "color": "inherit",
            },
            "nav-link-selected": {
                "background-color": "transparent",
                "color": "#1a73e8",
                "font-weight": "bold",
            },
        },
    )

    if nav_menu and nav_menu != "---" and nav_menu != st.session_state.current_nav:
        st.session_state.current_nav = nav_menu
        st.session_state['nav_menu'] = nav_menu
        st.rerun()


# ================= [메인 화면 렌더링] =================
current_nav = st.session_state.current_nav

if current_nav == "홈":
    render_home_view(on_navigate=navigate_to, show_erd_dialog_fn=show_erd_dialog)
else:
    c_back, c_title = st.columns([1.2, 8.8], vertical_alignment="center")
    with c_back:
        if st.button("← 홈", key="btn_back_home"):
            navigate_to("홈")
    with c_title:
        st.title(current_nav)

    st.divider()

    if current_nav == "데이터 삽입":
        render_data_upload()
    elif current_nav == "Job 워크스페이스":
        render_job_workspace()
    elif current_nav == "계층형 마스터 데이터":
        render_master_tree()
    elif current_nav == "공구 마스터":
        render_tool_master()
    elif current_nav == "DB 테이블":
        render_db_explorer(engine)
    elif current_nav == "데이터 다운로드":
        render_download_center()
    elif current_nav == "시스템 백업/복구":
        render_recovery()
