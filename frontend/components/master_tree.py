import pandas as pd
import streamlit as st

from .common import load_data
from cad_viewer_component import render_cad_viewer


@st.dialog("3D CAD 모델 뷰어 (360° 회전 / 메시 모드)", width="large", icon=":material/view_in_ar:")
def open_cad_dialog(part_code, cad_file_name):
    st.markdown(f"**부품명(Part Code):** `{part_code}` &nbsp;|&nbsp; **CAD 파일명:** `{cad_file_name}`")
    render_cad_viewer(part_code, height=540)


def render_master_tree():
    st.subheader("계층형 구조 조회 (ISO 14649)")

    part_query = """
        SELECT DISTINCT p.part_name AS display_part
        FROM part p
        LEFT JOIN workplan w ON p.part_code = w.part_code
        LEFT JOIN job j ON w.workplan_id = j.workplan_id
        ORDER BY display_part
    """
    part_codes_df = load_data(part_query)
    part_codes = [p for p in part_codes_df['display_part'].tolist() if pd.notnull(p)] if not part_codes_df.empty else []

    if 'mt_selected_parts' not in st.session_state:
        st.session_state.mt_selected_parts = []

    selected_parts = st.multiselect(
        "조회할 Part 명 선택 (비워두면 기본 상위 5개 표시)", part_codes,
        default=[p for p in st.session_state.mt_selected_parts if p in part_codes],
        key="mt_widget_parts",
    )
    st.session_state.mt_selected_parts = selected_parts

    parts_to_show = selected_parts if selected_parts else part_codes[:5]

    for p_code in parts_to_show:
        with st.expander(f"Part: {p_code}", expanded=True):
            # part_code는 숫자 키가 되었으므로 이름으로 부품을 찾은 뒤 그 키로 CAD를 조회한다.
            cad_query = """
                SELECT c.file_name, c.file_type, LENGTH(c.file_content) as file_size
                FROM cad_file_archive c
                JOIN part p ON c.part_code = p.part_code
                WHERE p.part_name = :part
            """
            cad_df = load_data(cad_query, params={"part": p_code})
            if not cad_df.empty:
                c_fname = cad_df.iloc[0]['file_name']
                c_ftype = str(cad_df.iloc[0]['file_type']).upper()

                col_c1, col_c2 = st.columns([3, 1])
                with col_c1:
                    st.markdown(f":material/view_in_ar: **연관 CAD 모델:** `{c_fname}` ({c_ftype})")
                with col_c2:
                    if st.button("3D 뷰어 / 메시 보기", key=f"btn_cad_modal_{p_code}", type="primary", width="stretch", icon=":material/visibility:"):
                        open_cad_dialog(p_code, c_fname)

                with st.expander("CAD 파일 메타데이터 정보 보기", expanded=False, icon=":material/description:"):
                    st.dataframe(cad_df, width="stretch", hide_index=True)
                st.divider()

            wp_query = """
                SELECT DISTINCT w.workplan_id, w.program_code, wfa.nc_raw_path
                FROM workplan w
                JOIN job j ON w.workplan_id = j.workplan_id
                JOIN part p ON w.part_code = p.part_code
                LEFT JOIN workplan_file_archive wfa ON wfa.workplan_id = w.workplan_id
                WHERE p.part_name = :part
            """
            part_wps = load_data(wp_query, params={"part": p_code})

            if part_wps.empty:
                st.info("해당 조건에 맞는 Workplan이 없습니다.")
                continue

            for _, wp_row in part_wps.iterrows():
                wp_id = wp_row['workplan_id']
                with st.expander(f"Workplan: {p_code} - {wp_row['program_code']} (ID: {wp_id})", expanded=False):
                    st.write(f"**NC File Path:** `{wp_row['nc_raw_path']}`")

                    ws_query = """
                        SELECT ws.step_order AS '순서', ws.operation_type AS '작업(Op)',
                               ws.xml_tool_code AS '사용 공구',
                               ws.spindle_speed AS '주축회전수 (RPM)',
                               ws.feed_rate AS '이송속도 (mm/min)',
                               t.company_name AS '제조사',
                               t.tool_type AS '공구종류',
                               t.cutter_diameter AS '직경',
                               t.tool_teeth AS '날수'
                        FROM workingstep ws
                        LEFT JOIN tool t ON ws.tool_id = t.tool_id
                        WHERE ws.workplan_id = :wp
                        ORDER BY ws.step_order
                    """
                    ws_df = load_data(ws_query, params={"wp": wp_id})

                    if not ws_df.empty:
                        st.markdown("##### 하위 가공 스텝 (Workingsteps)")

                        # 단위는 열 이름에만 적고 칸에는 숫자만 남긴다 (결측값은 '-')
                        disp_df = ws_df.copy()
                        for column in ('주축회전수 (RPM)', '이송속도 (mm/min)'):
                            disp_df[column] = disp_df[column].apply(
                                lambda v: f"{int(v):,}" if pd.notnull(v) else "-"
                            )
                        st.dataframe(disp_df, width="stretch", hide_index=True)
                    else:
                        st.info("등록된 Workingstep이 없습니다.")
