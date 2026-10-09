from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from DB.database import get_db, engine
from DB import models
from api.serializers import model_to_dict

router = APIRouter(tags=["Bulk Export"])

# 테이블명과 매핑되는 SQLAlchemy 모델 클래스 매핑
MODEL_MAP = {
    "part": models.Part,
    "workplan": models.Workplan,
    "workingstep": models.Workingstep,
    "tool": models.Tool,
    "job": models.Job,
    "machine_log": models.MachineLog,
    "surface_roughness": models.SurfaceRoughness,
    "inspection": models.Inspection,
    "env_memo": models.EnvMemo,
    "cad_file_archive": models.CadFileArchive,
    "workplan_file_archive": models.WorkplanFileArchive,
    "job_file_archive": models.JobFileArchive,
    "surface_roughness_archive": models.SurfaceRoughnessArchive,
    "log_file_archive": models.LogFileArchive,
    "app_user": models.User,
}

@router.get("/export/tables")
def list_exportable_tables():
    """내보내기 가능한 모든 테이블 목록과 설명 정보를 반환합니다."""
    return {
        "tables": [
            {"name": k, "description": getattr(v, "__table_args__", {}).get("comment", "") if isinstance(getattr(v, "__table_args__", None), dict) else ""}
            for k, v in MODEL_MAP.items()
        ]
    }

@router.get("/export/table/{table_name}")
def export_single_table(
    table_name: str,
    skip: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=10000, description="미지정 시 전체 레코드 추출"),
    db: Session = Depends(get_db)
):
    """
    지정한 단일 테이블의 모든 데이터를 JSON으로 추출합니다.
    (대용량 바이너리 컬럼은 제외하고 메타데이터와 일반 필드만 직렬화)
    """
    model_cls = MODEL_MAP.get(table_name.lower())
    if not model_cls:
        raise HTTPException(
            status_code=404,
            detail=f"테이블 '{table_name}'을 찾을 수 없습니다. 가능한 테이블: {list(MODEL_MAP.keys())}"
        )

    query = db.query(model_cls)
    total = query.count()
    if limit is not None:
        rows = query.offset(skip).limit(limit).all()
    else:
        rows = query.offset(skip).all()

    return {
        "table": table_name,
        "total_records": total,
        "count": len(rows),
        "data": [model_to_dict(row) for row in rows]
    }

@router.get("/export/all")
def export_all_database_data(db: Session = Depends(get_db)):
    """
    [전체 데이터 백업/추출용 엔드포인트]
    데이터베이스 내의 모든 테이블(부품, 공정계획, 공구, Job, 조도, 품질, 환경, 로그 등)의
    전체 레코드를 단일 JSON 객체로 일괄 추출합니다.
    """
    result = {
        "metadata": {
            "version": "1.0.0",
            "table_count": len(MODEL_MAP)
        },
        "database": {}
    }

    total_exported_rows = 0
    for tbl_name, model_cls in MODEL_MAP.items():
        try:
            rows = db.query(model_cls).all()
            serialized_rows = [model_to_dict(r) for r in rows]
            result["database"][tbl_name] = {
                "count": len(serialized_rows),
                "records": serialized_rows
            }
            total_exported_rows += len(serialized_rows)
        except Exception as e:
            result["database"][tbl_name] = {
                "error": str(e),
                "count": 0,
                "records": []
            }

    result["metadata"]["total_rows"] = total_exported_rows
    return result
