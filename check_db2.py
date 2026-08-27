import os
import glob
from backend.DB.database import engine
from sqlalchemy import text
import pandas as pd

raw_dir = 'machining_raw_data'

# Find all deepest directories that contain files
job_folders = []
for root, dirs, files in os.walk(raw_dir):
    if len(files) > 0: # If it has files, it's likely a job folder (or part of it)
        # We need the relative path of the job folder.
        # Actually the watchdog parses 3 levels deep: Project/Part/Job
        rel_path = os.path.relpath(root, raw_dir).replace('\\\\', '/')
        if len(rel_path.split('/')) >= 3:
            # Only count the 3-level deep directory as a job folder
            job_folder = '/'.join(rel_path.split('/')[:3])
            if job_folder not in job_folders:
                job_folders.append(job_folder)

print(f"Total job folders found in raw_data (3 levels deep): {len(job_folders)}")
for jf in job_folders:
    print(f" - {jf}")

with engine.connect() as conn:
    jobs = pd.read_sql(text("SELECT * FROM job"), conn)
    job_archives = pd.read_sql(text("SELECT job_id, LENGTH(xml_file_content) as xml_size FROM job_file_archive"), conn)
    roughness = pd.read_sql(text("SELECT * FROM surface_roughness"), conn)
    
    missing_xml_db = []
    missing_tdms_db = []
    missing_log_db = []
    missing_fpk_db = []
    
    for folder in job_folders:
        folder_path = os.path.join(raw_dir, folder)
        
        # Check files anywhere inside this job folder
        xml_files = []
        has_tdms = False
        has_log = False
        has_fpk = False
        
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = file.split('.')[-1].lower()
                if ext == 'xml' and 'coeff' not in file.lower():
                    xml_files.append(os.path.join(root, file))
                elif ext == 'tdms':
                    has_tdms = True
                elif ext == 'log':
                    has_log = True
                elif ext == 'fpk':
                    has_fpk = True
        
        has_xml = len(xml_files) > 0
        
        # Check DB
        job_exists = folder in jobs['source_folder'].values
        
        if not job_exists:
            print(f"WARNING: Folder '{folder}' is NOT IN DB!")
            continue
            
        job_id = jobs[jobs['source_folder'] == folder]['job_id'].iloc[0]
        
        if has_xml:
            job_archive_row = job_archives[job_archives['job_id'] == job_id]
            if job_archive_row.empty or pd.isnull(job_archive_row['xml_size'].iloc[0]):
                missing_xml_db.append(folder)
                
        tdms_path = jobs[jobs['job_id'] == job_id]['tdms_file_path'].iloc[0]
        if has_tdms and pd.isnull(tdms_path):
            missing_tdms_db.append(folder)
            
        log_path = jobs[jobs['job_id'] == job_id]['log_file_path'].iloc[0]
        if has_log and pd.isnull(log_path):
            missing_log_db.append(folder)
            
        if has_fpk:
            sr_count = len(roughness[roughness['job_id'] == job_id])
            if sr_count == 0:
                missing_fpk_db.append(folder)

    print(f"\nResults for folders mapped to DB:")
    print(f"Folders with XML but no XML BLOB: {len(missing_xml_db)} {missing_xml_db}")
    print(f"Folders with TDMS but no TDMS path mapped: {len(missing_tdms_db)} {missing_tdms_db}")
    print(f"Folders with LOG but no LOG path mapped: {len(missing_log_db)} {missing_log_db}")
    print(f"Folders with FPK but no SurfaceRoughness records: {len(missing_fpk_db)} {missing_fpk_db}")
    
    xml_cols = ['machine_code', 'start_time', 'end_time', 'cutting_seconds', 'is_finish']
    null_counts = jobs[xml_cols].isnull().sum()
    print("\nNULL counts for XML mapped columns in Job table:")
    print(null_counts)
