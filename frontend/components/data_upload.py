import datetime
import os

import streamlit as st

import job_layout

from .common import PROJECT_ROOT, RAW_DATA_DIR, engine, load_data
from .auth_view import current_worker_name
from cad_viewer_component import render_cad_viewer

# Job 드롭다운에서 '새로 만든다'를 뜻하는 항목.
# Job 폴더 이름은 DB 가 부여하는 PK 를 따라가므로 사람이 직접 적지 않는다.
# (숫자를 직접 적으면 같은 부품 안에서 기존 Job 번호와 충돌할 수 있다)
NEW_JOB_LABEL = "+ 새 Job 생성 (번호 자동 부여)"


def _fs_dirs(path):
    """경로 아래의 폴더 이름들. 없으면 빈 목록."""
    try:
        return {d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))}
    except OSError:
        return set()


def _list_projects(base_raw_dir):
    """선택 가능한 프로젝트 목록 (DB ∪ 원본 폴더).

    예전에는 원본 폴더만 훑었다. 그러면 DB 에는 있는데 폴더가 지워진 프로젝트가 목록에서
    사라져, 정작 그 부품에 파일을 더 넣으려 할 때 고를 수가 없었다. 두 곳을 합쳐서 보여준다.
    """
    names = _fs_dirs(base_raw_dir)
    try:
        df = load_data("SELECT DISTINCT project_code FROM part WHERE project_code IS NOT NULL")
        names |= {str(v) for v in df['project_code'].tolist() if v}
    except Exception:
        pass
    return sorted(names, key=str.lower)


def _list_parts(base_raw_dir, project_name):
    """해당 프로젝트의 부품 목록 (DB ∪ 원본 폴더)."""
    if not project_name:
        return []
    names = _fs_dirs(os.path.join(base_raw_dir, project_name))
    try:
        df = load_data(
            "SELECT part_name FROM part WHERE project_code = :proj AND part_name IS NOT NULL",
            params={"proj": project_name})
        names |= {str(v) for v in df['part_name'].tolist() if v}
    except Exception:
        pass
    return sorted(names, key=str.lower)


def _list_jobs(project_name, part_name):
    """해당 부품의 Job 목록. (표시 이름 -> 폴더 이름) 매핑을 함께 돌려준다."""
    if not (project_name and part_name):
        return [], {}
    # 프로젝트·부품 이름은 사용자가 직접 입력하는 값이라 SQL 에 그대로 끼워 넣지 않는다.
    df = load_data("""
        SELECT j.job_id, j.source_folder
        FROM job j
        LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
        LEFT JOIN part p ON w.part_code = p.part_code
        WHERE p.project_code = :project AND p.part_name = :part
        ORDER BY j.job_id
    """, params={"project": project_name, "part": part_name})
    labels, folder_by_label = [], {}
    for _, row in df.iterrows():
        sf = row['source_folder']
        if not sf:
            continue      # 폴더를 모르면 파일을 넣을 자리가 없다
        label = f"Job {row['job_id']} · {sf}"
        labels.append(label)
        folder_by_label[label] = str(sf).split('/')[-1]
    return labels, folder_by_label


def _job_id_of(project_name, part_name, job_folder):
    """폴더 이름으로 Job PK 를 되찾는다. 없으면 None."""
    df = load_data("""
        SELECT j.job_id
        FROM job j
        LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
        LEFT JOIN part p ON w.part_code = p.part_code
        WHERE p.project_code = :project AND p.part_name = :part
          AND j.source_folder = :sf
    """, params={"project": project_name, "part": part_name,
                 "sf": f"{project_name}/{part_name}/{job_folder}"})
    return int(df.iloc[0]['job_id']) if not df.empty else None


def _existing_files(job_dir):
    """이미 들어가 있는 자료 종류 -> 파일명. 업로더 옆 배지에 쓴다."""
    found = {}
    for key, kind, suffixes in (
        ('xml', job_layout.XML_DIR, ('.xml',)),
        ('tdms', job_layout.TDMS_DIR, ('.tdms',)),
        ('nc', job_layout.NC_DIR, ('.nc',)),
        ('log', job_layout.LOG_DIR, ('.log',)),
    ):
        hit = job_layout.find_files(job_dir, kind, suffixes)
        if hit:
            found[key] = os.path.basename(hit[0])
    return found


def _uploader(label, key, types=None, multiple=False, done_name=None, disabled=False):
    """제목 + '삽입 완료' 배지 + 업로더를 한 묶음으로 그린다."""
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(f"**{label}**")
    with c2:
        if done_name:
            st.badge("파일 삽입 완료", color="green", icon=":material/check_circle:")
    return st.file_uploader(label, type=types, accept_multiple_files=multiple,
                            key=key, label_visibility="collapsed", disabled=disabled)


def _save_job_attributes(job_id, worker_name, values, is_new_job):
    """업로드와 함께 받은 메타데이터·환경·품질 값을 DB 에 기록한다.

    예전에는 이 입력이 Job 워크스페이스의 '편집 모드' 안에 있어서, 파일을 올린 뒤 화면을
    옮겨 다시 찾아 들어가야 했다. 값을 아는 시점은 가공을 막 끝낸 업로드 때이므로 입력도
    그 자리에서 받는다.

    **빈 칸은 '변경하지 않음'으로 다룬다.** 이유가 있다.

      - `surface_roughness_ra/rz` 는 사람이 적는 값이 아니라 조도 CSV 에서 파서가 평균 내
        채우는 **측정값**이다(roughness_parser). 빈 칸을 그대로 NULL 로 쓰면 이미 측정된
        값을 지워 버린다.
      - 기존 Job 에 파일을 덧붙이는 경우, 이번에 적지 않은 칸까지 비우는 것은 의도가 아니다.

    작업자 이름은 로그인 계정에서 온다. 새 Job 이면 항상 적고, 기존 Job 은 **비어 있을 때만**
    채운다 — 이미 다른 사람이 기록돼 있다면 그 기록이 사실이므로 덮어쓰지 않는다.
    """
    from sqlalchemy import text as _text

    def _filled(items):
        """값이 실제로 들어온 칸만 남긴다."""
        return {k: v for k, v in items.items()
                if v is not None and not (isinstance(v, str) and not v.strip())}

    written = []

    with engine.connect() as conn:
        # --- job.machining_type ---
        mt = (values.get("machining_type") or "").strip()
        if mt:
            conn.execute(_text("UPDATE job SET machining_type=:mt WHERE job_id=:jid"),
                         {"mt": mt, "jid": job_id})
            written.append("가공 종류")

        # --- env_memo ---
        # Job 이 생길 때 빈 행이 함께 만들어지지만(job_manager.ensure_env_memo) 없으면 만든다.
        if not conn.execute(_text("SELECT 1 FROM env_memo WHERE job_id=:jid"),
                            {"jid": job_id}).first():
            conn.execute(_text("INSERT INTO env_memo (job_id) VALUES (:jid)"), {"jid": job_id})

        env = _filled({
            "temperature": values.get("temperature"),
            "humidity": values.get("humidity"),
            "chip_shape": values.get("chip_shape"),
            "abnormal_noise": values.get("abnormal_noise"),
            "free_memo": values.get("free_memo"),
        })

        if worker_name:
            current = conn.execute(_text("SELECT worker_name FROM env_memo WHERE job_id=:jid"),
                                   {"jid": job_id}).scalar()
            if is_new_job or not (current or "").strip():
                env["worker_name"] = worker_name
                written.append(f"작업자({worker_name})")
            elif current.strip() != worker_name:
                written.append(f"작업자는 기존 기록 '{current}' 유지")

        if env:
            # day_of_week 는 가공 시작시각에서 계산되는 파생값이라 여기서 건드리지 않는다.
            sets = ", ".join(f"{k}=:{k}" for k in env)
            conn.execute(_text(f"UPDATE env_memo SET {sets} WHERE job_id=:jid"),
                         {**env, "jid": job_id})

        # --- inspection ---
        ins = _filled({
            "dimension_tolerance": values.get("dimension_tolerance"),
            "surface_roughness_ra": values.get("roughness_ra"),
            "surface_roughness_rz": values.get("roughness_rz"),
            "pass_fail": values.get("pass_fail"),
        })
        if ins:
            if conn.execute(_text("SELECT 1 FROM inspection WHERE job_id=:jid"),
                            {"jid": job_id}).first():
                sets = ", ".join(f"{k}=:{k}" for k in ins)
                conn.execute(_text(f"UPDATE inspection SET {sets} WHERE job_id=:jid"),
                             {**ins, "jid": job_id})
            else:
                cols = ", ".join(ins)
                binds = ", ".join(f":{k}" for k in ins)
                conn.execute(_text(
                    f"INSERT INTO inspection (job_id, {cols}) VALUES (:jid, {binds})"),
                    {**ins, "jid": job_id})
            written.append("품질 점검")

        conn.commit()

    return written


def _to_float(text_value):
    """빈 칸은 None, 숫자가 아니면 None. (입력이 틀려도 업로드를 막지는 않는다)"""
    if text_value is None:
        return None
    text_value = str(text_value).strip()
    if not text_value:
        return None
    try:
        return float(text_value)
    except ValueError:
        return None


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

    # ------------------------------------------------------------------ Part 입력
    st.markdown("#### Part 입력")
    st.caption("목록에서 고르거나, 없는 이름을 직접 입력하면 새로 만들어집니다.")

    col_p, col_t, col_j = st.columns(3)

    with col_p:
        project_name = st.selectbox(
            "프로젝트 (Project)",
            options=_list_projects(base_raw_dir),
            index=None,
            placeholder="선택 또는 새 이름 입력",
            accept_new_options=True,
            key="dl_project",
        )
        project_name = (project_name or "").strip()

    with col_t:
        part_name = st.selectbox(
            "부품 (Part)",
            options=_list_parts(base_raw_dir, project_name),
            index=None,
            placeholder="선택 또는 새 이름 입력",
            accept_new_options=True,
            key="dl_part",
        )
        part_name = (part_name or "").strip()

    part_ready = bool(project_name and part_name)

    with col_j:
        job_labels, folder_by_label = _list_jobs(project_name, part_name) if part_ready else ([], {})
        job_choice = st.selectbox(
            "가공 차수 (Job)",
            options=[NEW_JOB_LABEL] + job_labels,
            index=None,
            placeholder="선택 안 함 (CAD만 업로드)",
            disabled=not part_ready,
            key="dl_job",
            help="비워 두면 Part 레벨 CAD 파일만 업로드합니다.",
        )

    is_new_job = job_choice == NEW_JOB_LABEL
    existing_job_folder = folder_by_label.get(job_choice) if job_choice else None
    job_ready = bool(is_new_job or existing_job_folder)

    if not part_ready:
        st.info("프로젝트와 부품을 지정하면 업로드 창이 활성화됩니다.", icon=":material/lightbulb:")
    else:
        where = (f"신규 Job (번호 자동)" if is_new_job
                 else f"Job 폴더 `{existing_job_folder}`" if existing_job_folder
                 else "Part 레벨 (CAD 전용)")
        st.success(f"**대상** · 프로젝트 `{project_name}` / 부품 `{part_name}` / {where}",
                   icon=":material/target:")

    st.markdown("---")

    # ------------------------------------------------------------------ 데이터 업로드
    st.markdown("#### 데이터 업로드")

    # --- Part 레벨: CAD ---
    st.markdown("##### :material/view_in_ar: Part 레벨")
    st.caption("CAD 모델 파일(STL/STEP)은 Job 단위가 아닌 Part 단위에 귀속됩니다. (최대 15MB)")

    cad_existing_name = None
    if part_ready:
        cad_dir_check = os.path.join(base_raw_dir, project_name, part_name, job_layout.CAD_DIR)
        if os.path.exists(cad_dir_check):
            hits = [f for f in os.listdir(cad_dir_check)
                    if f.lower().endswith(('.stl', '.step', '.stp'))]
            if hits:
                cad_existing_name = hits[0]

    file_cad = _uploader("CAD 파일 (.stl, .step, .stp) (1개) - 15MB 제한", "uploader_cad",
                         types=["stl", "step", "stp"], done_name=cad_existing_name,
                         disabled=not part_ready)

    if cad_existing_name:
        with st.expander(f"기존 CAD 모델 3D 뷰어 ({cad_existing_name}) - 360° 회전 / 메시 모드",
                         expanded=False, icon=":material/visibility:"):
            render_cad_viewer(part_name, height=460)

    # --- Job 레벨: 가공 데이터 ---
    st.markdown("##### :material/precision_manufacturing: Job 레벨")
    if part_ready and not job_ready:
        st.caption("가공 차수(Job)를 선택하거나 '새 Job 생성'을 고르면 아래 업로더가 열립니다.")
    else:
        st.caption("XML, NC 파일은 최대 15MB 제한, 기타 파일은 제한이 없습니다.")

    existing_files_info = {}
    if existing_job_folder:
        existing_files_info = _existing_files(
            os.path.join(base_raw_dir, project_name, part_name, existing_job_folder))

    off = not job_ready
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        file_xml = _uploader("XML 메타데이터 (.xml) (1개) - 15MB 제한", "uploader_xml",
                             types=["xml"], done_name=existing_files_info.get('xml'), disabled=off)
        file_nc = _uploader("NC 가공코드 (.nc) (1개) - 15MB 제한", "uploader_nc",
                            types=["nc"], done_name=existing_files_info.get('nc'), disabled=off)
        file_roughness = _uploader("표면 조도 데이터 (.csv) (여러 개)", "uploader_roughness",
                                   types=["csv"], multiple=True, disabled=off)
    with col_f2:
        file_tdms = _uploader("TDMS 고주파 센서 (.tdms) (1개)", "uploader_tdms",
                              types=["tdms"], done_name=existing_files_info.get('tdms'), disabled=off)
        file_log = _uploader("장비 로그 (.log) (1개)", "uploader_log",
                             types=["log"], done_name=existing_files_info.get('log'), disabled=off)
        file_others = _uploader("기타 참고용 파일 (여러 개)", "uploader_others",
                                multiple=True, disabled=off)

    # ------------------------------------------------------------ 가공 기록 입력
    st.markdown("---")
    st.markdown("#### 가공 기록")

    worker_name = current_worker_name()
    if job_ready:
        st.caption(f"작업자는 로그인 계정에서 자동으로 기록됩니다 — **{worker_name}** · "
                   "**빈 칸은 기존 값을 그대로 둡니다.**")
    else:
        st.caption("Job 을 지정하면 이 값들을 함께 기록할 수 있습니다. (CAD 단독 업로드에는 해당 없음)")

    meta = {}
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown("**메타데이터**")
        meta["machining_type"] = st.text_input(
            "가공 종류", key="dl_machining_type", disabled=off,
            placeholder="예: 황삭 / 정삭")
        st.markdown("**환경**")
        meta["temperature"] = _to_float(st.text_input("온도 (℃)", key="dl_temp", disabled=off))
        meta["humidity"] = _to_float(st.text_input("습도 (%)", key="dl_hum", disabled=off))
    with m2:
        st.markdown("**관찰**")
        meta["chip_shape"] = st.text_input("칩 형태", key="dl_chip", disabled=off)
        meta["abnormal_noise"] = st.text_input("이상 소음", key="dl_noise", disabled=off)
        meta["free_memo"] = st.text_area("수기 메모", key="dl_memo", disabled=off, height=110)
    with m3:
        st.markdown("**품질 점검**")
        meta["dimension_tolerance"] = st.text_input("치수 및 공차", key="dl_dim", disabled=off)
        # Ra/Rz 는 조도 CSV 를 올리면 파서가 측정값 평균으로 채운다.
        # 여기 적은 값은 그것을 덮어쓰므로, 비워 두는 것이 기본이다.
        meta["roughness_ra"] = _to_float(st.text_input(
            "표면조도 Ra (μm)", key="dl_ra", disabled=off,
            help="비워 두면 조도 CSV 에서 자동 계산됩니다. 값을 적으면 그 값이 우선합니다."))
        meta["roughness_rz"] = _to_float(st.text_input(
            "표면조도 Rz (μm)", key="dl_rz", disabled=off,
            help="비워 두면 조도 CSV 에서 자동 계산됩니다. 값을 적으면 그 값이 우선합니다."))
        meta["pass_fail"] = st.selectbox("합불 판정", ["", "PASS", "FAIL"],
                                         key="dl_pf", disabled=off)

    st.markdown("")
    submit_btn = st.button("업로드 및 파이프라인 전송", type="primary",
                           key="btn_submit_upload", disabled=not part_ready)

    if not submit_btn:
        return

    # ------------------------------------------------------------------ 검증 · 저장
    def check_size(file_obj, max_mb):
        if file_obj is None:
            return True
        files = file_obj if isinstance(file_obj, list) else [file_obj]
        return all(f.size <= max_mb * 1024 * 1024 for f in files)

    job_files = [file_xml, file_tdms, file_nc, file_log, file_roughness, file_others]
    core_files = [file_xml, file_tdms, file_nc, file_log, file_roughness]

    if not check_size(file_xml, 15) or not check_size(file_nc, 15) or not check_size(file_cad, 15):
        st.error("오류: XML, NC, CAD 파일은 15MB를 초과할 수 없습니다.")
        return
    if not job_ready:
        if not file_cad:
            st.error("오류: 업로드할 CAD 파일을 선택하세요. "
                     "(가공 데이터를 넣으려면 Job을 먼저 지정하세요)")
            return
    elif not any(job_files) and not file_cad:
        st.error("오류: 업로드할 파일을 최소 한 개 이상 선택해주세요.")
        return
    elif is_new_job and not any(core_files):
        st.error("오류: 기타 참고용 파일만으로는 새로운 Job을 생성할 수 없습니다. "
                 "핵심 데이터(XML, NC 등)를 포함해주세요.")
        return

    cad_dir = os.path.join(base_raw_dir, project_name, part_name, job_layout.CAD_DIR)
    target_job_dir = ""
    attr_job_id = None      # 가공 기록을 적을 Job (CAD 단독 업로드면 None)

    if is_new_job:
        from job_manager import get_or_create_job
        from DB.database import SessionLocal

        db_s = SessionLocal()
        try:
            placeholder_id = f"{project_name}/{part_name}/NEW_{datetime.datetime.now():%Y%m%d%H%M%S}"
            new_job = get_or_create_job(db_s, placeholder_id)
            new_pk = new_job.job_id
            db_s.commit()
        except Exception as e:
            db_s.rollback()
            # 여기서 폴더 이름을 임의로 정하면 안 된다.
            # 예전에는 실패 시 폴더를 "1" 로 고정했는데, 이미 있는 Job 1 폴더에 남의 파일을
            # 덮어썼고 롤백으로 번호까지 밀려 실제 Job 은 2번이 됐다. 만들지 않고 멈춘다.
            st.error(f"오류: 신규 Job 을 만들지 못해 업로드를 중단했습니다. ({e})")
            return
        finally:
            db_s.close()

        target_job_dir = os.path.join(base_raw_dir, project_name, part_name, str(new_pk))
        os.makedirs(target_job_dir, exist_ok=True)
        attr_job_id = new_pk
    elif existing_job_folder:
        target_job_dir = os.path.join(base_raw_dir, project_name, part_name, existing_job_folder)
        os.makedirs(target_job_dir, exist_ok=True)
        attr_job_id = _job_id_of(project_name, part_name, existing_job_folder)

    def save_file(uploaded_file, dest_dir):
        if not uploaded_file:
            return 0
        with open(os.path.join(dest_dir, uploaded_file.name), "wb") as f_out:
            f_out.write(uploaded_file.getbuffer())
        return 1

    saved_count = 0

    if file_cad:
        os.makedirs(cad_dir, exist_ok=True)
        saved_count += save_file(file_cad, cad_dir)

    if target_job_dir:
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

        for files, kind in ((file_roughness, job_layout.ROUGHNESS_DIR),
                            (file_others, job_layout.ETC_DIR)):
            if files:
                dest = job_layout.subdir_path(target_job_dir, kind, create=True)
                for uf in files:
                    saved_count += save_file(uf, dest)

    # 가공 기록(메타데이터·환경·품질)과 작업자 이름을 같은 Job 에 적는다.
    attr_written = []
    if attr_job_id is not None:
        try:
            attr_written = _save_job_attributes(attr_job_id, worker_name, meta, is_new_job)
        except Exception as e:
            st.warning(f"파일은 저장했지만 가공 기록을 쓰지 못했습니다: {e}")

    # 목록 조회는 60초 캐시라(common.load_data), 비우지 않으면 방금 만든 프로젝트·부품·Job 이
    # 한동안 드롭다운에 나타나지 않는다.
    st.cache_data.clear()

    msg_dir = target_job_dir if target_job_dir else cad_dir
    st.success(f"성공적으로 {saved_count}개의 파일을 업로드했습니다! (메인 폴더: {msg_dir})")
    if attr_written:
        st.success("가공 기록 반영: " + " · ".join(attr_written), icon=":material/edit_note:")
    st.info("데이터가 계층형 폴더에 저장되었으며, 백엔드 Watchdog에 의해 파이프라인 처리가 시작됩니다.")
