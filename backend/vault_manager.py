import os
import shutil

from dotenv import load_dotenv

# 프로젝트 최상위 경로. 여러 모듈이 각자 dirname 을 몇 번 거슬러 올라가는지 세어가며
# 같은 값을 다시 계산하고 있었는데, 폴더가 한 단계라도 바뀌면 전부 손봐야 해서
# 경로 기준은 여기 한 곳에서만 정한다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# .env 는 프로젝트 루트에 있다. dotenv 의 기본 탐색은 '현재 작업 폴더'부터 거슬러 올라가는
# 방식이라, 다른 폴더에서 이 모듈을 불러오면 .env 를 못 찾아 보관소 위치 설정이 통째로
# 무시된다. 경로를 못 박아 둔다. (이미 OS 환경변수에 있으면 그쪽이 우선이다 - override=False)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 코드와 데이터를 섞지 않도록 런타임 데이터는 전부 data/ 아래에 둔다.
# DB 에 저장되는 경로는 아래 각 루트 기준의 '상대경로'라, 이 상수만 바꾸면
# 폴더를 통째로 옮겨도 기존 레코드를 손볼 필요가 없다.
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")

VAULT_DIR_NAME = "archive_vault"
# 아무 설정도 없을 때 쓰는 예전 위치 (프로젝트 안)
DEFAULT_VAULT_ROOT = os.path.join(DATA_ROOT, VAULT_DIR_NAME)


def _clean_env_path(name):
    """환경변수에서 경로를 읽어 정리한다. 따옴표로 감싼 값과 %VAR%, ~ 도 풀어준다."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    raw = raw.strip('"').strip("'").strip()
    if not raw:
        return None
    return os.path.normpath(os.path.expandvars(os.path.expanduser(raw)))


def resolve_vault_root():
    """원본 백업 보관소(archive_vault)를 어디에 둘지 정한다.

    보관소가 프로젝트 안에 있으면 프로젝트 폴더를 지우는 순간 원본 백업까지 같이 사라진다.
    백업의 존재 이유가 '원본이 없어져도 복원할 수 있다'는 것이라, 보관소는 프로젝트와
    수명이 다른 곳(예: DB 가 설치된 폴더)에 두어야 한다. 그래서 위치를 환경변수로 뺀다.

    우선순위:
      1) ORCUS_VAULT_ROOT   - 보관소 절대경로를 직접 지정 (가장 명시적)
      2) ORCUS_DB_DATA_DIR  - DB 가 설치된 폴더. 그 아래 archive_vault/ 를 만들어 쓴다
      3) 둘 다 없으면 예전 위치(프로젝트 안 data/archive_vault)를 그대로 쓴다

    DB 에는 이 루트 기준의 '상대경로'만 저장되므로, 값을 바꾸고 폴더만 옮기면
    기존 레코드는 손대지 않아도 그대로 살아난다.
    """
    explicit = _clean_env_path("ORCUS_VAULT_ROOT")
    if explicit:
        return explicit

    db_data_dir = _clean_env_path("ORCUS_DB_DATA_DIR")
    if db_data_dir:
        return os.path.join(db_data_dir, VAULT_DIR_NAME)

    return DEFAULT_VAULT_ROOT


# 원본 파일 안전 보관소 (프로젝트 밖으로 뺄 수 있다)
VAULT_ROOT = resolve_vault_root()
# 보관소가 프로젝트 밖에 있는지. 초기화/삭제처럼 위험한 동작에서 경고 문구를 가르는 데 쓴다.
VAULT_IS_EXTERNAL = os.path.normcase(os.path.normpath(VAULT_ROOT)) != \
                    os.path.normcase(os.path.normpath(DEFAULT_VAULT_ROOT))

# 장비 데이터 유입 감시 폴더
RAW_DATA_ROOT = os.path.join(DATA_ROOT, "machining_raw_data")
# 변환 산출물(Parquet, 구간 판별 JSON)
PROCESSED_ROOT = os.path.join(DATA_ROOT, "processed_data")
# 파싱 실패 격리 보관소 (DLQ)
FAILED_ROOT = os.path.join(DATA_ROOT, "failed_data")


def is_inside_vault(path):
    """주어진 경로가 백업 보관소 자신이거나 그 안쪽인지 판정한다.

    보관소는 '원본이 없어져도 복원할 수 있다'는 마지막 방어선이라, 초기화 같은 일괄 삭제가
    실수로라도 닿으면 안 된다. 삭제를 수행하는 쪽에서 이 함수로 먼저 걸러낸다.

    문자열 앞부분 비교(startswith)로 판정하면 '...archive_vault_old' 같은 이름이 보관소
    안쪽으로 잘못 걸리므로, 경로를 단계별로 분해해 비교한다.
    """
    if not path:
        return False
    try:
        target = os.path.normcase(os.path.abspath(path))
        vault = os.path.normcase(os.path.abspath(VAULT_ROOT))
    except Exception:
        return False

    if target == vault:
        return True

    # os.path.commonpath 는 드라이브가 다르면 ValueError 를 낸다 (D: 프로젝트 vs C: 보관소).
    # 그 경우는 애초에 서로 무관한 경로라 False 가 맞다.
    try:
        return os.path.commonpath([target, vault]) == vault
    except ValueError:
        return False


def ensure_vault_root():
    """보관소 루트를 만들고 실제로 쓸 수 있는지 확인한다.

    반환: (사용가능 여부, 사람이 읽을 설명 문구)

    여기서 조용히 프로젝트 안쪽으로 되돌리지 않는다. 그렇게 하면 백업이 보호되고 있다고
    믿는 동안 실제로는 프로젝트와 함께 지워질 자리에 쌓이게 되어, 애초에 막으려던 사고를
    그대로 다시 부른다. 쓸 수 없으면 쓸 수 없다고 분명히 알린다.
    """
    try:
        os.makedirs(VAULT_ROOT, exist_ok=True)
    except Exception as e:
        return False, f"백업 보관소를 만들 수 없습니다: {VAULT_ROOT} ({e})"

    probe = os.path.join(VAULT_ROOT, ".write_test")
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
    except Exception as e:
        return False, f"백업 보관소에 쓸 수 없습니다: {VAULT_ROOT} ({e})"

    where = "프로젝트 밖" if VAULT_IS_EXTERNAL else "프로젝트 안"
    return True, f"백업 보관소({where}): {VAULT_ROOT}"

def get_rel_raw_data_path(abs_path):
    """
    Converts an absolute path to a relative path based on RAW_DATA_ROOT.
    Returns relative path using forward slashes for DB storage.
    If the path is already relative or outside the root, returns it as is.
    """
    if not abs_path:
        return None
    try:
        if os.path.isabs(abs_path) and RAW_DATA_ROOT in os.path.abspath(abs_path):
            rel = os.path.relpath(abs_path, RAW_DATA_ROOT)
            return rel.replace("\\", "/")
    except ValueError:
        pass
    
    # Check if it's already a relative path in DB format
    if "/" in abs_path or "\\" in abs_path:
        # Avoid breaking if it happens to be valid already
        if not os.path.isabs(abs_path):
            return abs_path.replace("\\", "/")
            
    return abs_path.replace("\\", "/")

def get_abs_raw_data_path(rel_path):
    """
    Resolves a DB stored relative raw data path into an absolute path on disk.
    If it's already an absolute path (for backward compatibility), returns it.
    """
    if not rel_path:
        return None
    if os.path.isabs(rel_path):
        return rel_path
    
    # It's a relative path, resolve it with RAW_DATA_ROOT
    subpaths = rel_path.split("/")
    return os.path.normpath(os.path.join(RAW_DATA_ROOT, *subpaths))

def get_vault_path(*subpaths):
    """
    Get the absolute path for a vault file/directory.
    Ensures the parent directories exist.
    """
    path = os.path.join(VAULT_ROOT, *subpaths)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

def save_to_vault(source_file_path, *vault_subpaths):
    """
    Copies a file from source_file_path into the archive_vault at the given subpaths.
    Returns the relative path to be stored in the DB.
    """
    if not source_file_path or not os.path.exists(source_file_path):
        return None
        
    abs_vault_path = get_vault_path(*vault_subpaths)
    shutil.copy2(source_file_path, abs_vault_path)
    
    # Return relative path for DB storage (using forward slashes for consistency)
    return "/".join(vault_subpaths)

def read_from_vault(relative_vault_path):
    """
    Reads the content of a file from the vault.
    Returns bytes or None.
    """
    if not relative_vault_path:
        return None
        
    # Convert relative path back to OS specific
    subpaths = relative_vault_path.split("/")
    abs_path = os.path.join(VAULT_ROOT, *subpaths)
    
    if os.path.exists(abs_path):
        with open(abs_path, "rb") as f:
            return f.read()
    return None

def get_abs_vault_path(relative_vault_path):
    """
    Returns the absolute path to a vault file.
    """
    if not relative_vault_path:
        return None
    subpaths = relative_vault_path.split("/")
    return os.path.join(VAULT_ROOT, *subpaths)

def file_exists_in_vault(relative_vault_path):
    """
    Checks if a file exists in the vault.
    """
    if not relative_vault_path:
        return False
    abs_path = get_abs_vault_path(relative_vault_path)
    return abs_path is not None and os.path.exists(abs_path)

