import os
import shutil

# Vault root directory
VAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "archive_vault")

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

