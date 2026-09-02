import os
import shutil

# Vault root directory
VAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "archive_vault")
# Raw data root directory
RAW_DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "machining_raw_data")

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

