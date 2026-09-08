import os
import tempfile

import streamlit as st

from .common import load_data


def _import_excel_section():
    st.markdown("#### 📥 엑셀로 공구 마스터 일괄 반영")
    st.caption(
        "실제 기계에 장착/보유 중인 공구 목록이 정리된 엑셀 파일을 올리면, tool_code 기준으로 upsert(있으면 갱신, "
        "없으면 신규 등록)됩니다. 필요한 열: 회사명 / 공구 종류 / 직경 / 규격 / 날 수 / 총 개수 / 공구 코드(또는 "
        "'5축 가공기', 'Tool Code') / 비고."
    )
    uploaded = st.file_uploader("공구 마스터 엑셀 업로드 (.xlsx)", type=["xlsx"], key="tm_excel_uploader")
    if uploaded and st.button("업로드한 엑셀로 공구 마스터 동기화 실행", type="primary", key="tm_btn_sync"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded.name)[1]) as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name
        try:
            from tool_inserter import parse_and_insert_tools
            with st.spinner("공구 마스터를 동기화하는 중입니다..."):
                parse_and_insert_tools(tmp_path)
            st.success("공구 마스터 동기화가 완료되었습니다.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"동기화 중 오류 발생: {e}")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def render_tool_master():
    st.subheader("공구 마스터 (Tool Master)")
    st.caption("XML 런타임 공구 호출과 매핑되는 공구 마스터 정보를 관리합니다.")

    _import_excel_section()
    st.divider()

    tools_df = load_data("SELECT tool_id, tool_code, company_name, tool_type, cutter_diameter, specification, tool_teeth, stock_count, memo FROM tool ORDER BY tool_code")

    if tools_df.empty:
        st.warning("등록된 공구가 없습니다. 위에서 엑셀을 업로드해 공구 마스터를 채워주세요.")
        return

    st.markdown("#### 📋 전체 공구 목록")
    st.dataframe(
        tools_df,
        use_container_width=True, hide_index=True,
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
