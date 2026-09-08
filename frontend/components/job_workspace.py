import os
import re

import pandas as pd
import streamlit as st
from sqlalchemy import text

from .common import engine, load_data, PROJECT_ROOT
from .filters import render_search_filters, render_job_table


def resolve_parquet_file(file_path, default_filename=None):
    """Parquet 파일의 실제 경로를 찾는다. DB 경로 -> processed_data -> archive_vault 순으로 탐색."""
    from backend.vault_manager import get_abs_raw_data_path
    if file_path:
        abs_p = get_abs_raw_data_path(file_path)
        if abs_p and os.path.exists(abs_p):
            return abs_p
    if default_filename:
        p1 = os.path.join(PROJECT_ROOT, "processed_data", default_filename)
        if os.path.exists(p1):
            return p1
        p2 = os.path.join(PROJECT_ROOT, "archive_vault", "processed_parquet", default_filename)
        if os.path.exists(p2):
            return p2
        p3 = os.path.join(PROJECT_ROOT, "archive_vault", "surface_roughness", default_filename)
        if os.path.exists(p3):
            return p3
    return None


@st.dialog("NC 데이터 원본 조회", width="large")
def show_nc_dialog(wp_id):
    query = f"SELECT nc_file_content FROM workplan_file_archive WHERE workplan_id = '{wp_id}'"
    df = load_data(query)
    if not df.empty and pd.notnull(df.iloc[0]['nc_file_content']):
        try:
            content = df.iloc[0]['nc_file_content'].decode('utf-8', errors='replace')
        except AttributeError:
            content = str(df.iloc[0]['nc_file_content'])
        st.code(content, language='text')
    else:
        st.info("해당 Workplan의 NC 원본 데이터가 아카이브에 없습니다.")


@st.dialog("XML 메타데이터 조회", width="large")
def show_xml_dialog(job_id):
    query = f"SELECT xml_file_content FROM job_file_archive WHERE job_id = {job_id}"
    df = load_data(query)
    if not df.empty and pd.notnull(df.iloc[0]['xml_file_content']):
        try:
            content = df.iloc[0]['xml_file_content'].decode('utf-8', errors='replace')
        except AttributeError:
            content = str(df.iloc[0]['xml_file_content'])
        st.code(content, language='xml')
    else:
        st.info("해당 Job의 XML 원본 데이터가 아카이브에 없습니다.")


@st.dialog("데이터 영구 삭제 확인")
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

    part_name = job_info['custom_part_name']
    job_time = job_info['start_time']

    part_jobs_query = '''
        SELECT j.start_time
        FROM job j
        LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
        LEFT JOIN part p ON w.part_code = p.part_code
        WHERE COALESCE(j.custom_part_name, p.part_code) = :part_name
        ORDER BY j.start_time ASC
    '''
    part_jobs_df = load_data(part_jobs_query, params={"part_name": part_name})
    iteration = 1
    if not part_jobs_df.empty:
        for idx, (i, row) in enumerate(part_jobs_df.iterrows()):
            if pd.notnull(job_time) and pd.notnull(row['start_time']) and row['start_time'] == job_time:
                iteration = idx + 1
                break

    proj_name = str(job_info['research_project'])
    part_display = str(part_name)
    date_str = "알 수 없음"
    if pd.notnull(job_info['start_time']):
        date_str = str(job_info['start_time']).split('.')[0]
    else:
        for p_candidate in [job_info['log_file_path'], job_info['tdms_file_path']]:
            if p_candidate and pd.notnull(p_candidate):
                m = re.search(r'__(\d{12})(?:_ext)?\.(?:log|tdms)', str(p_candidate))
                if m:
                    ts = m.group(1)
                    date_str = f"20{ts[0:2]}-{ts[2:4]}-{ts[4:6]} {ts[6:8]}:{ts[8:10]}:{ts[10:12]}"
                    break
        if date_str == "알 수 없음" and pd.notnull(job_info['tdms_parquet_path']):
            try:
                pq_p = resolve_parquet_file(job_info['tdms_parquet_path'], f"job_{target_job_id}_viz.parquet")
                if pq_p and os.path.exists(pq_p):
                    df_temp = pd.read_parquet(pq_p, columns=['Time Channel CNC'])
                    if not df_temp.empty and 'Time Channel CNC' in df_temp.columns:
                        t_val = df_temp['Time Channel CNC'].dropna().iloc[0]
                        date_str = str(t_val).split('.')[0]
            except Exception:
                pass

    # 핵심 요약을 한 줄짜리 지표 스트립으로 압축 (기존 텍스트 카드 대비 세로 공간 대폭 절약)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Part", part_display if part_display and part_display != 'None' else '없음')
    m2.metric("프로젝트", proj_name if proj_name and proj_name != 'None' else '없음')
    m3.metric("가공 순서", f"{iteration} 번째")
    cs = job_info['cutting_seconds']
    m4.metric("가공 시간", f"{cs:.1f} 초" if pd.notnull(cs) else '없음')
    st.caption(f"가공 일시: {date_str}")

    dash_col1, dash_col2 = st.columns([1, 2])

    with dash_col1:
        with st.container(border=True):
            st.markdown("##### 📄 Job 정보")
            nc_code = str(job_info['program_code'])
            st.write(f"**NC 코드:** {nc_code if nc_code and nc_code != 'None' and nc_code != 'nan' else '없음'}")
            md = job_info['moving_distance']
            st.write(f"**이동 거리:** {f'{md:.1f} mm' if pd.notnull(md) else '데이터 없음'}")

            b1, b2 = st.columns(2)
            with b1:
                if st.button("XML 원본 (팝업)", use_container_width=True, key="ws_btn_xml"):
                    show_xml_dialog(target_job_id)
            with b2:
                if st.button("NC 코드 (팝업)", use_container_width=True, key="ws_btn_nc"):
                    show_nc_dialog(job_info['workplan_id'])

    with dash_col2:
        import plotly.express as px

        with st.container(border=True):
            st.markdown("##### 📈 TDMS 고주파 센서 시계열 및 주파수 분석")
            parquet_path = resolve_parquet_file(job_info['tdms_parquet_path'], f"job_{target_job_id}_viz.parquet")
            if parquet_path:
                try:
                    tdms_data = pd.read_parquet(parquet_path)
                    st.caption(f"데이터 로드 완료: {len(tdms_data)} 행 (경로: {os.path.basename(parquet_path)})")

                    tab1, tab2, tab3 = st.tabs(["CNC 센서", "DAQ 진동/소음 (Envelope)", "FFT 스펙트럼"])

                    with tab1:
                        cnc_cols = [c for c in tdms_data.columns if c.startswith('CNC-')]
                        if cnc_cols:
                            selected_cnc = st.multiselect("확인할 CNC 채널 선택", cnc_cols, default=cnc_cols[:2] if len(cnc_cols) >= 2 else cnc_cols, key="ws_cnc_sel")
                            if selected_cnc:
                                fig_cnc = px.line(tdms_data, y=selected_cnc)
                                fig_cnc.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=300)
                                st.plotly_chart(fig_cnc, use_container_width=True)
                        else:
                            st.info("CNC 데이터 없음")

                    with tab2:
                        daq_base_cols = list(set([c.replace('_max', '').replace('_min', '') for c in tdms_data.columns if ('DAQ-' in c or 'SOUND' in c) and ('_max' in c or '_min' in c)]))
                        if daq_base_cols:
                            selected_daq = st.multiselect("확인할 DAQ 채널 선택", daq_base_cols, default=daq_base_cols[:1] if len(daq_base_cols) >= 1 else daq_base_cols, key="ws_daq_sel")
                            if selected_daq:
                                plot_cols = []
                                for c in selected_daq:
                                    plot_cols.extend([f"{c}_max", f"{c}_min"])
                                fig_daq = px.line(tdms_data, y=plot_cols)
                                fig_daq.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=300)
                                st.plotly_chart(fig_daq, use_container_width=True)
                        else:
                            st.info("DAQ 데이터 없음")

                    with tab3:
                        fft_path = resolve_parquet_file(job_info['tdms_fft_parquet_path'], f"job_{target_job_id}_fft.parquet")
                        if fft_path:
                            try:
                                fft_data = pd.read_parquet(fft_path)
                                if 'Frequency' in fft_data.columns:
                                    fft_cols = [c for c in fft_data.columns if c != 'Frequency']
                                    if fft_cols:
                                        selected_fft = st.multiselect("주파수 분석 채널", fft_cols, default=fft_cols[:1] if len(fft_cols) >= 1 else fft_cols, key="ws_fft_sel")
                                        if selected_fft:
                                            fig_fft = px.line(fft_data, x='Frequency', y=selected_fft, log_y=True)
                                            fig_fft.update_layout(xaxis_title="Frequency (Hz)", yaxis_title="PSD (Log)", margin=dict(l=0, r=0, t=30, b=0), height=300)
                                            st.plotly_chart(fig_fft, use_container_width=True)
                            except Exception as e:
                                st.warning(f"스펙트럼 렌더링 오류: {e}")
                        else:
                            st.info("FFT 데이터 없음")
                except Exception as e:
                    st.error(f"TDMS 데이터 오류: {e}")
            elif pd.notnull(job_info['tdms_file_path']):
                st.info("TDMS 데이터는 존재하나 현재 서버에서 시각화 처리(Parquet 변환) 중입니다. 잠시 후 새로고침 해주세요.")
            else:
                st.info("연결된 TDMS 데이터 없음")

    # 자주 안 보는 부가 정보는 접어두고, 필요할 때만 펼쳐서 스크롤 부담을 줄인다.
    with st.expander("🔽 부가 정보 더보기 (CNC 로그 · 조도 통계/프로파일 · 환경 · 품질)", expanded=False):
        detail_col1, detail_col2 = st.columns(2)

        with detail_col1:
            st.markdown("##### ⚙️ CNC 로그 요약 (1Hz)")
            from backend.vault_manager import get_abs_raw_data_path as _get_abs
            log_path_raw = job_info['log_file_path']
            log_path = _get_abs(log_path_raw) if pd.notnull(log_path_raw) else None
            if log_path and os.path.exists(log_path):
                try:
                    try:
                        log_data = pd.read_csv(log_path, sep='\t', encoding='utf-8')
                    except Exception:
                        log_data = pd.read_csv(log_path, sep='\t', encoding='cp949')

                    lm1, lm2, lm3 = st.columns(3)
                    if 'cls' in log_data.columns:
                        lm1.metric("Spindle Load Max", f"{log_data['cls'].max():.2f} %", f"Avg {log_data['cls'].mean():.2f} %", delta_color="off")
                    if 'crpm' in log_data.columns:
                        lm2.metric("Spindle RPM Max", f"{log_data['crpm'].max():.0f}", f"Avg {log_data['crpm'].mean():.0f}", delta_color="off")
                    if 'cfr' in log_data.columns:
                        lm3.metric("Feed Rate Max", f"{log_data['cfr'].max():.0f}", f"Avg {log_data['cfr'].mean():.0f}", delta_color="off")
                except Exception as ex:
                    st.error(f"로그 분석 오류: {ex}")
            else:
                st.info("연결된 로그 파일 없음")

            st.markdown("##### 📏 표면조도 통계")
            if not sr_df.empty:
                stats_df = sr_df[sr_df['ra'].notnull()][['measure_name', 'ra', 'rq', 'rz']]
                if not stats_df.empty:
                    st.dataframe(stats_df, hide_index=True, width="stretch")
                else:
                    st.info("수치 데이터가 없습니다.")
            else:
                st.info("조도 측정 데이터 없음")

            st.markdown("##### 🔬 표면조도 프로파일 곡선")
            if not sr_df.empty:
                curves_df = sr_df[sr_df['profile_parquet_path'].notnull()]
                if not curves_df.empty:
                    selected_curve = st.selectbox("단면 프로파일 선택", curves_df['measure_name'], label_visibility="collapsed", key="ws_curve_sel")
                    from backend.vault_manager import get_abs_raw_data_path as _get_abs2
                    curve_path = curves_df[curves_df['measure_name'] == selected_curve].iloc[0]['profile_parquet_path']
                    curve_path = _get_abs2(curve_path) if pd.notnull(curve_path) else None
                    try:
                        if curve_path and os.path.exists(curve_path):
                            curve_data = pd.read_parquet(curve_path)
                            fig_curve = px.line(curve_data, x='X', y='Z')
                            fig_curve.update_layout(xaxis_title="길이 (X, mm)", yaxis_title="높이 (Z, μm)", margin=dict(l=0, r=0, t=30, b=0), height=300)
                            st.plotly_chart(fig_curve, use_container_width=True)
                    except Exception as e:
                        st.error(f"곡선 데이터 렌더링 오류: {e}")
                else:
                    st.info("프로파일 평가 곡선 없음")
            else:
                st.info("표면조도 데이터 없음")

        with detail_col2:
            st.markdown("##### 🌡️ 환경 및 메모 (EnvMemo)")
            env_query = "SELECT * FROM env_memo WHERE job_id = :jid"
            env_df = load_data(env_query, params={"jid": target_job_id})
            if not env_df.empty:
                env_row = env_df.iloc[0]
                st.write(f"**작업자:** {env_row['worker_name'] if pd.notnull(env_row['worker_name']) else '-'}")
                st.write(f"**온도/습도:** {env_row['temperature']} °C / {env_row['humidity']} %")
                st.write(f"**요일:** {env_row['day_of_week'] if pd.notnull(env_row['day_of_week']) else '-'}")
                st.write(f"**칩 형태:** {env_row['chip_shape'] if pd.notnull(env_row['chip_shape']) else '-'}")
                st.write(f"**이상 소음:** {env_row['abnormal_noise'] if pd.notnull(env_row['abnormal_noise']) else '-'}")
                st.write(f"**수기 메모:** {env_row['free_memo'] if pd.notnull(env_row['free_memo']) else '-'}")
            else:
                st.info("환경 및 메모 데이터 없음")

            st.markdown("##### 🔍 품질 점검 (Inspection)")
            ins_query = "SELECT * FROM inspection WHERE job_id = :jid"
            ins_df = load_data(ins_query, params={"jid": target_job_id})
            if not ins_df.empty:
                ins_row = ins_df.iloc[0]
                st.write(f"**전체 조도(Ra/Rz):** {ins_row['surface_roughness_ra'] if pd.notnull(ins_row['surface_roughness_ra']) else '-'} / {ins_row['surface_roughness_rz'] if pd.notnull(ins_row['surface_roughness_rz']) else '-'}")
                st.write(f"**형상정밀도:** {ins_row['shape_accuracy'] if pd.notnull(ins_row['shape_accuracy']) else '-'}")
                st.write(f"**치수 및 공차:** {ins_row['dimension_tolerance'] if pd.notnull(ins_row['dimension_tolerance']) else '-'}")
                pf_val = ins_row['pass_fail']
                color = "green" if pf_val == "PASS" else "red" if pf_val == "FAIL" else "gray"
                st.markdown(f"**합불 판정:** <span style='color:{color}; font-weight:bold;'>{pf_val if pd.notnull(pf_val) and pf_val != '' else '미판정'}</span>", unsafe_allow_html=True)
            else:
                st.info("품질 점검 데이터 없음")


def _render_edit_tab(target_job_id, job_row):
    tab1, tab2, tab3 = st.tabs(["📝 Job 메타데이터", "🌡️ 환경 및 메모 (EnvMemo)", "🔍 품질 점검 (Inspection)"])

    with tab1:
        st.write(f"**Job ID: {job_row['job_id']} / 폴더: {job_row['source_folder']} / 시작시간: {job_row['start_time']}**")
        with st.form("ws_job_meta_form"):
            cpn_input = st.text_input("사용자 지정 Part 명", value=job_row['custom_part_name'] if pd.notnull(job_row['custom_part_name']) else "")
            rp_input = st.text_input("연구 프로젝트 명", value=job_row['research_project'] if pd.notnull(job_row['research_project']) else "")
            mt_input = st.text_input("가공 종류", value=job_row['machining_type'] if pd.notnull(job_row['machining_type']) else "")

            if st.form_submit_button("메타데이터 저장"):
                with engine.connect() as conn:
                    stmt = text("UPDATE job SET custom_part_name=:cpn, research_project=:rp, machining_type=:mt WHERE job_id=:jid")
                    conn.execute(stmt, {"cpn": cpn_input or None, "rp": rp_input or None, "mt": mt_input or None, "jid": target_job_id})
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

    st.markdown("#### 📋 필터링된 Job 목록")
    st.caption("가로로 스크롤하여 환경 메모, 품질 검사 등 관련된 모든 정보를 엑셀처럼 한눈에 확인할 수 있습니다.")
    render_job_table(job_df)

    st.divider()

    job_id_options = job_df['job_id'].tolist()
    if 'ws_target_job_id' not in st.session_state or st.session_state['ws_target_job_id'] not in job_id_options:
        st.session_state['ws_target_job_id'] = job_id_options[0]

    def format_job_option(jid):
        row = job_df[job_df['job_id'] == jid].iloc[0]
        return f"Job {jid} - {row['part_name']} (시작시간: {row['start_time']})"

    idx = job_id_options.index(st.session_state['ws_target_job_id'])
    selected_target = st.selectbox(
        "🎯 상세 확인할 Job 선택", job_id_options, index=idx,
        format_func=format_job_option, key="ws_job_select",
    )
    if selected_target != st.session_state['ws_target_job_id']:
        st.session_state['ws_target_job_id'] = selected_target
        st.rerun()

    target_job_id = st.session_state['ws_target_job_id']

    job_detail_query = '''
        SELECT j.job_id, j.source_folder, j.research_project, j.machining_type,
               COALESCE(j.custom_part_name, p.part_code) as custom_part_name,
               j.workplan_id, j.machine_code, j.start_time, j.is_finish,
               j.tdms_file_path, j.log_file_path, j.tdms_parquet_path, j.tdms_fft_parquet_path,
               j.cutting_seconds, j.moving_distance, w.program_code,
               m.max_spindle_load, m.avg_spindle_rpm, m.alarm_count
        FROM job j
        LEFT JOIN machine_log m ON j.job_id = m.job_id
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
        "SELECT measure_name, ra, rq, rz, profile_parquet_path FROM surface_roughness WHERE job_id = :jid",
        params={"jid": target_job_id},
    )

    header_col, toggle_col = st.columns([4, 1])
    with header_col:
        st.subheader(f"Job 상세 (Job ID: {target_job_id})")
    with toggle_col:
        edit_mode = st.toggle(
            "🔓 편집 모드", key="ws_edit_mode",
            help="켜면 메타데이터·환경·품질 수정 탭과 Job 영구 삭제 버튼이 나타납니다. 기본은 꺼져 있어 조회 중 실수로 데이터를 바꾸는 것을 막습니다.",
        )

    if edit_mode:
        tab_analysis, tab_edit = st.tabs(["📊 분석 보기", "📝 메타데이터 · 환경 · 품질 수정"])
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
