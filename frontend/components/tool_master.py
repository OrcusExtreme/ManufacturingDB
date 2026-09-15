import os
import tempfile
from datetime import datetime

import streamlit as st

from .common import load_data


def _archived_excel_path():
    """보관된 공구 마스터 원본 엑셀의 절대 경로 (없으면 None)."""
    try:
        from tool_inserter import TOOL_MASTER_VAULT_DIR, TOOL_MASTER_VAULT_FILENAME
        from vault_manager import VAULT_ROOT
    except ImportError:
        return None
    path = os.path.join(VAULT_ROOT, TOOL_MASTER_VAULT_DIR, TOOL_MASTER_VAULT_FILENAME)
    return path if os.path.exists(path) else None


def _archived_excel_section():
    path = _archived_excel_path()
    if not path:
        st.caption(":material/folder_off: 보관된 원본 엑셀이 없습니다. 엑셀을 업로드하면 archive_vault/tool_master/tool_info.xlsx 로 보관됩니다.")
        return

    updated_at = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
    size_kb = os.path.getsize(path) / 1024
    col_info, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_info:
        st.caption(f":material/folder_zip: 보관된 원본: `archive_vault/tool_master/tool_info.xlsx` · {size_kb:,.1f} KB · 최종 갱신 {updated_at}")
    with col_btn:
        with open(path, "rb") as f:
            st.download_button(
                "원본 내려받기",
                data=f.read(),
                file_name="tool_info.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key="tm_btn_download_origin",
            )


def _import_excel_section():
    st.markdown("#### :material/upload_file: 엑셀로 공구 마스터 일괄 반영")
    
    uploaded = st.file_uploader("공구 마스터 엑셀 업로드 (.xlsx)", type=["xlsx"], key="tm_excel_uploader")
    if not uploaded:
        return

    st.warning(
        "기존 공구 마스터와 보관된 원본 엑셀을 모두 지우고 이 파일 내용으로 교체합니다. "
        "(T1~T99 형식 코드만 등록되며, 가공 이력의 공구 연결은 공구 코드 기준으로 다시 맺습니다)",
        icon=":material/warning:",
    )
    confirmed = st.checkbox("기존 공구 마스터를 지우고 교체하는 데 동의합니다.", key="tm_chk_replace")

    if st.button("업로드한 엑셀로 공구 마스터 교체 실행", type="primary", key="tm_btn_sync", disabled=not confirmed):
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded.name)[1]) as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name
        try:
            from tool_inserter import parse_and_insert_tools
            with st.spinner("공구 마스터를 교체하는 중입니다..."):
                parse_and_insert_tools(tmp_path)
            if _archived_excel_path():
                st.success("공구 마스터를 업로드한 엑셀로 교체했습니다. 원본은 archive_vault/tool_master/tool_info.xlsx 로 보관했습니다.")
            else:
                st.warning("교체는 진행됐지만 원본 엑셀 보관에 실패했습니다. 서버 로그를 확인해주세요.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"교체 중 오류 발생: {e}")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def render_tool_master():
    st.subheader("공구 마스터 (Tool Master)")
    st.caption("XML 런타임 공구 호출과 매핑되는 공구 마스터 정보를 관리합니다.")

    _import_excel_section()
    _archived_excel_section()
    st.divider()

    tools_df = load_data("SELECT tool_id, tool_code, company_name, tool_type, cutter_diameter, specification, tool_teeth, stock_count, memo FROM tool ORDER BY tool_code")

    if tools_df.empty:
        st.warning("등록된 공구가 없습니다. 위에서 엑셀을 업로드해 공구 마스터를 채워주세요.")
        return

    st.markdown("#### :material/build: 전체 공구 목록")
    st.dataframe(
        tools_df,
        width="stretch", hide_index=True,
        column_config={
            "tool_id": "ID",
            "tool_code": "공구 코드",
            "company_name": "제조사",
            "tool_type": "종류",
            "cutter_diameter": st.column_config.NumberColumn("직경", format="%.2f"),
            "specification": "규격",
            "tool_teeth": "날수",
            "stock_count": "재고",
            "memo": "비고",
        },
    )
