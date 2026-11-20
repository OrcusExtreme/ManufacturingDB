import os
from DB.database import SessionLocal
from tdms_alignment import open_tdms, parse_tdms_filename, tdms_to_local

from job_manager import get_or_create_job

from vault_manager import archive_file

def parse_tdms(file_path, job_id):
    """
    TDMS 파일 처리를 담당하는 모듈.
    무거운 파싱을 피하기 위해 원본 대신 read_metadata()를 사용해
    가공 시작 일시 등 메타데이터만 0.1초만에 스캔하여 DB를 보완하고 파일 경로를 매핑합니다.
    동시에 원본 TDMS 파일을 archive_vault에 안전하게 복사 백업합니다.

    여기서 적는 시작 시각은 '기록을 시작한' 시각이라 임시값이다. 실제 가공 시작(실행 사이클 시작)과
    가공 프로그램은 tdms_visualizer 가 CNC 채널을 분석해 덮어쓴다 (job.summary_source='TDMS').
    """
    print(f"  -> [TDMS Parser] 파일: {os.path.basename(file_path)} / Job ID: {job_id}")

    db = SessionLocal()
    try:
        # Job 가져오기 또는 생성 (XML 독립)
        job = get_or_create_job(db, job_id)

        # 1. 파일명({장비}__{프로그램}__{라벨}__{YYMMDDHHMMSS}.tdms)에서 기록 시작 시각(한국 시간)과
        #    장비 코드를 읽는다. 시각은 화면에 날짜가 바로 보이게 하려는 임시값이다.
        name_info = parse_tdms_filename(file_path)
        if name_info["recorded_at"] is not None and job.start_time is None:
            job.start_time = name_info["recorded_at"]
            print(f"    - [TDMS Parser] 파일명의 기록 시작 시각을 임시로 반영: {job.start_time} "
                  f"(가공 시작은 CNC 채널 분석 후 확정)")
        if name_info["machine"] and not job.machine_code:
            job.machine_code = name_info["machine"]

        # 2. 파일 경로 매핑을 '먼저' 확정한다.
        #    이 한 줄이 tdms_visualizer 데몬이 Parquet 변환 대상을 찾는 유일한 근거라서
        #    아래 메타데이터 보완(선택 작업)보다 우선이다. 예전에는 순서가 반대였는데,
        #    Workplan program_code 보완이 uq_workplan_identity 에 걸리면 except 의 rollback 이
        #    이 매핑까지 되돌려서 Job 의 TDMS 경로·Parquet·가공구간이 통째로 비었다.
        from vault_manager import get_rel_raw_data_path
        from file_archive import set_job_paths
        set_job_paths(db, job.job_id, tdms_raw_path=get_rel_raw_data_path(file_path))
        db.commit()

        # 3. 메타데이터 초고속 추출 로직 (선택 작업)
        #    (수집 중 비정상 종료로 파일 끝이 깨졌거나 *.tdms_index 가 손상된 경우에도
        #     읽히도록 tdms_alignment.open_tdms 를 사용한다)
        #
        #    채널 속성의 ProgramName 은 더 이상 프로그램 코드로 쓰지 않는다. '기록을 시작할 때' 띄워 둔
        #    프로그램이라 실제 가공 프로그램과 다를 수 있다 (DB_Test/1: 속성은 O2202.NC 프로브, 실제는
        #    O0911.nc). 가공 프로그램은 tdms_visualizer 가 CNC-ProgramName 채널에서 절삭이 일어난
        #    프로그램으로 정한다.
        try:
            with open_tdms(file_path, metadata_only=True) as tdms_file:
                if len(tdms_file.groups()) > 0:
                    cnc_group = tdms_file.groups()[0]
                    target_ch = cnc_group['CNC-ProgramName'] if 'CNC-ProgramName' in cnc_group else (cnc_group.channels()[0] if len(cnc_group.channels()) > 0 else None)

                    if target_ch:
                        # 파일명에 시각이 없을 때만 wf_start_time 을 쓴다. 이 값은 UTC 라 한국 시간으로 바꾼다
                        # (예전에는 그대로 넣어 9시간 이르게 저장됐다).
                        wf_start_time = target_ch.properties.get('wf_start_time')
                        if wf_start_time is not None and job.start_time is None:
                            dt = tdms_to_local(wf_start_time)
                            if dt is not None:
                                job.start_time = dt
                                print(f"    - [TDMS Parser] wf_start_time(UTC)을 한국 시간으로 임시 반영: {dt}")
            db.commit()
        except Exception as meta_e:
            # 여기서 실패해도 위에서 이미 커밋한 경로 매핑은 살아남는다.
            db.rollback()
            print(f"    - [경고] TDMS 메타데이터 보완을 건너뜁니다: {meta_e}")
        # -------------------------------------------

        # 4. Vault에 TDMS 원본 백업 (원본과 같은 자리·같은 이름)
        tdms_vault_rel = archive_file(file_path)

        db.commit()
        print(f"    - TDMS 메타데이터 파싱, Vault 백업({tdms_vault_rel}) 및 파일 참조 매핑 완료 (내부 PK: {job.job_id})")
        
        # Parquet 변환은 여기서 직접 돌리지 않는다.
        #
        # 예전에는 이 자리에서 process_tdms_file() 을 바로 호출했는데, tdms_visualizer 데몬도
        # 5초마다 tdms_parquet_path 가 비어 있는 Job 을 집어가기 때문에 같은 파일을 두 프로세스가
        # 동시에 변환했다(464MB 파일 기준 76초 -> 110초로 늘어남). 게다가 이쪽은 processed_data
        # 절대경로를, 데몬은 raw_data 상대경로를 저장해서 Job 마다 경로 형식까지 달라졌다.
        # 변환 주체를 데몬 하나로 모아 중복과 경로 불일치를 함께 없앤다.
        # (데몬은 job_file_archive.tdms_raw_path 가 채워진 Job 을 폴링하므로,
        #  위 commit 시점에 이미 대상이 된다.)
        return True

    except Exception as e:
        db.rollback()
        print(f"    - [오류] TDMS 참조 업데이트 실패: {e}")
        return False
    finally:
        db.close()
