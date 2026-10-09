import sys
import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect

from DB.database import get_db, engine
from DB import models

router = APIRouter(tags=["Overview"])

@router.get("/overview")
def get_db_overview(db: Session = Depends(get_db)):
    """
    데이터베이스 연결 상태와 전체 테이블 목록 및 각 테이블의 총 레코드 수를 반환합니다.
    """
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    stats = {}
    total_records = 0
    with engine.connect() as conn:
        for t in table_names:
            try:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar() or 0
                stats[t] = cnt
                total_records += cnt
            except Exception as e:
                stats[t] = 0

    return {
        "status": "healthy",
        "database": os.getenv("DB_NAME", "orcus"),
        "total_tables": len(table_names),
        "total_records": total_records,
        "tables": stats
    }
