import os
import glob
import hashlib
import xml.etree.ElementTree as ET
from DB.database import SessionLocal
from DB.models import Part, Workplan, Workingstep, Job, Tool, WorkplanFileArchive, JobFileArchive
from vault_manager import save_to_vault
from integrity import sha256_bytes

def safe_float(val, default=0.0):
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    try:
        return int(val) if val else default
    except (ValueError, TypeError):
        return default

def parse_xml(file_path, job_id):
    """
    XML 파일을 파싱하여 DB에 저장하는 모듈
    (ISO14649 Part -> Workplan -> Workingstep 구조 반영)
    """
    print(f"  -> [XML Parser] 파일: {os.path.basename(file_path)} / Folder ID: {job_id}")
    
    db = SessionLocal()
    try:
        # XML 파싱
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 최상위 태그가 WorkModel이 아니면 무시
        if root.tag != 'WorkModel':
            print(f"    - [스킵] 메인 가공 데이터(WorkModel)가 아닙니다. (태그: {root.tag})")
            return
            
        # 동일한 폴더 내 NC 파일 찾기 (계층형 구조로 인해 1:1 매칭됨)
        xml_dir = os.path.dirname(file_path)
        
        nc_files = glob.glob(os.path.join(xml_dir, "*.nc"))
        nc_files.extend(glob.glob(os.path.join(xml_dir, "*.NC")))
        
        # 백업 파일 매핑 방지 필터링
        valid_nc_files = [f for f in nc_files if 'backup' not in f.lower() and 'old' not in f.lower()]
        nc_file_path = valid_nc_files[0] if valid_nc_files else None
        
        nc_hash = "NOHASH"
        if nc_file_path and os.path.exists(nc_file_path):
            with open(nc_file_path, 'rb') as f:
                nc_binary_data = f.read()
                # NC 파일 내부 텍스트(바이너리) 기반 고유 해시 추출 (동명이인 파일 식별)
                nc_hash = hashlib.md5(nc_binary_data).hexdigest()[:8]
            
        # 0. 계층형 폴더 구조(Project/Part/JobID)에서 Project, Part 명칭 강제 추출
        # (XML 내부 데이터가 부정확한 경우가 많으므로 사용자가 의도한 폴더 구조를 우선시함)
        path_parts = job_id.split('/')
        if len(path_parts) >= 3:
            project_code_from_path = path_parts[-3]
            part_code_from_path = path_parts[-2]
        else:
            project_code_from_path = root.findtext('ProjectCode') or 'Unknown'
            part_code_from_path = root.findtext('PartCode') or 'Unknown'

        work_id = root.findtext('WorkID') or 'UNKNOWN_WORKID'
        project_code = project_code_from_path
        material_code = root.findtext('MaterialCode') or 'Unknown'
        part_code = part_code_from_path
        program_code = root.findtext('ProgramCode') or 'Unknown'
        
        # 1. Part Upsert
        part = db.query(Part).filter(Part.part_code == part_code).first()
        if not part:
            part = Part(part_code=part_code, project_code=project_code, material_code=material_code)
            db.add(part)
            db.flush()
        else:
            if part.project_code == "Unknown" and project_code != "Unknown":
                part.project_code = project_code
            if part.material_code == "Unknown" and material_code != "Unknown":
                part.material_code = material_code
            
        # 2. Workplan Upsert (Hash 기반 식별로 이름만 같고 내용이 다른 파일 완벽 차단)
        workplan_id = f"{part_code}_{program_code}_{nc_hash}"
        workplan = db.query(Workplan).filter(Workplan.workplan_id == workplan_id).first()
        is_new_workplan = False
        if not workplan:
            workplan = Workplan(
                workplan_id=workplan_id,
                part_code=part_code,
                program_code=program_code,
                nc_file_path=nc_file_path
            )
            db.add(workplan)
            db.flush()
            
            # Save NC file to Vault
            nc_vault_path = None
            nc_binary_data_to_save = None
            if nc_file_path:
                nc_vault_path = save_to_vault(nc_file_path, "workplan_nc", f"{workplan_id}.nc")
                if nc_binary_data and len(nc_binary_data) <= 15 * 1024 * 1024:
                    nc_binary_data_to_save = nc_binary_data
                elif nc_binary_data:
                    print(f"    - [알림] NC 파일이 15MB를 초과하여 DB 내부 저장을 생략합니다.")
                
            wp_archive = WorkplanFileArchive(
                workplan_id=workplan_id,
                nc_file_path=nc_vault_path,
                nc_file_content=nc_binary_data_to_save,
                nc_file_sha256=sha256_bytes(nc_binary_data_to_save) if nc_binary_data_to_save else None
            )
            db.add(wp_archive)
            db.flush()
            is_new_workplan = True
            
        # 공구 마스터 매핑 딕셔너리 구성
        tools = db.query(Tool).all()
        tool_mapping = {t.tool_code: t for t in tools if t.tool_code}
        
        # <Tools> 에서 개별 공구의 제원/속성 정보 추출
        tool_properties = {}
        tools_node = root.find('Tools')
        if tools_node is not None:
            for tool_model in tools_node.findall('ToolModel'):
                props = {}
                properties_node = tool_model.find('Properties')
                if properties_node is not None:
                    for any_type in properties_node.findall('anyType'):
                        name = any_type.findtext('Name')
                        val = any_type.findtext('Value')
                        if name:
                            props[name] = val
                xml_tool_code = props.get('toolCode')
                if xml_tool_code:
                    tool_properties[xml_tool_code] = props
                    
        # 3. Workingstep Insert & Job 런타임 상태(tool_conditions) 구성
        tool_conditions_list = []
        tool_numbers_node = root.find('ToolNumbers')
        if tool_numbers_node is not None:
            for idx, int_node in enumerate(tool_numbers_node.findall('int'), start=1):
                if int_node.text is None:
                    continue
                tool_num = safe_int(int_node.text)
                target_tool_code = f"T{tool_num}" 
                props = tool_properties.get(target_tool_code, {})
                target_tool = tool_mapping.get(target_tool_code)
                
                # 새로운 Workplan일 경우에만 Workingstep을 생성(정적 계획이므로 1번만 생성)
                if is_new_workplan:
                    step = Workingstep(
                        workplan_id=workplan_id,
                        tool_id=target_tool.tool_id if target_tool else None,
                        operation_type=None,
                        step_order=idx,
                        tool_number=tool_num,
                        xml_tool_code=target_tool_code
                    )
                    db.add(step)
                    
                # 런타임 공구 상태(오프셋, 마모 등)는 Job 테이블에 JSON으로 저장
                offsets = {}
                for i in range(9):
                    key = f'offset{i}'
                    if key in props:
                        offsets[key] = safe_float(props[key], 0.0)
                tool_conditions_list.append({
                    "step_order": idx,
                    "tool_code": target_tool_code,
                    "used_count": safe_int(props.get('toolUsedCount'), 0),
                    "offsets": offsets
                })
        
        # 4. Job Upsert (런타임 실행 이력)
        start_time_text = root.findtext('StartTime')
        end_time_text = root.findtext('FinishTime')
        start_time = start_time_text.replace('T', ' ') if start_time_text else None
        end_time = end_time_text.replace('T', ' ') if end_time_text else None
        
        # Parse job_id (folder name) to extract research_project and custom_part_name
        parsed_rp = None
        parsed_cpn = None
        folder_parts = job_id.replace('\\', '/').split('/')
        if len(folder_parts) >= 3:
            parsed_rp = folder_parts[-3]
            parsed_cpn = folder_parts[-2]
            
        is_num = folder_parts[-1].isdigit() if folder_parts else False
        
        # Check uniqueness by job_id PK or source_folder
        if is_num:
            existing_job = db.query(Job).filter(
                (Job.job_id == int(folder_parts[-1])) | (Job.source_folder == job_id)
            ).first()
        else:
            existing_job = db.query(Job).filter(
                Job.source_folder == job_id
            ).first()
        
        xml_binary_data_to_save = None
        if os.path.exists(file_path):
            with open(file_path, 'rb') as xf:
                xml_data = xf.read()
                if len(xml_data) <= 15 * 1024 * 1024:
                    xml_binary_data_to_save = xml_data
                else:
                    print(f"    - [알림] XML 파일이 15MB를 초과하여 DB 내부 저장을 생략합니다.")
        
        if existing_job:
            # source_folder는 반드시 실제 디스크 폴더 경로(job_id 파라미터)와 일치해야 한다.
            # job_id PK 기반 문자열로 재구성하면 watchdog이 인식한 실제 폴더명과 어긋나서
            # (1) 같은 폴더의 다른 파일(NC/TDMS/Log)이 이 Job을 못 찾아 중복 Job을 만들고
            # (2) 다운로드/복구 기능이 존재하는 실제 폴더를 "유실됨"으로 오판하게 된다.
            canonical_sf = job_id
            if existing_job.source_folder != canonical_sf:
                existing_job.source_folder = canonical_sf

            xml_vault_path = save_to_vault(file_path, "jobs", *canonical_sf.split('/'), "metadata.xml")
            print(f"    - 이미 존재하는 Job (PK: {existing_job.job_id} / 폴더: {existing_job.source_folder}) 발견. Job 정보를 업데이트합니다.")
            existing_job.start_time = start_time
            existing_job.workplan_id = workplan_id
            existing_job.work_id = work_id
            existing_job.machine_code = root.findtext('MachineCode')
            existing_job.machine_ip = root.findtext('MachineIpAddress')
            existing_job.end_time = end_time
            existing_job.cutting_seconds = safe_float(root.findtext('CuttingSeconds'))
            existing_job.moving_distance = safe_float(root.findtext('MovingDistance'))
            existing_job.cutting_moving_distance = safe_float(root.findtext('CuttingMovingDistance'))
            existing_job.is_finish = (root.findtext('IsFinish') == 'true')
            existing_job.is_error = (root.findtext('IsError') == 'true')
            existing_job.tool_conditions = tool_conditions_list
            if parsed_rp and not existing_job.research_project:
                existing_job.research_project = parsed_rp
            if parsed_cpn and not existing_job.custom_part_name:
                existing_job.custom_part_name = parsed_cpn
                
            xml_sha256 = sha256_bytes(xml_binary_data_to_save) if xml_binary_data_to_save else None
            job_archive = db.query(JobFileArchive).filter(JobFileArchive.job_id == existing_job.job_id).first()
            if job_archive:
                job_archive.xml_file_path = xml_vault_path
                job_archive.xml_file_content = xml_binary_data_to_save
                job_archive.xml_file_sha256 = xml_sha256
            else:
                db.add(JobFileArchive(job_id=existing_job.job_id, xml_file_path=xml_vault_path, xml_file_content=xml_binary_data_to_save, xml_file_sha256=xml_sha256))
        else:
            new_job = Job(
                workplan_id=workplan_id,
                work_id=work_id,
                machine_code=root.findtext('MachineCode'),
                machine_ip=root.findtext('MachineIpAddress'),
                start_time=start_time,
                end_time=end_time,
                cutting_seconds=safe_float(root.findtext('CuttingSeconds')),
                moving_distance=safe_float(root.findtext('MovingDistance')),
                cutting_moving_distance=safe_float(root.findtext('CuttingMovingDistance')),
                is_finish=(root.findtext('IsFinish') == 'true'),
                is_error=(root.findtext('IsError') == 'true'),
                tool_conditions=tool_conditions_list,
                research_project=parsed_rp,
                custom_part_name=parsed_cpn
            )
            db.add(new_job)
            db.flush()

            # source_folder는 watchdog이 실제로 감지한 디스크 폴더 경로(job_id 파라미터)를 그대로 사용한다.
            # job_id PK 기반으로 재구성하면 실제 폴더명과 어긋나 이후 NC/TDMS/Log 파일이 이 Job을
            # 찾지 못하고 중복 Job을 만들며, 다운로드/복구 기능도 존재하는 폴더를 "유실됨"으로 오판한다.
            new_job.source_folder = job_id
            db.flush()

            xml_vault_path = save_to_vault(file_path, "jobs", *new_job.source_folder.split('/'), "metadata.xml")
            db.add(JobFileArchive(
                job_id=new_job.job_id, xml_file_path=xml_vault_path, xml_file_content=xml_binary_data_to_save,
                xml_file_sha256=sha256_bytes(xml_binary_data_to_save) if xml_binary_data_to_save else None
            ))
            
        db.commit()
        print(f"    - XML 파싱 완료. DB 저장 성공 (폴더명: {job_id})")
        
    except Exception as e:
        db.rollback()
        print(f"    - [오류] XML 파싱/저장 실패: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
