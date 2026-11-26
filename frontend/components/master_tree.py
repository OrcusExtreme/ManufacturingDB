import pandas as pd
import streamlit as st

from .common import load_data
from cad_viewer_component import render_cad_viewer
from parsers.nc_parser import step_lines_text, step_path_text  # noqa: E402  (common 이 backend 를 sys.path 에 올린다)

# 워킹스텝은 절삭 패스마다 한 행이라(같은 자리를 같은 조건으로 다시 깎은 층·다듬질 패스만 합침) 조건이 같은
# 행이 여럿일 수 있다 (O0911: T6 S3800 F710 네 줄). 패스 수 · NC 줄 · 시작 -> 끝 · 절삭 길이로 구분한다.
WORKINGSTEP_QUERY = """
    SELECT ws.step_order AS '순서', ws.operation_type AS '작업(Op)',
           ws.xml_tool_code AS '사용 공구',
           ws.spindle_speed AS '주축회전수 (RPM)',
           ws.feed_rate AS '이송속도 (mm/min)',
           ws.depth_of_cut AS '절입 깊이 (mm)',
           ws.pass_count AS '패스 수',
           ws.nc_line_start, ws.nc_line_end, ws.start_position, ws.end_position,
           ws.cutting_distance AS '절삭 길이 (mm)',
           t.manufacturer AS '제조사',
           t.iso14649_class AS 'ISO 14649 분류',
           t.cutter_diameter AS '직경',
           t.number_of_teeth AS '날수'
    FROM workingstep ws
    LEFT JOIN tool t ON ws.tool_id = t.tool_id
    WHERE ws.workplan_id = :wp
    ORDER BY ws.step_order
"""
WORKINGSTEP_COLUMNS = ['순서', '작업(Op)', '사용 공구', '주축회전수 (RPM)', '이송속도 (mm/min)', '절입 깊이 (mm)',
                       '패스 수', 'NC 줄', '경로 (시작 → 끝)', '절삭 길이 (mm)',
                       '제조사', 'ISO 14649 분류', '직경', '날수']


def workingstep_table(ws_df):
    """화면에 보일 워킹스텝 표. 단위는 열 이름에만 적고 칸에는 숫자만 남긴다 (결측값은 '-')."""
    disp = ws_df.copy()
    for column in ('주축회전수 (RPM)', '이송속도 (mm/min)'):
        disp[column] = disp[column].apply(lambda v: f"{int(v):,}" if pd.notnull(v) else "-")
    # 깊이는 0.5 처럼 소수가 본값이라 정수로 자르지 않는다 (2.0 -> '2').
    # 비어 있으면 Z0 위에서만 깎았거나 한 스텝에 깊이가 섞인 경우(층을 나눠 내려간 스텝)다.
    disp['절입 깊이 (mm)'] = disp['절입 깊이 (mm)'].apply(lambda v: f"{v:g}" if pd.notnull(v) else "-")
    # 아래 칸은 V3.3.1 부터 채운다. 예전 판에서 만든 행은 tools/recompute_jobs.bat 전까지 '-' 다.
    disp['패스 수'] = disp['패스 수'].apply(lambda v: f"{int(v)}" if pd.notnull(v) else "-")
    disp['NC 줄'] = [step_lines_text(a, b) or "-" for a, b in zip(disp['nc_line_start'], disp['nc_line_end'])]
    disp['경로 (시작 → 끝)'] = [step_path_text(a, b) or "-"
                              for a, b in zip(disp['start_position'], disp['end_position'])]
    disp['절삭 길이 (mm)'] = disp['절삭 길이 (mm)'].apply(lambda v: f"{v:,.1f}" if pd.notnull(v) else "-")
    return disp[WORKINGSTEP_COLUMNS]


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

                    ws_df = load_data(WORKINGSTEP_QUERY, params={"wp": wp_id})

                    if not ws_df.empty:
                        st.markdown("##### 하위 가공 스텝 (Workingsteps)")
                        st.caption("절삭 패스마다 한 스텝 (같은 자리를 같은 조건으로 다시 깎은 층·다듬질 패스는 합침). "
                                   "NC 줄은 위 NC 파일의 줄 번호입니다.")
                        st.dataframe(workingstep_table(ws_df), width="stretch", hide_index=True)
                    else:
                        st.info("등록된 Workingstep이 없습니다.")
