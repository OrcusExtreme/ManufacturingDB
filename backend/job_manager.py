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

    # 7. Job 생성
    new_job = Job(
        workplan_id=workplan.workplan_id,
        work_id="UNKNOWN_WORKID",
        research_project=parsed_rp,
        custom_part_name=parsed_cpn
    )
    db.add(new_job)
    db.flush() # new_job.job_id 취득

    # source_folder 는 반드시 '실제 디스크 폴더'와 같아야 한다.
    #
    # 예전에는 여기서 {Project}/{Part}/{job_id} 로 다시 만들었는데, 폴더 이름이 DB가 부여한
    # PK 와 다르면(예: 폴더는 7 인데 PK 는 9) 같은 폴더의 다음 파일이 이 Job 을 찾지 못해
    # 파일마다 Job 을 새로 만들었다. 실측에서 폴더 하나가 Job 8개로 쪼개졌다.
    # 폴더 경로를 받은 경우에는 그 값을 그대로 쓰고, 폴더를 모를 때만 PK 로 합성한다.
    if len(folder_parts) >= 3:
        new_job.source_folder = normalized_id
    else:
        new_job.source_folder = f"{parsed_rp}/{parsed_cpn}/{new_job.job_id}"
    db.flush()

    print(f"    - [Job Manager] 신규 Job 등록 완료: PK {new_job.job_id} -> source_folder: {new_job.source_folder}")
    return new_job


def adopt_workplan_identity(db, job, program_code=None, nc_hash=None, nc_file_path=None):
    """
    Job 이 물고 있는 Workplan 에 '진짜 신원'(program_code / nc_hash)을 반영한다.

    get_or_create_job 이 만드는 임시 Workplan 은 (program_code='Unknown', nc_hash='NOHASH')
    이고, NC 파서는 나중에 해시를, TDMS 파서는 프로그램명을 뒤늦게 알게 된다. 예전에는 그 값을
    그대로 UPDATE 했는데, 같은 부품의 같은 프로그램이 이미 등록돼 있으면
    uq_workplan_identity(part_code, program_code, nc_hash) 에 걸려 IntegrityError 가 났다.
    그러면 그 트랜잭션 전체가 rollback 되면서 같이 담겨 있던 tdms_file_path 매핑까지 날아간다.
    (실측: 2차 가공 Job 의 TDMS 경로·Parquet·가공구간이 통째로 비었다.)

    애초에 ISO 14649 기준으로 같은 NC 프로그램을 두 번 돌린 것은 'Workplan 1개 + Job 2개'다.
    그래서 충돌은 오류가 아니라 '합쳐야 한다'는 신호로 다룬다.

    반환값: Job 이 최종적으로 참조하게 된 Workplan (없으면 None)
    """
    from DB.models import Job, Workingstep, WorkplanFileArchive

    workplan = job.workplan if job is not None else None
    if workplan is None:
        return None

    target_program = program_code if program_code else workplan.program_code
    target_hash = nc_hash if nc_hash else workplan.nc_hash

    if target_program == workplan.program_code and target_hash == workplan.nc_hash:
        if nc_file_path and not workplan.nc_file_path:
            workplan.nc_file_path = nc_file_path
        return workplan

    twin = db.query(Workplan).filter(
        Workplan.part_code == workplan.part_code,
        Workplan.program_code == target_program,
        Workplan.nc_hash == target_hash,
        Workplan.workplan_id != workplan.workplan_id,
    ).first()

    if twin is None:
        workplan.program_code = target_program
        workplan.nc_hash = target_hash
        if nc_file_path:
            workplan.nc_file_path = nc_file_path
        db.flush()
        return workplan

    # 이미 같은 신원의 Workplan 이 있다 -> 임시 Workplan 을 흡수시킨다.
    stale_id = workplan.workplan_id
    print(f"    - [Job Manager] 동일 신원 Workplan {twin.workplan_id} 발견. "
          f"임시 Workplan {stale_id} 을(를) 합칩니다. ({target_program} / {target_hash})")

    if nc_file_path and not twin.nc_file_path:
        twin.nc_file_path = nc_file_path
    elif workplan.nc_file_path and not twin.nc_file_path:
        twin.nc_file_path = workplan.nc_file_path

    # Workingstep: 받는 쪽이 비어 있을 때만 옮긴다.
    # 신원이 같다는 것은 NC 내용이 같다는 뜻이라, 양쪽 다 있으면 같은 공정의 사본이다.
    if db.query(Workingstep).filter(Workingstep.workplan_id == twin.workplan_id).count() == 0:
        db.query(Workingstep).filter(Workingstep.workplan_id == stale_id).update(
            {Workingstep.workplan_id: twin.workplan_id}, synchronize_session=False)

    # NC 원본 아카이브도 받는 쪽이 비어 있을 때만 넘긴다.
    if db.query(WorkplanFileArchive).filter(
            WorkplanFileArchive.workplan_id == twin.workplan_id).first() is None:
        db.query(WorkplanFileArchive).filter(
            WorkplanFileArchive.workplan_id == stale_id).update(
            {WorkplanFileArchive.workplan_id: twin.workplan_id}, synchronize_session=False)

    # 이 Job 뿐 아니라 임시 Workplan 을 보고 있는 모든 Job 을 함께 옮긴다.
    db.query(Job).filter(Job.workplan_id == stale_id).update(
        {Job.workplan_id: twin.workplan_id}, synchronize_session=False)
    db.flush()
    db.expire(job, ['workplan_id', 'workplan'])

    db.query(Workplan).filter(Workplan.workplan_id == stale_id).delete(synchronize_session=False)
    db.flush()
    return twin


def discard_orphan_workplan(db, workplan_id):
    """Job 이 다른 Workplan 으로 옮겨 가면서 아무도 참조하지 않게 된 임시 Workplan 을 지운다.

    TDMS 메타데이터의 ProgramName 과 XML 의 프로그램 코드가 다를 수 있다(예: DAQ 는
    'O2202.NC' 로 기록됐는데 실제 STEP-NC 기록은 'O0911.nc'). TDMS 가 XML 보다 먼저 처리되면
    임시 Workplan 이 TDMS 쪽 이름으로 굳어지고, 뒤이어 XML 파서가 Job 을 진짜 Workplan 으로
    옮기면서 그 행이 Job 없는 고아로 남았다. 계층형 마스터 화면에 가공 이력이 하나도 없는
    Workplan 이 끼어 보이는 원인이다.

    'NC 만 올려두고 아직 돌리지 않은 계획'을 지우지 않도록, 방금 Job 이 떠나서 참조 수가 0이
    된 경우에만 부른다. 다른 Job 이 아직 보고 있으면 아무것도 하지 않는다.
    """
    from DB.models import Job

    if not workplan_id:
        return False
    if db.query(Job).filter(Job.workplan_id == workplan_id).count() > 0:
        return False

    stale = db.query(Workplan).filter(Workplan.workplan_id == workplan_id).first()
    if stale is None:
        return False

    print(f"    - [Job Manager] 참조가 사라진 임시 Workplan {workplan_id} "
          f"({stale.program_code} / {stale.nc_hash}) 을(를) 정리합니다.")
    db.delete(stale)   # Workingstep·NC 아카이브는 cascade 로 함께 정리된다
    db.flush()
    return True
