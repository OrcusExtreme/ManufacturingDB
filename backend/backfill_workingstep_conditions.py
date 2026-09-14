"""기존 DB의 Workplan 및 Archive Vault에 저장된 NC 파일들을 스캔하여,
workingstep 레코드의 feed_rate 및 spindle_speed를 일괄 백필하는 스크립트.
"""
import os
import sys

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text
from DB.database import SessionLocal
from DB.models import Workingstep
from parsers.nc_parser import sync_workplan_nc_cutting_conditions


def backfill_workingstep_conditions():
    db = SessionLocal()
    updated_count = 0
    total_workplans = 0
    try:
        rows = db.execute(text("SELECT workplan_id, nc_file_path, program_code FROM workplan")).fetchall()
        total_workplans = len(rows)
        print(f"[백필 시작] 총 {total_workplans}개의 Workplan을 대상으로 NC 가공조건 백필을 진행합니다...")

        for wp_id, nc_path, prog_code in rows:
            # 해당 Workplan의 workingstep 중 feed_rate 또는 spindle_speed가 비어있는지 확인
            missing_steps = db.query(Workingstep).filter(
                Workingstep.workplan_id == wp_id,
                (Workingstep.feed_rate == None) | (Workingstep.spindle_speed == None)
            ).all()

            if not missing_steps:
                continue

            success = sync_workplan_nc_cutting_conditions(db, wp_id, nc_file_path=nc_path)
            if success:
                updated_count += 1
                print(f"  - Workplan '{wp_id}' ({prog_code}) 가공조건 동기화 완료")

        print(f"[백필 완료] 총 {updated_count}개의 Workplan에 대해 가공조건을 갱신했습니다.")
    except Exception as e:
        print(f"[백필 에러]: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    backfill_workingstep_conditions()
