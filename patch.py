import os

path = r'd:\Project_Lab\Lab Database\Project\frontend\user_dashboard.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if 'st.info("환경 및 메모 데이터 없음")' in line:
        start_idx = i
        break

if start_idx != -1:
    for i in range(start_idx, len(lines)):
        if 'st.info("연결된 TDMS 데이터 없음")' in line:
            end_idx = i
            break
        if 'st.info("연결된 TDMS 데이터 없음")' in lines[i]:
            end_idx = i
            break

if start_idx != -1 and end_idx != -1:
    correct_block = """                        st.info("환경 및 메모 데이터 없음")

                # 3.6 Inspection Card
                with st.container(border=True):
                    st.markdown("##### 🔍 품질 점검 (Inspection)")
                    ins_query = f"SELECT * FROM inspection WHERE job_id = {target_job_id}"
                    ins_df = load_data(ins_query)
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

            with dash_col2:
                import plotly.express as px
                
                # 4. TDMS Charts Card
                with st.container(border=True):
                    st.markdown("##### 📈 TDMS 고주파 센서 시계열 및 주파수 분석")
                    parquet_path = job_info['tdms_parquet_path']
                    if pd.notnull(parquet_path):
                        try:
                            tdms_data = pd.read_parquet(parquet_path)
                            st.caption(f"데이터 로드 완료: {len(tdms_data)} 행")
                            
                            tab1, tab2, tab3 = st.tabs(["CNC 센서", "DAQ 진동/소음 (Envelope)", "FFT 스펙트럼"])
                            
                            with tab1:
                                cnc_cols = [c for c in tdms_data.columns if c.startswith('CNC-')]
                                if cnc_cols:
                                    selected_cnc = st.multiselect("확인할 CNC 채널 선택", cnc_cols, default=cnc_cols[:2] if len(cnc_cols) >= 2 else cnc_cols, key="cnc_sel")
                                    if selected_cnc:
                                        fig_cnc = px.line(tdms_data, y=selected_cnc)
                                        fig_cnc.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=300)
                                        st.plotly_chart(fig_cnc, use_container_width=True)
                                else:
                                    st.info("CNC 데이터 없음")
                                    
                            with tab2:
                                daq_base_cols = list(set([c.replace('_max', '').replace('_min', '') for c in tdms_data.columns if ('DAQ-' in c or 'SOUND' in c) and ('_max' in c or '_min' in c)]))
                                if daq_base_cols:
                                    selected_daq = st.multiselect("확인할 DAQ 채널 선택", daq_base_cols, default=daq_base_cols[:1] if len(daq_base_cols) >= 1 else daq_base_cols, key="daq_sel")
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
                                fft_path = job_info['tdms_fft_parquet_path']
                                if pd.notnull(fft_path):
                                    try:
                                        fft_data = pd.read_parquet(fft_path)
                                        if 'Frequency' in fft_data.columns:
                                            fft_cols = [c for c in fft_data.columns if c != 'Frequency']
                                            if fft_cols:
                                                selected_fft = st.multiselect("주파수 분석 채널", fft_cols, default=fft_cols[:1] if len(fft_cols) >= 1 else fft_cols, key="fft_sel")
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
                    else:
                        st.info("연결된 TDMS 데이터 없음")\n"""

    new_lines = lines[:start_idx] + [correct_block] + lines[end_idx+1:]
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print("Fixed!")
else:
    print("Could not find bounds", start_idx, end_idx)
