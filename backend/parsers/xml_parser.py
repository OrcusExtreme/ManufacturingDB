import os
import glob
import hashlib
import xml.etree.ElementTree as ET
from backend.DB.database import SessionLocal
from backend.DB.models import Part, Workplan, Workingstep, Job, Tool, WorkplanFileArchive, JobFileArchive, MachiningFeature, WorkingstepFeatureLink
from backend.vault_manager import save_to_vault

def safe_float(val, default=0.0):
    try: return float(val) if val else default
    except (ValueError, TypeError): return default

def safe_int(val, default=0):
    try: return int(val) if val else default
    except (ValueError, TypeError): return default

def parse_xml(file_path, job_id):
    """
    ISO 14649 XML 파일을 파싱하여 DB에 저장하는 모듈
    (MACHINING -> workpiece, features, operations, work_plans)
    """
    print(f"  -> [XML Parser] 파일: {os.path.basename(file_path)} / Folder ID: {job_id}")
    
    db = SessionLocal()
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        if root.tag != 'MACHINING':
            print(f"    - [스킵] ISO14649 가공 데이터(MACHINING)가 아닙니다. (태그: {root.tag})")
            return
            
        # 동일한 폴더 내 NC 파일 찾기
        xml_dir = os.path.dirname(file_path)
        nc_files = glob.glob(os.path.join(xml_dir, "*.nc"))
        nc_files.extend(glob.glob(os.path.join(xml_dir, "*.NC")))
        valid_nc_files = [f for f in nc_files if 'backup' not in f.lower() and 'old' not in f.lower()]
        nc_file_path = valid_nc_files[0] if valid_nc_files else None
        
        nc_hash = "NOHASH"
        nc_binary_data = None
        if nc_file_path and os.path.exists(nc_file_path):
            with open(nc_file_path, 'rb') as f:
                nc_binary_data = f.read()
                nc_hash = hashlib.md5(nc_binary_data).hexdigest()[:8]
            
        # 계층형 폴더 구조(Project/Part/JobID)에서 Project, Part 명칭 강제 추출
        path_parts = job_id.replace('\\', '/').split('/')
        if len(path_parts) >= 3:
            project_code_from_path = path_parts[-3]
            part_code_from_path = path_parts[-2]
        else:
            project_code_from_path = 'Unknown'
            part_code_from_path = 'Unknown'

        # 1. Part 추출 및 Upsert
        material_code = 'Unknown'
        xml_part_code = None
        workpiece = root.find('workpiece')
        if workpiece is not None:
            xml_part_code = workpiece.findtext('its_id')
            its_mat = workpiece.find('its_material')
            if its_mat is not None and its_mat.findtext('material_identifier'):
                material_code = its_mat.findtext('material_identifier')
                
        # XML에 its_id가 있으면 우선 적용, 없으면 폴더명 기반 사용
        part_code = xml_part_code if xml_part_code else part_code_from_path
        
        part = db.query(Part).filter(Part.part_code == part_code).first()
        if not part:
            part = Part(part_code=part_code, project_code=project_code_from_path, material_code=material_code)
            db.add(part)
            db.flush()
        else:
            if part.project_code == "Unknown" and project_code_from_path != "Unknown":
                part.project_code = project_code_from_path
            if (not part.material_code or part.material_code == "Unknown") and material_code != "Unknown":
                part.material_code = material_code
            
        # 2. Workplan 추출 및 Upsert
        program_code = 'Unknown'
        work_plans_node = root.find('work_plans')
        if work_plans_node is not None:
            wp_node = work_plans_node.find('work_plan')
            if wp_node is not None:
                program_code = wp_node.findtext('its_id') or 'Unknown'

        # Workplan 고유 ID에서 part_code를 제거하여 이름 변경에 영향받지 않도록 독립적으로 구성
        workplan_id = f"{program_code}_{nc_hash}"
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
                nc_file_content=nc_binary_data_to_save
            )
            db.add(wp_archive)
            db.flush()
            is_new_workplan = True

        if is_new_workplan:
            # 3. Features 추출 및 Insert
            feature_mapping = {}
            features_node = root.find('features')
            if features_node is not None:
                for feat in features_node:
                    its_id = feat.findtext('its_id')
                    if its_id:
                        new_feat = MachiningFeature(
                            workplan_id=workplan_id,
                            feature_its_id=its_id,
                            feature_type=feat.tag,
                            feature_name=its_id
                        )
                        db.add(new_feat)
                        db.flush()
                        feature_mapping[its_id] = new_feat.feature_id
                        
            # 4. Operations 딕셔너리 구성
            operations = {}
            ops_node = root.find('operations')
            if ops_node is not None:
                for op in ops_node:
                    its_id = op.findtext('its_id')
                    if its_id:
                        tool_node = op.find('tool')
                        tech_node = op.find('technology')
                        
                        op_info = {
                            'type': op.tag,
                            'tool_id': tool_node.findtext('its_id') if tool_node is not None else None,
                            'feed_rate': safe_float(tech_node.findtext('feed_rate')) if tech_node is not None else None,
                            'spindle_speed': safe_float(tech_node.findtext('spindle_speed')) if tech_node is not None else None,
                        }
                        
                        if tool_node is not None:
                            op_info.update({
                                'overall_length': safe_float(tool_node.findtext('overall_length')),
                                'cutting_edge_length': safe_float(tool_node.findtext('cutting_edge_length')),
                                'corner_radius': safe_float(tool_node.findtext('corner_radius')),
                                'hand_of_cut': tool_node.findtext('hand_of_cut'),
                                'cutter_diameter': safe_float(tool_node.findtext('cutter_diameter')),
                                'number_of_teeth': safe_int(tool_node.findtext('number_of_teeth'))
                            })
                            
                        operations[its_id] = op_info
                        
            # 공구 마스터 매핑 딕셔너리
            tools = db.query(Tool).all()
            db_tool_mapping = {t.tool_code: t for t in tools if t.tool_code}
            
            # 5. Workingsteps 추출 및 N:M 매핑
            if work_plans_node is not None:
                wp_node = work_plans_node.find('work_plan')
                if wp_node is not None:
                    steps_node = wp_node.find('workingsteps')
                    if steps_node is not None:
                        for step in steps_node.findall('working_step'):
                            step_order = safe_int(step.findtext('order'))
                            op_ref = step.findtext('operation_ref')
                            
                            op_data = operations.get(op_ref, {})
                            op_type = op_data.get('type')
                            xml_tool_code = op_data.get('tool_id')
                            
                            # XML 공구 ID로 공구 마스터 매칭 (간단히 T번호 추출해서 매칭 시도)
                            db_tool = None
                            if xml_tool_code:
                                for code, t in db_tool_mapping.items():
                                    if code in xml_tool_code:
                                        db_tool = t
                                        break
                                
                                # 공구가 DB에 없다면 새로 생성하여 맵핑
                                if not db_tool:
                                    db_tool = Tool(
                                        tool_code=xml_tool_code,
                                        tool_type='Unknown',
                                        cutter_diameter=op_data.get('cutter_diameter'),
                                        overall_length=op_data.get('overall_length'),
                                        cutting_edge_length=op_data.get('cutting_edge_length'),
                                        corner_radius=op_data.get('corner_radius'),
                                        hand_of_cut=op_data.get('hand_of_cut'),
                                        tool_teeth=op_data.get('number_of_teeth')
                                    )
                                    db.add(db_tool)
                                    db.flush()
                                    db_tool_mapping[xml_tool_code] = db_tool
                                        
                            new_step = Workingstep(
                                workplan_id=workplan_id,
                                tool_id=db_tool.tool_id if db_tool else None,
                                operation_type=op_type,
                                feed_rate=op_data.get('feed_rate'),
                                spindle_speed=op_data.get('spindle_speed'),
                                step_order=step_order,
                                tool_number=step_order, # 기본값
                                xml_tool_code=xml_tool_code,
                                tool_company=db_tool.company_name if db_tool else None,
                                tool_type=db_tool.tool_type if db_tool else None,
                                tool_diameter=op_data.get('cutter_diameter') or (db_tool.cutter_diameter if db_tool else None),
                                tool_overall_length=op_data.get('overall_length') or (db_tool.overall_length if db_tool else None),
                                tool_cutting_edge_length=op_data.get('cutting_edge_length') or (db_tool.cutting_edge_length if db_tool else None),
                                tool_corner_radius=op_data.get('corner_radius') or (db_tool.corner_radius if db_tool else None),
                                tool_hand_of_cut=op_data.get('hand_of_cut') or (db_tool.hand_of_cut if db_tool else None),
                                tool_spec=db_tool.specification if db_tool else None,
                                tool_teeth=op_data.get('number_of_teeth') or (db_tool.tool_teeth if db_tool else None)
                            )
                            db.add(new_step)
                            db.flush()
                            
                            # N:M Feature 매핑
                            f_refs = step.find('feature_refs')
                            if f_refs is not None:
                                for ref in f_refs:
                                    feat_id_str = ref.text.strip() if ref.text else None
                                    if feat_id_str and feat_id_str in feature_mapping:
                                        link = WorkingstepFeatureLink(
                                            step_id=new_step.step_id,
                                            feature_id=feature_mapping[feat_id_str]
                                        )
                                        db.add(link)

        # 6. Job Upsert (새 XML에는 가공 이력 메타데이터가 없으므로 폴더명 기반으로만 생성)
        existing_job = db.query(Job).filter(Job.source_folder == job_id).first()
        
        xml_vault_path = save_to_vault(file_path, "jobs", job_id, "metadata.xml")
        xml_binary_data_to_save = None
        if os.path.exists(file_path):
            with open(file_path, 'rb') as xf:
                xml_data = xf.read()
                if len(xml_data) <= 15 * 1024 * 1024:
                    xml_binary_data_to_save = xml_data
                else:
                    print(f"    - [알림] XML 파일이 15MB를 초과하여 DB 내부 저장을 생략합니다.")
                    
        if existing_job:
            print(f"    - 이미 존재하는 Job (폴더: {job_id}) 발견. Job 정보를 업데이트합니다.")
            existing_job.workplan_id = workplan_id
            existing_job.work_id = program_code
            if not existing_job.research_project:
                existing_job.research_project = project_code_from_path
            if not existing_job.custom_part_name:
                existing_job.custom_part_name = part_code_from_path
                
            job_archive = db.query(JobFileArchive).filter(JobFileArchive.job_id == existing_job.job_id).first()
            if job_archive:
                job_archive.xml_file_path = xml_vault_path
                job_archive.xml_file_content = xml_binary_data_to_save
            else:
                db.add(JobFileArchive(job_id=existing_job.job_id, xml_file_path=xml_vault_path, xml_file_content=xml_binary_data_to_save))
        else:
            new_job = Job(
                source_folder=job_id,
                workplan_id=workplan_id,
                work_id=program_code,
                research_project=project_code_from_path,
                custom_part_name=part_code_from_path
            )
            db.add(new_job)
            db.flush()
            db.add(JobFileArchive(job_id=new_job.job_id, xml_file_path=xml_vault_path, xml_file_content=xml_binary_data_to_save))
            
        db.commit()
        print(f"    - XML 파싱 완료. DB 저장 성공 (폴더명: {job_id})")
        
    except Exception as e:
        db.rollback()
        print(f"    - [오류] XML 파싱/저장 실패: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
