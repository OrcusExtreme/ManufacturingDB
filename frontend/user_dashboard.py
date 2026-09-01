import streamlit as st
from streamlit_option_menu import option_menu
import pandas as pd
import os
import datetime
import sys
import zipfile
import io

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'backend'))
sys.path.append(os.path.dirname(__file__))

from DB.database import engine
from sqlalchemy import text
from recovery_engine import restore_single_job_to_raw_data, get_job_archive_files, create_single_job_zip
from cad_viewer_component import render_cad_viewer

def resolve_parquet_file(file_path, default_filename=None):
    """
    Parquet 파일의 실제 경로를 찾습니다.
    DB 경로 -> processed_data -> archive_vault 순으로 탐색
    """
    if file_path and os.path.exists(file_path):
        return file_path
    if default_filename:
        # 1. processed_data 탐색
        p1 = os.path.join(PROJECT_ROOT, "processed_data", default_filename)
        if os.path.exists(p1):
            return p1
        # 2. archive_vault 탐색
        p2 = os.path.join(PROJECT_ROOT, "archive_vault", "processed_parquet", default_filename)
        if os.path.exists(p2):
            return p2
        # 3. surface_roughness vault 탐색
        p3 = os.path.join(PROJECT_ROOT, "archive_vault", "surface_roughness", default_filename)
        if os.path.exists(p3):
            return p3
    return None


def get_download_data_from_folder(folder_path, categories):
    import tempfile
    import zipfile
    import os
    
    # Collect all matching files
    files_to_download = []
    
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            abs_path = os.path.join(root, file)
            rel_path = os.path.relpath(abs_path, folder_path)
            
            # Category filtering
            include = False
            if '전체' in categories:
                include = True
            else:
                l_path = abs_path.lower()
                if '표면 조도' in categories and ('surface' in l_path or '조도' in l_path or l_path.endswith('.fpk')):
                    include = True
                if 'tdms' in categories and (l_path.endswith('.tdms') or l_path.endswith('.tdms_index')):
                    include = True
                if 'nc 프로그램' in categories and (l_path.endswith('.xml') or l_path.endswith('.nc')):
                    include = True
                if 'parquet' in categories and ('parquet' in l_path):
                    include = True
                if '장비 로그' in categories and (l_path.endswith('.log') or l_path.endswith('.txt')):
                    include = True
                    
            if include:
                files_to_download.append((abs_path, rel_path))
                
    if not files_to_download:
        return None, None, None, False
        
    if len(files_to_download) == 1:
        # single file download
        abs_path, rel_path = files_to_download[0]
        file_name = os.path.basename(abs_path)
        # determine mime type
        mime = "application/octet-stream"
        if file_name.endswith('.xml'):
            mime = "text/xml"
        elif file_name.endswith('.txt') or file_name.endswith('.log'):
            mime = "text/plain"
        return abs_path, file_name, mime, False
        
    # multiple files -> zip
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_zip_path = temp_zip.name
    temp_zip.close()
    
    with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for abs_path, rel_path in files_to_download:
            zf.write(abs_path, rel_path)
    return temp_zip_path, "data_archive.zip", "application/zip", True

def file_iterator(file_path, chunk_size=65536):
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk

# Page Config

# Page Config
st.set_page_config(page_title="가공 데이터 대시보드 (사용자용)", layout="wide")



st.title("공작기계지능화실험실 제조 DB 대시보드")

@st.cache_data(ttl=60)
def load_data(query, params=None):
    from sqlalchemy import text
    if params:
        return pd.read_sql(text(query), con=engine, params=params)
    return pd.read_sql(text(query), con=engine)

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

# Init session states for filters so they persist across menu switches
if 'selected_projs' not in st.session_state: st.session_state.selected_projs = []
if 'selected_parts' not in st.session_state: st.session_state.selected_parts = []
if 'selected_job_labels' not in st.session_state: st.session_state.selected_job_labels = []
if 'selected_dates' not in st.session_state: st.session_state.selected_dates = []
if 'selected_machs' not in st.session_state: st.session_state.selected_machs = []
if 'target_job_id' not in st.session_state: st.session_state.target_job_id = None
if 'nav_menu' not in st.session_state: st.session_state.nav_menu = "가공 검색"

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
    
    options_list = ["계층형 마스터 데이터", "가공 검색", "가공 이력 & 센서 분석", "데이터 다운로드", "DB 테이블 조회"]
    
    if 'current_nav' not in st.session_state:
        st.session_state.current_nav = st.session_state.get('nav_menu', "가공 검색")
        
    default_idx = options_list.index(st.session_state.current_nav) if st.session_state.current_nav in options_list else 1
        
    nav_menu = option_menu(
        menu_title=None, 
        options=options_list, 
        icons=["diagram-3", "search", "graph-up", "cloud-download", "database"], 
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
    

@st.dialog("🖼️ CAD 모델 형상 이미지", width="large")
def open_cad_dialog(part_code, cad_file_name):
    st.markdown(f"**부품명(Part Code):** `{part_code}` &nbsp;|&nbsp; **CAD 파일명:** `{cad_file_name}`")
    render_cad_viewer(part_code, height=520)

if nav_menu == "계층형 마스터 데이터":
    st.subheader("계층형 구조 조회 (ISO 14649)")
    
    part_query = """
        SELECT DISTINCT COALESCE(j.custom_part_name, p.part_code) AS display_part 
        FROM part p
        LEFT JOIN workplan w ON p.part_code = w.part_code
        LEFT JOIN job j ON w.workplan_id = j.workplan_id
        ORDER BY display_part
    """
    part_codes_df = load_data(part_query)
    part_codes = [p for p in part_codes_df['display_part'].tolist() if pd.notnull(p)] if not part_codes_df.empty else []
    
    selected_parts = st.multiselect("조회할 Part 명 선택 (비워두면 기본 상위 5개 표시)", part_codes, default=[p for p in st.session_state.selected_parts if p in part_codes])
    st.session_state.selected_parts = selected_parts
    
    parts_to_show = selected_parts if selected_parts else part_codes[:5]
        
    for p_code in parts_to_show:
        with st.expander(f"Part: {p_code}", expanded=True):
            cad_query = f"""
                SELECT file_name, file_type, LENGTH(file_content) as file_size, file_path
                FROM cad_file_archive
                WHERE part_code = '{p_code}'
            """
            cad_df = load_data(cad_query)
            if not cad_df.empty:
                c_fname = cad_df.iloc[0]['file_name']
                c_ftype = str(cad_df.iloc[0]['file_type']).upper()
                
                col_c1, col_c2 = st.columns([3, 1])
                with col_c1:
                    st.markdown(f"**📐 연관 CAD 모델:** `{c_fname}` ({c_ftype})")
                with col_c2:
                    if st.button("🖼️ 형상 이미지 보기", key=f"btn_cad_modal_{p_code}", type="primary", use_container_width=True):
                        open_cad_dialog(p_code, c_fname)
                
                with st.expander("📄 CAD 파일 메타데이터 정보 보기", expanded=False):
                    st.dataframe(cad_df, width="stretch", hide_index=True)
                st.divider()

            wp_query = f"""
                SELECT DISTINCT w.workplan_id, w.program_code, w.nc_file_path 
                FROM workplan w
                JOIN job j ON w.workplan_id = j.workplan_id
                JOIN part p ON w.part_code = p.part_code
                WHERE COALESCE(j.custom_part_name, p.part_code) = '{p_code}'
            """
            part_wps = load_data(wp_query)
            
            if part_wps.empty:
                st.info("해당 조건에 맞는 Workplan이 없습니다.")
                continue
            
            for _, wp_row in part_wps.iterrows():
                wp_id = wp_row['workplan_id']
                # UI 표시 시, 사용자가 변경한 Part 이름(p_code)을 반영하여 동적 타이틀 생성
                with st.expander(f"Workplan: {p_code} - {wp_row['program_code']} (ID: {wp_id})", expanded=False):
                    st.write(f"**NC File Path:** `{wp_row['nc_file_path']}`")
                    
                    ws_query = f"""
                        SELECT ws.step_order AS '순서', ws.operation_type AS '작업(Op)', 
                               GROUP_CONCAT(f.feature_type SEPARATOR ', ') AS '형상(Feature)', 
                               GROUP_CONCAT(f.feature_name SEPARATOR ', ') AS '형상명',
                               ws.xml_tool_code AS '사용 공구', 
                               t.tool_type AS '공구종류', 
                               t.cutter_diameter AS '직경',
                               t.tool_teeth AS '날수'
                        FROM workingstep ws
                        LEFT JOIN workingstep_feature_link wfl ON ws.step_id = wfl.step_id
                        LEFT JOIN machining_feature f ON wfl.feature_id = f.feature_id
                        LEFT JOIN tool t ON ws.tool_id = t.tool_id
                        WHERE ws.workplan_id = '{wp_id}'
                        GROUP BY ws.step_id
                        ORDER BY ws.step_order
                    """
                    ws_df = load_data(ws_query)
                    
                    if not ws_df.empty:
                        st.markdown("##### 하위 가공 스텝 (Workingsteps)")
                        st.dataframe(ws_df, width="stretch", hide_index=True)
                    else:
                        st.info("등록된 Workingstep이 없습니다.")

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
            
            part_params = {}
            if selected_projs:
                proj_keys = [f"p_proj_{i}" for i in range(len(selected_projs))]
                for k, v in zip(proj_keys, selected_projs):
                    part_params[k] = v
                part_query += f" WHERE j.research_project IN ({', '.join([':'+k for k in proj_keys])})"
                
            part_query += " ORDER BY display_part"
            
            part_codes_df = load_data(part_query, params=part_params)
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
    main_params = {}
    
    if selected_projs:
        proj_keys = [f"m_proj_{i}" for i in range(len(selected_projs))]
        for k, v in zip(proj_keys, selected_projs): main_params[k] = v
        conditions.append(f"j.research_project IN ({', '.join([':'+k for k in proj_keys])})")
        
    if selected_machs:
        mach_keys = [f"m_mach_{i}" for i in range(len(selected_machs))]
        for k, v in zip(mach_keys, selected_machs): main_params[k] = v
        conditions.append(f"j.machining_type IN ({', '.join([':'+k for k in mach_keys])})")
        
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
        
    if min_cut_sec > 0:
        conditions.append(f"j.cutting_seconds >= :min_cut_sec")
        main_params['min_cut_sec'] = min_cut_sec
    if max_cut_sec > 0:
        conditions.append(f"j.cutting_seconds <= :max_cut_sec")
        main_params['max_cut_sec'] = max_cut_sec
    if min_ra > 0.0:
        conditions.append(f"i.surface_roughness_ra >= :min_ra")
        main_params['min_ra'] = min_ra
    if max_ra > 0.0:
        conditions.append(f"i.surface_roughness_ra <= :max_ra")
        main_params['max_ra'] = max_ra
    if min_rpm > 0:
        conditions.append(f"m.avg_spindle_rpm >= :min_rpm")
        main_params['min_rpm'] = min_rpm
    if max_rpm > 0:
        conditions.append(f"m.avg_spindle_rpm <= :max_rpm")
        main_params['max_rpm'] = max_rpm
        
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
    job_df = load_data(job_query, params=main_params)
    
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

elif nav_menu == "가공 이력 & 센서 분석":
    all_jobs_query = "SELECT job_id FROM job ORDER BY job_id DESC"
    all_jobs_df = load_data(all_jobs_query)
    all_job_ids = all_jobs_df['job_id'].tolist() if not all_jobs_df.empty else []
    
    if not all_job_ids:
        st.warning("등록된 가공 이력(Job)이 없습니다.")
    else:
        if not st.session_state.target_job_id or st.session_state.target_job_id not in all_job_ids:
            st.session_state.target_job_id = all_job_ids[0]
            
        target_job_id = st.session_state.target_job_id
        
        job_query = f"""
            SELECT j.job_id, j.source_folder, j.research_project, j.machining_type, COALESCE(j.custom_part_name, p.part_code) as custom_part_name, j.workplan_id, j.machine_code, j.start_time, j.is_finish, j.tdms_file_path, j.log_file_path, j.tdms_parquet_path, j.tdms_fft_parquet_path,
                   j.cutting_seconds, j.moving_distance, w.program_code,
                   m.max_spindle_load, m.avg_spindle_rpm, m.alarm_count
            FROM job j
            LEFT JOIN machine_log m ON j.job_id = m.job_id
            LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
            LEFT JOIN part p ON w.part_code = p.part_code
            WHERE j.job_id = {target_job_id}
        """
        job_df = load_data(job_query)
        
        if job_df.empty:
            st.error(f"Job ID {target_job_id} 정보를 찾을 수 없습니다.")
        else:
            job_info = job_df.iloc[0]
            
            c_title, c_select = st.columns([4, 1])
            with c_title:
                st.subheader(f"Job 상세 분석 (Job ID: {target_job_id})")
            with c_select:
                idx = all_job_ids.index(target_job_id)
                selected_target = st.selectbox("분석할 Job ID", all_job_ids, index=idx, label_visibility="collapsed")
                if selected_target != target_job_id:
                    st.session_state.target_job_id = selected_target
                    st.rerun()
            
            # --- Metrics Display ---
            part_name = job_info['custom_part_name']
            job_time = job_info['start_time']
            
            part_jobs_query = f'''
                SELECT j.start_time 
                FROM job j
                LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
                LEFT JOIN part p ON w.part_code = p.part_code
                WHERE COALESCE(j.custom_part_name, p.part_code) = '{part_name}'
                ORDER BY j.start_time ASC
            '''
            part_jobs_df = load_data(part_jobs_query)
            iteration = 1
            if not part_jobs_df.empty:
                for idx, (i, row) in enumerate(part_jobs_df.iterrows()):
                    if pd.notnull(job_time) and pd.notnull(row['start_time']) and row['start_time'] == job_time:
                        iteration = idx + 1
                        break
            
            # Card Layout for Dashboard
            dash_col1, dash_col2 = st.columns([1, 2])
            
            with dash_col1:
                # 1. Job Metadata Card
                with st.container(border=True):
                    st.markdown("##### 📄 Job 요약 정보")
                    proj_name = str(job_info['research_project'])
                    part_display = str(part_name)
                    date_str = str(job_info['start_time']).split('.')[0] if pd.notnull(job_info['start_time']) else "알 수 없음"
                    
                    st.write(f"**프로젝트:** {proj_name if proj_name and proj_name != 'None' else '없음'}")
                    st.write(f"**Part 이름:** {part_display if part_display and part_display != 'None' else '없음'}")
                    st.write(f"**가공 일시:** {date_str}")
                    st.write(f"**가공 순서:** {iteration} 번째")
                    st.divider()
                    nc_code = str(job_info['program_code'])
                    st.write(f"**NC 코드:** {nc_code if nc_code and nc_code != 'None' and nc_code != 'nan' else '없음'}")
                    cs = job_info['cutting_seconds']
                    st.write(f"**가공 시간:** {f'{cs:.1f} 초' if pd.notnull(cs) else '데이터 없음'}")
                    md = job_info['moving_distance']
                    st.write(f"**이동 거리:** {f'{md:.1f} mm' if pd.notnull(md) else '데이터 없음'}")
                    
                    st.divider()
                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button("XML 원본 (팝업)", use_container_width=True):
                            show_xml_dialog(target_job_id)
                    with b2:
                        if st.button("NC 코드 (팝업)", use_container_width=True):
                            show_nc_dialog(job_info['workplan_id'])

                # 2. EXT Log Metrics Card
                with st.container(border=True):
                    st.markdown("##### ⚙️ CNC 로그 요약 (1Hz)")
                    log_path = job_info['log_file_path']
                    if pd.notnull(log_path):
                        try:
                            try:
                                log_data = pd.read_csv(log_path, sep='\t', encoding='utf-8')
                            except Exception:
                                log_data = pd.read_csv(log_path, sep='\t', encoding='cp949')
                                
                            if 'cls' in log_data.columns:
                                cls_max = log_data['cls'].max()
                                cls_avg = log_data['cls'].mean()
                                st.metric("Spindle Load (cls) Max", f"{cls_max:.2f} %", f"Avg: {cls_avg:.2f} %", delta_color="off")
                            
                            if 'crpm' in log_data.columns:
                                crpm_max = log_data['crpm'].max()
                                crpm_avg = log_data['crpm'].mean()
                                st.metric("Spindle RPM (crpm) Max", f"{crpm_max:.0f}", f"Avg: {crpm_avg:.0f}", delta_color="off")
                                
                            if 'cfr' in log_data.columns:
                                cfr_max = log_data['cfr'].max()
                                cfr_avg = log_data['cfr'].mean()
                                st.metric("Feed Rate (cfr) Max", f"{cfr_max:.0f}", f"Avg: {cfr_avg:.0f}", delta_color="off")
                        except Exception as ex:
                            st.error(f"로그 분석 오류: {ex}")
                    else:
                        st.info("연결된 로그 파일 없음")

                # 3. Surface Roughness Stats Card
                with st.container(border=True):
                    st.markdown("##### 📏 표면조도 통계")
                    sr_query = f"SELECT measure_name, ra, rq, rz, profile_parquet_path FROM surface_roughness WHERE job_id = {target_job_id}"
                    sr_df = load_data(sr_query)
                    if not sr_df.empty:
                        stats_df = sr_df[sr_df['ra'].notnull()][['measure_name', 'ra', 'rq', 'rz']]
                        if not stats_df.empty:
                            st.dataframe(stats_df, hide_index=True, width="stretch")
                        else:
                            st.info("수치 데이터가 없습니다.")
                    else:
                        st.info("조도 측정 데이터 없음")

                # 3.5 EnvMemo Card
                with st.container(border=True):
                    st.markdown("##### 🌡️ 환경 및 메모 (EnvMemo)")
                    env_query = f"SELECT * FROM env_memo WHERE job_id = {target_job_id}"
                    env_df = load_data(env_query)
                    if not env_df.empty:
                        env_row = env_df.iloc[0]
                        st.write(f"**작업자:** {env_row['worker_name'] if pd.notnull(env_row['worker_name']) else '-'}")
                        st.write(f"**온도/습도:** {env_row['temperature']} °C / {env_row['humidity']} %")
                        st.write(f"**요일:** {env_row['day_of_week'] if 'day_of_week' in env_row and pd.notnull(env_row['day_of_week']) else '-'}")
                        st.write(f"**칩 형태:** {env_row['chip_shape'] if 'chip_shape' in env_row and pd.notnull(env_row['chip_shape']) else '-'}")
                        st.write(f"**이상 소음:** {env_row['abnormal_noise'] if 'abnormal_noise' in env_row and pd.notnull(env_row['abnormal_noise']) else '-'}")
                        st.write(f"**수기 메모:** {env_row['free_memo'] if pd.notnull(env_row['free_memo']) else '-'}")
                    else:
                        st.info("환경 및 메모 데이터 없음")

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
                    parquet_path = resolve_parquet_file(job_info['tdms_parquet_path'], f"job_{target_job_id}_viz.parquet")
                    if parquet_path:
                        try:
                            tdms_data = pd.read_parquet(parquet_path)
                            st.caption(f"데이터 로드 완료: {len(tdms_data)} 행 (경로: {os.path.basename(parquet_path)})")
                            
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
                                fft_path = resolve_parquet_file(job_info['tdms_fft_parquet_path'], f"job_{target_job_id}_fft.parquet")
                                if fft_path:
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
                    elif pd.notnull(job_info['tdms_file_path']):
                        st.info("TDMS 데이터는 존재하나 현재 서버에서 시각화 처리(Parquet 변환) 중입니다. 잠시 후 새로고침 해주세요.")
                    else:
                        st.info("연결된 TDMS 데이터 없음")

                # 5. Surface Profile Card
                with st.container(border=True):
                    st.markdown("##### 🔬 표면조도 프로파일 곡선")
                    if not sr_df.empty:
                        curves_df = sr_df[sr_df['profile_parquet_path'].notnull()]
                        if not curves_df.empty:
                            selected_curve = st.selectbox("단면 프로파일 선택", curves_df['measure_name'], label_visibility="collapsed")
                            curve_path = curves_df[curves_df['measure_name'] == selected_curve].iloc[0]['profile_parquet_path']
                            try:
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

elif nav_menu == "데이터 다운로드":
    all_jobs_query = "SELECT job_id FROM job ORDER BY job_id DESC"
    all_jobs_df = load_data(all_jobs_query)
    all_job_ids = all_jobs_df['job_id'].tolist() if not all_jobs_df.empty else []
    
    if not all_job_ids:
        st.warning("등록된 가공 이력(Job)이 없습니다.")
    else:
        if not st.session_state.target_job_id or st.session_state.target_job_id not in all_job_ids:
            st.session_state.target_job_id = all_job_ids[0]
            
        target_job_id = st.session_state.target_job_id
        
        st.subheader(f"데이터 다운로드 (Job ID: {target_job_id})")
        
        idx = all_job_ids.index(target_job_id)
        selected_target = st.selectbox("다운로드할 Job ID를 선택하세요:", all_job_ids, index=idx, key='dl_job_select')
        if selected_target != target_job_id:
            st.session_state.target_job_id = selected_target
            st.rerun()

        job_query = f"SELECT source_folder FROM job WHERE job_id = {target_job_id}"
        job_df = load_data(job_query)
        
        if job_df.empty:
            st.error("해당 Job 정보를 찾을 수 없습니다.")
        else:
            sf = job_df.iloc[0]['source_folder']
            sf_parts = sf.replace('\\', '/').split('/') if sf else []
            project_name = sf_parts[0] if len(sf_parts) > 0 else "Project"
            part_name = sf_parts[1].replace(' ', '') if len(sf_parts) > 1 else "Part"
            j_id = sf_parts[2] if len(sf_parts) > 2 else str(target_job_id)
            base_name = f"{project_name}_{part_name}_Job{j_id}"
            
            folder_path = os.path.join(PROJECT_ROOT, "machining_raw_data", *sf_parts) if sf_parts else None
            folder_exists = folder_path is not None and os.path.exists(folder_path)
            
            # Vault/DB 아카이브 파일 매핑 조회
            archive_map = get_job_archive_files(target_job_id)
            
            if not folder_exists:
                st.warning("⚠️ 해당 원본 폴더(machining_raw_data)가 로컬 디스크에서 유실/삭제된 상태입니다.\n\n"
                           "현재 **안전 보관소(Vault & DB Archive)**에서 실시간 스트리밍 다운로드가 가능하며, 아래 버튼을 눌러 로컬 디스크로 즉시 복원할 수도 있습니다.")
                c_res1, c_res2 = st.columns([1, 3])
                with c_res1:
                    if st.button("🔄 로컬 디스크로 원본 파일 자동 복원", key="btn_restore_disk", type="secondary"):
                        with st.spinner("Vault 및 DB로부터 파일을 복원하는 중입니다..."):
                            dest, count = restore_single_job_to_raw_data(target_job_id)
                            if count > 0:
                                st.success(f"총 {count}개의 원본 파일이 {dest}에 정상 복구되었습니다!")
                                st.rerun()
                            else:
                                st.error("복구할 수 있는 백업 파일이 Vault/DB에 존재하지 않습니다.")
            
            st.divider()
            st.markdown("#### 📦 전체 데이터 다운로드")
            st.write("해당 Job의 모든 원본 파일과 가공/분석 데이터를 한 번에 압축하여 다운로드합니다.")
            
            if st.button("전체 파일 압축 준비하기", type="primary", key="btn_all"):
                with st.spinner("전체 파일을 압축하는 중입니다..."):
                    if folder_exists:
                        data_path, fname, mime, is_temp = get_download_data_from_folder(folder_path, ['전체'])
                    else:
                        data_path = create_single_job_zip(target_job_id, ['전체'])
                        mime = "application/zip"
                        
                    if data_path:
                        st.session_state['dl_all_path'] = data_path
                        st.session_state['dl_all_fname'] = f"{base_name}_AllData.zip"
                        st.session_state['dl_all_mime'] = mime
                    else:
                        st.error("다운로드할 수 있는 원본/아카이브 파일이 없습니다.")
                        
            if 'dl_all_path' in st.session_state and st.session_state.get('dl_all_fname'):
                if os.path.exists(st.session_state['dl_all_path']):
                    st.success("전체 파일 압축이 완료되었습니다!")
                    with open(st.session_state['dl_all_path'], "rb") as f:
                        st.download_button(
                            label="📥 전체 다운로드 (ZIP)",
                            data=f,
                            file_name=st.session_state['dl_all_fname'],
                            mime=st.session_state['dl_all_mime']
                        )
                
            st.divider()
            st.markdown("#### 🎯 선택적 데이터 다운로드")
            st.write("원하는 카테고리의 데이터만 선별하여 다운로드합니다.")
            
            # 가용 카테고리 검사 (디스크 또는 아카이브)
            available_cats = set()
            if folder_exists:
                for root_dir, dirs, files in os.walk(folder_path):
                    for file in files:
                        l_path = file.lower()
                        if 'surface' in l_path or '조도' in l_path or l_path.endswith('.fpk'):
                            available_cats.add('표면 조도 (Surface Roughness)')
                        if l_path.endswith('.tdms'):
                            available_cats.add('고주파 센서 (tdms)')
                        if l_path.endswith('.nc'):
                            available_cats.add('NC 프로그램 (nc)')
                        if l_path.endswith('.xml') and 'coeff' not in l_path:
                            available_cats.add('메타데이터 (xml)')
                        if 'parquet' in l_path:
                            available_cats.add('Parquet 데이터 (parquet)')
                        if l_path.endswith('.log') or l_path.endswith('.csv'):
                            available_cats.add('장비 로그 (log)')
            else:
                if archive_map.get('roughness'): available_cats.add('표면 조도 (Surface Roughness)')
                if archive_map.get('tdms'): available_cats.add('고주파 센서 (tdms)')
                if archive_map.get('nc'): available_cats.add('NC 프로그램 (nc)')
                if archive_map.get('xml'): available_cats.add('메타데이터 (xml)')
                if archive_map.get('parquet'): available_cats.add('Parquet 데이터 (parquet)')
                if archive_map.get('log'): available_cats.add('장비 로그 (log)')
                        
            categories = [
                '표면 조도 (Surface Roughness)',
                '고주파 센서 (tdms)',
                'NC 프로그램 (nc)',
                '메타데이터 (xml)',
                'Parquet 데이터 (parquet)',
                '장비 로그 (log)'
            ]
            
            valid_categories = [c for c in categories if c in available_cats]
            selected_cats = []
            
            if not valid_categories:
                st.warning("다운로드할 수 있는 상세 데이터 파일이 원본 폴더 및 아카이브에 존재하지 않습니다.")
            else:
                selected_cats = st.multiselect("다운로드할 데이터 유형 선택 (여러 개 선택 가능):", valid_categories)
            
            if st.button("선택한 데이터 다운로드 준비하기", key="btn_selective"):
                if not selected_cats:
                    st.warning("선택된 데이터가 없습니다.")
                else:
                    with st.spinner("파일을 준비하는 중입니다..."):
                        import tempfile
                        import zipfile
                        
                        dl_items = []
                        for cat in selected_cats:
                            cat_files = []
                            cat_eng = ""
                            cat_key = ""
                            if '표면 조도' in cat: cat_eng = "SurfaceRoughness"; cat_key = 'roughness'
                            elif '고주파' in cat: cat_eng = "TDMS"; cat_key = 'tdms'
                            elif 'NC' in cat: cat_eng = "NCProgram"; cat_key = 'nc'
                            elif '메타데이터' in cat: cat_eng = "XMLMetadata"; cat_key = 'xml'
                            elif 'Parquet' in cat: cat_eng = "ParquetData"; cat_key = 'parquet'
                            elif '로그' in cat: cat_eng = "EquipmentLog"; cat_key = 'log'
                            
                            if folder_exists:
                                for root, dirs, files in os.walk(folder_path):
                                    for file in files:
                                        abs_path = os.path.join(root, file)
                                        rel_path = os.path.relpath(abs_path, folder_path)
                                        l_path = abs_path.lower()
                                        
                                        include = False
                                        if cat == '표면 조도 (Surface Roughness)' and ('surface' in l_path or '조도' in l_path or l_path.endswith('.fpk')):
                                            include = True
                                        elif cat == '고주파 센서 (tdms)' and l_path.endswith('.tdms'):
                                            include = True
                                        elif cat == 'NC 프로그램 (nc)' and l_path.endswith('.nc'):
                                            include = True
                                        elif cat == '메타데이터 (xml)' and l_path.endswith('.xml') and 'coeff' not in l_path:
                                            include = True
                                        elif cat == 'Parquet 데이터 (parquet)' and 'parquet' in l_path:
                                            include = True
                                        elif cat == '장비 로그 (log)' and (l_path.endswith('.log') or l_path.endswith('.csv')):
                                            include = True
                                            
                                        if include:
                                            cat_files.append((abs_path, rel_path))
                            else:
                                for abs_p, bname, arcname in archive_map.get(cat_key, []):
                                    if os.path.exists(abs_p):
                                        cat_files.append((abs_p, arcname))
                                        
                            if not cat_files:
                                continue
                                
                            cat_short_name = cat.split(' (')[0]
                            if len(cat_files) == 1:
                                abs_path, rel_path = cat_files[0]
                                file_name = os.path.basename(abs_path)
                                mime = "application/octet-stream"
                                ext = os.path.splitext(file_name)[1]
                                
                                if file_name.endswith('.xml'): mime = "text/xml"
                                elif file_name.endswith('.txt') or file_name.endswith('.log') or file_name.endswith('.csv'): mime = "text/plain"
                                
                                dl_fname = f"{base_name}_{cat_eng}{ext}"
                                dl_items.append({
                                    'label': f"📥 {cat_short_name} 다운로드 ({file_name})",
                                    'path': abs_path,
                                    'fname': dl_fname,
                                    'mime': mime
                                })
                            else:
                                temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                                with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zf:
                                    for abs_path, rel_path in cat_files:
                                        zf.write(abs_path, arcname=rel_path)
                                        
                                dl_fname = f"{base_name}_{cat_eng}.zip"
                                dl_items.append({
                                    'label': f"📥 {cat_short_name} 다운로드 (ZIP)",
                                    'path': temp_zip.name,
                                    'fname': dl_fname,
                                    'mime': "application/zip"
                                })
                        
                        st.session_state['dl_sel_items'] = dl_items
                        
            if st.session_state.get('dl_sel_items'):
                st.success("선택하신 파일들이 준비되었습니다! 아래 버튼을 눌러 개별 다운로드하세요.")
                for idx, item in enumerate(st.session_state['dl_sel_items']):
                    if os.path.exists(item['path']):
                        with open(item['path'], "rb") as f:
                            st.download_button(
                                label=item['label'],
                                data=f,
                                file_name=item['fname'],
                                mime=item['mime'],
                                key=f"dl_btn_{sf}_{idx}"
                            )

elif nav_menu == "DB 테이블 조회":
    st.header("🗄️ DB 테이블 통합 조회")
    st.markdown("데이터베이스에 존재하는 모든 테이블의 데이터를 조회합니다.")
    
    from sqlalchemy import inspect
    
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    
    if not table_names:
        st.info("데이터베이스에 테이블이 존재하지 않습니다.")
    else:
        selected_table = st.selectbox("조회할 테이블을 선택하세요:", table_names)
        
        if selected_table:
            st.subheader(f"`{selected_table}` 테이블 데이터")
            
            try:
                with engine.connect() as conn:
                    # table_names are sourced from the DB schema itself, safe from SQL injection
                    query = f"SELECT * FROM `{selected_table}`"
                    df = pd.read_sql(text(query), conn)
                
                if df.empty:
                    st.warning("선택한 테이블에 데이터가 없습니다.")
                else:
                    st.dataframe(df, use_container_width=True)
                    st.caption(f"총 {len(df)} 행(Row)의 데이터가 조회되었습니다.")
            except Exception as e:
                st.error(f"데이터 조회 중 오류가 발생했습니다: {e}")
