from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from DB.database import get_db
from DB.models import Tool
from api.serializers import model_to_dict

router = APIRouter(tags=["Tools"])

@router.get("/tools")
def list_tools(
    iso_class: Optional[str] = Query(None, description="ISO 14649 공구 분류 (ENDMILL, TWIST_DRILL 등)"),
    mounted_only: bool = Query(False, description="매거진에 장착된 공구만 조회(tool_code 존재)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """ISO 14649 공구 마스터 목록을 조회합니다."""
    query = db.query(Tool)
    if iso_class:
        query = query.filter(Tool.iso14649_class == iso_class)
    if mounted_only:
        query = query.filter(Tool.tool_code.isnot(None))

    total = query.count()
    items = query.order_by(Tool.tool_code).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [model_to_dict(t) for t in items]
    }

@router.get("/tools/{tool_id}")
def get_tool(tool_id: int, db: Session = Depends(get_db)):
    """특정 공구의 상세 스펙(직경, 날 수, 재질 등)을 조회합니다."""
    tool = db.query(Tool).filter(Tool.tool_id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="공구를 찾을 수 없습니다.")
    return model_to_dict(tool)

@router.get("/tools/by-code/{tool_code}")
def get_tool_by_code(tool_code: str, db: Session = Depends(get_db)):
    """매거진 포트 T번호(예: T06, T1)로 공구를 조회합니다."""
    # 표준화 처리: 숫자로 들어올 경우 T06 형식 맞춤 지원
    clean_code = tool_code.upper().strip()
    if clean_code.isdigit():
        clean_code = f"T{int(clean_code):02d}"

    tool = db.query(Tool).filter(Tool.tool_code == clean_code).first()
    if not tool:
        raise HTTPException(status_code=404, detail=f"공구 번호 '{tool_code}'를 찾을 수 없습니다.")
    return model_to_dict(tool)
