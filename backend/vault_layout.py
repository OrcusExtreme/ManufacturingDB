"""보관소(archive_vault) 안의 파일 배치 규칙 — 단일 출처.

왜 경로를 DB 에 저장하지 않나
-----------------------------
보관소 경로는 자유로운 값이 아니라 **레코드로부터 계산되는 규칙**이다.

    XML      jobs/{source_folder}/metadata.xml
    NC       workplan_nc/WP{workplan_id}.nc
    TDMS     tdms_files/Job_{job_id}/{파일명}
    로그     machine_logs/Job_{job_id}/{파일명}
    조도     surface_roughness/Job_{job_id}/{파일명}
    Parquet  processed_parquet/job_{job_id}_viz.parquet · _fft.parquet
    CAD      cad_models/Part_{부품명}/{파일명}
    etc      etc_files/Job_{job_id}/{파일명}

같은 규칙을 DB 컬럼에도 적어 두면 규칙이 두 곳(코드 + 데이터)에 살게 된다. 그러면 배치를
한 번 바꿀 때 이미 쌓인 행이 전부 옛 규칙을 가리키고, 어느 쪽이 맞는지 알 수 없다.
규칙은 여기 한 곳에만 두고 필요할 때 계산한다.

원본 경로(*_raw_path)는 이야기가 다르다. 장비가 어디에 떨궜는지는 규칙이 아니라 사실이라
DB 에 남는다. 보관소 안의 파일 이름도 대개 그 원본의 파일명과 같으므로, 이름이 필요한
자리에서는 *_raw_path 의 basename 을 쓰고, 그래도 모르면 폴더를 훑는다(resolve_in_dir).
"""
import os

from vault_manager import VAULT_ROOT, get_abs_vault_path

# 보관소 최상위 분류 폴더
JOBS_DIR = "jobs"
WORKPLAN_NC_DIR = "workplan_nc"
TDMS_DIR = "tdms_files"
LOG_DIR = "machine_logs"
ROUGHNESS_DIR = "surface_roughness"
PARQUET_DIR = "processed_parquet"
CAD_DIR = "cad_models"
ETC_DIR = "etc_files"
TOOL_MASTER_DIR = "tool_master"


def _basename(path):
    """경로에서 파일 이름만 뽑는다. 구분자가 섞여 있어도 안전하게."""
    if not path:
        return None
    return os.path.basename(str(path).replace("\\", "/"))


# ---------------------------------------------------------------------------
# 경로 계산 (보관소 루트 기준 상대경로)
# ---------------------------------------------------------------------------
def xml_rel(source_folder):
    """jobs/{프로젝트}/{부품}/{차수}/metadata.xml"""
    if not source_folder:
        return None
    return "/".join([JOBS_DIR, *str(source_folder).strip("/").split("/"), "metadata.xml"])


def workplan_nc_rel(workplan_id):
    """workplan_nc/WP{id}.nc"""
    return f"{WORKPLAN_NC_DIR}/WP{workplan_id}.nc" if workplan_id else None


def tdms_dir_rel(job_id):
    return f"{TDMS_DIR}/Job_{job_id}"


def log_dir_rel(job_id):
    return f"{LOG_DIR}/Job_{job_id}"


def roughness_dir_rel(job_id):
    return f"{ROUGHNESS_DIR}/Job_{job_id}"


def etc_dir_rel(job_id):
    return f"{ETC_DIR}/Job_{job_id}"


def cad_dir_rel(part_name):
    return f"{CAD_DIR}/Part_{part_name}" if part_name else None


def parquet_rel(job_id, kind="viz"):
    """processed_parquet/job_{id}_viz.parquet · _fft.parquet"""
    return f"{PARQUET_DIR}/job_{job_id}_{kind}.parquet" if job_id else None


def in_dir_rel(dir_rel, file_name):
    """분류 폴더 안의 파일 한 개를 가리키는 상대경로."""
    name = _basename(file_name)
    return f"{dir_rel}/{name}" if (dir_rel and name) else None


# ---------------------------------------------------------------------------
# 실제 파일 찾기
# ---------------------------------------------------------------------------
def abs_if_exists(rel_path):
    """상대경로를 절대경로로 바꾸고, 실제로 있을 때만 돌려준다."""
    if not rel_path:
        return None
    p = get_abs_vault_path(rel_path)
    return p if p and os.path.exists(p) else None


def resolve_in_dir(dir_rel, preferred_name=None, suffixes=None):
    """분류 폴더에서 파일 하나를 찾는다.

    1) preferred_name(대개 원본 파일명)이 그 폴더에 있으면 그것
    2) 없으면 폴더를 훑어 조건에 맞는 첫 파일
       (보관소 파일명이 원본과 다를 수 있어 — 예: 측정기가 이름을 바꿔 내보낸 조도 CSV)

    반환: 절대경로 또는 None
    """
    direct = abs_if_exists(in_dir_rel(dir_rel, preferred_name)) if preferred_name else None
    if direct:
        return direct

    folder = get_abs_vault_path(dir_rel) if dir_rel else None
    if not folder or not os.path.isdir(folder):
        return None
    lowered = tuple(s.lower() for s in suffixes) if suffixes else None
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        if lowered and not name.lower().endswith(lowered):
            continue
        return full
    return None


def list_in_dir(dir_rel, suffixes=None):
    """분류 폴더 안의 파일 전부를 절대경로로 돌려준다 (이름순)."""
    folder = get_abs_vault_path(dir_rel) if dir_rel else None
    if not folder or not os.path.isdir(folder):
        return []
    lowered = tuple(s.lower() for s in suffixes) if suffixes else None
    out = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if os.path.isfile(full) and (not lowered or name.lower().endswith(lowered)):
            out.append(full)
    return out


# ---------------------------------------------------------------------------
# 레코드에서 바로 찾기 (호출부가 규칙을 몰라도 되도록)
# ---------------------------------------------------------------------------
def find_job_xml(job):
    return abs_if_exists(xml_rel(getattr(job, "source_folder", None)))


def find_workplan_nc(workplan_id):
    return abs_if_exists(workplan_nc_rel(workplan_id))


def find_job_tdms(job_id, raw_path=None):
    return resolve_in_dir(tdms_dir_rel(job_id), _basename(raw_path), (".tdms",))


def find_job_log(job_id, raw_path=None):
    return resolve_in_dir(log_dir_rel(job_id), _basename(raw_path))


def find_job_parquet(job_id, kind="viz"):
    return abs_if_exists(parquet_rel(job_id, kind))


def find_part_cad(part_name, file_name=None):
    return resolve_in_dir(cad_dir_rel(part_name), _basename(file_name),
                          (".step", ".stp", ".stl"))
