from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from DB.database import get_db
from DB.models import Part, Workplan, Workingstep
from api.serializers import model_to_dict

router = APIRouter(tags=["Master & Process Planning"])

@router.get("/parts")
def list_parts(
    skip: int = Query(0, ge=0, description="건너뛸 레코드 수"),
    limit: int = Query(50, ge=1, le=500, description="가져올 최대 레코드 수"),
    project_code: Optional[str] = Query(None, description="프로젝트 코드로 필터링"),
    db: Session = Depends(get_db)
):
    """부품(Part) 마스터 목록을 조회합니다."""
    query = db.query(Part)
    if project_code:
        query = query.filter(Part.project_code == project_code)
    
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [model_to_dict(p) for p in items]
    }

@router.get("/parts/{part_code}")
def get_part_detail(part_code: int, db: Session = Depends(get_db)):
    """특정 부품의 상세 정보 및 연결된 Workplan 목록을 조회합니다."""
    part = db.query(Part).filter(Part.part_code == part_code).first()
    if not part:
        raise HTTPException(status_code=404, detail="부품을 찾을 수 없습니다.")
    
    data = model_to_dict(part)
    data["workplans"] = [model_to_dict(wp) for wp in part.workplans]
    return data

@router.get("/workplans")
def list_workplans(
    part_code: Optional[int] = Query(None, description="특정 부품의 Workplan만 필터링"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """ISO 14649 Workplan(NC 가공계획) 목록을 조회합니다."""
    query = db.query(Workplan)
    if part_code is not None:
        query = query.filter(Workplan.part_code == part_code)
        
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [model_to_dict(wp) for wp in items]
    }

@router.get("/workplans/{workplan_id}")
def get_workplan_detail(workplan_id: int, db: Session = Depends(get_db)):
    """특정 Workplan의 상세 정보 및 포함된 단위공정(Workingstep) 순서를 조회합니다."""
    wp = db.query(Workplan).filter(Workplan.workplan_id == workplan_id).first()
    if not wp:
        raise HTTPException(status_code=404, detail="Workplan을 찾을 수 없습니다.")
    
    data = model_to_dict(wp)
    data["workingsteps"] = [model_to_dict(ws) for ws in sorted(wp.workingsteps, key=lambda x: x.step_order)]
    return data

@router.get("/workingsteps")
def list_workingsteps(
    workplan_id: Optional[int] = Query(None, description="특정 Workplan의 단위공정만 필터"),
    tool_number: Optional[int] = Query(None, description="호출 공구 번호로 필터"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """단위 공정(Workingstep: 공구 호출 순서 및 F/S 가공조건) 목록을 조회합니다."""
    query = db.query(Workingstep)
    if workplan_id is not None:
        query = query.filter(Workingstep.workplan_id == workplan_id)
    if tool_number is not None:
        query = query.filter(Workingstep.tool_number == tool_number)
        
    total = query.count()
    items = query.order_by(Workingstep.step_order).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [model_to_dict(ws) for ws in items]
    }
