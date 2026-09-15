import os
import shutil

# 프로젝트 최상위 경로. 여러 모듈이 각자 dirname 을 몇 번 거슬러 올라가는지 세어가며
# 같은 값을 다시 계산하고 있었는데, 폴더가 한 단계라도 바뀌면 전부 손봐야 해서
# 경로 기준은 여기 한 곳에서만 정한다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 코드와 데이터를 섞지 않도록 런타임 데이터는 전부 data/ 아래에 둔다.
# DB 에 저장되는 경로는 아래 각 루트 기준의 '상대경로'라, 이 상수만 바꾸면
# 폴더를 통째로 옮겨도 기존 레코드를 손볼 필요가 없다.
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")

# 원본 파일 안전 보관소
VAULT_ROOT = os.path.join(DATA_ROOT, "archive_vault")
# 장비 데이터 유입 감시 폴더
RAW_DATA_ROOT = os.path.join(DATA_ROOT, "machining_raw_data")
# 변환 산출물(Parquet, 구간 판별 JSON)
PROCESSED_ROOT = os.path.join(DATA_ROOT, "processed_data")
# 파싱 실패 격리 보관소 (DLQ)
FAILED_ROOT = os.path.join(DATA_ROOT, "failed_data")

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

