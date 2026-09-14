from DB.models import Job, Part, Workplan

def get_or_create_job(db, job_identifier):
    """
    주어진 job_identifier(job_id 숫자 또는 'Project/Part/job_id' 경로)로 Job을 조회하고, 
    없으면 더미 Part/Workplan을 이용해 새 Job을 생성하여 반환합니다.
    새 Job 생성 시 DB auto-increment PK(job_id)를 취득하여 
    source_folder를 '{Project}/{Part}/{job_id}' 형식으로 일치화합니다.
    """
    # 1. 숫자 PK로 직접 전달된 경우
    if isinstance(job_identifier, int) or (isinstance(job_identifier, str) and job_identifier.isdigit()):
        job = db.query(Job).filter(Job.job_id == int(job_identifier)).first()
        if job:
            return job
            
    # 2. source_folder 경로로 조회
    normalized_id = str(job_identifier).replace('\\', '/').strip('/')
    job = db.query(Job).filter(Job.source_folder == normalized_id).first()
    if job:
        return job
        
    # 3. 'Project/Part/number' 형식에서 마지막이 숫자인 경우 PK로 2차 조회
    folder_parts = normalized_id.split('/')
    if len(folder_parts) >= 3 and folder_parts[-1].isdigit():
        target_pk = int(folder_parts[-1])
        job = db.query(Job).filter(Job.job_id == target_pk).first()
        if job:
            # source_folder 경로 동기화
            canonical_sf = f"{folder_parts[-3]}/{folder_parts[-2]}/{job.job_id}"
            if job.source_folder != canonical_sf:
                job.source_folder = canonical_sf
                db.flush()
            return job

    print(f"    - [Job Manager] Job({job_identifier})이 존재하지 않아 새로 생성합니다.")
    
    # 4. 프로젝트 명과 Part 명 파싱
    parsed_rp = "UnknownProject"
    parsed_cpn = "UnknownPart"
    
    if len(folder_parts) >= 3:
        parsed_rp = folder_parts[-3]
        parsed_cpn = folder_parts[-2]
    elif len(folder_parts) == 2:
        parsed_rp = folder_parts[0]
        parsed_cpn = folder_parts[1]
    elif len(folder_parts) == 1:
        if '_' in folder_parts[0]:
            sub_parts = folder_parts[0].split('_')
            if len(sub_parts) >= 2:
                parsed_rp = sub_parts[0]
                parsed_cpn = sub_parts[1]
        else:
            parsed_rp = folder_parts[0]
        
    # 5. 더미 Part 조회 및 생성 (이름으로 찾고, 키는 DB가 부여한 번호를 사용)
    dummy_part_name = parsed_cpn if parsed_cpn else "UNKNOWN_PART"
    part = db.query(Part).filter(Part.part_name == dummy_part_name).first()
    if not part:
        part = Part(
            part_name=dummy_part_name,
            project_code=parsed_rp,
            material_code="Unknown"
        )
        db.add(part)
        db.flush()

    # 6. 더미 Workplan 조회 및 생성 (부품 + 프로그램 + NC 해시 조합으로 식별)
    workplan = db.query(Workplan).filter(
        Workplan.part_code == part.part_code,
        Workplan.program_code == "Unknown",
        Workplan.nc_hash == "NOHASH",
    ).first()
    if not workplan:
        workplan = Workplan(
            part_code=part.part_code,
            program_code="Unknown",
            nc_hash="NOHASH",
        )
        db.add(workplan)
        db.flush()

    # 7. Job 생성 (PK 먼저 생성 후 source_folder를 {Project}/{Part}/{job_id}로 확정)
    new_job = Job(
        workplan_id=workplan.workplan_id,
        work_id="UNKNOWN_WORKID",
        research_project=parsed_rp,
        custom_part_name=parsed_cpn
    )
    db.add(new_job)
    db.flush() # new_job.job_id 취득
    
    new_job.source_folder = f"{parsed_rp}/{parsed_cpn}/{new_job.job_id}"
    db.flush()
    
    print(f"    - [Job Manager] 신규 Job 등록 완료: PK {new_job.job_id} -> source_folder: {new_job.source_folder}")
    return new_job
