"""T번호 -> 공구 마스터 공구 지정 화면 조각 ('데이터 삽입' · 'Job 워크스페이스' 가 함께 쓴다).

규칙(기본값, 스냅샷, 공구 마스터 교체 뒤 다시 잇기)은 backend/tool_assignment.py 에 있다.
여기서는 고르는 화면과 보여 주는 표만 그린다.
"""
import pandas as pd
import streamlit as st

from .common import engine

import tool_assignment as ta  # noqa: E402  (common 이 backend 를 sys.path 에 올린 뒤에 불러온다)
from tool_codes import format_tool_code  # noqa: E402


def _number_text(value):
    value = float(value)
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.1f}"


def _span_text(span):
    if not span:
        return None
    low, high = span
    return _number_text(low) if low == high else f"{_number_text(low)}~{_number_text(high)}"


def called_summary(info):
    """'스텝 10개 · S 8,000~9,500 · F 150~600'. 모르는 항목은 뺀다."""
    parts = []
    if info.get("steps"):
        parts.append(f"스텝 {info['steps']}개")
    spindle = _span_text(info.get("spindle"))
    if spindle:
        parts.append(f"S {spindle}")
    feed = _span_text(info.get("feed"))
    if feed:
        parts.append(f"F {feed}")
    return " · ".join(parts)


def render_tool_picker(called, existing, key_prefix, disabled=False, confirm_untouched=True):
    """T번호마다 공구를 고르는 상자를 그리고, 저장할 지정 {T번호: tool_id 또는 None} 을 돌려준다.

    called    {T번호: {"steps", "spindle", "feed"}} (가공 순서)
    existing  이미 저장된 지정 (tool_assignment.load_assignments)
    confirm_untouched
              True(업로드 화면): 아직 지정이 없는 번호는 손대지 않았어도 '보고 확인한 것' 으로 담는다.
              가공을 막 끝낸 업로드 때가 어느 공구를 꽂았는지 가장 확실히 아는 시점이기 때문이다.
              False(Job 화면): 나중에 고칠 때는 바꾼 번호만 담는다. 지금 T번호 자동 연결은 '지금' 매거진
              기준이라, 손대지 않은 번호까지 지정으로 굳히면 그때와 다른 공구가 기록될 수 있다.

    기본값은 이미 지정한 공구, 없으면 T번호 자동 연결이다. 손대지 않은 기존 지정은 다시 쓰지 않으므로
    지정자·시각이 그대로 남는다.
    """
    with engine.connect() as conn:
        tools = ta.load_tools(conn)
    auto = ta.auto_tool_ids(tools)
    options = [None, *tools]

    picks = {}
    columns = st.columns(2)
    for i, (number, info) in enumerate(called.items()):
        current = existing.get(number)
        default = current["tool_id"] if current else auto.get(number)
        index = options.index(default) if default in options else 0

        def _label(tool_id, number=number, current=current):
            if tool_id is None:
                if current and current.get("tool_snapshot"):
                    return f"(지정 공구가 공구 마스터에서 빠짐) {ta.tool_label(current['tool_snapshot'])}"
                return "공구 마스터에 없는 공구 / 모름"
            label = ta.tool_label(tools[tool_id])
            return f"{label}  ← T번호 자동" if auto.get(number) == tool_id else label

        title = format_tool_code(number) or str(number)
        summary = called_summary(info)
        if summary:
            title += f" · {summary}"
        if number == 0:
            title += " · 공구 호출 전 구간"
        with columns[i % 2]:
            choice = st.selectbox(title, options, index=index, format_func=_label,
                                  key=f"{key_prefix}_tool_{number}", disabled=disabled)
        if choice != options[index] or (current is None and confirm_untouched):
            picks[number] = choice
    return picks


_BASIS_TEXT = {"자동": "T번호 자동", "없음": "연결 없음"}


def _basis_text(row):
    if row["basis"] != "지정":
        return _BASIS_TEXT.get(row["basis"], row["basis"])
    who = f" ({row['assigned_by']})" if row.get("assigned_by") else ""
    if row["in_master"]:
        return f"지정{who}"
    if row["label"]:
        return f"지정{who} · 공구 마스터에서 빠짐 (지정 당시 제원)"
    return f"지정{who} · 공구 마스터에 없는 공구"


def render_job_tools(job_id, worker_name=None):
    """Job 의 T번호별 공구 표와 '공구 지정 수정' 을 그린다 (Job 워크스페이스)."""
    flash_key = f"ws_tool_flash_{job_id}"
    flash = st.session_state.pop(flash_key, None)
    if flash:
        st.success(flash, icon=":material/check_circle:")

    with engine.connect() as conn:
        rows = ta.job_tool_rows(conn, job_id)
        existing = ta.load_assignments(conn, job_id)
    if not rows:
        st.caption("— 이 Job 의 워킹스텝(공구 호출)이 아직 없습니다. NC 나 XML 이 수집되면 나타납니다.")
        return

    table = pd.DataFrame([{
        "T번호": r["tool_code"],
        "스텝": r["steps"],
        "공구": r["label"] or "-",
        "근거": _basis_text(r),
    } for r in rows])
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"스텝": st.column_config.NumberColumn("스텝", format="%d")})
    st.caption("지정한 공구가 있으면 그것을, 없으면 공구 마스터에서 같은 T번호로 장착된 공구를 보여 줍니다. "
               "지정한 공구는 공구 마스터를 교체해도 이 Job 의 기록으로 남습니다.")

    with st.expander("공구 지정 수정", icon=":material/edit:"):
        st.caption("바꾼 번호만 저장합니다. 지정하지 않은 번호는 계속 지금 공구 마스터의 T번호로 연결됩니다.")
        called = {r["tool_number"]: {"steps": r["steps"]} for r in rows}
        picks = render_tool_picker(called, existing, key_prefix=f"ws_{job_id}", confirm_untouched=False)
        if st.button("공구 지정 저장", type="primary", key=f"ws_tool_save_{job_id}", disabled=not picks):
            try:
                with engine.begin() as conn:
                    written = ta.save_assignments(conn, job_id, picks, assigned_by=worker_name)
            except Exception as e:
                st.error(f"공구 지정을 저장하지 못했습니다: {e}")
                return
            st.session_state[flash_key] = ("공구 지정을 저장했습니다: "
                                           + " · ".join(format_tool_code(n) or str(n) for n in written))
            st.cache_data.clear()
            st.rerun()
