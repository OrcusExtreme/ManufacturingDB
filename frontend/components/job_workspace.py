import html
import json
import os
import re

import pandas as pd
import streamlit as st
from sqlalchemy import text

from .common import engine, load_data
from vault_manager import PROCESSED_ROOT
import job_layout
import vault_layout
from .filters import render_search_filters, render_job_table
from .tool_picker import render_job_tools
from cad_viewer_component import find_cad_file_name, render_cad_viewer
from job_manager import material_is_known
from weather_service import describe as describe_weather


# 카드 안에 들어가는 그래프/표 높이를 한곳에서 관리해, 화면 전체가 같은 세로 리듬으로 정렬되게 한다.
CHART_HEIGHT = 320
CARD_TABLE_HEIGHT = 240


def _empty_note(message):
    """데이터가 없을 때의 표기. 큰 st.info 박스는 실제 내용과 같은 높이를 차지해
    '빈 카드'가 화면을 지배하므로, 한 줄 캡션으로 낮게 표시한다."""
    st.caption(f"— {message}")


def _card_header(icon, title):
    """카드 제목. 구글 클라우드 콘솔처럼 아이콘 + 제목 한 줄로 카드를 시작한다."""
    st.markdown(f"##### {icon} {title}")


def _kv_list(items):
    """'작은 라벨 → 그 아래 값'을 세로로 쌓아 보여준다.

    지표(st.metric)를 가로로 늘어놓으면 카드 폭이 좁아질수록 값이 잘리고 눈이 좌우로
    움직여야 해서, 콘솔 대시보드처럼 한 줄에 한 항목씩 세로로 쌓는 방식으로 표시한다.
    """
    rows = []
    for label, value in items:
        rows.append(
            "<div style='margin:0 0 0.7rem 0;'>"
            f"<div style='font-size:0.75rem;opacity:0.6;line-height:1.35;'>{html.escape(str(label))}</div>"
            f"<div style='font-size:0.95rem;line-height:1.5;word-break:break-word;'>{html.escape(str(value))}</div>"
            "</div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


def _load_machining_window(job_info, job_id, source_folder=None):
    """가공 구간 판별 결과를 읽는다. DB 값이 없으면 processed_data 사이드카를 본다."""
    raw = job_info.get('machining_window') if hasattr(job_info, 'get') else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if isinstance(raw, dict) and raw:
        return raw

    # 작업 폴더는 {Project}/{Part}/{Job} 계층이고 파일 이름에 job_id 를 쓰지 않는다.
    if not source_folder:
        return {}
    side = os.path.join(PROCESSED_ROOT, *str(source_folder).split('/'), "window.json")
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as f:
                data = json.load(f)
            win = data.get("window", {})
            if win:
                win.setdefault("daq_sync", data.get("daq_sync", {}))
                return win
        except Exception:
            pass
    return {}


_WINDOW_METHOD_LABEL = {
    "nc_block": "NC 블록 대조",
    "activity": "스핀들/이송 활동",
    "hint": "기록된 가공 시각",      # Job 에 이미 적힌 시작·종료 시각 (예전 Job 은 장비 XML 값)
    "full": "기록 전체",
}


def _program_mismatch_note(window):
    """NC 파일의 프로그램과 TDMS 에서 실제로 깎은 프로그램이 다르면 경고 문구. 같거나 모르면 None."""
    if not window or not window.get("program_mismatch"):
        return None
    basis = ("NC 블록이 맞아 절삭 이동거리는 NC 로 계산했습니다." if window.get("method") == "nc_block"
             else "NC 블록이 맞지 않아 절삭 이동거리는 TDMS 실측값으로 계산했습니다.")
    return (f"NC 파일의 프로그램(`{window.get('nc_program')}`)과 TDMS 에서 가공한 프로그램"
            f"(`{window.get('program_name')}`)이 다릅니다. {basis} 다른 NC 를 올렸다면 워킹스텝(공구·절삭조건)도 "
            "틀리므로 NC 파일을 확인하세요.")


def _machining_window_caption(window):
    """그래프 위에 한 줄로 붙일 구간 판별 요약."""
    if not window:
        return None
    parts = []
    dur = window.get("duration_seconds")
    if dur:
        parts.append(f"가공 구간 {dur:,.1f}초")
    method = _WINDOW_METHOD_LABEL.get(window.get("method"), window.get("method") or "-")
    parts.append(f"판별: {method}")
    if window.get("program_blocks"):
        if window.get("method") == "nc_block":
            parts.append(f"NC 블록 {window.get('matched_blocks', 0)}/{window['program_blocks']}종 실행"
                         f" ({window.get('nc_coverage', 0) * 100:.0f}%)")
        else:
            # NC 원본은 찾았지만 실행 블록과 맞지 않은 경우 (예: 프로브 매크로만 보관된 Job)
            parts.append(f"NC 대조 불일치 ({window.get('matched_blocks', 0)}/{window['program_blocks']}종)")
    if window.get("total_rows"):
        parts.append(f"CNC {window.get('rows', 0):,}/{window['total_rows']:,}행")
    if window.get("program_end_basis"):        # 완료 여부를 무엇으로 판단했는지
        parts.append(f"완료 근거: {window['program_end_basis']}")
    if window.get("position_jumps"):           # 총 이동거리에서 뺀 G43/G49 공구 길이 보정 등의 위치 표시 점프
        parts.append(f"이동거리에서 뺀 위치 점프 {window['position_jumps']}번 "
                     f"({window.get('position_jump_mm') or 0:,.1f}mm)")
    sync = window.get("daq_sync") or {}
    if sync.get("applied"):
        parts.append(f"DAQ 시계 보정 {sync.get('lag_seconds', 0):+.2f}초")
    return " · ".join(parts)


def _style_chart(fig, **layout):
    """카드가 반 폭이라 범례가 옆에 붙으면 파형이 눌린다. 범례를 그래프 위쪽 가로 방향으로 빼서
    가로 폭을 파형이 온전히 쓰게 하고, 카드마다 같은 높이·여백을 갖도록 통일한다."""
    fig.update_layout(
        margin=dict(l=0, r=0, t=30, b=0),
        height=CHART_HEIGHT,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
        **layout,
    )
    return fig


def _quality_badge(pass_fail):
    """합불 판정을 색 배지로 표시한다 (PASS 초록 / FAIL 빨강 / 그 외 회색)."""
    if pass_fail == "PASS":
        st.badge("품질 PASS", color="green", icon=":material/check_circle:")
    elif pass_fail == "FAIL":
        st.badge("품질 FAIL", color="red", icon=":material/cancel:")
    else:
        st.badge("품질 미판정", color="gray")


def _fmt(value, fallback="-"):
    """DB의 NULL/빈 문자열/'nan' 문자열을 하나의 표기('-')로 정규화한다."""
    if value is None:
        return fallback
    try:
        if pd.isnull(value):
            return fallback
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    return fallback if text_value in ("", "None", "nan") else text_value


_STATS_SOURCE_TEXT = {"TDMS": "TDMS CNC 채널 (실행 사이클, 약 27Hz)", "LOG": "장비 로그 (10Hz)"}


def _as_json(raw):
    """DB 의 JSON 칸 값. 판다스로 읽으면 글자로 온다. 못 읽으면 None."""
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, float) and pd.isnull(raw)):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _num_text(value, digits=0, suffix=""):
    """숫자 하나를 '1,234.5 단위' 로. 비어 있으면 '-'."""
    if value is None or pd.isnull(value):
        return "-"
    return f"{float(value):,.{digits}f}{suffix}"


def _render_machine_indicators(data):
    """장비 로그의 장비 계산값 요약(machine_log.machine_indicators)을 펼침 표로 보여 준다."""
    if not isinstance(data, dict):
        return
    values = data.get("values") or {}
    labels = data.get("labels") or {}
    zero = data.get("all_zero") or []
    if not values and not zero:
        return
    with st.expander(f"장비 계산값 · 장비 로그 ({len(values)}개)", icon=":material/analytics:"):
        if values:
            table = pd.DataFrame([{"항목": labels.get(code, code), "열": code, "평균": v.get("mean"),
                                   "최대": v.get("max"), "최소": v.get("min")} for code, v in values.items()])
            st.dataframe(table, hide_index=True, width="stretch")
        st.caption(f"{data.get('basis', '')} {data.get('cutting_rows', 0)}행 기준 (전체 {data.get('rows', 0)}행)")
        if zero:
            st.caption("전부 0으로 기록된 항목 (장비에서 해당 계산이 꺼져 있었을 수 있음): " + ", ".join(zero))


def _cnc_chart_options(job_info, log_data, indicators):
    """CNC 그래프에서 고를 항목. (항목 -> 값, x 축 값, x 축 제목, 출처).

    장비 로그가 있으면 로그의 회전수·이송·부하와 장비 계산값(채터 수준·소리 등)을,
    로그가 없으면 TDMS CNC 채널(가공 구간 Parquet)을 보여 준다.
    """
    if log_data is not None and not log_data.empty:
        x, x_title = None, "샘플 (장비 로그)"
        if 'time' in log_data.columns:
            t = pd.to_datetime(log_data['time'].astype(str).str.strip(), format="%y%m%d %H:%M:%S.%f",
                               errors="coerce")
            if t.notna().sum() > 1:
                x, x_title = (t - t.dropna().iloc[0]).dt.total_seconds(), "로그 경과시간 (초)"
        options = {}
        for label, column in (("주축 부하 (%)", "cls"), ("주축 회전수 (RPM)", "crpm"), ("이송속도 (mm/min)", "cfr")):
            if column in log_data.columns:
                options[label] = pd.to_numeric(log_data[column], errors="coerce")
        labels = (indicators or {}).get("labels") or {}
        for column in ((indicators or {}).get("values") or {}):
            if column in log_data.columns:
                options[f"{labels.get(column, column)} (장비 계산)"] = pd.to_numeric(log_data[column], errors="coerce")
        return options, x, x_title, "장비 로그"

    parquet_path = resolve_parquet_file(job_info['tdms_parquet_raw_path'], job_info.get('source_folder'),
                                        "viz.parquet")
    if parquet_path:
        try:
            frame = pd.read_parquet(parquet_path)
        except Exception:
            return {}, None, "", ""
        options = {}
        for label, column in (("주축 부하 (%)", "CNC-Z-SpindleLoad"), ("주축 회전수 (RPM)", "CNC-Z-SpindleSpeed"),
                              ("이송속도 (mm/min)", "CNC-ActualFeedRate")):
            if column in frame.columns:
                options[label] = pd.to_numeric(frame[column], errors="coerce")
        x = frame["time_s"] if "time_s" in frame.columns else None
        return options, x, ("가공 경과시간 (초)" if x is not None else "샘플"), "TDMS CNC 채널 (가공 구간)"
    return {}, None, "", ""


def resolve_parquet_file(file_path, source_folder=None, default_filename=None):
    """Parquet 파일의 실제 경로를 찾는다. DB 경로 -> processed_data -> archive_vault 순.

    보관소와 작업 폴더가 모두 {Project}/{Part}/{Job} 계층을 쓰고 파일 이름에는 job_id 가
    들어가지 않으므로, source_folder 만 있으면 세 자리를 모두 짚을 수 있다.
    """
    from backend.vault_manager import get_abs_raw_data_path
    if file_path:
        abs_p = get_abs_raw_data_path(file_path)
        if abs_p and os.path.exists(abs_p):
            return abs_p
    if source_folder and default_filename:
        seg = str(source_folder).split('/')
        p1 = os.path.join(PROCESSED_ROOT, *seg, default_filename)
        if os.path.exists(p1):
            return p1
        for kind in (job_layout.PARQUET_DIR, job_layout.ROUGHNESS_DIR):
            rel = vault_layout.kind_dir_rel(source_folder, kind)
            p2 = vault_layout.abs_if_exists(f"{rel}/{default_filename}") if rel else None
            if p2:
                return p2
    return None


@st.dialog("NC 데이터 원본 조회", width="large", icon=":material/terminal:")
def show_nc_dialog(wp_id):
    query = "SELECT nc_file_content FROM workplan_file_archive WHERE workplan_id = :wp"
    df = load_data(query, params={"wp": wp_id})
    if not df.empty and pd.notnull(df.iloc[0]['nc_file_content']):
        try:
            content = df.iloc[0]['nc_file_content'].decode('utf-8', errors='replace')
        except AttributeError:
            content = str(df.iloc[0]['nc_file_content'])
        st.code(content, language='text')
    else:
        st.info("해당 Workplan의 NC 원본 데이터가 아카이브에 없습니다.")


def _has_archived_xml(job_id):
    """이 Job 에 보관된 장비 XML 원본이 있는지."""
    try:
        df = load_data("SELECT 1 AS has_xml FROM job_file_archive "
                       "WHERE job_id = :jid AND xml_file_content IS NOT NULL", params={"jid": int(job_id)})
    except Exception:
        return False
    return not df.empty


@st.dialog("XML 메타데이터 조회", width="large", icon=":material/data_object:")
def show_xml_dialog(job_id):
    query = "SELECT xml_file_content FROM job_file_archive WHERE job_id = :jid"
    df = load_data(query, params={"jid": job_id})
    if not df.empty and pd.notnull(df.iloc[0]['xml_file_content']):
        try:
            content = df.iloc[0]['xml_file_content'].decode('utf-8', errors='replace')
        except AttributeError:
            content = str(df.iloc[0]['xml_file_content'])
        st.code(content, language='xml')
    else:
        st.info("해당 Job의 XML 원본 데이터가 아카이브에 없습니다.")


@st.dialog("CAD 형상 조회", width="large", icon=":material/view_in_ar:")
def show_cad_dialog(part_name, cad_file_name):
    st.markdown(f"**부품:** `{part_name}` &nbsp;|&nbsp; **CAD 파일:** `{cad_file_name}`")
    st.caption("마우스 드래그로 360° 회전, 휠로 확대·축소할 수 있습니다.")
    render_cad_viewer(part_name, height=540)


@st.dialog("데이터 영구 삭제 확인", icon=":material/delete_forever:")
def confirm_delete_dialog(job_id):
    st.write(f"정말로 **Job ID {job_id}** 데이터를 삭제하시겠습니까?")
    st.write("DB 연쇄 삭제(Cascade) 규칙에 따라 센서, 조도, 파일 아카이브 데이터도 함께 삭제되며, 되돌릴 수 없습니다.")
    if st.button("예, 삭제합니다.", type="primary"):
        with engine.connect() as conn:
            try:
                stmt = text("DELETE FROM job WHERE job_id = :jid")
                result = conn.execute(stmt, {"jid": job_id})
                conn.commit()
                if result.rowcount > 0:
                    st.success(f"Job ID {job_id} 데이터가 성공적으로 삭제되었습니다.")
                    st.cache_data.clear()
                    st.session_state.pop('ws_target_job_id', None)
                    st.rerun()
                else:
                    st.error(f"Job ID {job_id}를 찾을 수 없습니다.")
            except Exception as e:
                st.error(f"삭제 중 오류 발생: {e}")


def _render_analysis_tab(target_job_id, job_info, sr_df):
    import plotly.express as px
    from backend.vault_manager import get_abs_raw_data_path

    part_name = job_info['part_name']
    job_time = job_info['start_time']

    part_jobs_query = '''
        SELECT j.start_time
        FROM job j
        LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
        LEFT JOIN part p ON w.part_code = p.part_code
        WHERE p.part_name = :part_name
        ORDER BY j.start_time ASC
    '''
    part_jobs_df = load_data(part_jobs_query, params={"part_name": part_name})
    iteration = 1
    if not part_jobs_df.empty:
        for idx, (i, row) in enumerate(part_jobs_df.iterrows()):
            if pd.notnull(job_time) and pd.notnull(row['start_time']) and row['start_time'] == job_time:
                iteration = idx + 1
                break

    proj_name = str(job_info['project_code'])
    part_display = str(part_name)
    date_str = "알 수 없음"
    if pd.notnull(job_info['start_time']):
        date_str = str(job_info['start_time']).split('.')[0]
    else:
        for p_candidate in [job_info['log_raw_path'], job_info['tdms_raw_path']]:
            if p_candidate and pd.notnull(p_candidate):
                m = re.search(r'__(\d{12})(?:_ext)?\.(?:log|tdms)', str(p_candidate))
                if m:
                    ts = m.group(1)
                    date_str = f"20{ts[0:2]}-{ts[2:4]}-{ts[4:6]} {ts[6:8]}:{ts[8:10]}:{ts[10:12]}"
                    break
        if date_str == "알 수 없음" and pd.notnull(job_info['tdms_parquet_raw_path']):
            try:
                pq_p = resolve_parquet_file(job_info['tdms_parquet_raw_path'],
                                            job_info.get('source_folder'), "viz.parquet")
                if pq_p and os.path.exists(pq_p):
                    df_temp = pd.read_parquet(pq_p, columns=['Time Channel CNC'])
                    if not df_temp.empty and 'Time Channel CNC' in df_temp.columns:
                        # TDMS 시각은 UTC 다. 화면은 한국 시간으로 보여 준다.
                        from tdms_alignment import tdms_to_local
                        t_val = tdms_to_local(df_temp['Time Channel CNC'].dropna().iloc[0])
                        date_str = str(t_val).split('.')[0]
            except Exception:
                pass

    env_df = load_data("SELECT * FROM env_memo WHERE job_id = :jid", params={"jid": target_job_id})
    ins_df = load_data("SELECT * FROM inspection WHERE job_id = :jid", params={"jid": target_job_id})
    env_row = env_df.iloc[0] if not env_df.empty else None
    ins_row = ins_df.iloc[0] if not ins_df.empty else None

    # CNC 로그는 '요약' 카드와 '추이' 그래프 카드 두 곳에서 쓰이므로 한 번만 읽어 공유한다.
    log_path_raw = job_info['log_raw_path']
    log_path = get_abs_raw_data_path(log_path_raw) if pd.notnull(log_path_raw) else None
    log_data = None
    log_error = None
    if log_path and os.path.exists(log_path):
        try:
            try:
                log_data = pd.read_csv(log_path, sep='\t', encoding='utf-8')
            except Exception:
                log_data = pd.read_csv(log_path, sep='\t', encoding='cp949')
        except Exception as ex:
            log_error = str(ex)

    def _log_stat(column, digits=0, suffix=""):
        """최대/평균을 한 줄로 합쳐 세로 목록에 넣는다. 컬럼이 없으면 '-'."""
        if log_data is None or column not in log_data.columns:
            return "-"
        series = pd.to_numeric(log_data[column], errors='coerce').dropna()
        if series.empty:
            return "-"
        return f"최대 {series.max():.{digits}f}{suffix} / 평균 {series.mean():.{digits}f}{suffix}"

    # ── 정보 영역: 3개 '열'에 카드를 세로로 쌓는다(콘솔 대시보드 방식).
    #     같은 열의 카드는 위 카드가 짧으면 아래 카드가 그만큼 위로 올라붙고,
    #     각 열의 마지막 카드에만 height="stretch"를 주어 세 열의 아래 끝을 맞춘다.
    col_info, col_result, col_log = st.columns(3, gap="medium")

    with col_info:
        with st.container(border=True, height="stretch"):
            _card_header(":material/description:", "Job 정보")
            end_time_val = job_info['end_time']
            _kv_list([
                ("Job ID", target_job_id),
                ("Part 명", _fmt(part_display, fallback="미지정")),
                # 'Unknown' 은 부품을 만들 때 넣는 자리표시자다 — 업로드 화면에서 소재를 정하면 바뀐다.
                ("소재", job_info['material_code'] if material_is_known(job_info['material_code'])
                 else "미등록"),
                ("연구 프로젝트", _fmt(proj_name, fallback="미지정")),
                ("가공 종류", _fmt(job_info['machining_type'])),
                ("가공 순서", f"{iteration} 번째"),
                ("가공 시작", date_str),
                ("가공 종료", str(end_time_val).split('.')[0] if pd.notnull(end_time_val) else "-"),
                ("장비 코드", _fmt(job_info['machine_code'])),
                ("NC 프로그램", _fmt(job_info['program_code'])),
            ])
            st.divider()
            # 장비 XML 은 더 받지 않는다. 예전 Job 이나 장비가 수집 폴더에 직접 넣은 XML 이 보관돼 있을 때만 보여 준다.
            if _has_archived_xml(target_job_id):
                if st.button("XML 원본 보기", width="stretch", key="ws_btn_xml", icon=":material/data_object:"):
                    show_xml_dialog(target_job_id)
            if st.button("NC 코드 보기", width="stretch", key="ws_btn_nc", icon=":material/terminal:"):
                show_nc_dialog(job_info['workplan_id'])

            # CAD는 Job이 아니라 Part에 귀속되므로, 등록된 부품 이름으로 모델을 찾는다.
            registry_part_name = _fmt(job_info['registry_part_name'], fallback="")
            cad_file_name = find_cad_file_name(registry_part_name) if registry_part_name else None
            if cad_file_name:
                if st.button("CAD 형상 보기", width="stretch", key="ws_btn_cad", icon=":material/view_in_ar:"):
                    show_cad_dialog(registry_part_name, cad_file_name)
            else:
                _empty_note("등록된 CAD 모델 없음")

    with col_result:
        with st.container(border=True):
            _card_header(":material/verified:", "가공 결과 · 품질")
            _quality_badge(ins_row['pass_fail'] if ins_row is not None and pd.notnull(ins_row['pass_fail']) else None)

            cs = job_info['cutting_seconds']
            md = job_info['moving_distance']
            cmd_val = job_info['cutting_moving_distance']
            result_items = [
                ("가공(절삭) 시간", f"{cs:.1f} 초" if pd.notnull(cs) else "-"),
                ("총 이동 거리", f"{md:.1f} mm" if pd.notnull(md) else "-"),
                ("절삭 이동 거리", f"{cmd_val:.1f} mm" if pd.notnull(cmd_val) else "-"),
            ]
            if ins_row is not None:
                ra_val = ins_row['surface_roughness_ra']
                rz_val = ins_row['surface_roughness_rz']
                result_items += [
                    ("전체 표면조도 Ra", f"{ra_val:.3f} μm" if pd.notnull(ra_val) else "-"),
                    ("전체 표면조도 Rz", f"{rz_val:.3f} μm" if pd.notnull(rz_val) else "-"),
                    ("치수 및 공차", _fmt(ins_row['dimension_tolerance'])),
                ]
            st.write("")
            _kv_list(result_items)
            if ins_row is None:
                _empty_note("품질 점검 데이터가 아직 입력되지 않았습니다")

        with st.container(border=True, height="stretch"):
            _card_header(":material/straighten:", "표면조도 측정 통계")
            stats_df = sr_df[sr_df['ra'].notnull()][['measure_name', 'ra', 'rq', 'rz']] if not sr_df.empty else pd.DataFrame()
            if not stats_df.empty:
                # 측정 지점 수만큼 표가 자라되(내용에 맞는 높이), 지점이 아주 많은 Job에서
                # 카드가 끝없이 길어지지 않도록 상한선만 둔다.
                table_height = min(len(stats_df) * 35 + 38, CARD_TABLE_HEIGHT)
                st.dataframe(
                    stats_df, hide_index=True, width="stretch", height=table_height,
                    column_config={
                        "measure_name": "측정 위치",
                        "ra": st.column_config.NumberColumn("Ra (μm)", format="%.3f"),
                        "rq": st.column_config.NumberColumn("Rq (μm)", format="%.3f"),
                        "rz": st.column_config.NumberColumn("Rz (μm)", format="%.3f"),
                    },
                )
                st.caption(f"측정 지점 {len(stats_df)}곳 · Ra 평균 {stats_df['ra'].mean():.3f} μm")
            else:
                _empty_note("조도 측정 데이터 없음")

    indicators = _as_json(job_info.get('machine_indicators'))

    with col_log:
        with st.container(border=True):
            _card_header(":material/summarize:", "CNC 요약")
            alarm_count = job_info['alarm_count']
            if pd.notnull(job_info.get('max_spindle_load')):
                # DB 의 요약 (TDMS 실행 사이클 기준, TDMS 가 없으면 장비 로그 기준)
                rpm_max, rpm_avg = _num_text(job_info.get('max_spindle_rpm')), _num_text(job_info.get('avg_spindle_rpm'))
                items = [
                    ("주축 부하 최대", _num_text(job_info['max_spindle_load'], 1, " %")),
                    ("주축 회전수 최대 / 평균", f"{rpm_max} / {rpm_avg} RPM"),
                    ("이송속도 최대", _num_text(job_info.get('max_feed_rate'), 0, " mm/min")),
                ]
            else:
                # DB 요약이 없는 예전 Job: 로그 파일에서 바로 계산한다
                items = [
                    ("Spindle Load", _log_stat('cls', digits=2, suffix=" %")),
                    ("Spindle RPM", _log_stat('crpm')),
                    ("Feed Rate", _log_stat('cfr')),
                ]
            # 알람은 TDMS 에도, 지금 장비 로그에도 열이 없다. 모르면 '0건' 이 아니라 모른다고 적는다.
            items.append(("알람 발생", f"{int(alarm_count)} 건" if pd.notnull(alarm_count) else "모름 (알람 기록 없음)"))
            _kv_list(items)
            source = job_info.get('stats_source')
            if pd.notnull(source):
                st.caption(f"출처: {_STATS_SOURCE_TEXT.get(source, source)}")
            _render_machine_indicators(indicators)

            if log_error:
                st.error(f"로그 분석 오류: {log_error}")
            elif log_data is None and pd.isnull(job_info.get('max_spindle_load')):
                _empty_note("연결된 로그·TDMS 요약 없음")

        with st.container(border=True, height="stretch"):
            _card_header(":material/thermostat:", "작업 환경 및 메모")
            if env_row is not None:
                temp_val = env_row['temperature']
                hum_val = env_row['humidity']
                _kv_list([
                    ("온도 / 습도",
                     f"{temp_val:.1f} °C / {hum_val:.1f} %" if pd.notnull(temp_val) and pd.notnull(hum_val)
                     else f"{_fmt(temp_val)} / {_fmt(hum_val)}"),
                    # 날씨 칸이 생기기 전(스키마 보정 전)의 DB 에서도 카드가 깨지지 않게 .get 으로 읽는다.
                    ("날씨", _fmt(env_row.get('weather'))),
                    ("작업자", _fmt(env_row['worker_name'])),
                    ("요일", _fmt(env_row['day_of_week'])),
                    ("칩 형태", _fmt(env_row['chip_shape'])),
                    ("이상 소음", _fmt(env_row['abnormal_noise'])),
                    # 메모 값 자체가 '없음'일 수 있어, 미입력 표기는 다른 항목과 같은 '-'로 통일한다.
                    ("수기 메모", _fmt(env_row['free_memo'])),
                ])
                # 온도·습도 칸에는 사람이 적은 값과 가공 시각 외기 값(자동)이 섞일 수 있어 어느 쪽인지 밝힌다.
                weather_time = env_row.get('weather_time')
                auto_note = describe_weather(env_row.get('weather_source'),
                                             weather_time if pd.notnull(weather_time) else None)
                if auto_note:
                    st.caption(auto_note)
            else:
                _empty_note("환경 및 메모 데이터 없음")

    # ── 사용 공구: 이 Job 에서 쓴 공구의 제원 (조회만. 공구 지정은 업로드 화면에서 한다)
    with st.container(border=True):
        _card_header(":material/build:", "사용 공구 제원")
        render_job_tools(target_job_id)

    # ── 그래프 패널: 파형 3종을 하나의 패널로 묶고, 세 카드는 서로 높이를 맞춘다.
    with st.container(border=True):
        _card_header(":material/bar_chart:", "그래프")
        chart_col, log_chart_col, curve_col = st.columns(3, gap="medium")

    with chart_col.container(border=True, height="stretch"):
        _card_header(":material/timeline:", "TDMS 그래프")
        parquet_path = resolve_parquet_file(job_info['tdms_parquet_raw_path'],
                                            job_info.get('source_folder'), "viz.parquet")
        if parquet_path:
            try:
                tdms_data = pd.read_parquet(parquet_path)
                machining_window = _load_machining_window(job_info, target_job_id,
                                                          job_info.get('source_folder'))
                n_cnc = int(tdms_data['time_s'].notna().sum()) if 'time_s' in tdms_data.columns else len(tdms_data)
                n_daq = int(tdms_data['daq_time_s'].notna().sum()) if 'daq_time_s' in tdms_data.columns else 0
                st.caption(f"CNC {n_cnc:,} 행 · DAQ {n_daq:,} 행 · {os.path.basename(parquet_path)}")

                window_caption = _machining_window_caption(machining_window)
                if window_caption:
                    st.caption(window_caption)
                mismatch = _program_mismatch_note(machining_window)
                if mismatch:
                    st.warning(mismatch, icon=":material/warning:")

                tab1, tab2, tab3 = st.tabs(["CNC", "DAQ", "FFT"])

                # CNC 와 DAQ 는 행 수가 수백 배 차이나므로 각자의 경과시간 열을 x 축으로 쓴다.
                # 두 축의 0초는 같은 시점(가공 시작)이라 탭을 오가며 같은 구간을 비교할 수 있다.
                x_cnc = 'time_s' if 'time_s' in tdms_data.columns else None
                x_daq = 'daq_time_s' if 'daq_time_s' in tdms_data.columns else None
                axis_title = "가공 경과시간 (초)"

                with tab1:
                    cnc_cols = [c for c in tdms_data.columns if c.startswith('CNC-')]
                    if cnc_cols:
                        selected_cnc = st.multiselect("CNC 채널", cnc_cols, default=cnc_cols[:2] if len(cnc_cols) >= 2 else cnc_cols, key="ws_cnc_sel")
                        if selected_cnc:
                            if x_cnc:
                                frame = tdms_data[[x_cnc] + selected_cnc].dropna(subset=[x_cnc])
                                fig_cnc = px.line(frame, x=x_cnc, y=selected_cnc)
                                _style_chart(fig_cnc, xaxis_title=axis_title)
                            else:
                                fig_cnc = px.line(tdms_data, y=selected_cnc)
                                _style_chart(fig_cnc, xaxis_title="샘플 인덱스")
                            st.plotly_chart(fig_cnc, width="stretch")
                    else:
                        _empty_note("CNC 데이터 없음")

                with tab2:
                    daq_base_cols = sorted(set([c.replace('_max', '').replace('_min', '') for c in tdms_data.columns if ('DAQ-' in c or 'SOUND' in c) and ('_max' in c or '_min' in c)]))
                    if daq_base_cols:
                        selected_daq = st.multiselect("DAQ 채널", daq_base_cols, default=daq_base_cols[:1] if len(daq_base_cols) >= 1 else daq_base_cols, key="ws_daq_sel")
                        if selected_daq:
                            plot_cols = []
                            for c in selected_daq:
                                plot_cols.extend([f"{c}_max", f"{c}_min"])
                            if x_daq:
                                frame = tdms_data[[x_daq] + plot_cols].dropna(subset=[x_daq])
                                fig_daq = px.line(frame, x=x_daq, y=plot_cols)
                                _style_chart(fig_daq, xaxis_title=axis_title)
                            else:
                                fig_daq = px.line(tdms_data, y=plot_cols)
                                _style_chart(fig_daq, xaxis_title="샘플 인덱스")
                            st.plotly_chart(fig_daq, width="stretch")
                    else:
                        _empty_note("DAQ 데이터 없음")

                with tab3:
                    fft_path = resolve_parquet_file(job_info['tdms_fft_parquet_raw_path'],
                                                    job_info.get('source_folder'), "fft.parquet")
                    if fft_path:
                        try:
                            fft_data = pd.read_parquet(fft_path)
                            if 'Frequency' in fft_data.columns:
                                fft_cols = [c for c in fft_data.columns if c != 'Frequency']
                                if fft_cols:
                                    selected_fft = st.multiselect("FFT 채널", fft_cols, default=fft_cols[:1] if len(fft_cols) >= 1 else fft_cols, key="ws_fft_sel")
                                    if selected_fft:
                                        fig_fft = px.line(fft_data, x='Frequency', y=selected_fft, log_y=True)
                                        _style_chart(fig_fft, xaxis_title="Frequency (Hz)", yaxis_title="PSD (Log)")
                                        st.plotly_chart(fig_fft, width="stretch")
                        except Exception as e:
                            st.warning(f"스펙트럼 렌더링 오류: {e}")
                    else:
                        _empty_note("FFT 데이터 없음")
            except Exception as e:
                st.error(f"TDMS 데이터 오류: {e}")
        elif pd.notnull(job_info['tdms_raw_path']):
            st.info("TDMS 데이터는 존재하나 현재 서버에서 시각화 처리(Parquet 변환) 중입니다. 잠시 후 새로고침 해주세요.")
        else:
            _empty_note("연결된 TDMS 데이터 없음")

    with log_chart_col.container(border=True, height="stretch"):
        _card_header(":material/show_chart:", "CNC 그래프")
        options, x_values, x_title, origin = _cnc_chart_options(job_info, log_data, indicators)
        if options:
            picked_label = st.selectbox("표시할 항목", list(options), key="ws_log_series")
            series = options[picked_label]
            fig_log = px.line(x=x_values, y=series) if x_values is not None else px.line(y=series)
            _style_chart(fig_log, xaxis_title=x_title, yaxis_title=picked_label, showlegend=False)
            st.plotly_chart(fig_log, width="stretch")
            st.caption(f"{origin} · {len(series.dropna()):,} 샘플")
        elif log_error:
            st.error(f"로그 분석 오류: {log_error}")
        else:
            _empty_note("연결된 로그·TDMS 데이터 없음")

    with curve_col.container(border=True, height="stretch"):
        _card_header(":material/waves:", "표면조도 프로파일 곡선")
        curves_df = sr_df[sr_df['profile_parquet_raw_path'].notnull()] if not sr_df.empty else pd.DataFrame()
        if not curves_df.empty:
            selected_curve = st.selectbox("단면 프로파일 선택", curves_df['measure_name'], key="ws_curve_sel")
            from backend.vault_manager import get_abs_raw_data_path as _get_abs2
            curve_path = curves_df[curves_df['measure_name'] == selected_curve].iloc[0]['profile_parquet_raw_path']
            curve_path = _get_abs2(curve_path) if pd.notnull(curve_path) else None
            try:
                if curve_path and os.path.exists(curve_path):
                    curve_data = pd.read_parquet(curve_path)
                    fig_curve = px.line(curve_data, x='X', y='Z')
                    _style_chart(fig_curve, xaxis_title="길이 (X, mm)", yaxis_title="높이 (Z, μm)")
                    st.plotly_chart(fig_curve, width="stretch")
            except Exception as e:
                st.error(f"곡선 데이터 렌더링 오류: {e}")
        else:
            _empty_note("프로파일 평가 곡선 없음")


def render_job_workspace():
    st.subheader("Job 워크스페이스")
    st.caption("조건별로 Job을 검색하고 센서 분석 결과를 확인합니다. "
               "메타데이터·환경·품질 값은 '데이터 삽입' 화면에서 업로드와 함께 입력하고, "
               "원본 파일을 내려받으려면 왼쪽 '데이터 다운로드' 메뉴를 이용하세요.")

    result = render_search_filters(key_prefix="ws")
    job_df = result["job_df"]

    st.divider()

    if job_df.empty:
        st.warning("조건에 맞는 가공 이력(Job)이 없습니다. 위 필터를 조정해주세요.")
        return

    list_col, opt_col = st.columns([3, 1], vertical_alignment="bottom")
    with list_col:
        st.markdown(f"#### :material/list_alt: 필터링된 Job 목록 ({len(job_df)}건)")
    with opt_col:
        show_all_columns = st.toggle(
            "모든 컬럼 보기", key="ws_show_all_columns",
            help="끄면 핵심 컬럼만 보여 가로 스크롤 없이 읽을 수 있고, 켜면 환경·품질·로그 컬럼까지 모두 표시합니다.",
        )
    st.caption("표에서 행을 클릭하면 아래 'Job 상세'가 해당 Job으로 전환됩니다.")

    job_id_options = job_df['job_id'].tolist()
    if 'ws_target_job_id' not in st.session_state or st.session_state['ws_target_job_id'] not in job_id_options:
        st.session_state['ws_target_job_id'] = job_id_options[0]

    clicked_job_id = render_job_table(
        job_df, key="ws_job_table", selectable=True, show_all_columns=show_all_columns,
    )
    if clicked_job_id is not None and clicked_job_id != st.session_state['ws_target_job_id']:
        st.session_state['ws_target_job_id'] = clicked_job_id
        st.rerun()

    st.divider()

    def format_job_option(jid):
        row = job_df[job_df['job_id'] == jid].iloc[0]
        return f"Job {jid} - {row['part_name']} (시작시간: {row['start_time']})"

    # 선택 드롭다운과 편집 토글을 한 줄에 두어, 상세 화면의 조작부를 한 곳으로 모은다.
    select_col, toggle_col = st.columns([4, 1], vertical_alignment="bottom")
    with select_col:
        idx = job_id_options.index(st.session_state['ws_target_job_id'])
        selected_target = st.selectbox(
            ":material/manage_search: 상세 확인할 Job 선택", job_id_options, index=idx,
            format_func=format_job_option, key="ws_job_select",
        )
    with toggle_col:
        edit_mode = st.toggle(
            ":material/lock_open: 삭제 모드", key="ws_edit_mode",
            help="켜면 Job 영구 삭제 버튼이 나타납니다. 기본은 꺼져 있어 조회 중 실수로 지우는 것을 막습니다.\n"
                 "메타데이터·환경·품질 값은 '데이터 삽입' 화면에서 입력합니다.",
        )

    if selected_target != st.session_state['ws_target_job_id']:
        st.session_state['ws_target_job_id'] = selected_target
        st.rerun()

    target_job_id = st.session_state['ws_target_job_id']

    job_detail_query = '''
        SELECT j.job_id, j.source_folder, p.project_code, j.machining_type,
               p.part_name, p.part_name AS registry_part_name, p.material_code,
               j.workplan_id, j.machine_code, j.start_time, j.end_time, j.is_finish, j.is_error,
               jfa.tdms_raw_path, lfa.log_raw_path,
               jfa.tdms_parquet_raw_path, jfa.tdms_fft_parquet_raw_path,
               j.cutting_seconds, j.moving_distance, j.cutting_moving_distance,
               j.machining_window, w.program_code,
               m.max_spindle_load, m.avg_spindle_rpm, m.alarm_count,
               m.max_spindle_rpm, m.max_feed_rate, m.stats_source, m.machine_indicators
        FROM job j
        LEFT JOIN machine_log m ON j.job_id = m.job_id
        LEFT JOIN log_file_archive lfa ON lfa.log_id = m.log_id
        LEFT JOIN job_file_archive jfa ON jfa.job_id = j.job_id
        LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
        LEFT JOIN part p ON w.part_code = p.part_code
        WHERE j.job_id = :jid
    '''
    job_detail_df = load_data(job_detail_query, params={"jid": target_job_id})

    if job_detail_df.empty:
        st.error(f"Job ID {target_job_id} 정보를 찾을 수 없습니다.")
        return

    job_info = job_detail_df.iloc[0]
    sr_df = load_data(
        """SELECT s.measure_name, s.ra, s.rq, s.rz, a.profile_parquet_raw_path
             FROM surface_roughness s
             LEFT JOIN surface_roughness_archive a ON a.roughness_id = s.roughness_id
             WHERE s.job_id = :jid""",
        params={"jid": target_job_id},
    )

    _render_analysis_tab(target_job_id, job_info, sr_df)

    if edit_mode:
        st.divider()
        st.subheader("가공 이력(Job) 데이터 삭제")
        st.warning("경고: 삭제된 데이터는 복구할 수 없습니다. DB 연쇄 삭제(Cascade) 규칙에 따라 센서, 조도, 파일 아카이브 데이터도 함께 삭제됩니다.")
        if st.button(f"현재 선택된 Job (ID {target_job_id}) 영구 삭제", type="primary", key="ws_btn_delete"):
            confirm_delete_dialog(target_job_id)
