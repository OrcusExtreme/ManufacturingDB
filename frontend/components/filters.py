import datetime

import pandas as pd
import streamlit as st

from .common import load_data


def _init_state(key_prefix):
    defaults = {
        f"{key_prefix}_projs": [],
        f"{key_prefix}_parts": [],
        f"{key_prefix}_job_labels": [],
        f"{key_prefix}_dates": [],
        f"{key_prefix}_machs": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def render_search_filters(key_prefix="ws"):
    """5단계 캐스케이딩 필터(프로젝트/Part/N차가공/일자/가공종류) + 고급 필터를 렌더링하고,
    조건에 맞는 Job 목록 DataFrame과 선택된 값들을 반환한다.

    Returns:
        dict with keys: job_df, selected_projs, selected_parts, selected_jobs,
        selected_job_labels, selected_dates, selected_machs
    """
    _init_state(key_prefix)
    k_projs = f"{key_prefix}_projs"
    k_parts = f"{key_prefix}_parts"
    k_labels = f"{key_prefix}_job_labels"
    k_dates = f"{key_prefix}_dates"
    k_machs = f"{key_prefix}_machs"

    with st.container():
        c1, c2, c3 = st.columns(3)
        with c1:
            proj_query = "SELECT DISTINCT research_project FROM job WHERE research_project IS NOT NULL AND research_project != ''"
            projs_df = load_data(proj_query)
            projs = projs_df['research_project'].tolist() if not projs_df.empty else []
            selected_projs = st.multiselect(
                "1. 연구 프로젝트 명", projs,
                default=[p for p in st.session_state[k_projs] if p in projs],
                key=f"{key_prefix}_widget_projs",
            )
            st.session_state[k_projs] = selected_projs

        with c2:
            part_query = """
                SELECT DISTINCT COALESCE(j.custom_part_name, p.part_name) AS display_part
                FROM job j
                JOIN workplan w ON j.workplan_id = w.workplan_id
                JOIN part p ON w.part_code = p.part_code
            """
            part_params = {}
            if selected_projs:
                proj_keys = [f"p_proj_{i}" for i in range(len(selected_projs))]
                for k, v in zip(proj_keys, selected_projs):
                    part_params[k] = v
                part_query += f" WHERE j.research_project IN ({', '.join([':' + k for k in proj_keys])})"
            part_query += " ORDER BY display_part"

            part_codes_df = load_data(part_query, params=part_params)
            part_codes = part_codes_df['display_part'].tolist() if not part_codes_df.empty else []
            selected_parts = st.multiselect(
                "2. Part 명 (사용자 지정 우선)", part_codes,
                default=[p for p in st.session_state[k_parts] if p in part_codes],
                key=f"{key_prefix}_widget_parts",
            )
            st.session_state[k_parts] = selected_parts

        with c3:
            job_iteration_query = """
                SELECT j.job_id, COALESCE(j.custom_part_name, p.part_name) AS part_name, j.start_time
                FROM job j
                JOIN workplan w ON j.workplan_id = w.workplan_id
                JOIN part p ON w.part_code = p.part_code
            """
            filters_sql = []
            iter_params = {}
            if selected_projs:
                proj_keys = [f"i_proj_{i}" for i in range(len(selected_projs))]
                for k, v in zip(proj_keys, selected_projs):
                    iter_params[k] = v
                filters_sql.append(f"j.research_project IN ({', '.join([':' + k for k in proj_keys])})")
            if selected_parts:
                part_keys = [f"i_part_{i}" for i in range(len(selected_parts))]
                for k, v in zip(part_keys, selected_parts):
                    iter_params[k] = v
                filters_sql.append(f"COALESCE(j.custom_part_name, p.part_name) IN ({', '.join([':' + k for k in part_keys])})")

            if filters_sql:
                job_iteration_query += " WHERE " + " AND ".join(filters_sql)
            job_iteration_query += " ORDER BY COALESCE(j.custom_part_name, p.part_name), j.start_time ASC"
            job_iter_df = load_data(job_iteration_query, params=iter_params)

            job_options = []
            job_id_mapping = {}
            if not job_iter_df.empty:
                job_iter_df['iteration'] = job_iter_df.groupby('part_name').cumcount() + 1
                for _, row in job_iter_df.iterrows():
                    label = f"{row['part_name']} - {row['iteration']}차 가공 (Job {row['job_id']})"
                    job_options.append(label)
                    job_id_mapping[label] = row['job_id']

            selected_job_labels = st.multiselect(
                "3. 가공 순서 (N차 가공)", job_options,
                default=[l for l in st.session_state[k_labels] if l in job_options],
                key=f"{key_prefix}_widget_labels",
            )
            st.session_state[k_labels] = selected_job_labels
            selected_jobs = [job_id_mapping[l] for l in selected_job_labels]

        c4, c5 = st.columns([2, 1])
        with c4:
            date_query = "SELECT MIN(DATE(start_time)) as min_date, MAX(DATE(start_time)) as max_date FROM job WHERE start_time IS NOT NULL"
            date_df = load_data(date_query)
            min_date = date_df.iloc[0]['min_date'] if not date_df.empty and pd.notnull(date_df.iloc[0]['min_date']) else None
            max_date = date_df.iloc[0]['max_date'] if not date_df.empty and pd.notnull(date_df.iloc[0]['max_date']) else None

            selected_dates = []
            if min_date and max_date:
                if isinstance(min_date, str):
                    min_date = datetime.datetime.strptime(min_date, '%Y-%m-%d').date()
                    max_date = datetime.datetime.strptime(max_date, '%Y-%m-%d').date()
                default_dates = st.session_state[k_dates] if st.session_state[k_dates] else (min_date, max_date)
                selected_dates = st.date_input(
                    "4. 실험 일자 범위", value=default_dates, min_value=min_date, max_value=max_date,
                    key=f"{key_prefix}_widget_dates",
                )
                st.session_state[k_dates] = selected_dates

        with c5:
            mach_query = "SELECT DISTINCT machining_type FROM job WHERE machining_type IS NOT NULL AND machining_type != ''"
            mach_df = load_data(mach_query)
            machs = mach_df['machining_type'].tolist() if not mach_df.empty else []
            selected_machs = st.multiselect(
                "5. 가공 종류 (선택사항)", machs,
                default=[m for m in st.session_state[k_machs] if m in machs],
                key=f"{key_prefix}_widget_machs",
            )
            st.session_state[k_machs] = selected_machs

    with st.expander("고급 검색 필터", expanded=False, icon=":material/tune:"):
        st.caption("아래 조건들을 설정하면 더 정밀하게 데이터를 필터링할 수 있습니다. (0이면 조건 무시)")
        adv_c1, adv_c2, adv_c3 = st.columns(3)
        with adv_c1:
            min_cut_sec = st.number_input("최소 가공 시간 (초)", value=0, min_value=0, key=f"{key_prefix}_min_cut")
            max_cut_sec = st.number_input("최대 가공 시간 (초)", value=0, min_value=0, key=f"{key_prefix}_max_cut")
        with adv_c2:
            min_ra = st.number_input("최소 표면 조도 Ra (μm)", value=0.0, min_value=0.0, step=0.1, key=f"{key_prefix}_min_ra")
            max_ra = st.number_input("최대 표면 조도 Ra (μm)", value=0.0, min_value=0.0, step=0.1, key=f"{key_prefix}_max_ra")
        with adv_c3:
            max_rpm = st.number_input("최대 평균 RPM", value=0, min_value=0, key=f"{key_prefix}_max_rpm")
            min_rpm = st.number_input("최소 평균 RPM", value=0, min_value=0, key=f"{key_prefix}_min_rpm")

    conditions = []
    main_params = {}

    if selected_projs:
        proj_keys = [f"m_proj_{i}" for i in range(len(selected_projs))]
        for k, v in zip(proj_keys, selected_projs):
            main_params[k] = v
        conditions.append(f"j.research_project IN ({', '.join([':' + k for k in proj_keys])})")

    if selected_machs:
        mach_keys = [f"m_mach_{i}" for i in range(len(selected_machs))]
        for k, v in zip(mach_keys, selected_machs):
            main_params[k] = v
        conditions.append(f"j.machining_type IN ({', '.join([':' + k for k in mach_keys])})")

    if selected_jobs:
        job_keys = [f"m_job_{i}" for i in range(len(selected_jobs))]
        for k, v in zip(job_keys, selected_jobs):
            main_params[k] = v
        conditions.append(f"j.job_id IN ({', '.join([':' + k for k in job_keys])})")
    elif selected_parts:
        part_keys = [f"m_part_{i}" for i in range(len(selected_parts))]
        for k, v in zip(part_keys, selected_parts):
            main_params[k] = v
        conditions.append(f"COALESCE(j.custom_part_name, p.part_name) IN ({', '.join([':' + k for k in part_keys])})")

    if selected_dates and len(selected_dates) == 2:
        start_dt = selected_dates[0].strftime('%Y-%m-%d 00:00:00')
        end_dt = selected_dates[1].strftime('%Y-%m-%d 23:59:59')
        conditions.append("(j.start_time BETWEEN :start_dt AND :end_dt OR j.start_time IS NULL)")
        main_params['start_dt'] = start_dt
        main_params['end_dt'] = end_dt

    if min_cut_sec > 0:
        conditions.append("j.cutting_seconds >= :min_cut_sec")
        main_params['min_cut_sec'] = min_cut_sec
    if max_cut_sec > 0:
        conditions.append("j.cutting_seconds <= :max_cut_sec")
        main_params['max_cut_sec'] = max_cut_sec
    if min_ra > 0.0:
        conditions.append("i.surface_roughness_ra >= :min_ra")
        main_params['min_ra'] = min_ra
    if max_ra > 0.0:
        conditions.append("i.surface_roughness_ra <= :max_ra")
        main_params['max_ra'] = max_ra
    if min_rpm > 0:
        conditions.append("m.avg_spindle_rpm >= :min_rpm")
        main_params['min_rpm'] = min_rpm
    if max_rpm > 0:
        conditions.append("m.avg_spindle_rpm <= :max_rpm")
        main_params['max_rpm'] = max_rpm

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    job_query = f"""
        SELECT
            j.job_id, j.start_time, j.research_project, COALESCE(j.custom_part_name, p.part_name) as part_name, j.machining_type,
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

    return {
        "job_df": job_df,
        "selected_projs": selected_projs,
        "selected_parts": selected_parts,
        "selected_jobs": selected_jobs,
        "selected_job_labels": selected_job_labels,
        "selected_dates": selected_dates,
        "selected_machs": selected_machs,
    }


JOB_TABLE_COLUMN_CONFIG = {
    "job_id": "Job ID",
    "start_time": "시작 시간",
    "research_project": "프로젝트",
    "part_name": "Part 명",
    "machining_type": "가공 종류",
    "cutting_seconds": "가공 시간(초)",
    "is_finish": "완료",
    "surface_roughness_ra": "조도 Ra(μm)",
    "surface_roughness_rz": "조도 Rz(μm)",
    "pass_fail": "합불 판정",
    "worker_name": "작업자",
    "temperature": "온도(°C)",
    "humidity": "습도(%)",
    "free_memo": "작업자 메모",
    "max_spindle_load": "최대 부하(%)",
    "avg_spindle_rpm": "평균 RPM",
    "alarm_count": "알람 수",
}


# 전체 17개 컬럼을 항상 펼치면 가로 스크롤 없이는 읽을 수 없어서, 기본은 아래 핵심 컬럼만 보여준다.
JOB_TABLE_ESSENTIAL_COLUMNS = [
    "job_id", "start_time", "part_name", "machining_type",
    "cutting_seconds", "surface_roughness_ra", "pass_fail", "worker_name",
]


def render_job_table(job_df, key=None, selectable=False, show_all_columns=True):
    """Job 목록 표를 렌더링한다.

    Args:
        selectable: True면 행 클릭으로 Job을 선택할 수 있고, 클릭된 job_id를 반환한다.
        show_all_columns: False면 `JOB_TABLE_ESSENTIAL_COLUMNS`만 표시한다.

    Returns:
        선택된 job_id (선택이 없거나 selectable=False면 None)
    """
    import streamlit as st

    column_config = {
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
        "alarm_count": st.column_config.NumberColumn("알람 수", format="%d"),
    }

    column_order = None
    if not show_all_columns:
        column_order = [c for c in JOB_TABLE_ESSENTIAL_COLUMNS if c in job_df.columns]

    select_kwargs = {"on_select": "rerun", "selection_mode": "single-row"} if selectable else {}

    event = st.dataframe(
        job_df,
        width="stretch",
        hide_index=True,
        column_config=column_config,
        column_order=column_order,
        key=key,
        **select_kwargs,
    )

    if selectable:
        selected_rows = list(event.selection["rows"]) if event.selection else []
        if selected_rows:
            row_pos = selected_rows[0]
            if 0 <= row_pos < len(job_df):
                return int(job_df.iloc[row_pos]["job_id"])
    return None
