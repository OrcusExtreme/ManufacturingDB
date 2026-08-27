import os
import glob
from sqlalchemy import create_engine, text
import pandas as pd

engine = create_engine('mysql+pymysql://root:0000@127.0.0.1:3306/lab_db')

raw_dir = 'machining_raw_data'
folders = [f for f in os.listdir(raw_dir) if os.path.isdir(os.path.join(raw_dir, f))]
print(f"Total folders in raw_data: {len(folders)}")

with engine.connect() as conn:
    jobs = pd.read_sql(text("SELECT * FROM job"), conn)
    print(f"Total jobs in DB: {len(jobs)}")
    
    parts = pd.read_sql(text("SELECT * FROM part"), conn)
    print(f"Total parts in DB: {len(parts)}")
    
    machine_logs = pd.read_sql(text("SELECT * FROM machine_log"), conn)
    print(f"Total machine_log records: {len(machine_logs)}")
    
    roughness = pd.read_sql(text("SELECT * FROM surface_roughness"), conn)
    print(f"Total surface_roughness records: {len(roughness)}")
    
    # Check for NULLs in XML mapped columns
    xml_cols = ['machine_code', 'start_time', 'end_time', 'cutting_seconds', 'is_finish']
    null_counts = jobs[xml_cols].isnull().sum()
    print("\nNULL counts for XML mapped columns in Job table:")
    print(null_counts)
    
    # Check for missing archive BLOBs
    job_archives = pd.read_sql(text("SELECT job_id, LENGTH(xml_file_content) as xml_size FROM job_file_archive"), conn)
    print(f"\nTotal job_file_archives: {len(job_archives)}")
    print(f"Jobs with missing XML BLOB: {job_archives['xml_size'].isnull().sum()}")

    print("\n--- Detailed File Presence vs DB Analysis ---")
    
    # Scan raw_data and cross-check
    missing_xml_db = []
    missing_tdms_db = []
    missing_log_db = []
    missing_fpk_db = []
    
    for folder in folders:
        folder_path = os.path.join(raw_dir, folder)
        
        # Check XML
        xml_files = glob.glob(os.path.join(folder_path, '*.xml'))
        xml_files = [f for f in xml_files if 'coeff' not in os.path.basename(f).lower()]
        has_xml = len(xml_files) > 0
        
        # Check TDMS
        has_tdms = len(glob.glob(os.path.join(folder_path, '*.tdms'))) > 0
        
        # Check LOG
        has_log = len(glob.glob(os.path.join(folder_path, '*.log'))) > 0
        
        # Check FPK (Surface Roughness)
        has_fpk = len(glob.glob(os.path.join(folder_path, '*.fpk'))) > 0
        
        # Check if Job exists in DB
        job_exists = folder in jobs['source_folder'].values
        
        if not job_exists:
            print(f"Folder '{folder}' NOT IN DB!")
            continue
            
        job_id = jobs[jobs['source_folder'] == folder]['job_id'].iloc[0]
        
        # Cross check XML
        job_archive_row = job_archives[job_archives['job_id'] == job_id]
        if has_xml:
            if job_archive_row.empty or pd.isnull(job_archive_row['xml_size'].iloc[0]):
                missing_xml_db.append(folder)
                
        # Cross check TDMS mapped path
        tdms_path = jobs[jobs['job_id'] == job_id]['tdms_file_path'].iloc[0]
        if has_tdms and pd.isnull(tdms_path):
            missing_tdms_db.append(folder)
            
        # Cross check LOG mapped path
        log_path = jobs[jobs['job_id'] == job_id]['log_file_path'].iloc[0]
        if has_log and pd.isnull(log_path):
            missing_log_db.append(folder)
            
        # Cross check FPK mapped records
        if has_fpk:
            sr_count = len(roughness[roughness['job_id'] == job_id])
            if sr_count == 0:
                missing_fpk_db.append(folder)
                
    print(f"Folders with XML but no XML BLOB in DB: {len(missing_xml_db)} {missing_xml_db}")
    print(f"Folders with TDMS but no TDMS path mapped in Job: {len(missing_tdms_db)} {missing_tdms_db}")
    print(f"Folders with LOG but no LOG path mapped in Job: {len(missing_log_db)} {missing_log_db}")
    print(f"Folders with FPK but no SurfaceRoughness records in DB: {len(missing_fpk_db)} {missing_fpk_db}")
