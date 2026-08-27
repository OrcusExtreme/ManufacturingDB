import os
from DB.models import Job, Part, Workplan

def get_or_create_job(db, job_id):
    """
    주어진 job_id(폴더명)로 Job을 조회하고, 없으면 더미 Part/Workplan을 이용해 
    Job을 생성한 뒤 반환합니다. 
    폴더명에서 프로젝트 명과 Part 명을 파싱하여 Job 정보에 저장합니다.
    """
    job = db.query(Job).filter(Job.source_folder == job_id).first()
    
    if job:
        return job
        
    print(f"    - [Job Manager] Job({job_id})이 존재하지 않아 새로 생성합니다.")
    
    # 1. job_id (폴더명) 파싱
    #    xml_parser 경유: "Project/Part/JobID" 형식 (/ 구분)
    #    기타 경유: "ProjectName_PartName_JobID" 형식 (_ 구분) — fallback
    parsed_rp = None
    parsed_cpn = None
    
    # '/' 구분자가 있으면 계층형 폴더 경로로 파싱 (우선)
    normalized_id = job_id.replace('\\', '/')
    if '/' in normalized_id:
        folder_parts = normalized_id.split('/')
        if len(folder_parts) >= 3:
            parsed_rp = folder_parts[-3]
            parsed_cpn = folder_parts[-2]
        elif len(folder_parts) == 2:
            parsed_rp = folder_parts[0]
            parsed_cpn = folder_parts[1]
        elif len(folder_parts) == 1:
            parsed_rp = folder_parts[0]
    else:
        # fallback: '_' 구분자로 파싱
        folder_parts = job_id.split('_')
        if len(folder_parts) >= 3:
            parsed_rp = folder_parts[0]
            parsed_cpn = folder_parts[1]
        elif len(folder_parts) == 2:
            parsed_rp = folder_parts[0]
            parsed_cpn = folder_parts[1]
        elif len(folder_parts) == 1:
            parsed_rp = folder_parts[0]
        
    # 2. 더미 Part 조회 및 생성
    if parsed_cpn:
        dummy_part_code = parsed_cpn
    else:
        dummy_part_code = "UNKNOWN_PART"
    part = db.query(Part).filter(Part.part_code == dummy_part_code).first()
    if not part:
        part = Part(
            part_code=dummy_part_code, 
            project_code="Unknown", 
            material_code="Unknown"
        )
        db.add(part)
        db.flush()
        
    # 3. 더미 Workplan 조회 및 생성
    dummy_workplan_id = f"UNKNOWN_WORKPLAN_{dummy_part_code}"
    workplan = db.query(Workplan).filter(Workplan.workplan_id == dummy_workplan_id).first()
    if not workplan:
        workplan = Workplan(
            workplan_id=dummy_workplan_id,
            part_code=dummy_part_code,
            program_code="Unknown"
        )
        db.add(workplan)
        db.flush()
        
    # 4. Job 생성
    new_job = Job(
        source_folder=job_id,
        workplan_id=dummy_workplan_id,
        work_id="UNKNOWN_WORKID",
        research_project=parsed_rp,
        custom_part_name=parsed_cpn
    )
    
    db.add(new_job)
    db.flush()
    
    return new_job
