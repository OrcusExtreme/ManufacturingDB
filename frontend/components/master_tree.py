import pandas as pd
import streamlit as st

from .common import load_data
from cad_viewer_component import render_cad_viewer
from parsers.nc_geometry import feature_size_text  # noqa: E402  (common 이 backend 를 sys.path 에 올린다)
from parsers.nc_parser import step_lines_text  # noqa: E402

# 워킹스텝 = ISO 14649 의 가공 형상 · 공정(황삭/정삭)과 전략 · 절삭조건 · 공구가 같은 이어진 패스 (nc_parser 의
# '워킹스텝 분리 규칙'). 같은 형상을 깎은 스텝은 형상 번호가 같다.
# 이 화면은 보이는 모양만 바꾼다 (DB 값은 그대로): 형상 · 전략은 DB 원문, 공정은 Roughing / Finishing 등 짧은 이름,
# 공구 정보는 한 칸. 형상 분류 · 크기 · 체적 · ae 는 부품의 소재 크기가 있을 때만 기록되므로 없으면 '-' 다.
WORKINGSTEP_QUERY = """
    SELECT ws.step_order AS '순서',
           ws.feature_no, ws.feature_type, ws.feature_geometry, ws.operation_type, ws.strategy, ws.toolpaths,
           ws.xml_tool_code, t.iso14649_class, t.cutter_diameter, t.number_of_teeth,
           ws.spindle_speed AS '주축회전수 (RPM)',
           ws.feed_rate AS '이송속도 (mm/min)',
           ws.depth_of_cut AS '축방향 절입 ap (mm)',
           ws.radial_cutting_depth AS '반경 절입 ae (mm)',
           ws.pass_count AS '패스 수',
           ws.nc_line_start, ws.nc_line_end,
           ws.cutting_distance AS '절삭 길이 (mm)',
           ws.removed_volume AS '제거 체적 (mm³)'
    FROM workingstep ws
    LEFT JOIN tool t ON ws.tool_id = t.tool_id
    WHERE ws.workplan_id = :wp
    ORDER BY ws.step_order
"""
WORKINGSTEP_COLUMNS = ['순서', '형상 번호', '가공 형상', '형상 크기 (mm)', '공정', '전략', '공구',
                       '주축회전수 (RPM)', '이송속도 (mm/min)', '축방향 절입 ap (mm)', '반경 절입 ae (mm)',
                       '패스 수', 'NC 줄', '절삭 길이 (mm)', '제거 체적 (mm³)']
# 구멍 가공 공정 (DB 의 ISO 14649-11 이름 -> 화면)
_HOLE_OPERATIONS = {'drilling': 'Drilling', 'tapping': 'Tapping', 'boring': 'Boring', 'back_boring': 'Back Boring'}


def _number(v, fmt):
    return format(v, fmt) if pd.notnull(v) else "-"


def _num(v):
    return f"{float(v):g}"


def _text(v):
    return v if isinstance(v, str) and v else None


def _stage(operation, toolpaths):
    """'rough' / 'finish' / 구멍 가공 이름 / None. 밀링 공정 이름이 비었으면(소재 크기를 몰라 기록하지 않음)
    패스별 경로(toolpaths)의 stage 로 본다."""
    import json

    op = _text(operation)
    if op and op.endswith('_finish_milling'):
        return 'finish'
    if op and op.endswith('_rough_milling'):
        return 'rough'
    if op:
        return op
    if isinstance(toolpaths, str):
        toolpaths = json.loads(toolpaths)
    stages = {tp.get('stage') for tp in toolpaths or [] if isinstance(tp, dict) and tp.get('stage')}
    return stages.pop() if len(stages) == 1 else None


def operation_labels(feature_nos, operations, toolpaths=None):
    """공정 칸: 'Roughing' / 'Finishing' (밀링), 'Drilling' 등 (구멍 가공). DB 의 operation_type 은 그대로 둔다.

    ISO 14649 에서는 형상을 마지막 치수로 만드는 공정이 정삭이다. 그래서 정삭 스텝이 따로 없는 형상은 그 형상의
    밀링 스텝을 'Finishing' 으로 보인다 (Job 1 의 슬롯처럼 한 번에 깎아 끝낸 형상). 정삭 스텝이 따로 있는
    형상만 앞의 스텝이 'Roughing' 이다. NC 가 정하지 않은 값(사람이 적은 값)은 그대로 보인다.
    """
    toolpaths = list(toolpaths) if toolpaths is not None else [None] * len(list(operations))
    stages = [_stage(op, tp) for op, tp in zip(operations, toolpaths)]
    finished = {no for no, stage in zip(feature_nos, stages) if pd.notnull(no) and stage == 'finish'}
    labels = []
    for no, op, stage in zip(feature_nos, operations, stages):
        if stage == 'finish':
            labels.append("Finishing")
        elif stage == 'rough':
            labels.append("Roughing" if pd.isnull(no) or no in finished else "Finishing")
        elif stage:
            labels.append(_HOLE_OPERATIONS.get(stage, stage))
        else:
            labels.append("-")
    return labels


def stock_text(x, y, z, origin):
    """부품 소재 한 줄: '121 × 101 × 28 mm (origin: corner)'. 모르면 None."""
    if any(pd.isnull(v) or float(v) <= 0 for v in (x, y, z)):
        return None
    return f"{_num(x)} × {_num(y)} × {_num(z)} mm (origin: {_text(origin) or 'corner'})"


def tool_text(code, iso_class, diameter, teeth):
    """공구 칸: 'T06 · ENDMILL · Ø12 · 3 flutes'. 공구 마스터와 연결되지 않은 T번호는 번호만."""
    parts = [_text(code), _text(iso_class)]
    if pd.notnull(diameter):
        parts.append(f"Ø{_num(diameter)}")
    if pd.notnull(teeth) and int(teeth) > 0:
        parts.append(f"{int(teeth)} flutes")
    return " · ".join(p for p in parts if p) or "-"


def workingstep_table(ws_df):
    """화면에 보일 워킹스텝 표. 단위는 열 이름에만 적고 칸에는 숫자만 남긴다 (결측값은 '-').

    형상 · 공정 · 전략 · 절입 · 패스 칸은 V3.3.1~V3.3.2 부터 NC 로 채운다. 예전 판에서 만든 행은 tools/recompute_jobs.bat
    전까지 '-' 이고, 공정 칸에는 사람이 적어 둔 값이 그대로 보인다.
    """
    disp = ws_df.copy()
    for column in ('주축회전수 (RPM)', '이송속도 (mm/min)'):
        disp[column] = disp[column].apply(lambda v: f"{int(v):,}" if pd.notnull(v) else "-")
    # 절입은 0.5 처럼 소수가 본값이라 정수로 자르지 않는다 (2.0 -> '2').
    for column in ('축방향 절입 ap (mm)', '반경 절입 ae (mm)'):
        disp[column] = disp[column].apply(lambda v: _number(v, "g"))
    disp['형상 번호'] = disp['feature_no'].apply(lambda v: f"{int(v)}" if pd.notnull(v) else "-")
    disp['가공 형상'] = [_text(v) or "-" for v in disp['feature_type']]
    disp['형상 크기 (mm)'] = disp['feature_geometry'].apply(feature_size_text)
    disp['공정'] = operation_labels(disp['feature_no'], disp['operation_type'], disp['toolpaths'])
    disp['전략'] = [_text(v) or "-" for v in disp['strategy']]
    disp['공구'] = [tool_text(*row) for row in zip(disp['xml_tool_code'], disp['iso14649_class'],
                                                  disp['cutter_diameter'], disp['number_of_teeth'])]
    disp['패스 수'] = disp['패스 수'].apply(lambda v: f"{int(v)}" if pd.notnull(v) else "-")
    disp['NC 줄'] = [step_lines_text(a, b) or "-" for a, b in zip(disp['nc_line_start'], disp['nc_line_end'])]
    disp['절삭 길이 (mm)'] = disp['절삭 길이 (mm)'].apply(lambda v: _number(v, ",.1f"))
    disp['제거 체적 (mm³)'] = disp['제거 체적 (mm³)'].apply(lambda v: _number(v, ",.0f"))
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
            stock_df = load_data("SELECT stock_x, stock_y, stock_z, stock_origin FROM part WHERE part_name = :part",
                                 params={"part": p_code})
            stock = stock_text(*stock_df.iloc[0]) if not stock_df.empty else None
            if stock:
                st.caption(f"소재 (stock): {stock}")
            else:
                st.caption("소재 크기 미입력 — 가공 형상 분류 · 크기 · 제거 체적 · 반경 절입은 기록하지 않습니다 "
                           "(데이터 삽입 화면에서 부품의 소재 크기를 넣으면 판단합니다).")
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
                        st.caption("ISO 14649 워킹스텝: 가공 형상 · 공정과 전략 · 절삭조건 · 공구가 같은 이어진 절삭 "
                                   "패스. 같은 형상은 형상 번호가 같고, 정삭 스텝이 따로 없는 형상은 그 공정이 "
                                   "Finishing 입니다. NC 줄은 위 NC 파일의 줄 번호입니다.")
                        st.dataframe(workingstep_table(ws_df), width="stretch", hide_index=True)
                    else:
                        st.info("등록된 Workingstep이 없습니다.")
