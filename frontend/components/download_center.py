import os
import tempfile
import zipfile

import pandas as pd
import streamlit as st

import job_layout

from .common import RAW_DATA_DIR, load_data

ALL = "(전체)"
RAW_DIR = RAW_DATA_DIR

# (표시 이름, 파일명용 영문 슬러그, 판별 규칙) - 위에서부터 먼저 일치하는 유형으로 분류된다.
CATEGORY_RULES = [
    ("메타데이터 (XML)", "XMLMetadata", lambda p: p.endswith(".xml") and "coeff" not in p),
    ("NC 프로그램", "NCProgram", lambda p: p.endswith(".nc")),
    ("고주파 센서 (TDMS)", "TDMS", lambda p: p.endswith((".tdms", ".tdms_index"))),
    ("표면 조도", "SurfaceRoughness", lambda p: "surface" in p or "조도" in p or p.endswith(".fpk")),
    ("장비 로그", "EquipmentLog", lambda p: p.endswith((".log", ".txt", ".csv"))),
    ("Parquet 분석 데이터", "ParquetData", lambda p: p.endswith(".parquet")),
    ("CAD 모델", "CADModel", lambda p: p.endswith((".stl", ".stp", ".step"))),
]
ETC_LABEL = "기타 파일"
CATEGORY_ORDER = [label for label, _slug, _rule in CATEGORY_RULES] + [ETC_LABEL]
CATEGORY_SLUGS = {label: slug for label, slug, _rule in CATEGORY_RULES}
CATEGORY_SLUGS[ETC_LABEL] = "EtcFiles"


def _categorize(path):
    lower = path.lower()
    for label, _slug, rule in CATEGORY_RULES:
        if rule(lower):
            return label
    return ETC_LABEL


@st.cache_data(ttl=60)
def _job_files(job_id, source_folder):
    """Job 1건의 (실제 경로, 상대 경로) 목록. 원본 폴더가 있으면 폴더에서, 없으면 Vault/DB 아카이브에서 가져온다."""
    folder = _job_folder(source_folder)
    if folder:
        found = []
        for root, _dirs, names in os.walk(folder):
            for name in names:
                abs_path = os.path.join(root, name)
                found.append((abs_path, os.path.relpath(abs_path, folder).replace("\\", "/")))
        if found:
            return found

    from recovery_engine import get_job_archive_files

    archived = []
    for items in get_job_archive_files(job_id).values():
        for abs_path, _base_name, arcname in items:
            if os.path.exists(abs_path):
                archived.append((abs_path, arcname))
    return archived


def _job_folder(source_folder):
    """machining_raw_data 아래 실제 원본 폴더 경로. 없으면 None."""
    normalized = (source_folder or "").replace("\\", "/").strip("/")
    if not normalized:
        return None
    folder = os.path.join(RAW_DIR, *normalized.split("/"))
    return folder if os.path.isdir(folder) else None


def _part_folder(source_folder):
    """Job 의 source_folder({Project}/{Part}/{JobID})에서 Part 폴더 경로를 얻는다.

    CAD 도면은 가공차수가 아니라 부품에 딸린 자료라 Job 폴더보다 한 단계 위에 있다.
    """
    normalized = (source_folder or "").replace("\\", "/").strip("/")
    segments = normalized.split("/")
    if len(segments) < 2:
        return None
    return os.path.join(RAW_DIR, *segments[:2])


def _part_cad_files(scoped):
    """선택한 부품의 CAD 파일 목록 [(실제 경로, 압축 내 경로)].

    원본 폴더(CAD_Files/)를 먼저 보고, 없으면 Vault 경로 -> DB BLOB 순으로 조달한다.
    BLOB 만 남아 있는 경우에는 임시 파일로 풀어 내려받을 수 있게 한다.
    """
    if scoped.empty:
        return []

    source_folder = scoped.iloc[0]['source_folder'] or ""
    part_dir = _part_folder(source_folder)
    if part_dir:
        cad_dir = os.path.join(part_dir, job_layout.CAD_DIR)
        if os.path.isdir(cad_dir):
            found = [(os.path.join(cad_dir, name), f"{job_layout.CAD_DIR}/{name}")
                     for name in sorted(os.listdir(cad_dir))
                     if os.path.isfile(os.path.join(cad_dir, name))]
            if found:
                return found

    # 원본 폴더가 없거나 비었으면 아카이브에서 조달한다.
    from sqlalchemy import text as _text
    import vault_layout

    from .common import engine
    collected = []
    with engine.connect() as conn:
        part_code = conn.execute(_text(
            "SELECT w.part_code FROM job j JOIN workplan w ON j.workplan_id = w.workplan_id "
            "WHERE j.job_id = :jid"
        ), {"jid": int(scoped.iloc[0]['job_id'])}).scalar()
        if not part_code:
            return []

        rows = conn.execute(_text(
            "SELECT c.cad_id, c.file_name, c.file_content, p.part_name "
            "FROM cad_file_archive c JOIN part p ON p.part_code = c.part_code "
            "WHERE c.part_code = :pc ORDER BY c.cad_id"
        ), {"pc": part_code}).fetchall()

    for cad_id, file_name, file_content, part_nm in rows:
        name = file_name or f"Part{part_code}_{cad_id}.step"
        # 보관소 경로는 저장하지 않고 규칙으로 계산한다 (vault_layout).
        abs_vault = vault_layout.find_part_cad(part_nm, file_name)
        if abs_vault:
            collected.append((abs_vault, f"{job_layout.CAD_DIR}/{name}"))
        elif file_content:
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(name)[1])
            temp.write(file_content)
            temp.close()
            collected.append((temp.name, f"{job_layout.CAD_DIR}/{name}"))
    return collected


@st.cache_data(ttl=60)
def _job_catalog():
    df = load_data("""
        SELECT j.job_id, j.source_folder, j.start_time, j.machining_type, p.project_code,
               p.part_name AS part_name
        FROM job j
        LEFT JOIN workplan w ON j.workplan_id = w.workplan_id
        LEFT JOIN part p ON w.part_code = p.part_code
        ORDER BY j.job_id
    """)
    if df.empty:
        return df

    folder_parts = df['source_folder'].fillna("").str.replace("\\", "/", regex=False).str.split("/")
    df['project_name'] = df['project_code'].fillna(folder_parts.str[0]).replace("", pd.NA).fillna("(프로젝트 미지정)")
    df['part_name'] = df['part_name'].fillna(folder_parts.str[1]).replace("", pd.NA).fillna("(Part 미지정)")
    return df


def _collect_files(jobs_df, category=None):
    """선택 범위의 (실제 경로, 압축 내 경로) 목록. category가 None이면 모든 유형을 포함한다."""
    collected = []
    for _, row in jobs_df.iterrows():
        source_folder = row['source_folder'] or f"{row['project_name']}/{row['part_name']}/{row['job_id']}"
        prefix = source_folder.replace("\\", "/").strip("/")
        for abs_path, rel_path in _job_files(row['job_id'], row['source_folder'] or ""):
            if category is None or _categorize(abs_path) == category:
                collected.append((abs_path, f"{prefix}/{rel_path}"))
    return collected


def _make_zip(files):
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_zip.close()
    used_names = set()
    with zipfile.ZipFile(temp_zip.name, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for abs_path, arcname in files:
            unique = arcname
            counter = 1
            while unique in used_names:
                base, ext = os.path.splitext(arcname)
                unique = f"{base}_{counter}{ext}"
                counter += 1
            used_names.add(unique)
            zf.write(abs_path, arcname=unique)
    return temp_zip.name


def _prepare(state_key, files, download_name):
    """파일이 1개면 원본 그대로, 여러 개면 ZIP으로 묶어 다운로드 슬롯에 담는다."""
    if len(files) == 1:
        abs_path, arcname = files[0]
        # 내려받는 이름은 압축 내 경로(원래 이름)를 따른다. DB BLOB 에서 풀어낸 임시 파일은
        # 실제 경로가 tmpXXXX 라서 그대로 쓰면 사용자에게 의미 없는 이름이 내려간다.
        st.session_state[state_key] = {
            'path': abs_path,
            'file_name': os.path.basename(arcname) or os.path.basename(abs_path),
            'mime': "application/octet-stream",
        }
    else:
        st.session_state[state_key] = {
            'path': _make_zip(files),
            'file_name': f"{download_name}.zip",
            'mime': "application/zip",
        }


def _offer_download(state_key, label):
    prepared = st.session_state.get(state_key)
    if prepared and os.path.exists(prepared['path']):
        with open(prepared['path'], "rb") as f:
            st.download_button(
                label=f"{label} — {prepared['file_name']}", data=f, file_name=prepared['file_name'],
                mime=prepared['mime'], key=f"{state_key}_btn", type="primary",
                icon=":material/download:",
            )


def render_download_center():
    st.subheader("데이터 다운로드 내비게이션")
    st.caption("프로젝트 → Part → Job 순으로 좁혀가며 원하는 범위를 내려받습니다. 가공 이력(Job)까지 고르면 "
               "해당 Job에 실제로 존재하는 데이터 유형만 버튼으로 나타납니다.")

    catalog = _job_catalog()
    if catalog.empty:
        st.warning("등록된 가공 이력(Job)이 없어 다운로드할 데이터가 없습니다.")
        return

    n1, n2, n3 = st.columns(3)
    with n1:
        projects = sorted(catalog['project_name'].unique().tolist())
        sel_project = st.selectbox(":material/counter_1: 프로젝트 (Project Name)", [ALL] + projects, key="dc_project")

    scoped = catalog if sel_project == ALL else catalog[catalog['project_name'] == sel_project]

    with n2:
        parts = sorted(scoped['part_name'].unique().tolist())
        sel_part = st.selectbox(":material/counter_2: 부품 (Part Name)", [ALL] + parts, key="dc_part")

    if sel_part != ALL:
        scoped = scoped[scoped['part_name'] == sel_part]

    with n3:
        job_ids = scoped['job_id'].tolist()
        job_labels = {
            row['job_id']: f"Job {row['job_id']} · {row['part_name']} ({str(row['start_time']).split('.')[0] if pd.notnull(row['start_time']) else '시간 미상'})"
            for _, row in scoped.iterrows()
        }
        sel_job = st.selectbox(
            ":material/counter_3: 가공 이력 (Job)", [ALL] + job_ids,
            format_func=lambda x: x if x == ALL else job_labels.get(x, f"Job {x}"), key="dc_job",
        )

    if sel_job != ALL:
        scoped = scoped[scoped['job_id'] == sel_job]

    if sel_job != ALL:
        # Job이 정해지면 프로젝트/부품도 하나로 확정되므로, 실제 값으로 파일명을 만든다.
        job_row = scoped.iloc[0]
        name_parts = [job_row['project_name'], job_row['part_name'], f"Job{sel_job}"]
    else:
        name_parts = [
            sel_project if sel_project != ALL else "AllProjects",
            sel_part if sel_part != ALL else "AllParts",
            "AllJobs",
        ]
    scope_name = "_".join(str(p) for p in name_parts).replace(" ", "").replace("/", "-")

    # 선택 범위가 바뀌면 이전 범위로 준비해 둔 다운로드는 무효다.
    if st.session_state.get('dc_scope_sig') != scope_name:
        st.session_state['dc_scope_sig'] = scope_name
        st.session_state.pop('dc_prepared', None)
        st.session_state.pop('dc_single_download', None)
        st.session_state.pop('dc_cad', None)

    st.divider()

    # CAD 도면은 가공차수가 아니라 부품에 딸린 자료라, 부품이 정해지는 순간 따로 받을 수 있다.
    if sel_part != ALL:
        _render_cad_download(scoped, sel_project, sel_part)

    if sel_job == ALL:
        _render_scope_download(scoped, scope_name)
    else:
        _render_job_download(scoped, scope_name, sel_job)


def _render_cad_download(scoped, sel_project, sel_part):
    """선택한 부품의 CAD 도면만 따로 내려받는 영역."""
    cad_files = _part_cad_files(scoped)
    if not cad_files:
        return

    st.markdown("#### :material/view_in_ar: 부품 도면(CAD) 다운로드")
    st.caption(f"`{sel_part}` 부품에 등록된 3D 도면입니다. 가공차수와 무관하게 부품 단위로 보관됩니다.")

    names = ", ".join(os.path.basename(arc) for _abs, arc in cad_files)
    st.caption(f"대상 파일 {len(cad_files)}개: {names}")

    project_label = sel_project if sel_project != ALL else "AllProjects"
    cad_name = f"{project_label}_{sel_part}_CAD".replace(" ", "").replace("/", "-")

    if st.button("CAD 파일", key="dc_btn_cad", icon=":material/view_in_ar:"):
        with st.spinner("CAD 파일을 준비하는 중입니다..."):
            _prepare('dc_cad', cad_files, cad_name)

    _offer_download('dc_cad', "CAD 다운로드")
    st.divider()


def _render_scope_download(scoped, scope_name):
    st.markdown("#### :material/folder_zip: 선택 범위 전체 다운로드")
    st.caption("가공 이력(Job)을 선택하면 데이터 유형별·파일별로 나눠 받을 수 있습니다.")

    if st.button("선택 범위 압축 준비", type="primary", key="dc_btn_scope"):
        files = _collect_files(scoped)
        if files:
            with st.spinner("파일을 압축하는 중입니다..."):
                _prepare('dc_prepared', files, scope_name)
        else:
            st.session_state.pop('dc_prepared', None)
            st.warning("선택한 범위에 내려받을 수 있는 파일이 없습니다.")

    _offer_download('dc_prepared', "다운로드")


def _render_job_download(scoped, scope_name, job_id):
    job_row = scoped.iloc[0]

    if _job_folder(job_row['source_folder']) is None:
        st.warning("이 Job의 원본 폴더가 로컬 디스크에 없습니다. 아래 다운로드는 Vault/DB 아카이브에서 직접 제공되며, "
                   "필요하면 원본 폴더로 복원할 수도 있습니다.")
        if st.button("로컬 디스크로 원본 복원", key="dc_btn_restore", icon=":material/restore:"):
            from recovery_engine import restore_single_job_to_raw_data
            with st.spinner("Vault 및 DB로부터 파일을 복원하는 중입니다..."):
                dest, count = restore_single_job_to_raw_data(int(job_id))
            if count > 0:
                st.success(f"총 {count}개의 원본 파일을 {dest}에 복원했습니다.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("복원할 수 있는 백업 파일이 Vault/DB에 존재하지 않습니다.")

    files = _collect_files(scoped)
    if not files:
        st.warning("이 Job에는 내려받을 수 있는 파일이 없습니다.")
        return

    counts = {}
    for abs_path, _arc in files:
        label = _categorize(abs_path)
        counts[label] = counts.get(label, 0) + 1
    available = sorted(counts.keys(), key=CATEGORY_ORDER.index)

    st.markdown("#### :material/counter_4: 내려받을 데이터 유형 선택")
    st.caption("이 Job에 존재하는 유형만 표시됩니다. 버튼을 누르면 바로 아래에 다운로드 버튼이 나타납니다.")

    buttons = [("전체 데이터", None, len(files))] + [(label, label, counts[label]) for label in available]
    cols = st.columns(4)
    for idx, (btn_label, category, count) in enumerate(buttons):
        with cols[idx % 4]:
            if st.button(f"{btn_label} ({count})", key=f"dc_cat_{idx}", width="stretch"):
                slug = "AllData" if category is None else CATEGORY_SLUGS[category]
                with st.spinner("파일을 준비하는 중입니다..."):
                    _prepare('dc_prepared', _collect_files(scoped, category), f"{scope_name}_{slug}")

    _offer_download('dc_prepared', "다운로드")

    st.divider()
    st.markdown("#### :material/counter_5: 개별 파일 다운로드")

    file_rows = pd.DataFrame([
        {
            "파일명": os.path.basename(abs_path),
            "데이터 유형": _categorize(abs_path),
            "경로": arcname,
            "크기(MB)": round(os.path.getsize(abs_path) / (1024 * 1024), 3) if os.path.exists(abs_path) else 0,
        }
        for abs_path, arcname in files
    ])
    st.dataframe(file_rows, width="stretch", hide_index=True, height=260)

    picked_idx = st.selectbox(
        "개별로 내려받을 파일 선택", list(range(len(files))),
        format_func=lambda i: f"{os.path.basename(files[i][0])}  —  {files[i][1]}",
        key="dc_single_file",
    )

    if st.button("이 파일 다운로드 준비", key="dc_btn_single"):
        _prepare('dc_single_download', [files[picked_idx]], "file")

    _offer_download('dc_single_download', "다운로드")
