import datetime
import os

import streamlit as st

import job_layout

from .common import PROJECT_ROOT, RAW_DATA_DIR, load_data
from cad_viewer_component import render_cad_viewer


def render_data_upload():
    st.subheader("외부 데이터 삽입")
    st.markdown("로컬에 있는 가공 데이터를 직접 업로드하여 파이프라인에 삽입합니다.")

    # Streamlit 기본 안내문("10GB per file • STL, STEP, STP")은 서버 설정값이라
    # 화면에 적힌 '15MB 제한' 안내와 어긋나 혼동을 줘서 숨긴다.
    # (testid가 stFileUploadDropzone -> stFileUploaderDropzone 으로 바뀌어 선택자를 갱신)
    st.markdown("""
        <style>
        [data-testid="stFileUploaderDropzoneInstructions"] {
            display: none !important;
        }
        </style>
    """, unsafe_allow_html=True)

    base_raw_dir = RAW_DATA_DIR
    os.makedirs(base_raw_dir, exist_ok=True)

    st.markdown("#### Step 1. 대상 Project 및 Part 설정")
    is_new_proj_part = st.segmented_control(
        "Project / Part 선택 방식",
        ["기존 항목 선택", "신규 생성"],
        default="기존 항목 선택",
        key="seg_is_new_proj_part",
    )
    if is_new_proj_part is None:
        is_new_proj_part = "기존 항목 선택"

    project_name = ""
    part_name = ""

    if is_new_proj_part == "신규 생성":
        project_name = st.text_input("1. 연구 프로젝트 명 (Project Name)", placeholder="예: Alchemist", key="input_proj_new")
        part_name = st.text_input("2. 가공 Part 명 (Part Name)", placeholder="예: Bracket", key="input_part_new")
    else:
        all_projects = sorted([d for d in os.listdir(base_raw_dir) if os.path.isdir(os.path.join(base_raw_dir, d))])
        if not all_projects:
            st.warning("존재하는 Project가 없습니다. '신규 생성'을 선택해주세요.")
        else:
            project_name = st.selectbox("1. 추가할 Project 선택", [""] + all_projects, key="select_proj_exist")

            if project_name:
                proj_dir = os.path.join(base_raw_dir, project_name)
                all_parts = sorted([d for d in os.listdir(proj_dir) if os.path.isdir(os.path.join(proj_dir, d))])
                part_name = st.selectbox("2. 추가할 Part 선택", [""] + all_parts, key="select_part_exist")

    target_ready = bool(project_name and project_name.strip() and part_name and part_name.strip())

    if not target_ready:
        st.info("Project와 Part를 선택하거나 입력하면 업로드 타겟 및 CAD 업로드 창이 자동으로 표시됩니다.", icon=":material/lightbulb:")
        return

    target_project_name = project_name.strip()
    target_part_name = part_name.strip()

    st.success(f"**선택 대상:** 프로젝트: **`{target_project_name}`** / 부품(Part): **`{target_part_name}`**",
              icon=":material/target:")

    st.markdown("---")
    st.markdown("#### Step 2. 데이터 업로드 타겟 선택")

    upload_target = st.segmented_control(
        "어느 레벨에 데이터를 업로드하시겠습니까?",
        [
            "Part 레벨 (CAD 파일 단독 업로드)",
            "신규 Job 생성 및 데이터 업로드",
            "기존 Job에 데이터 추가",
        ],
        default="신규 Job 생성 및 데이터 업로드",
        key="seg_upload_target",
    )
    if upload_target is None:
        upload_target = "신규 Job 생성 및 데이터 업로드"

    st.markdown("##### :material/view_in_ar: Part 레벨 CAD 파일")
    st.caption("CAD 모델 파일(STL/STEP)은 Job 단위가 아닌 Part 단위에 귀속됩니다. (최대 15MB 제한)")

    cad_existing_name = None
    cad_dir_check = os.path.join(base_raw_dir, target_project_name, target_part_name, job_layout.CAD_DIR)
    if os.path.exists(cad_dir_check):
        cad_existing_files = [f for f in os.listdir(cad_dir_check) if f.lower().endswith(('.stl', '.step', '.stp'))]
        if cad_existing_files:
            cad_existing_name = cad_existing_files[0]

    c_cad1, c_cad2 = st.columns([3, 2])
    with c_cad1:
        st.markdown("**CAD 파일 업로드 (.stl, .step, .stp) (1개) - 15MB 제한**")
    with c_cad2:
        if cad_existing_name:
            st.badge("파일 삽입 완료", color="green", icon=":material/check_circle:")
    file_cad = st.file_uploader("CAD 파일 업로드", type=["stl", "step", "stp"], accept_multiple_files=False, key="uploader_cad", label_visibility="collapsed")

    if cad_existing_name:
        with st.expander(f"기존 CAD 모델 3D 뷰어 ({cad_existing_name}) - 360° 회전 / 메시 모드", expanded=False, icon=":material/visibility:"):
            render_cad_viewer(target_part_name, height=460)

    existing_job_folder = None
    job_selected_ready = False
    existing_files_info = {}

    if upload_target == "Part 레벨 (CAD 파일 단독 업로드)":
        job_selected_ready = True
    elif upload_target == "신규 Job 생성 및 데이터 업로드":
        job_selected_ready = True
        st.info("DB의 고유 식별자 PK(job_id)와 일치하는 신규 가공 폴더가 자동으로 생성되어 아래 파일들이 등록됩니다.", icon=":material/lightbulb:")
    elif upload_target == "기존 Job에 데이터 추가":
        # 프로젝트·부품 이름은 사용자가 직접 입력하는 값이라 SQL 에 그대로 끼워 넣지 않는다.
        # 이름에 작은따옴표가 하나만 들어가도 쿼리가 깨지고, 의도적으로 조작할 여지도 생긴다.
        job_query = """
            SELECT j.job_id, j.source_folder
            FROM job j
            LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
            LEFT JOIN part p ON w.part_code = p.part_code
            WHERE p.project_code = :project
              AND p.part_name = :part
        """
        job_df = load_data(job_query, params={"project": target_project_name,
                                              "part": target_part_name})
        if job_df.empty:
            st.warning("해당 Part에 등록된 DB Job 레코드가 없습니다. 먼저 '신규 Job 생성'으로 데이터를 추가하세요.")
        else:
            job_choices = job_df['job_id'].tolist()
            selected_job_id = st.selectbox("추가할 Job ID (DB 고유 식별자) 선택", [""] + job_choices, key="select_existing_job")
            if selected_job_id:
                matched_row = job_df[job_df['job_id'] == selected_job_id].iloc[0]
                sf = matched_row['source_folder']
                if sf:
                    existing_job_folder = sf.split('/')[-1]
                    job_selected_ready = True

                    cur_job_dir = os.path.join(base_raw_dir, target_project_name, target_part_name, existing_job_folder)
                    # 자료 종류별 하위 폴더를 먼저 보고, 예전 구조로 남아 있으면 Job 루트도 본다.
                    for key, kind, suffixes in (
                        ('xml', job_layout.XML_DIR, ('.xml',)),
                        ('tdms', job_layout.TDMS_DIR, ('.tdms',)),
                        ('nc', job_layout.NC_DIR, ('.nc',)),
                        ('log', job_layout.LOG_DIR, ('.log',)),
                    ):
                        found = job_layout.find_files(cur_job_dir, kind, suffixes)
                        if found:
                            existing_files_info[key] = os.path.basename(found[0])
                else:
                    st.error("선택한 Job의 원본 폴더 경로(source_folder)가 DB에 기록되지 않아 추가할 수 없습니다.")
            else:
                st.info("위에서 데이터를 추가할 기존 Job ID를 선택하시면 나머지 가공 파일 업로드 창이 열립니다.", icon=":material/lightbulb:")

    file_xml = None
    file_tdms = None
    file_nc = None
    file_log = None
    file_roughness = None
    file_others = None

    if job_selected_ready:
        if upload_target != "Part 레벨 (CAD 파일 단독 업로드)":
            st.markdown("---")
            st.markdown("#### Step 3. Job 가공 데이터 파일 업로드")
            st.caption("XML, NC 파일은 최대 15MB 제한, 기타 파일은 제한이 없습니다.")

            col_f1, col_f2 = st.columns(2)
            with col_f1:
                c_x1, c_x2 = st.columns([3, 2])
                with c_x1:
                    st.markdown("**XML 메타데이터 파일 (.xml) (1개) - 15MB 제한**")
                with c_x2:
                    if 'xml' in existing_files_info:
                        st.badge("파일 삽입 완료", color="green", icon=":material/check_circle:")
                file_xml = st.file_uploader("XML 메타데이터 파일 업로드", type=["xml"], accept_multiple_files=False, key="uploader_xml", label_visibility="collapsed")

                st.write("")
                c_n1, c_n2 = st.columns([3, 2])
                with c_n1:
                    st.markdown("**NC 가공코드 파일 (.nc) (1개) - 15MB 제한**")
                with c_n2:
                    if 'nc' in existing_files_info:
                        st.badge("파일 삽입 완료", color="green", icon=":material/check_circle:")
                file_nc = st.file_uploader("NC 가공코드 파일 업로드", type=["nc"], accept_multiple_files=False, key="uploader_nc", label_visibility="collapsed")

                st.write("")
                st.markdown("**표면 조도 데이터 업로드 (.csv) (다중 선택 가능)**")
                file_roughness = st.file_uploader("표면 조도 데이터 업로드", type=["csv"], accept_multiple_files=True, key="uploader_roughness", label_visibility="collapsed")

            with col_f2:
                c_t1, c_t2 = st.columns([3, 2])
                with c_t1:
                    st.markdown("**TDMS 고주파 센서 파일 (.tdms) (1개)**")
                with c_t2:
                    if 'tdms' in existing_files_info:
                        st.badge("파일 삽입 완료", color="green", icon=":material/check_circle:")
                file_tdms = st.file_uploader("TDMS 고주파 센서 파일 업로드", type=["tdms"], accept_multiple_files=False, key="uploader_tdms", label_visibility="collapsed")

                st.write("")
                c_l1, c_l2 = st.columns([3, 2])
                with c_l1:
                    st.markdown("**장비 로그(Log) 파일 (.log) (1개)**")
                with c_l2:
                    if 'log' in existing_files_info:
                        st.badge("파일 삽입 완료", color="green", icon=":material/check_circle:")
                file_log = st.file_uploader("장비 로그 파일 업로드", type=["log"], accept_multiple_files=False, key="uploader_log", label_visibility="collapsed")

                st.write("")
                st.markdown("**기타 참고용 파일 업로드 (다중 선택 가능)**")
                file_others = st.file_uploader("기타 참고용 파일 업로드", accept_multiple_files=True, key="uploader_others", label_visibility="collapsed")

        submit_btn = st.button("업로드 및 파이프라인 전송", type="primary", key="btn_submit_upload")

        if submit_btn:
            def check_size(file_obj, max_mb):
                if file_obj is None:
                    return True
                if isinstance(file_obj, list):
                    for f_obj in file_obj:
                        if f_obj.size > max_mb * 1024 * 1024:
                            return False
                    return True
                return file_obj.size <= max_mb * 1024 * 1024

            if not target_project_name or not target_project_name.strip() or not target_part_name or not target_part_name.strip():
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
                project_clean = target_project_name.strip()
                part_clean = target_part_name.strip()

                target_job_dir = ""
                cad_dir = os.path.join(base_raw_dir, project_clean, part_clean, job_layout.CAD_DIR)

                if upload_target == "신규 Job 생성 및 데이터 업로드":
                    from job_manager import get_or_create_job
                    from DB.database import SessionLocal

                    db_s = SessionLocal()
                    try:
                        new_job = get_or_create_job(db_s, f"{project_clean}/{part_clean}/NEW_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}")
                        new_pk = new_job.job_id
                        db_s.commit()
                    except Exception:
                        db_s.rollback()
                        new_pk = None
                    finally:
                        db_s.close()

                    part_dir = os.path.join(base_raw_dir, project_clean, part_clean)
                    os.makedirs(part_dir, exist_ok=True)
                    target_job_dir = os.path.join(part_dir, str(new_pk) if new_pk else "1")
                    os.makedirs(target_job_dir, exist_ok=True)
                elif upload_target == "기존 Job에 데이터 추가":
                    target_job_dir = os.path.join(base_raw_dir, project_clean, part_clean, existing_job_folder)
                    os.makedirs(target_job_dir, exist_ok=True)

                def save_file(uploaded_file, dest_dir):
                    if uploaded_file:
                        fpath = os.path.join(dest_dir, uploaded_file.name)
                        with open(fpath, "wb") as f_out:
                            f_out.write(uploaded_file.getbuffer())
                        return 1
                    return 0

                saved_count = 0

                if file_cad:
                    os.makedirs(cad_dir, exist_ok=True)
                    saved_count += save_file(file_cad, cad_dir)

                if target_job_dir:
                    roughness_dir = os.path.join(target_job_dir, job_layout.ROUGHNESS_DIR)
                    etc_dir = os.path.join(target_job_dir, job_layout.ETC_DIR)

                    # 자료 종류별 하위 폴더에 바로 넣는다 (수집기가 옮길 필요가 없도록)
                    for uploaded, kind in (
                        (file_xml, job_layout.XML_DIR),
                        (file_tdms, job_layout.TDMS_DIR),
                        (file_nc, job_layout.NC_DIR),
                        (file_log, job_layout.LOG_DIR),
                    ):
                        if uploaded:
                            saved_count += save_file(
                                uploaded, job_layout.subdir_path(target_job_dir, kind, create=True))

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
