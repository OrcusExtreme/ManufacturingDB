import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from DB.database import get_db
from DB.models import Part, Workplan

def fix_parts():
    db = next(get_db())
    try:
        # Get all parts that have '_' in them and project name prepended
        bad_parts = ["Capstone_Computer", "DigitalThread_Hole", "Alchemist_Bracket"]
        
        for bad_code in bad_parts:
            bad_part = db.query(Part).filter(Part.part_code == bad_code).first()
            if not bad_part:
                continue
                
            good_code = bad_code.split('_')[1] if '_' in bad_code else bad_code
            
            good_part = db.query(Part).filter(Part.part_code == good_code).first()
            
            if not good_part:
                # If the good part doesn't exist, we can just rename the bad part
                print(f"Renaming part {bad_code} to {good_code}")
                # Because part_code is a primary key, we might need to create a new one and remap, then delete
                good_part = Part(
                    part_code=good_code,
                    project_code=bad_part.project_code,
                    material_code=bad_part.material_code
                )
                db.add(good_part)
                db.flush()
                
            # Remap workplans
            workplans = db.query(Workplan).filter(Workplan.part_code == bad_code).all()
            for wp in workplans:
                print(f"Remapping workplan {wp.workplan_id} from {bad_code} to {good_code}")
                wp.part_code = good_code
            
            # Delete bad part
            print(f"Deleting bad part {bad_code}")
            db.delete(bad_part)
            
        db.commit()
        print("Database fix completed successfully.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    fix_parts()
