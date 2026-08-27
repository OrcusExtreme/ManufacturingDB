import os
import sys

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy.orm import Session
from DB.database import get_db
from job_manager import get_or_create_job

def parse_nc(file_path, job_id_str=None):
    """
    단독으로 업로드된 NC 파일을 파싱하여 DB에 반영합니다.
    XML 없이 NC 파일만 업로드된 경우에도 Job과 Workplan을 생성/조회하고 nc_file_path를 업데이트합니다.
    """
    if not job_id_str:
        return False
        
    parts = file_path.split(os.sep)
    try:
        raw_idx = parts.index("machining_raw_data")
        project_name = parts[raw_idx + 1]
        part_name = parts[raw_idx + 2]
    except ValueError:
        print(f"[NC Parser] 파일 경로에서 프로젝트/부품 정보를 찾을 수 없습니다: {file_path}")
        return False

    db = next(get_db())
    try:
        # get_or_create_job은 필요시 Part, Workplan, Job을 모두 생성합니다.
        job = get_or_create_job(db, job_id_str)
        if not job:
            print(f"[NC Parser] Job 생성 실패: {job_id_str}")
            return False
            
        # 해당 Job의 Workplan에 nc_file_path 업데이트
        if job.workplan:
            job.workplan.nc_file_path = file_path
            
            # program_code가 없는 경우 NC 파일명을 프로그램 코드로 임시 사용
            if not job.workplan.program_code or job.workplan.program_code == "UNKNOWN":
                job.workplan.program_code = os.path.splitext(os.path.basename(file_path))[0]
            
            # NC 파일 바이너리 데이터 읽기 및 저장
            nc_binary_data = None
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    nc_data = f.read()
                    if len(nc_data) <= 15 * 1024 * 1024:
                        nc_binary_data = nc_data
                    else:
                        print(f"    - [알림] NC 파일이 15MB를 초과하여 DB 내부 저장을 생략합니다.")
            
            from DB.models import WorkplanFileArchive
            wp_archive = db.query(WorkplanFileArchive).filter(WorkplanFileArchive.workplan_id == job.workplan_id).first()
            if wp_archive:
                wp_archive.nc_file_path = file_path
                wp_archive.nc_file_content = nc_binary_data
            else:
                wp_archive = WorkplanFileArchive(
                    workplan_id=job.workplan_id,
                    nc_file_path=file_path,
                    nc_file_content=nc_binary_data
                )
                db.add(wp_archive)
                
            db.commit()
            print(f"[NC Parser] NC 파일 파싱 및 BLOB 저장 완료 (Job PK: {job.job_id}): {file_path}")
            return True
        else:
            print(f"[NC Parser] Workplan이 존재하지 않습니다. (Job PK: {job.job_id})")
            return False
            
    except Exception as e:
        print(f"[NC Parser] 데이터베이스 처리 중 에러 발생: {e}")
        db.rollback()
        return False
    finally:
        db.close()
