import os
from dotenv import load_dotenv
load_dotenv()

from DB.database import engine, Base
# 모든 모델을 import 해와야 테이블 구조를 읽을 수 있음
from DB.models import *
from sqlalchemy import text

def reset_database():
    print("Connecting to DB to drop and recreate tables...")
    with engine.connect() as conn:
        conn.execute(text('SET FOREIGN_KEY_CHECKS = 0;'))
        try:
            conn.execute(text('DROP TABLE IF EXISTS daq;'))
            conn.execute(text('DROP TABLE IF EXISTS tool_usage;'))
            conn.execute(text('DROP TABLE IF EXISTS machining_operation;'))
        except Exception:
            pass
        Base.metadata.drop_all(bind=conn)
        conn.execute(text('SET FOREIGN_KEY_CHECKS = 1;'))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    print("DB tables recreated successfully with new schema!")

if __name__ == "__main__":
    reset_database()
