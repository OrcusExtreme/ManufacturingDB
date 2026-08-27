import os
import glob
import time
from backend.DB.database import SessionLocal
from backend.parsers.xml_parser import parse_xml
from backend.parsers.nc_parser import parse_nc
from backend.parsers.log_parser import parse_log
from backend.parsers.roughness_parser import parse_roughness
from backend.parsers.tdms_parser import parse_tdms

raw_dir = 'machining_raw_data'

# Find all 3-level deep job folders
job_folders = []
for root, dirs, files in os.walk(raw_dir):
    if len(files) > 0:
        rel_path = os.path.relpath(root, raw_dir).replace('\\', '/')
        if len(rel_path.split('/')) >= 3:
            job_folder = '/'.join(rel_path.split('/')[:3])
            if job_folder not in job_folders:
                job_folders.append(job_folder)

print(f"Total job folders found: {len(job_folders)}")

for folder in job_folders:
    folder_path = os.path.join(raw_dir, folder)
    print(f"\nProcessing Job Folder: {folder}")
    
    xml_files = []
    nc_files = []
    log_files = []
    fpk_files = []
    tdms_files = []
    
    for root, _, files in os.walk(folder_path):
        for file in files:
            ext = file.split('.')[-1].lower()
            full_path = os.path.join(root, file)
            
            if ext == 'xml' and 'coeff' not in file.lower() and 'old' not in file.lower() and 'backup' not in file.lower():
                xml_files.append(full_path)
            elif ext == 'nc':
                nc_files.append(full_path)
            elif ext == 'tdms':
                tdms_files.append(full_path)
            elif ext == 'log':
                log_files.append(full_path)
            elif ext in ['fpk', 'txt', 'csv']:
                if '조도' in file or 'roughness' in file.lower() or ext == 'fpk':
                    fpk_files.append(full_path)
            elif ext in ['step', 'stp', 'stl']:
                # The folder hierarchy is project/part/...
                parts = full_path.replace('\\', '/').split('/')
                try:
                    raw_idx = parts.index('machining_raw_data')
                    project_name = parts[raw_idx + 1]
                    part_name = parts[raw_idx + 2]
                    from backend.parsers.cad_parser import parse_cad
                    print(f" -> Found CAD: {full_path}")
                    parse_cad(full_path, project_name, part_name)
                except ValueError:
                    pass
                    
    if xml_files:
        print(f" -> Found XML: {xml_files[0]}")
        parse_xml(xml_files[0], folder)
    elif nc_files:
        print(f" -> Found NC (no XML): {nc_files[0]}")
        parse_nc(nc_files[0], folder)
        
    for log_f in log_files:
        print(f" -> Found LOG: {log_f}")
        parse_log(log_f, folder)
        
    if fpk_files:
        print(f" -> Found Roughness: {fpk_files[0]}")
        parse_roughness(folder_path, folder)
        
    for tdms_f in tdms_files:
        print(f" -> Found TDMS: {tdms_f}")
        parse_tdms(tdms_f, folder)

print("\nReparsing complete!")
