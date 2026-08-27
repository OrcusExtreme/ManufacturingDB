import streamlit as st
import pandas as pd
import os
import datetime
import sys
import zipfile
import io
from streamlit_option_menu import option_menu

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'backend'))

from DB.database import engine
from sqlalchemy import text

def create_zip_from_folder(folder_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, folder_path)
                zf.write(abs_path, rel_path)
    buf.seek(0)
    return buf

# Page Config
st.set_page_config(page_title="가공 데이터 대시보드 (관리자용)", layout="wide")
st.title("공작기계지능화실험실 제조 DB 대시보드 (Admin)")

@st.cache_data(ttl=60)
def load_data(query, params=None):
    from sqlalchemy import text
    if params:
        return pd.read_sql(text(query), con=engine, params=params)
    return pd.read_sql(text(query), con=engine)

if 'selected_projs' not in st.session_state: st.session_state.selected_projs = []
if 'selected_parts' not in st.session_state: st.session_state.selected_parts = []
if 'selected_job_labels' not in st.session_state: st.session_state.selected_job_labels = []
if 'selected_dates' not in st.session_state: st.session_state.selected_dates = []
if 'selected_machs' not in st.session_state: st.session_state.selected_machs = []
if 'target_job_id' not in st.session_state: st.session_state.target_job_id = None
if 'nav_menu' not in st.session_state: st.session_state.nav_menu = "데이터 수정"

# --- Sidebar: Navigation & DB Schema ---
with st.sidebar:

    st.header("DB 스키마 구조")
    with st.expander("핵심 테이블 구조 안내"):
        st.markdown("""
        - **Part**: 가공 대상 부품 (part_code)
        - **Workplan**: NC 프로그램 단위 계획 (workplan_id)
        - **Workingstep**: 개별 가공 단위 (공구 호출 순서)
        - **MachiningFeature**: 가공 형상 (Hole, PlanarFace 등)
        - **Tool**: 공구 마스터 정보
        - **Job**: 실제 1회성 가공 이력 (Job ID, Project, Type)
        - **MachineLog**: 장비 로그 요약
        """)
    
    st.divider()
    st.title("내비게이션")
    
    options_list = ["가공 검색", "데이터 수정", "데이터 삽입"]
    
    if 'current_nav' not in st.session_state:
        st.session_state.current_nav = st.session_state.get('nav_menu', "데이터 수정")
        
    default_idx = options_list.index(st.session_state.current_nav) if st.session_state.current_nav in options_list else 1
        
    nav_menu = option_menu(
        menu_title=None, 
        options=options_list, 
        icons=["search", "pencil-square", "cloud-upload"], 
        menu_icon="cast", 
        default_index=default_idx,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "gray", "font-size": "18px"}, 
            "nav-link": {
                "font-size": "16px", 
                "text-align": "left", 
                "margin":"0px", 
                "--hover-color": "rgba(26, 115, 232, 0.1)",
                "color": "inherit"
            },
            "nav-link-selected": {
                "background-color": "transparent",
                "color": "#1a73e8",
                "font-weight": "bold"
            }
        }
    )
    
    if nav_menu != st.session_state.current_nav:
        st.session_state.current_nav = nav_menu
        st.session_state['nav_menu'] = nav_menu
        st.rerun()

    if nav_menu == "데이터 수정":
        st.divider()
        st.header("조건별 실험 검색")
    
        # 1. Project Filter
        proj_query = "SELECT DISTINCT research_project FROM job WHERE research_project IS NOT NULL AND research_project != ''"
        projs_df = load_data(proj_query)
        projs = projs_df['research_project'].tolist() if not projs_df.empty else []
        selected_projs = st.multiselect("1. 연구 프로젝트 명", projs, default=[])
    
        # 2. Part Filter (Dependent on Project)
        part_query = """
            SELECT DISTINCT COALESCE(j.custom_part_name, p.part_code) AS display_part 
            FROM job j 
            JOIN workplan w ON j.workplan_id = w.workplan_id 
            JOIN part p ON w.part_code = p.part_code
        """
        
        part_params = {}
        if selected_projs:
            proj_keys = [f"p_proj_{i}" for i in range(len(selected_projs))]
            for k, v in zip(proj_keys, selected_projs):
                part_params[k] = v
            part_query += f" WHERE j.research_project IN ({', '.join([':'+k for k in proj_keys])})"
            
        part_query += " ORDER BY display_part"
    
        part_codes_df = load_data(part_query, params=part_params)
        part_codes = part_codes_df['display_part'].tolist() if not part_codes_df.empty else []
        selected_parts = st.multiselect("2. Part 명 (사용자 지정 우선)", part_codes, default=[])
    
        # 3. Iteration (Job 가공 순서) Filter (Dependent on Part)
        job_iteration_query = """
            SELECT j.job_id, COALESCE(j.custom_part_name, p.part_code) AS part_name, j.start_time 
            FROM job j
            JOIN workplan w ON j.workplan_id = w.workplan_id 
            JOIN part p ON w.part_code = p.part_code
        """
        filters = []
        iter_params = {}
        if selected_projs:
            proj_keys = [f"i_proj_{i}" for i in range(len(selected_projs))]
            for k, v in zip(proj_keys, selected_projs): iter_params[k] = v
            filters.append(f"j.research_project IN ({', '.join([':'+k for k in proj_keys])})")
            
        if selected_parts:
            part_keys = [f"i_part_{i}" for i in range(len(selected_parts))]
            for k, v in zip(part_keys, selected_parts): iter_params[k] = v
            filters.append(f"COALESCE(j.custom_part_name, p.part_code) IN ({', '.join([':'+k for k in part_keys])})")
        
        if filters:
            job_iteration_query += " WHERE " + " AND ".join(filters)
        
        job_iteration_query += " ORDER BY COALESCE(j.custom_part_name, p.part_code), j.start_time ASC"
        job_iter_df = load_data(job_iteration_query, params=iter_params)
    
        job_options = []
        job_id_mapping = {}
        if not job_iter_df.empty:
            job_iter_df['iteration'] = job_iter_df.groupby('part_name').cumcount() + 1
            for _, row in job_iter_df.iterrows():
                label = f"{row['part_name']} - {row['iteration']}차 가공 (Job {row['job_id']})"
                job_options.append(label)
                job_id_mapping[label] = row['job_id']
            
        selected_job_labels = st.multiselect("3. 가공 순서 (N차 가공)", job_options, default=[])
        selected_jobs = [job_id_mapping[l] for l in selected_job_labels]
    
        # 4. Date Range Filter
        date_query = "SELECT MIN(DATE(start_time)) as min_date, MAX(DATE(start_time)) as max_date FROM job WHERE start_time IS NOT NULL"
        date_df = load_data(date_query)
        min_date = date_df.iloc[0]['min_date'] if not date_df.empty and pd.notnull(date_df.iloc[0]['min_date']) else None
        max_date = date_df.iloc[0]['max_date'] if not date_df.empty and pd.notnull(date_df.iloc[0]['max_date']) else None
    
        selected_dates = []
        if min_date and max_date:
            if isinstance(min_date, str):
                min_date = datetime.datetime.strptime(min_date, '%Y-%m-%d').date()
                max_date = datetime.datetime.strptime(max_date, '%Y-%m-%d').date()
            selected_dates = st.date_input("4. 실험 일자 범위", value=(min_date, max_date), min_value=min_date, max_value=max_date)

        # 5. Machining Type (Optional)
        mach_query = "SELECT DISTINCT machining_type FROM job WHERE machining_type IS NOT NULL AND machining_type != ''"
        mach_df = load_data(mach_query)
        machs = mach_df['machining_type'].tolist() if not mach_df.empty else []
        selected_machs = st.multiselect("5. 가공 종류 (선택사항)", machs, default=[])

        st.divider()
        st.header("Job 직접 검색 (빠른 편집)")
        direct_job_id = st.number_input("Job ID 직접 입력 (0이면 위 필터 사용)", min_value=0, value=0, step=1)

if nav_menu == "데이터 수정":
    # --- Main Layout: Admin ---
    st.subheader("실험 메타데이터 및 환경/품질 점검 단일 Job 통합 수정")

    conditions = []
    main_params = {}
    
    if direct_job_id > 0:
        conditions.append(f"j.job_id = :direct_job_id")
        main_params['direct_job_id'] = direct_job_id
    else:
        if selected_jobs:
            job_keys = [f"m_job_{i}" for i in range(len(selected_jobs))]
            for k, v in zip(job_keys, selected_jobs): main_params[k] = v
            conditions.append(f"j.job_id IN ({', '.join([':'+k for k in job_keys])})")
        elif selected_parts:
            part_keys = [f"m_part_{i}" for i in range(len(selected_parts))]
            for k, v in zip(part_keys, selected_parts): main_params[k] = v
            conditions.append(f"COALESCE(j.custom_part_name, p.part_code) IN ({', '.join([':'+k for k in part_keys])})")
        
        if selected_dates and len(selected_dates) == 2:
            start_dt = selected_dates[0].strftime('%Y-%m-%d 00:00:00')
            end_dt = selected_dates[1].strftime('%Y-%m-%d 23:59:59')
            conditions.append(f"(j.start_time BETWEEN :start_dt AND :end_dt OR j.start_time IS NULL)")
            main_params['start_dt'] = start_dt
            main_params['end_dt'] = end_dt
        
    where_clause = " AND ".join(conditions) if conditions else "1=1"

    job_query = f'''
        SELECT j.job_id, COALESCE(j.custom_part_name, p.part_code) as part_name, j.start_time 
        FROM job j
        JOIN workplan w ON j.workplan_id = w.workplan_id
        JOIN part p ON w.part_code = p.part_code
        WHERE {where_clause} 
        ORDER BY j.start_time DESC LIMIT 100
    '''
    target_jobs_df = load_data(job_query, params=main_params)

    if target_jobs_df.empty:
        st.info("검색된 Job이 없습니다. 좌측 필터를 변경해주세요.")
        st.stop()

    # 1. Job Selection
    target_job_options = target_jobs_df['job_id'].tolist()
    def format_job_option(jid):
        row = target_jobs_df[target_jobs_df['job_id'] == jid].iloc[0]
        st_time = row['start_time']
        pname = row['part_name']
        return f"Job {jid} - {pname} (시작시간: {st_time})"

    selected_job_id = st.selectbox("수정할 대상 Job 선택", target_job_options, format_func=format_job_option)

    st.divider()

    if selected_job_id:
        # Load specific job details
        job_detail_query = f'''
            SELECT j.job_id, j.source_folder, j.start_time, j.workplan_id, 
                   COALESCE(j.custom_part_name, p.part_code) as custom_part_name, 
                   j.research_project, j.machining_type 
            FROM job j
            JOIN workplan w ON j.workplan_id = w.workplan_id
            JOIN part p ON w.part_code = p.part_code
            WHERE j.job_id = {selected_job_id}
        '''
        job_detail_df = load_data(job_detail_query)
    
        if not job_detail_df.empty:
            job_row = job_detail_df.iloc[0]
        
            tab1, tab2, tab3 = st.tabs(["📝 Job 메타데이터", "🌡️ 환경 및 메모 (EnvMemo)", "🔍 품질 점검 (Inspection)"])
        
            with tab1:
                st.write(f"**Job ID: {job_row['job_id']} / 폴더: {job_row['source_folder']} / 시작시간: {job_row['start_time']}**")
                with st.form("job_meta_form"):
                    cpn_input = st.text_input("사용자 지정 Part 명", value=job_row['custom_part_name'] if pd.notnull(job_row['custom_part_name']) else "")
                    rp_input = st.text_input("연구 프로젝트 명", value=job_row['research_project'] if pd.notnull(job_row['research_project']) else "")
                    mt_input = st.text_input("가공 종류", value=job_row['machining_type'] if pd.notnull(job_row['machining_type']) else "")
                
                    if st.form_submit_button("메타데이터 저장"):
                        with engine.connect() as conn:
                            stmt = text("UPDATE job SET custom_part_name=:cpn, research_project=:rp, machining_type=:mt WHERE job_id=:jid")
                            conn.execute(stmt, {"cpn": cpn_input if cpn_input else None, "rp": rp_input if rp_input else None, "mt": mt_input if mt_input else None, "jid": selected_job_id})
                            conn.commit()
                        st.success("메타데이터가 업데이트되었습니다.")
                        st.cache_data.clear()
                        st.rerun()
                    
            with tab2:
                env_query = f"SELECT * FROM env_memo WHERE job_id = {selected_job_id}"
                env_df = load_data(env_query)
                env_row = env_df.iloc[0] if not env_df.empty else None
            
                with st.form("env_memo_form"):
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
                        
                            conn.execute(stmt, {"wn": wn or None, "temp": temp_parsed, "hum": hum_parsed, "dow": dow or None, "cs": cs or None, "an": an or None, "fm": fm or None, "jid": selected_job_id})
                            conn.commit()
                        st.success("EnvMemo가 성공적으로 저장되었습니다.")
                        st.cache_data.clear()
                        st.rerun()
                    
            with tab3:
                ins_query = f"SELECT * FROM inspection WHERE job_id = {selected_job_id}"
                ins_df = load_data(ins_query)
                ins_row = ins_df.iloc[0] if not ins_df.empty else None
            
                with st.form("inspection_form"):
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
                        
                            conn.execute(stmt, {"dt": dt or None, "sra": sra_parsed, "srz": srz_parsed, "sa": sa or None, "pf": pf or None, "jid": selected_job_id})
                            conn.commit()
                        st.success("Inspection이 성공적으로 저장되었습니다.")
                        st.cache_data.clear()
                        st.rerun()

    st.divider()
    st.subheader("가공 이력(Job) 데이터 삭제 (DB)")
    st.warning("경고: 삭제된 데이터는 복구할 수 없습니다. DB 연쇄 삭제(Cascade) 규칙에 따라 센서, 조도, 파일 아카이브 데이터도 함께 삭제됩니다.")

    @st.dialog("데이터 영구 삭제 확인")
    def confirm_delete_dialog(job_id):
        st.write(f"정말로 **Job ID {job_id}** 데이터를 삭제하시겠습니까?")
        st.write("이 작업은 되돌릴 수 없습니다.")
        if st.button("예, 삭제합니다.", type="primary"):
            from sqlalchemy import text
            with engine.connect() as conn:
                try:
                    stmt = text("DELETE FROM job WHERE job_id = :jid")
                    result = conn.execute(stmt, {"jid": job_id})
                    conn.commit()
                    if result.rowcount > 0:
                        st.success(f"Job ID {job_id} 데이터가 성공적으로 삭제되었습니다.")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"Job ID {job_id}를 찾을 수 없습니다.")
                except Exception as e:
                    st.error(f"삭제 중 오류 발생: {e}")

    if selected_job_id:
        if st.button(f"현재 선택된 Job (ID {selected_job_id}) 영구 삭제", type="primary"):
            confirm_delete_dialog(selected_job_id)

    st.divider()
    st.subheader("시스템 데이터 복구 및 내보내기 (Disaster Recovery)")
    st.info("DB에 저장된 모든 원본 파일(XML, NC, CAD, Parquet, 로그, 조도)을 물리적 폴더 구조로 재구성하여 ZIP 파일로 다운로드합니다.")
    def file_iterator(file_path, chunk_size=65536):
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    if st.button("복구 파일(ZIP) 생성 시작", type="primary"):
        with st.spinner("원본 파일을 재구성 중입니다. 데이터 크기에 따라 시간이 걸릴 수 있습니다..."):
            try:
                from recovery_engine import create_recovery_zip
                zip_path = create_recovery_zip()
                st.session_state['admin_recovery_zip_path'] = zip_path
            except Exception as e:
                st.error(f"복구 중 오류 발생: {e}")

    if 'admin_recovery_zip_path' in st.session_state:
        with open(st.session_state['admin_recovery_zip_path'], "rb") as f:
            st.download_button(
                label="복구 데이터 다운로드 (ZIP)",
                data=f,
                file_name="recovered_data.zip",
                mime="application/zip"
            )

elif nav_menu == "가공 검색":
    st.subheader("조건별 실험 검색 및 Job 찾기")
    
    with st.container():
        c1, c2, c3 = st.columns(3)
        with c1:
            proj_query = "SELECT DISTINCT research_project FROM job WHERE research_project IS NOT NULL AND research_project != ''"
            projs_df = load_data(proj_query)
            projs = projs_df['research_project'].tolist() if not projs_df.empty else []
            selected_projs = st.multiselect("1. 연구 프로젝트 명", projs, default=[p for p in st.session_state.selected_projs if p in projs])
            st.session_state.selected_projs = selected_projs
            
        with c2:
            part_query = """
                SELECT DISTINCT COALESCE(j.custom_part_name, p.part_code) AS display_part 
                FROM job j 
                JOIN workplan w ON j.workplan_id = w.workplan_id 
                JOIN part p ON w.part_code = p.part_code
            """
            formatted_projs = ""
            if selected_projs:
                formatted_projs = ', '.join([f"'{p.replace(chr(39), chr(39)+chr(39))}'" for p in selected_projs])
                part_query += f" WHERE j.research_project IN ({formatted_projs})"
            part_query += " ORDER BY display_part"
            
            part_codes_df = load_data(part_query)
            part_codes = part_codes_df['display_part'].tolist() if not part_codes_df.empty else []
            selected_parts = st.multiselect("2. Part 명 (사용자 지정 우선)", part_codes, default=[p for p in st.session_state.selected_parts if p in part_codes])
            st.session_state.selected_parts = selected_parts
            
        with c3:
            job_iteration_query = """
                SELECT j.job_id, COALESCE(j.custom_part_name, p.part_code) AS part_name, j.start_time 
                FROM job j
                JOIN workplan w ON j.workplan_id = w.workplan_id 
                JOIN part p ON w.part_code = p.part_code
            """
            filters = []
            if selected_projs:
                filters.append(f"j.research_project IN ({formatted_projs})")
            if selected_parts:
                formatted_parts = ', '.join([f"'{p.replace(chr(39), chr(39)+chr(39))}'" for p in selected_parts])
                filters.append(f"COALESCE(j.custom_part_name, p.part_code) IN ({formatted_parts})")
                
            if filters:
                job_iteration_query += " WHERE " + " AND ".join(filters)
                
            job_iteration_query += " ORDER BY COALESCE(j.custom_part_name, p.part_code), j.start_time ASC"
            job_iter_df = load_data(job_iteration_query)
            
            job_options = []
            job_id_mapping = {}
            if not job_iter_df.empty:
                job_iter_df['iteration'] = job_iter_df.groupby('part_name').cumcount() + 1
                for _, row in job_iter_df.iterrows():
                    label = f"{row['part_name']} - {row['iteration']}차 가공 (Job {row['job_id']})"
                    job_options.append(label)
                    job_id_mapping[label] = row['job_id']
                    
            selected_job_labels = st.multiselect("3. 가공 순서 (N차 가공)", job_options, default=[l for l in st.session_state.selected_job_labels if l in job_options])
            st.session_state.selected_job_labels = selected_job_labels
            selected_jobs = [job_id_mapping[l] for l in selected_job_labels]

        c4, c5 = st.columns([2, 1])
        with c4:
            date_query = "SELECT MIN(DATE(start_time)) as min_date, MAX(DATE(start_time)) as max_date FROM job WHERE start_time IS NOT NULL"
            date_df = load_data(date_query)
            min_date = date_df.iloc[0]['min_date'] if not date_df.empty and pd.notnull(date_df.iloc[0]['min_date']) else None
            max_date = date_df.iloc[0]['max_date'] if not date_df.empty and pd.notnull(date_df.iloc[0]['max_date']) else None
            
            if min_date and max_date:
                if isinstance(min_date, str):
                    min_date = datetime.datetime.strptime(min_date, '%Y-%m-%d').date()
                    max_date = datetime.datetime.strptime(max_date, '%Y-%m-%d').date()
                default_dates = st.session_state.selected_dates if st.session_state.selected_dates else (min_date, max_date)
                selected_dates = st.date_input("4. 실험 일자 범위", value=default_dates, min_value=min_date, max_value=max_date)
                st.session_state.selected_dates = selected_dates
            else:
                selected_dates = []

        with c5:
            mach_query = "SELECT DISTINCT machining_type FROM job WHERE machining_type IS NOT NULL AND machining_type != ''"
            mach_df = load_data(mach_query)
            machs = mach_df['machining_type'].tolist() if not mach_df.empty else []
            selected_machs = st.multiselect("5. 가공 종류 (선택사항)", machs, default=[m for m in st.session_state.selected_machs if m in machs])
            st.session_state.selected_machs = selected_machs

    with st.expander("⚙️ 고급 검색 필터", expanded=False):
        st.caption("아래 조건들을 설정하면 더 정밀하게 데이터를 필터링할 수 있습니다. (0이면 조건 무시)")
        adv_c1, adv_c2, adv_c3 = st.columns(3)
        with adv_c1:
            min_cut_sec = st.number_input("최소 가공 시간 (초)", value=0, min_value=0)
            max_cut_sec = st.number_input("최대 가공 시간 (초)", value=0, min_value=0)
        with adv_c2:
            min_ra = st.number_input("최소 표면 조도 Ra (μm)", value=0.0, min_value=0.0, step=0.1)
            max_ra = st.number_input("최대 표면 조도 Ra (μm)", value=0.0, min_value=0.0, step=0.1)
        with adv_c3:
            max_rpm = st.number_input("최대 평균 RPM", value=0, min_value=0)
            min_rpm = st.number_input("최소 평균 RPM", value=0, min_value=0)

    st.divider()
    
    # Build query for the dataframe
    conditions = []
    if selected_projs:
        conditions.append(f"j.research_project IN ({formatted_projs})")
    if selected_machs:
        formatted_machs = ', '.join([f"'{m.replace(chr(39), chr(39)+chr(39))}'" for m in selected_machs])
        conditions.append(f"j.machining_type IN ({formatted_machs})")
        
    if selected_jobs:
        conditions.append(f"j.job_id IN ({','.join(map(str, selected_jobs))})")
    elif selected_parts:
        formatted_parts = ', '.join([f"'{p.replace(chr(39), chr(39)+chr(39))}'" for p in selected_parts])
        conditions.append(f"COALESCE(j.custom_part_name, p.part_code) IN ({formatted_parts})")
        
    if selected_dates and len(selected_dates) == 2:
        start_dt = selected_dates[0].strftime('%Y-%m-%d 00:00:00')
        end_dt = selected_dates[1].strftime('%Y-%m-%d 23:59:59')
        conditions.append(f"(j.start_time BETWEEN '{start_dt}' AND '{end_dt}' OR j.start_time IS NULL)")
        
    if min_cut_sec > 0:
        conditions.append(f"j.cutting_seconds >= {min_cut_sec}")
    if max_cut_sec > 0:
        conditions.append(f"j.cutting_seconds <= {max_cut_sec}")
    if min_ra > 0.0:
        conditions.append(f"i.surface_roughness_ra >= {min_ra}")
    if max_ra > 0.0:
        conditions.append(f"i.surface_roughness_ra <= {max_ra}")
    if min_rpm > 0:
        conditions.append(f"m.avg_spindle_rpm >= {min_rpm}")
    if max_rpm > 0:
        conditions.append(f"m.avg_spindle_rpm <= {max_rpm}")
        
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    job_query = f"""
        SELECT 
            j.job_id, j.start_time, j.research_project, COALESCE(j.custom_part_name, p.part_code) as part_name, j.machining_type,
            j.cutting_seconds, j.is_finish,
            i.surface_roughness_ra, i.surface_roughness_rz, i.pass_fail,
            e.worker_name, e.temperature, e.humidity, e.free_memo,
            m.max_spindle_load, m.avg_spindle_rpm, m.alarm_count
        FROM job j
        LEFT JOIN machine_log m ON j.job_id = m.job_id
        LEFT JOIN inspection i ON j.job_id = i.job_id
        LEFT JOIN env_memo e ON j.job_id = e.job_id
        JOIN workplan w ON j.workplan_id = w.workplan_id
        JOIN part p ON w.part_code = p.part_code
        WHERE {where_clause}
        ORDER BY j.start_time DESC LIMIT 100;
    """
    job_df = load_data(job_query)
    
    if job_df.empty:
        st.warning("조건에 맞는 가공 이력(Job)이 없습니다.")
    else:
        st.markdown("#### 📋 필터링된 Job 전체 메타데이터 목록")
        st.caption("가로로 스크롤하여 환경 메모, 품질 검사 등 관련된 모든 정보를 엑셀처럼 한눈에 확인할 수 있습니다.")
        st.dataframe(
            job_df, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "job_id": st.column_config.NumberColumn("Job ID", format="%d"),
                "start_time": st.column_config.DatetimeColumn("시작 시간", format="YYYY-MM-DD HH:mm"),
                "research_project": "프로젝트",
                "part_name": "Part 명",
                "machining_type": "가공 종류",
                "cutting_seconds": st.column_config.NumberColumn("가공 시간(초)", format="%.1f"),
                "is_finish": "완료",
                "surface_roughness_ra": st.column_config.NumberColumn("조도 Ra(μm)", format="%.3f"),
                "surface_roughness_rz": st.column_config.NumberColumn("조도 Rz(μm)", format="%.3f"),
                "pass_fail": "합불 판정",
                "worker_name": "작업자",
                "temperature": st.column_config.NumberColumn("온도(°C)", format="%.1f"),
                "humidity": st.column_config.NumberColumn("습도(%)", format="%.1f"),
                "free_memo": "작업자 메모",
                "max_spindle_load": st.column_config.NumberColumn("최대 부하(%)", format="%.1f"),
                "avg_spindle_rpm": st.column_config.NumberColumn("평균 RPM", format="%.0f"),
                "alarm_count": st.column_config.NumberColumn("알람 수", format="%d")
            }
        )



elif nav_menu == "데이터 삽입":
    st.subheader("외부 데이터 삽입")
    st.markdown("로컬에 있는 가공 데이터를 직접 업로드하여 파이프라인에 삽입합니다.")
    
    # CSS Injection to hide the default "10GB per file" text in file uploader
    st.markdown("""
        <style>
        [data-testid="stFileUploadDropzone"] small {
            display: none !important;
        }
        </style>
    """, unsafe_allow_html=True)
    
    base_raw_dir = os.path.join(PROJECT_ROOT, "machining_raw_data")
    os.makedirs(base_raw_dir, exist_ok=True)
    
    st.markdown("#### Step 1. 대상 Project 및 Part 설정")
    is_new_proj_part = st.radio("Project / Part 선택 방식", ["기존 항목 선택", "신규 생성"], horizontal=True)
    
    project_name = ""
    part_name = ""
    
    if is_new_proj_part == "신규 생성":
        col1, col2 = st.columns(2)
        with col1:
            project_name = st.text_input("연구 프로젝트 명 (Project Name)", placeholder="예: Alchemist")
        with col2:
            part_name = st.text_input("가공 Part 명 (Part Name)", placeholder="예: Bracket")
    else:
        all_projects = sorted([d for d in os.listdir(base_raw_dir) if os.path.isdir(os.path.join(base_raw_dir, d))])
        if not all_projects:
            st.warning("존재하는 Project가 없습니다. '신규 생성'을 선택해주세요.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                project_name = st.selectbox("추가할 Project 선택", [""] + all_projects)
            
            if project_name:
                proj_dir = os.path.join(base_raw_dir, project_name)
                all_parts = sorted([d for d in os.listdir(proj_dir) if os.path.isdir(os.path.join(proj_dir, d))])
                with col2:
                    part_name = st.selectbox("추가할 Part 선택", [""] + all_parts)
    
    st.markdown("---")
    st.markdown("#### Step 2. 데이터 업로드 타겟 선택")
    
    upload_target = st.radio("어느 레벨에 데이터를 업로드하시겠습니까?", [
        "Part 레벨 (CAD 파일 단독 업로드)", 
        "신규 Job 생성 및 데이터 업로드", 
        "기존 Job에 데이터 추가"
    ], index=1)
    
    existing_job_folder = None
    if upload_target == "기존 Job에 데이터 추가" and project_name and part_name:
        # DB에서 현재 Project/Part에 속하는 Job의 Primary Key(job_id) 목록 조회
        job_query = f"""
            SELECT j.job_id, j.source_folder 
            FROM job j
            LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
            LEFT JOIN part p ON w.part_code = p.part_code
            WHERE j.research_project = '{project_name}'
              AND COALESCE(j.custom_part_name, p.part_code) = '{part_name}'
        """
        job_df = load_data(job_query)
        if job_df.empty:
            st.warning("해당 Part에 등록된 DB Job 레코드가 없습니다. 먼저 '신규 Job 생성'으로 데이터를 추가하세요.")
        else:
            job_choices = job_df['job_id'].tolist()
            selected_job_id = st.selectbox("추가할 Job ID (DB 고유 식별자) 선택", [""] + job_choices)
            if selected_job_id:
                # DB의 source_folder를 가져와 물리적 경로로 역추적
                matched_row = job_df[job_df['job_id'] == selected_job_id].iloc[0]
                sf = matched_row['source_folder']
                if sf:
                    # source_folder는 "Project/Part/JobID" 형식이므로 마지막 JobID 폴더명만 추출
                    # existing_job_folder는 이후 로직에서 os.path.join(base, proj, part, existing) 형태로 합쳐짐
                    existing_job_folder = sf.split('/')[-1]
                else:
                    st.error("선택한 Job의 원본 폴더 경로(source_folder)가 DB에 기록되지 않아 추가할 수 없습니다.")

    st.markdown("---")
    st.markdown("#### Step 3. 파일 업로드")
    
    file_xml = None
    file_tdms = None
    file_nc = None
    file_log = None
    file_cad = None
    file_roughness = None
    file_others = None

    if upload_target == "Part 레벨 (CAD 파일 단독 업로드)":
        st.caption("CAD 파일은 Job 단위가 아니라 Part 단위에 귀속됩니다. (최대 15MB 제한)")
        file_cad = st.file_uploader("CAD 파일 업로드 (STL/STEP) (1개) - 15MB 제한", type=["stl", "step", "stp"], accept_multiple_files=False)
    else:
        st.caption("XML, NC 파일은 최대 15MB 제한, 기타 파일은 제한이 없습니다.")
        file_xml = st.file_uploader("XML 파일 업로드 (1개) - 15MB 제한", type=["xml"], accept_multiple_files=False)
        file_tdms = st.file_uploader("TDMS 파일 업로드 (1개)", type=["tdms"], accept_multiple_files=False)
        file_nc = st.file_uploader("NC 파일 업로드 (1개) - 15MB 제한", type=["nc", "txt"], accept_multiple_files=False)
        file_log = st.file_uploader("로그(Log) 파일 업로드 (다중 선택 가능)", type=["log", "txt"], accept_multiple_files=True)
        file_roughness = st.file_uploader("표면 조도 데이터 업로드 (다중 선택 가능)", type=["csv", "fpk", "txt"], accept_multiple_files=True)
        file_others = st.file_uploader("기타 참고용 파일 업로드 (다중 선택 가능)", accept_multiple_files=True)

    submit_btn = st.button("업로드 및 파이프라인 전송", type="primary")
    
    if submit_btn:
        def check_size(file_obj, max_mb):
            if file_obj is None: return True
            if isinstance(file_obj, list):
                for f_obj in file_obj:
                    if f_obj.size > max_mb * 1024 * 1024: return False
                return True
            else:
                if file_obj.size > max_mb * 1024 * 1024: return False
                return True
                
        # 유효성 검사
        if not project_name or not project_name.strip() or not part_name or not part_name.strip():
            st.error("오류: Project 이름과 Part 이름을 모두 지정해주세요.")
        elif not check_size(file_xml, 15) or not check_size(file_nc, 15) or not check_size(file_cad, 15):
            st.error("오류: XML, NC, CAD 파일은 15MB를 초과할 수 없습니다.")
        elif upload_target == "기존 Job에 데이터 추가" and not existing_job_folder:
            st.error("오류: 데이터가 추가될 기존 Job을 선택해주세요.")
        elif upload_target == "Part 레벨 (CAD 파일 단독 업로드)" and not file_cad:
            st.error("오류: 업로드할 CAD 파일을 선택해주세요.")
        elif upload_target != "Part 레벨 (CAD 파일 단독 업로드)" and not any([file_xml, file_tdms, file_nc, file_log, file_roughness, file_others]):
            st.error("오류: 업로드할 파일을 최소 한 개 이상 선택해주세요.")
        elif upload_target == "신규 Job 생성 및 데이터 업로드" and not any([file_xml, file_tdms, file_nc, file_log, file_roughness]) and file_others:
            st.error("오류: 기타 참고용 파일만으로는 새로운 Job을 생성할 수 없습니다. 핵심 데이터(XML, NC 등)를 포함해주세요.")
        else:
            project_clean = project_name.strip()
            part_clean = part_name.strip()
            
            target_job_dir = ""
            cad_dir = os.path.join(base_raw_dir, project_clean, part_clean, "CAD_Files")
            
            # Job 폴더 결정
            if upload_target == "신규 Job 생성 및 데이터 업로드":
                part_dir = os.path.join(base_raw_dir, project_clean, part_clean)
                os.makedirs(part_dir, exist_ok=True)
                max_num = 0
                for d in os.listdir(part_dir):
                    if os.path.isdir(os.path.join(part_dir, d)) and d.isdigit():
                        max_num = max(max_num, int(d))
                new_num = max_num + 1
                job_number_str = f"{new_num:03d}"
                target_job_dir = os.path.join(part_dir, job_number_str)
                os.makedirs(target_job_dir, exist_ok=True)
            elif upload_target == "기존 Job에 데이터 추가":
                target_job_dir = os.path.join(base_raw_dir, project_clean, part_clean, existing_job_folder)
                os.makedirs(target_job_dir, exist_ok=True)
            
            # 실제 파일 저장 로직
            def save_file(uploaded_file, dest_dir):
                if uploaded_file:
                    fpath = os.path.join(dest_dir, uploaded_file.name)
                    with open(fpath, "wb") as f_out:
                        f_out.write(uploaded_file.getbuffer())
                    return 1
                return 0
                
            saved_count = 0
            
            # CAD는 항상 Part 레벨에 저장
            if file_cad:
                os.makedirs(cad_dir, exist_ok=True)
                saved_count += save_file(file_cad, cad_dir)
            
            # 나머지 Job 레벨 파일 저장
            if target_job_dir:
                roughness_dir = os.path.join(target_job_dir, "Surface_Roughness")
                etc_dir = os.path.join(target_job_dir, "etc")
                
                saved_count += save_file(file_xml, target_job_dir)
                saved_count += save_file(file_tdms, target_job_dir)
                saved_count += save_file(file_nc, target_job_dir)
                
                if file_log:
                    for uf in file_log:
                        saved_count += save_file(uf, target_job_dir)
                
                if file_roughness:
                    os.makedirs(roughness_dir, exist_ok=True)
                    for uf in file_roughness:
                        saved_count += save_file(uf, roughness_dir)
                        
                if file_others:
                    os.makedirs(etc_dir, exist_ok=True)
                    for uf in file_others:
                        saved_count += save_file(uf, etc_dir)
                        
            msg_dir = target_job_dir if target_job_dir else cad_dir
            st.success(f"성공적으로 {saved_count}개의 파일을 업로드했습니다! (메인 폴더: {msg_dir})")
            st.info("데이터가 계층형 폴더에 저장되었으며, 백엔드 Watchdog에 의해 파이프라인 처리가 시작됩니다.")
