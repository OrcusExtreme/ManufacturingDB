"""공구 마스터 화면 — ISO 14649 형식 공구 마스터 조회 · 교체 · 내려받기.

공구 마스터는 파일 한 장으로 통째로 교체한다(backend/tool_inserter.py). 고치는 흐름은
'현재 공구 마스터 CSV 내려받기 → 엑셀 등에서 수정 → 업로드' 다. 형식은 docs/tool_iso14649.csv 와 같다.
"""
import os
import tempfile
from datetime import datetime

import streamlit as st

from .common import load_data

FLASH_KEY = "tm_flash"

# 화면에 보일 열 이름. 순서는 tool_inserter.TOOL_COLUMNS 와 같다.
COLUMN_LABELS = {
    "tool_id": "ID",
    "tool_code": "T번호",
    "manufacturer": "제조사",
    "model_code": "형번",
    "iso14649_class": "ISO 14649 분류",
    "hand_of_cut": "절삭 방향",
    "cutter_diameter": "직경 Dc",
    "shank_diameter": "생크 Ds",
    "cutting_edge_length": "날장 Lc",
    "overall_length": "전장 OAL",
    "usable_length": "유효장 LU",
    "corner_radius": "코너 R",
    "point_angle": "선단각°",
    "approach_angle": "진입각°",
    "helix_angle": "비틀림각°",
    "number_of_teeth": "날 수",
    "tool_material": "재질",
    "coating_type": "코팅",
    "coolant_through": "내부 급유",
    "stock_count": "재고",
    "gauge_length": "게이지 길이",
    "memo": "비고",
}


def _vault_file():
    """보관소의 공구 마스터 보관본 (절대 경로, 표시용 상대 경로). 없으면 (None, 상대 경로)."""
    try:
        from tool_inserter import TOOL_MASTER_VAULT_REL_PATH
        from vault_manager import get_abs_vault_path
    except ImportError:
        return None, "공구 마스터.csv"
    path = get_abs_vault_path(TOOL_MASTER_VAULT_REL_PATH)
    return (path if path and os.path.exists(path) else None), TOOL_MASTER_VAULT_REL_PATH


def _show_flash():
    flash = st.session_state.pop(FLASH_KEY, None)
    if flash:
        st.success(flash, icon=":material/check_circle:")


def _import_section():
    st.markdown("#### :material/upload_file: 파일로 공구 마스터 교체")
    st.caption("ISO 14649 형식(아래에서 내려받는 CSV 와 같은 열)의 CSV · XLSX 를 올리면 공구 마스터를 "
               "통째로 교체합니다. T번호가 있는 행은 매거진 장착 공구, 비어 있으면 미장착 재고입니다.")

    uploaded = st.file_uploader("공구 마스터 파일 (.csv, .xlsx)", type=["csv", "xlsx"],
                                key="tm_file_uploader")
    if not uploaded:
        return

    st.warning(
        "기존 공구 마스터를 모두 지우고 이 파일 내용으로 교체합니다. 가공 이력의 공구 연결은 T번호 "
        "기준으로 다시 맺습니다. 파일에 형식 오류가 한 곳이라도 있으면 아무것도 바꾸지 않습니다.",
        icon=":material/warning:",
    )
    confirmed = st.checkbox("기존 공구 마스터를 지우고 교체하는 데 동의합니다.", key="tm_chk_replace")
    if not st.button("업로드한 파일로 공구 마스터 교체", type="primary", key="tm_btn_sync",
                     disabled=not confirmed):
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded.name)[1]) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = tmp.name
    try:
        from tool_inserter import parse_and_insert_tools
        with st.spinner("공구 마스터를 교체하는 중입니다..."):
            result = parse_and_insert_tools(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not result.ok:
        st.error(f"반영하지 않았습니다 — {result.message} 기존 공구 마스터는 그대로입니다.",
                 icon=":material/error:")
        if result.problems:
            st.markdown("\n".join(f"- {p}" for p in result.problems[:50]))
            if len(result.problems) > 50:
                st.caption(f"… 외 {len(result.problems) - 50}곳")
        return

    archived = "" if result.archived else " (보관소 보관본 저장은 실패 — 서버 로그 확인)"
    st.session_state[FLASH_KEY] = (
        f"공구 마스터를 교체했습니다: {result.inserted}건 (장착 {result.mounted} · 미장착 "
        f"{result.unmounted}). 가공 이력 공구 연결 {result.linked}건 · 미연결 {result.unlinked}건."
        f"{archived}")
    st.cache_data.clear()
    st.rerun()


def _download_section():
    try:
        from tool_inserter import export_tool_master_csv
        data = export_tool_master_csv()
    except Exception as e:
        st.caption(f":material/error: 현재 공구 마스터를 내보내지 못했습니다: {e}")
        return

    path, rel = _vault_file()
    col_info, col_btn = st.columns([4, 1.4], vertical_alignment="center")
    with col_info:
        if path:
            updated_at = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
            st.caption(f":material/folder_zip: 보관소 보관본: `archive_vault/{rel}` · 최종 갱신 {updated_at}")
        else:
            st.caption(f":material/folder_off: 보관소 보관본(`archive_vault/{rel}`)이 아직 없습니다. "
                       "공구 마스터를 한 번 교체하면 만들어집니다.")
    with col_btn:
        st.download_button(
            "현재 공구 마스터 CSV",
            data=data,
            file_name=f"공구 마스터_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
            icon=":material/download:",
            key="tm_btn_download_master",
            width="stretch",
        )


def render_tool_master():
    st.subheader("공구 마스터 (Tool Master)")
    st.caption("ISO 14649 기반 공구 마스터입니다. 매거진에 장착된 공구는 T번호로 NC/XML 의 공구 호출과 "
               "연결되고, 미장착 재고는 T번호 없이 재고로만 관리합니다.")
    _show_flash()

    _import_section()
    _download_section()
    st.divider()

    try:
        from tool_inserter import TOOL_COLUMNS
    except ImportError:
        st.error("공구 마스터 모듈을 불러오지 못했습니다.")
        return
    tools_df = load_data(f"SELECT tool_id, {', '.join(TOOL_COLUMNS)} FROM tool ORDER BY tool_id")
    if tools_df.empty:
        st.warning("등록된 공구가 없습니다. 위에서 공구 마스터 파일을 올려 채워 주세요.")
        return

    mounted = tools_df["tool_code"].notna()
    views = {
        "전체": tools_df,
        "매거진 장착": tools_df[mounted],
        "미장착 재고": tools_df[~mounted],
    }
    st.markdown("#### :material/build: 공구 목록")
    view = st.radio("보기", list(views), horizontal=True, label_visibility="collapsed", key="tm_view",
                    format_func=lambda v: f"{v} {len(views[v])}")
    shown = views[view].copy()
    shown["coolant_through"] = shown["coolant_through"].map({1: "예", 0: "아니오", True: "예", False: "아니오"})

    st.dataframe(
        shown,
        width="stretch", hide_index=True,
        column_config={col: label for col, label in COLUMN_LABELS.items()},
    )
