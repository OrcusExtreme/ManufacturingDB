import streamlit as st


def render_recovery():
    st.subheader("시스템 데이터 복구 및 내보내기 (Disaster Recovery)")
    st.info("DB에 저장된 모든 원본 파일(XML, NC, CAD, Parquet, 로그, 조도)을 물리적 폴더 구조로 재구성하여 ZIP 파일로 다운로드합니다.")
    st.caption("원본이 저장 시점과 바이트 단위로 동일한지에 대한 SHA-256 복원 검증은 백엔드 파이프라인이 "
               "백그라운드에서 주기적으로 수행하며, 불일치가 감지되면 실행 로그에 경고가 출력됩니다.")

    if st.button("복구 파일(ZIP) 생성 시작", type="primary", key="recovery_btn_generate"):
        with st.spinner("원본 파일을 재구성 중입니다. 데이터 크기에 따라 시간이 걸릴 수 있습니다..."):
            try:
                from recovery_engine import create_recovery_zip
                zip_path = create_recovery_zip()
                st.session_state['recovery_zip_path'] = zip_path
            except Exception as e:
                st.error(f"복구 중 오류 발생: {e}")

    if 'recovery_zip_path' in st.session_state:
        with open(st.session_state['recovery_zip_path'], "rb") as f:
            st.download_button(
                label="복구 데이터 다운로드 (ZIP)",
                data=f,
                file_name="recovered_data.zip",
                mime="application/zip",
                key="recovery_btn_download",
            )
