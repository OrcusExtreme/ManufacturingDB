"""홈 화면 및 Quick Access 컴포넌트."""
import os
import streamlit as st

# 브랜드 자산은 프로젝트 루트의 assets/ 한 곳에 모아 둔다.
# 컨트롤러(controller/system_controller.cpp)도 같은 파일을 읽으므로 frontend 안에 두지 않는다.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(_PROJECT_ROOT, "assets")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
LOGO_ICO_PATH = os.path.join(ASSETS_DIR, "logo.ico")
LOGO_JPG_PATH = os.path.join(ASSETS_DIR, "logo.jpg")
LOGO_CARD_PATH = os.path.join(ASSETS_DIR, "logo_card.png")


def render_home_view(on_navigate, show_erd_dialog_fn):
    """홈 화면 및 빠른 바로가기(Quick Access) 카드를 렌더링합니다."""
    # 1. 상단 브랜드 헤더 (DB 전용 앱 로고 + 대시보드 타이틀)
    col_logo, col_title = st.columns([1, 11], vertical_alignment="center")
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=76)
        else:
            st.markdown("## ⚙️")

    with col_title:
        st.markdown("<h1 style='margin:0; padding:0;'>공작기계지능화실험실 제조 DB 대시보드</h1>", unsafe_allow_html=True)
        st.caption("데이터베이스 플랫폼 v3.0.0")

    st.divider()

    # 2. Quick Access (빠른 바로가기) 섹션
    st.subheader("Quick Access (빠른 바로가기)")
    st.caption("핵심 데이터 분석, 마스터 관리 및 파이프라인 기능으로 빠르게 이동합니다.")

    # 3. 8개 기능 카드 그리드 (4열 x 2행)
    # Row 1
    r1_c1, r1_c2, r1_c3, r1_c4 = st.columns(4)
    with r1_c1:
        with st.container(border=True):
            st.markdown("##### :material/view_kanban: Job 워크스페이스")
            st.caption("가공 이력 검색, 센서 통계, Envelope & FFT 분석")
            if st.button("바로가기 →", key="qa_job_workspace", use_container_width=True):
                on_navigate("Job 워크스페이스")

    with r1_c2:
        with st.container(border=True):
            st.markdown("##### :material/account_tree: 계층형 마스터 데이터")
            st.caption("ISO 14649 공정 트리 및 3D CAD 모델 뷰어")
            if st.button("바로가기 →", key="qa_master_tree", use_container_width=True):
                on_navigate("계층형 마스터 데이터")

    with r1_c3:
        with st.container(border=True):
            st.markdown("##### :material/build: 공구 마스터")
            st.caption("공구 18종 카탈로그, 엑셀 일괄 등록 및 형상 관리")
            if st.button("바로가기 →", key="qa_tool_master", use_container_width=True):
                on_navigate("공구 마스터")

    with r1_c4:
        with st.container(border=True):
            st.markdown("##### :material/cloud_upload: 데이터 삽입")
            st.caption("XML, NC, TDMS, Log, 조도 CSV 수동 업로드")
            if st.button("바로가기 →", key="qa_data_upload", use_container_width=True):
                on_navigate("데이터 삽입")

    # Row 2
    r2_c1, r2_c2, r2_c3, r2_c4 = st.columns(4)
    with r2_c1:
        with st.container(border=True):
            st.markdown("##### :material/table_chart: DB 테이블")
            st.caption("14개 테이블 그리드 조회, 다중 필터링 및 직접 수정")
            if st.button("바로가기 →", key="qa_db_explorer", use_container_width=True):
                on_navigate("DB 테이블")

    with r2_c2:
        with st.container(border=True):
            st.markdown("##### :material/download: 데이터 다운로드")
            st.caption("프로젝트/Part/Job 3단계 드릴다운 ZIP 일괄 조달")
            if st.button("바로가기 →", key="qa_download_center", use_container_width=True):
                on_navigate("데이터 다운로드")

    with r2_c3:
        with st.container(border=True):
            st.markdown("##### :material/security: 시스템 백업/복구")
            st.caption("Vault 및 BLOB 무손실 재해 복구 아카이브 생성")
            if st.button("바로가기 →", key="qa_recovery", use_container_width=True):
                on_navigate("시스템 백업/복구")

    with r2_c4:
        with st.container(border=True):
            st.markdown("##### :material/schema: DB 관계도 (ERD)")
            st.caption("14개 테이블 인터랙티브 ERD 관계도 다이얼로그")
            if st.button("관계도 열기 →", key="qa_show_erd", use_container_width=True):
                show_erd_dialog_fn()
