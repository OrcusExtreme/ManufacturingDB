import streamlit as st
from streamlit_option_menu import option_menu

from components.common import engine
from components.job_workspace import render_job_workspace
from components.master_tree import render_master_tree
from components.data_upload import render_data_upload
from components.db_explorer import render_db_explorer
from components.download_center import render_download_center
from components.recovery import render_recovery
from components.tool_master import render_tool_master
from erd_component import render_interactive_erd

st.set_page_config(page_title="공작기계지능화실험실 통합 제조 DB 대시보드", layout="wide")
st.title("공작기계지능화실험실 제조 DB 대시보드")

if 'nav_menu' not in st.session_state:
    st.session_state.nav_menu = "Job 워크스페이스"


@st.dialog("데이터베이스 관계도 (ERD)", width="large")
def show_erd_dialog():
    st.caption("테이블을 클릭하면 확대·이동할 수 있으며, 상세 데이터 조회는 'DB 테이블' 메뉴에서 이어집니다.")
    render_interactive_erd(selected_table="part", height=560, key="erd_viewer_sidebar")


with st.sidebar:
    st.header("DB 스키마 구조")

    if st.button("🗺️ DB 관계도(ERD) 보기", use_container_width=True, key="btn_show_erd"):
        show_erd_dialog()

    st.divider()

    # "---"는 option_menu가 구분선으로 렌더링하는 항목이라, 데이터 입출력 두 개를
    # 나머지 내비게이션 선택지 위쪽 그룹으로 분리하면서도 메뉴 상태는 하나로 유지된다.
    options_list = ["데이터 삽입", "데이터 다운로드", "---",
                    "Job 워크스페이스", "계층형 마스터 데이터", "공구 마스터", "DB 테이블", "시스템 백업/복구"]
    icons_list = ["cloud-upload", "download", None,
                  "kanban", "diagram-3", "wrench", "database", "shield-check"]

    if 'current_nav' not in st.session_state:
        st.session_state.current_nav = st.session_state.get('nav_menu', options_list[0])

    default_idx = options_list.index(st.session_state.current_nav) if st.session_state.current_nav in options_list else 0

    nav_menu = option_menu(
        menu_title=None,
        options=options_list,
        icons=icons_list,
        menu_icon="cast",
        default_index=default_idx,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "gray", "font-size": "18px"},
            "nav-link": {
                "font-size": "16px",
                "text-align": "left",
                "margin": "0px",
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

    if nav_menu != st.session_state.current_nav:
        st.session_state.current_nav = nav_menu
        st.session_state['nav_menu'] = nav_menu
        st.rerun()

if nav_menu == "데이터 삽입":
    render_data_upload()
elif nav_menu == "Job 워크스페이스":
    render_job_workspace()
elif nav_menu == "계층형 마스터 데이터":
    render_master_tree()
elif nav_menu == "공구 마스터":
    render_tool_master()
elif nav_menu == "DB 테이블":
    render_db_explorer(engine)
elif nav_menu == "데이터 다운로드":
    render_download_center()
elif nav_menu == "시스템 백업/복구":
    render_recovery()
