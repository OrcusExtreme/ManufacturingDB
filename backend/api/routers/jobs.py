from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from DB.database import get_db
from DB.models import Job, MachineLog, SurfaceRoughness, Inspection, EnvMemo
from api.serializers import model_to_dict, job_to_full_dict

router = APIRouter(tags=["Jobs & Machining Data"])

@router.get("/jobs")
def list_jobs(
    workplan_id: Optional[int] = Query(None, description="특정 Workplan의 가공 이력만 필터"),
    is_error: Optional[bool] = Query(None, description="에러 발생 여부 필터"),
    machine_code: Optional[str] = Query(None, description="공작기계 식별코드(MachineCode) 필터"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """실제 수행된 가공 이력(Job) 목록을 조회합니다."""
    query = db.query(Job)
    if workplan_id is not None:
        query = query.filter(Job.workplan_id == workplan_id)
    if is_error is not None:
        query = query.filter(Job.is_error == is_error)
    if machine_code:
        query = query.filter(Job.machine_code == machine_code)

    total = query.count()
    items = query.order_by(Job.start_time.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [model_to_dict(j) for j in items]
    }

@router.get("/jobs/{job_id}")
def get_job_summary(job_id: int, db: Session = Depends(get_db)):
    """특정 가공 이력의 기본 메타데이터(가공시간, 장비, 에러여부)를 조회합니다."""
    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="가공 이력(Job)을 찾을 수 없습니다.")
    return model_to_dict(job)

@router.get("/jobs/{job_id}/full")
def get_job_full_profile(job_id: int, db: Session = Depends(get_db)):
    """
    [핵심 엔드포인트] 특정 Job에 연관된 모든 데이터(부품, 공정계획, 단위공정, 장비로그,
    표면조도 측정값, 품질검사, 작업자환경메모, 파일 경로)를 단일 계층형 JSON으로 조회합니다.
    """
    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="가공 이력(Job)을 찾을 수 없습니다.")
    return job_to_full_dict(job)

@router.get("/jobs/{job_id}/machine-log")
def get_job_machine_log(job_id: int, db: Session = Depends(get_db)):
    """특정 Job의 장비 알람 및 스핀들 부하/RPM 요약 로그를 조회합니다."""
    log = db.query(MachineLog).filter(MachineLog.job_id == job_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="해당 Job의 장비 로그를 찾을 수 없습니다.")
    return model_to_dict(log)

@router.get("/jobs/{job_id}/roughness")
def get_job_roughness(job_id: int, db: Session = Depends(get_db)):
    """특정 Job의 표면 조도(Ra, Rq, Rz) 측정 결과 목록을 조회합니다."""
    items = db.query(SurfaceRoughness).filter(SurfaceRoughness.job_id == job_id).all()
    return {
        "job_id": job_id,
        "count": len(items),
        "items": [model_to_dict(r) for r in items]
    }

@router.get("/jobs/{job_id}/inspection")
def get_job_inspection(job_id: int, db: Session = Depends(get_db)):
    """특정 Job의 가공 완료 후 품질 측정 및 합불 판정(PASS/FAIL) 결과를 조회합니다."""
    insp = db.query(Inspection).filter(Inspection.job_id == job_id).first()
    if not insp:
        raise HTTPException(status_code=404, detail="해당 Job의 품질 검사 결과를 찾을 수 없습니다.")
    return model_to_dict(insp)

@router.get("/jobs/{job_id}/env-memo")
def get_job_env_memo(job_id: int, db: Session = Depends(get_db)):
    """특정 Job의 작업자 이름, 온·습도 환경, 칩 형태, 자유 메모를 조회합니다."""
    memo = db.query(EnvMemo).filter(EnvMemo.job_id == job_id).first()
    if not memo:
        raise HTTPException(status_code=404, detail="해당 Job의 작업자/환경 메모를 찾을 수 없습니다.")
    return model_to_dict(memo)
