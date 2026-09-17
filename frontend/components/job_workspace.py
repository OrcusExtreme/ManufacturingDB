import html
import json
import os
import re

import pandas as pd
import streamlit as st
from sqlalchemy import text

from .common import engine, load_data
from vault_manager import PROCESSED_ROOT, VAULT_ROOT
from .filters import render_search_filters, render_job_table
from cad_viewer_component import find_cad_file_name, render_cad_viewer


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


def _load_machining_window(job_info, job_id):
    """가공 구간 판별 결과를 읽는다. DB 값이 없으면 processed_data 사이드카를 본다."""
    raw = job_info.get('machining_window') if hasattr(job_info, 'get') else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if isinstance(raw, dict) and raw:
        return raw

    side = os.path.join(PROCESSED_ROOT, f"job_{job_id}_window.json")
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
    "hint": "XML 가공시간",
    "full": "기록 전체",
}


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


def resolve_parquet_file(file_path, default_filename=None):
    """Parquet 파일의 실제 경로를 찾는다. DB 경로 -> processed_data -> archive_vault 순으로 탐색."""
    from backend.vault_manager import get_abs_raw_data_path
    if file_path:
        abs_p = get_abs_raw_data_path(file_path)
        if abs_p and os.path.exists(abs_p):
            return abs_p
    if default_filename:
        p1 = os.path.join(PROCESSED_ROOT, default_filename)
        if os.path.exists(p1):
            return p1
        p2 = os.path.join(VAULT_ROOT, "processed_parquet", default_filename)
        if os.path.exists(p2):
            return p2
        p3 = os.path.join(VAULT_ROOT, "surface_roughness", default_filename)
        if os.path.exists(p3):
            return p3
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
                pq_p = resolve_parquet_file(job_info['tdms_parquet_raw_path'], f"job_{target_job_id}_viz.parquet")
                if pq_p and os.path.exists(pq_p):
                    df_temp = pd.read_parquet(pq_p, columns=['Time Channel CNC'])
                    if not df_temp.empty and 'Time Channel CNC' in df_temp.columns:
                        t_val = df_temp['Time Channel CNC'].dropna().iloc[0]
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
                ("소재", _fmt(job_info['material_code'], fallback="미등록")),
                ("연구 프로젝트", _fmt(proj_name, fallback="미지정")),
                ("가공 종류", _fmt(job_info['machining_type'])),
                ("가공 순서", f"{iteration} 번째"),
                ("가공 시작", date_str),
                ("가공 종료", str(end_time_val).split('.')[0] if pd.notnull(end_time_val) else "-"),
                ("장비 코드", _fmt(job_info['machine_code'])),
                ("NC 프로그램", _fmt(job_info['program_code'])),
            ])
            st.divider()
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
                    ("형상 정밀도", _fmt(ins_row['shape_accuracy'])),
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

    with col_log:
        with st.container(border=True):
            _card_header(":material/summarize:", "CNC 로그 요약 (1Hz)")
            alarm_count = job_info['alarm_count']
            _kv_list([
                ("Spindle Load", _log_stat('cls', digits=2, suffix=" %")),
                ("Spindle RPM", _log_stat('crpm')),
                ("Feed Rate", _log_stat('cfr')),
                ("알람 발생", f"{int(alarm_count)} 건" if pd.notnull(alarm_count) else "-"),
            ])

            if log_error:
                st.error(f"로그 분석 오류: {log_error}")
            elif log_data is None:
                _empty_note("연결된 로그 파일 없음")

        with st.container(border=True, height="stretch"):
            _card_header(":material/thermostat:", "작업 환경 및 메모")
            if env_row is not None:
                temp_val = env_row['temperature']
                hum_val = env_row['humidity']
                _kv_list([
                    ("온도 / 습도",
                     f"{temp_val:.1f} °C / {hum_val:.1f} %" if pd.notnull(temp_val) and pd.notnull(hum_val)
                     else f"{_fmt(temp_val)} / {_fmt(hum_val)}"),
                    ("작업자", _fmt(env_row['worker_name'])),
                    ("요일", _fmt(env_row['day_of_week'])),
                    ("칩 형태", _fmt(env_row['chip_shape'])),
                    ("이상 소음", _fmt(env_row['abnormal_noise'])),
                    # 메모 값 자체가 '없음'일 수 있어, 미입력 표기는 다른 항목과 같은 '-'로 통일한다.
                    ("수기 메모", _fmt(env_row['free_memo'])),
                ])
            else:
                _empty_note("환경 및 메모 데이터 없음")

    # ── 그래프 패널: 파형 3종을 하나의 패널로 묶고, 세 카드는 서로 높이를 맞춘다.
    with st.container(border=True):
        _card_header(":material/bar_chart:", "그래프")
        chart_col, log_chart_col, curve_col = st.columns(3, gap="medium")

    with chart_col.container(border=True, height="stretch"):
        _card_header(":material/timeline:", "TDMS 그래프")
        parquet_path = resolve_parquet_file(job_info['tdms_parquet_raw_path'], f"job_{target_job_id}_viz.parquet")
        if parquet_path:
            try:
                tdms_data = pd.read_parquet(parquet_path)
                machining_window = _load_machining_window(job_info, target_job_id)
                n_cnc = int(tdms_data['time_s'].notna().sum()) if 'time_s' in tdms_data.columns else len(tdms_data)
                n_daq = int(tdms_data['daq_time_s'].notna().sum()) if 'daq_time_s' in tdms_data.columns else 0
                st.caption(f"CNC {n_cnc:,} 행 · DAQ {n_daq:,} 행 · {os.path.basename(parquet_path)}")

                window_caption = _machining_window_caption(machining_window)
                if window_caption:
                    st.caption(window_caption)

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
                    fft_path = resolve_parquet_file(job_info['tdms_fft_parquet_raw_path'], f"job_{target_job_id}_fft.parquet")
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
        _card_header(":material/show_chart:", "CNC 로그 그래프")
        log_series = [("Spindle Load (%)", 'cls'), ("Spindle RPM", 'crpm'), ("Feed Rate", 'cfr')]
        available_series = [(label, col) for label, col in log_series
                            if log_data is not None and col in log_data.columns]
        if available_series:
            picked_label = st.selectbox("표시할 항목", [label for label, _ in available_series], key="ws_log_series")
            picked_col = dict(available_series)[picked_label]
            series = pd.to_numeric(log_data[picked_col], errors='coerce')
            fig_log = px.line(y=series)
            _style_chart(fig_log, xaxis_title="샘플 (1초 간격)", yaxis_title=picked_label, showlegend=False)
            st.plotly_chart(fig_log, width="stretch")
            st.caption(f"총 {len(series.dropna()):,} 샘플 (1초 간격)")
        elif log_error:
            st.error(f"로그 분석 오류: {log_error}")
        else:
            _empty_note("연결된 로그 파일 없음")

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


def _render_edit_tab(target_job_id, job_row):
    tab1, tab2, tab3 = st.tabs([":material/edit_note: Job 메타데이터", ":material/thermostat: 환경 및 메모 (EnvMemo)", ":material/fact_check: 품질 점검 (Inspection)"])

    with tab1:
        st.write(f"**Job ID: {job_row['job_id']} / 폴더: {job_row['source_folder']} / 시작시간: {job_row['start_time']}**")
        with st.form("ws_job_meta_form"):
            # 부품명·프로젝트명은 part 테이블 하나가 정답이다. Job 마다 덮어쓰던 사본을
            # 없앴으므로 여기서는 수정하지 않는다. (부품 정보는 계층형 마스터 화면에서 관리)
            st.caption(f"부품 **{job_row['part_name']}** · 프로젝트 **{job_row['project_code']}** "
                       "— 부품 정보는 계층형 마스터 데이터 화면에서 관리합니다.")
            mt_input = st.text_input("가공 종류", value=job_row['machining_type'] if pd.notnull(job_row['machining_type']) else "")

            if st.form_submit_button("메타데이터 저장"):
                with engine.connect() as conn:
                    stmt = text("UPDATE job SET machining_type=:mt WHERE job_id=:jid")
                    conn.execute(stmt, {"mt": mt_input or None, "jid": target_job_id})
                    conn.commit()
                st.success("메타데이터가 업데이트되었습니다.")
                st.cache_data.clear()
                st.rerun()

    with tab2:
        env_df = load_data("SELECT * FROM env_memo WHERE job_id = :jid", params={"jid": target_job_id})
        env_row = env_df.iloc[0] if not env_df.empty else None

        with st.form("ws_env_memo_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                wn = st.text_input("작업자 (worker_name)", value=env_row['worker_name'] if env_row is not None and pd.notnull(env_row['worker_name']) else "")
                temp_val = str(env_row['temperature']) if env_row is not None and pd.notnull(env_row['temperature']) else ""
                temp = st.text_input("온도 (temperature)", value=temp_val)
                hum_val = str(env_row['humidity']) if env_row is not None and pd.notnull(env_row['humidity']) else ""
                hum = st.text_input("습도 (humidity)", value=hum_val)
            with col2:
                dow = st.text_input("요일 (day_of_week)", value=env_row['day_of_week'] if env_row is not None and pd.notnull(env_row['day_of_week']) else "")
                cs = st.text_input("칩 형태 (chip_shape)", value=env_row['chip_shape'] if env_row is not None and pd.notnull(env_row['chip_shape']) else "")
                an = st.text_input("이상 소음 (abnormal_noise)", value=env_row['abnormal_noise'] if env_row is not None and pd.notnull(env_row['abnormal_noise']) else "")
            with col3:
                fm = st.text_area("수기 메모 (free_memo)", value=env_row['free_memo'] if env_row is not None and pd.notnull(env_row['free_memo']) else "")

            if st.form_submit_button("EnvMemo 저장"):
                with engine.connect() as conn:
                    if env_row is not None:
                        stmt = text("UPDATE env_memo SET worker_name=:wn, temperature=:temp, humidity=:hum, day_of_week=:dow, chip_shape=:cs, abnormal_noise=:an, free_memo=:fm WHERE job_id=:jid")
                    else:
                        stmt = text("INSERT INTO env_memo (job_id, worker_name, temperature, humidity, day_of_week, chip_shape, abnormal_noise, free_memo) VALUES (:jid, :wn, :temp, :hum, :dow, :cs, :an, :fm)")

                    temp_parsed = float(temp) if temp and temp.strip() else None
                    hum_parsed = float(hum) if hum and hum.strip() else None

                    conn.execute(stmt, {"wn": wn or None, "temp": temp_parsed, "hum": hum_parsed, "dow": dow or None, "cs": cs or None, "an": an or None, "fm": fm or None, "jid": target_job_id})
                    conn.commit()
                st.success("EnvMemo가 성공적으로 저장되었습니다.")
                st.cache_data.clear()
                st.rerun()

    with tab3:
        ins_df = load_data("SELECT * FROM inspection WHERE job_id = :jid", params={"jid": target_job_id})
        ins_row = ins_df.iloc[0] if not ins_df.empty else None

        with st.form("ws_inspection_form"):
            col1, col2 = st.columns(2)
            with col1:
                dt = st.text_input("치수 및 공차 (dimension_tolerance)", value=ins_row['dimension_tolerance'] if ins_row is not None and pd.notnull(ins_row['dimension_tolerance']) else "")
                sra_val = str(ins_row['surface_roughness_ra']) if ins_row is not None and pd.notnull(ins_row['surface_roughness_ra']) else ""
                sra = st.text_input("표면조도 Ra", value=sra_val)
                srz_val = str(ins_row['surface_roughness_rz']) if ins_row is not None and pd.notnull(ins_row['surface_roughness_rz']) else ""
                srz = st.text_input("표면조도 Rz", value=srz_val)
            with col2:
                sa = st.text_input("형상정밀도 (shape_accuracy)", value=ins_row['shape_accuracy'] if ins_row is not None and pd.notnull(ins_row['shape_accuracy']) else "")
                pf_options = ["", "PASS", "FAIL"]
                current_pf = ins_row['pass_fail'] if ins_row is not None and pd.notnull(ins_row['pass_fail']) else ""
                pf_index = pf_options.index(current_pf) if current_pf in pf_options else 0
                pf = st.selectbox("합불 판정 (pass_fail)", options=pf_options, index=pf_index)

            if st.form_submit_button("Inspection 저장"):
                with engine.connect() as conn:
                    if ins_row is not None:
                        stmt = text("UPDATE inspection SET dimension_tolerance=:dt, surface_roughness_ra=:sra, surface_roughness_rz=:srz, shape_accuracy=:sa, pass_fail=:pf WHERE job_id=:jid")
                    else:
                        stmt = text("INSERT INTO inspection (job_id, dimension_tolerance, surface_roughness_ra, surface_roughness_rz, shape_accuracy, pass_fail) VALUES (:jid, :dt, :sra, :srz, :sa, :pf)")

                    sra_parsed = float(sra) if sra and sra.strip() else None
                    srz_parsed = float(srz) if srz and srz.strip() else None

                    conn.execute(stmt, {"dt": dt or None, "sra": sra_parsed, "srz": srz_parsed, "sa": sa or None, "pf": pf or None, "jid": target_job_id})
                    conn.commit()
                st.success("Inspection이 성공적으로 저장되었습니다.")
                st.cache_data.clear()
                st.rerun()


def render_job_workspace():
    st.subheader("Job 워크스페이스")
    st.caption("조건별로 Job을 검색하고 센서 분석 결과를 확인합니다. 데이터 수정/삭제가 필요하면 Job 상세에서 '편집 모드'를 켜고, "
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
            ":material/lock_open: 편집 모드", key="ws_edit_mode",
            help="켜면 메타데이터·환경·품질 수정 탭과 Job 영구 삭제 버튼이 나타납니다. 기본은 꺼져 있어 조회 중 실수로 데이터를 바꾸는 것을 막습니다.",
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
               m.max_spindle_load, m.avg_spindle_rpm, m.alarm_count
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

    if edit_mode:
        tab_analysis, tab_edit = st.tabs([":material/analytics: 분석 보기", ":material/edit: 메타데이터 · 환경 · 품질 수정"])
        with tab_analysis:
            _render_analysis_tab(target_job_id, job_info, sr_df)
        with tab_edit:
            _render_edit_tab(target_job_id, job_info)
    else:
        _render_analysis_tab(target_job_id, job_info, sr_df)

    if edit_mode:
        st.divider()
        st.subheader("가공 이력(Job) 데이터 삭제")
        st.warning("경고: 삭제된 데이터는 복구할 수 없습니다. DB 연쇄 삭제(Cascade) 규칙에 따라 센서, 조도, 파일 아카이브 데이터도 함께 삭제됩니다.")
        if st.button(f"현재 선택된 Job (ID {target_job_id}) 영구 삭제", type="primary", key="ws_btn_delete"):
            confirm_delete_dialog(target_job_id)
