import os
import argparse
from sqlalchemy.orm import Session
from DB.database import engine
from DB.models import JobFileArchive, WorkplanFileArchive, CadFileArchive, Job

def restore_files(output_dir, job_id=None):
    """
    DB에 저장된 바이너리 데이터를 읽어 원본 파일(XML, NC, CAD)로 복원합니다.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    print(f"[{output_dir}] 디렉토리로 복원을 시작합니다...")
    
    with Session(engine) as session:
        # 특정 Job ID만 복구할지, 전체를 복구할지 쿼리
        if job_id:
            jobs = session.query(Job).filter(Job.job_id == job_id).all()
        else:
            jobs = session.query(Job).all()
            
        if not jobs:
            print("복구할 Job 데이터를 찾을 수 없습니다.")
            return

        for job in jobs:
            print(f"\n--- 복구 중: Job ID {job.job_id} ({job.source_folder}) ---")
            job_out_dir = os.path.join(output_dir, job.source_folder)
            os.makedirs(job_out_dir, exist_ok=True)
            
            # 1. XML 복원
            job_archive = session.query(JobFileArchive).filter_by(job_id=job.job_id).first()
            if job_archive and job_archive.xml_file_content:
                xml_path = os.path.join(job_out_dir, "metadata.xml")
                with open(xml_path, 'wb') as f:
                    f.write(job_archive.xml_file_content)
                print(f"  [성공] XML 복원 완료: {xml_path}")
            else:
                print(f"  [건너뜀] XML 데이터가 DB에 없습니다.")
                
            # 2. NC 복원 (Workplan 기반)
            wp_archive = session.query(WorkplanFileArchive).filter_by(workplan_id=job.workplan_id).first()
            if wp_archive and wp_archive.nc_file_content:
                # 파일명은 원래 경로에서 추론하거나 workplan_id 사용
                orig_name = os.path.basename(wp_archive.nc_file_path) if wp_archive.nc_file_path else f"{job.workplan_id}.nc"
                nc_path = os.path.join(job_out_dir, orig_name)
                with open(nc_path, 'wb') as f:
                    f.write(wp_archive.nc_file_content)
                print(f"  [성공] NC 복원 완료: {nc_path}")
            else:
                print(f"  [건너뜀] NC 데이터가 DB에 없습니다.")
                
            # 3. CAD 복원
            cad_archives = session.query(CadFileArchive).filter_by(job_id=job.job_id).all()
            for cad in cad_archives:
                if cad.file_content:
                    cad_path = os.path.join(job_out_dir, cad.file_name)
                    with open(cad_path, 'wb') as f:
                        f.write(cad.file_content)
                    print(f"  [성공] CAD({cad.file_type}) 복원 완료: {cad_path}")
                else:
                    print(f"  [건너뜀] CAD({cad.file_name}) 파일 내용은 DB에 없습니다 (15MB 초과 등).")

    print("\n모든 복구 작업이 완료되었습니다.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DB에서 원본 파일 복구 유틸리티")
    parser.add_argument("--output", "-o", type=str, default="./restored_data", help="복원할 대상 디렉토리 경로 (기본: ./restored_data)")
    parser.add_argument("--job_id", "-j", type=int, help="특정 Job ID만 복원하려면 지정하세요", default=None)
    
    args = parser.parse_args()
    
    restore_files(args.output, args.job_id)
