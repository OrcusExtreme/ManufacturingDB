import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from DB.database import SessionLocal
from DB.models import Part, Workplan, Job

def run_cleanup():
    db = SessionLocal()
    try:
        # Find all dummy workplans
        dummy_wps = db.query(Workplan).filter(Workplan.workplan_id.like("UNKNOWN_WORKPLAN_%")).all()
        
        deleted_wps = 0
        deleted_parts = 0
        
        for wp in dummy_wps:
            # Check if any job uses it
            job_count = db.query(Job).filter(Job.workplan_id == wp.workplan_id).count()
            if job_count == 0:
                db.delete(wp)
                deleted_wps += 1
                
        # Flush so workplans are deleted before parts (due to FK)
        db.flush()
        
        # Find all dummy parts
        dummy_parts = db.query(Part).filter(Part.part_code.like("UNKNOWN_PART_%")).all()
        
        for part in dummy_parts:
            # Check if any workplan uses it
            wp_count = db.query(Workplan).filter(Workplan.part_code == part.part_code).count()
            if wp_count == 0:
                db.delete(part)
                deleted_parts += 1
                
        if deleted_wps > 0 or deleted_parts > 0:
            db.commit()
            print(f"[알림] 더미 데이터 청소 완료: Workplan {deleted_wps}건, Part {deleted_parts}건 삭제됨.")
        else:
            print("[알림] 정리할 더미 데이터가 없습니다.")
            
    except Exception as e:
        db.rollback()
        print(f"오류 발생 (Dummy Cleanup): {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_cleanup()
