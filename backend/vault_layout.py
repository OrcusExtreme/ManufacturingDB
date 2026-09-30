"""보관소(archive_vault) 안의 파일 배치 규칙 — 단일 출처.

규칙은 한 줄이다: **보관소 상대경로 = machining_raw_data 상대경로.**

    machining_raw_data/Alchemist/DB_test/1/TDMS/foo.tdms
    archive_vault/     Alchemist/DB_test/1/TDMS/foo.tdms

그래서 보관소 트리는 원본 트리를 그대로 비춘다.

    archive_vault/
      {Project}/{Part}/{JobID}/XML · NC · TDMS · Log · Surface_Roughness
                              /processed_parquet · etc
      {Project}/{Part}/CAD_Files/
      실험실 공구 정리.xlsx

왜 바꿨나
---------
예전에는 보관소가 파일을 **합성한 이름과 id 기반 폴더**에 담았다.

    jobs/{source_folder}/metadata.xml       <- 원래 이름을 버림
    workplan_nc/WP{workplan_id}.nc          <- 원래 이름을 버림
    tdms_files/Job_{job_id}/...             <- job_id 에 의존

job_id·workplan_id 는 대리키(surrogate key)라 재구축·수집순서·실패에 따라 값이 달라진다.
실제로 auto-increment 가 한 번 밀리자 보관소의 Job_1 이 job2 로 복원됐고, 이름을 잃은
`WP2.nc` 가 다시 program_code 로 읽히면서 신원이 또 어긋났다. 복원이 왕복하지 않았다.

원본 경로를 그대로 쓰면 경로에 id 가 전혀 없으므로 id 가 어떻게 바뀌든 보관소는 멀쩡하고,
복원은 단순 폴더 복사가 되어 파일명까지 원래대로 돌아온다.

분류 폴더 이름(XML/Log/TDMS/...)은 원본과 같아야 하므로 job_layout 의 상수를 그대로 쓴다.
"""
import os

import job_layout
from vault_manager import get_abs_vault_path


def _segments(source_folder):
    """'Alchemist/DB_test/1' -> ['Alchemist', 'DB_test', '1']"""
    if not source_folder:
        return []
    return [s for s in str(source_folder).replace("\\", "/").strip("/").split("/") if s]


# ---------------------------------------------------------------------------
# 경로 계산 (보관소 루트 기준 상대경로)
# ---------------------------------------------------------------------------
def vault_rel_for_raw(raw_rel_path):
    """원본 상대경로를 보관소 상대경로로. 규칙의 전부 — 항등 함수다.

    이 함수가 존재하는 이유는 변환이 필요해서가 아니라, '두 트리가 같다'는 규칙을
    코드에 한 번 적어 두기 위해서다. 나중에 규칙이 달라지면 여기만 고치면 된다.
    """
    if not raw_rel_path:
        return None
    return str(raw_rel_path).replace("\\", "/").strip("/")


def job_dir_rel(source_folder):
    """'{Project}/{Part}/{JobID}'"""
    seg = _segments(source_folder)
    return "/".join(seg) if seg else None


def part_dir_rel(source_folder):
    """'{Project}/{Part}' — CAD_Files 의 부모 (Job 폴더의 형제)."""
    seg = _segments(source_folder)
    return "/".join(seg[:-1]) if len(seg) >= 2 else None


def cad_dir_rel(project_code, part_name):
    """'{Project}/{Part}/CAD_Files' — 도면은 가공차수와 무관해 Part 레벨에 있다."""
    if not project_code or not part_name:
        return None
    return f"{project_code}/{part_name}/{job_layout.CAD_DIR}"


def kind_dir_rel(source_folder, kind):
    """'{Project}/{Part}/{JobID}/{분류}' — 분류 이름은 job_layout 상수를 쓴다."""
    base = job_dir_rel(source_folder)
    return f"{base}/{kind}" if base else None


def tool_master_rel(file_name=None):
    """보관소 루트의 공구 마스터 엑셀 (raw_data 루트와 같은 자리)."""
    return file_name or TOOL_MASTER_FILENAME


TOOL_MASTER_FILENAME = "실험실 공구 정리.xlsx"


# ---------------------------------------------------------------------------
# 절대경로로 바꾸기
# ---------------------------------------------------------------------------
def abs_if_exists(rel_path):
    """상대경로를 절대경로로 바꾸고, 실제로 있을 때만 돌려준다."""
    if not rel_path:
        return None
    p = get_abs_vault_path(rel_path)
    return p if p and os.path.exists(p) else None


def job_dir_abs(source_folder):
    """보관소 안의 Job 폴더 절대경로 (없어도 경로는 돌려준다)."""
    rel = job_dir_rel(source_folder)
    return get_abs_vault_path(rel) if rel else None


# ---------------------------------------------------------------------------
# 파일 찾기 — 분류 폴더 우선, 없으면 Job 루트 (job_layout.find_files 와 같은 규칙)
# ---------------------------------------------------------------------------
def find_in_job(source_folder, kind, extensions, preferred_name=None):
    """보관소의 Job 폴더에서 해당 분류의 파일 하나를 찾는다.

    preferred_name(대개 원본 파일명)이 있으면 그것을 먼저 고른다. 보관소가 원본 이름을
    그대로 쓰므로 대개 바로 맞지만, 못 찾으면 분류 폴더를 훑어 첫 파일을 돌려준다.
    """
    job_dir = job_dir_abs(source_folder)
    if not job_dir or not os.path.isdir(job_dir):
        return None
    found = job_layout.find_files(job_dir, kind, extensions)
    if not found:
        return None
    if preferred_name:
        wanted = os.path.basename(str(preferred_name).replace("\\", "/")).lower()
        for p in found:
            if os.path.basename(p).lower() == wanted:
                return p
    return found[0]


def list_in_job(source_folder, kind, extensions=None):
    """보관소 Job 폴더의 한 분류에 든 파일 전부 (이름순)."""
    job_dir = job_dir_abs(source_folder)
    if not job_dir or not os.path.isdir(job_dir):
        return []
    folder = os.path.join(job_dir, kind)
    if not os.path.isdir(folder):
        return []
    suffixes = tuple(e.lower() for e in extensions) if extensions else None
    out = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if os.path.isfile(full) and (not suffixes or name.lower().endswith(suffixes)):
            out.append(full)
    return out


# ---------------------------------------------------------------------------
# 레코드에서 바로 찾기 (호출부가 규칙을 몰라도 되도록)
# ---------------------------------------------------------------------------
def _sf(job_or_sf):
    """Job 객체든 source_folder 문자열이든 받아 준다."""
    return getattr(job_or_sf, "source_folder", job_or_sf)


def find_job_xml(job_or_sf, raw_path=None):
    return find_in_job(_sf(job_or_sf), job_layout.XML_DIR, (".xml",), raw_path)


def find_job_nc(job_or_sf, raw_path=None):
    return find_in_job(_sf(job_or_sf), job_layout.NC_DIR, (".nc",), raw_path)


def find_job_tdms(job_or_sf, raw_path=None):
    return find_in_job(_sf(job_or_sf), job_layout.TDMS_DIR, (".tdms",), raw_path)


def find_job_log(job_or_sf, raw_path=None):
    return find_in_job(_sf(job_or_sf), job_layout.LOG_DIR, (".log", ".csv"), raw_path)


def find_job_parquet(job_or_sf, kind="viz"):
    """processed_parquet/viz.parquet · fft.parquet — 이름에 id 를 쓰지 않는다."""
    rel = kind_dir_rel(_sf(job_or_sf), job_layout.PARQUET_DIR)
    return abs_if_exists(f"{rel}/{kind}.parquet") if rel else None


def list_job_roughness(job_or_sf, extensions=None):
    return list_in_job(_sf(job_or_sf), job_layout.ROUGHNESS_DIR, extensions)


def list_job_etc(job_or_sf):
    return list_in_job(_sf(job_or_sf), job_layout.ETC_DIR)


def find_part_cad(project_code, part_name, file_name=None):
    """도면은 Part 레벨({Project}/{Part}/CAD_Files)에 있다."""
    rel = cad_dir_rel(project_code, part_name)
    if not rel:
        return None
    folder = get_abs_vault_path(rel)
    if not folder or not os.path.isdir(folder):
        return None
    suffixes = (".step", ".stp", ".stl")
    names = sorted(n for n in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, n)))
    if file_name:
        wanted = os.path.basename(str(file_name).replace("\\", "/")).lower()
        for n in names:
            if n.lower() == wanted:
                return os.path.join(folder, n)
    for n in names:
        if n.lower().endswith(suffixes):
            return os.path.join(folder, n)
    return None


def list_part_cad(project_code, part_name):
    rel = cad_dir_rel(project_code, part_name)
    if not rel:
        return []
    folder = get_abs_vault_path(rel)
    if not folder or not os.path.isdir(folder):
        return []
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder))
            if os.path.isfile(os.path.join(folder, n))]
