import datetime
import decimal
from typing import Any, Dict, List, Optional, Set

# 보안상 외부 API 응답에서 무조건 제외해야 하는 컬럼명
SENSITIVE_COLUMNS: Set[str] = {"password_hash", "token_hash"}

def serialize_val(val: Any) -> Any:
    """기본 데이터 타입을 JSON 직렬화 가능한 형태로 변환합니다."""
    if val is None:
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, (bytes, bytearray)):
        return f"<binary {len(val)} bytes>"
    return val

def model_to_dict(
    instance: Any,
    exclude_binary: bool = True,
    exclude_columns: Optional[Set[str]] = None
) -> Optional[Dict[str, Any]]:
    """SQLAlchemy 모델 인스턴스를 딕셔너리로 변환합니다."""
    if instance is None:
        return None
    
    ex_cols = SENSITIVE_COLUMNS.copy()
    if exclude_columns:
        ex_cols.update(exclude_columns)

    result = {}
    for col in instance.__table__.columns:
        col_name = col.name
        if col_name in ex_cols:
            continue
        val = getattr(instance, col_name)
        if isinstance(val, (bytes, bytearray)) and exclude_binary:
            continue
        result[col_name] = serialize_val(val)
    return result

def job_to_full_dict(job: Any) -> Dict[str, Any]:
    """Job 인스턴스와 이에 연결된 공정, 계측, 품질, 환경 데이터를 계층형 딕셔너리로 통합합니다."""
    data = model_to_dict(job) or {}
    
    # 1. 공정 계획 (Workplan & Part & Workingsteps)
    if job.workplan:
        wp = job.workplan
        data["workplan"] = model_to_dict(wp)
        if wp.part:
            data["part"] = model_to_dict(wp.part)
        else:
            data["part"] = None
        data["workingsteps"] = [model_to_dict(ws) for ws in (wp.workingsteps or [])]
    else:
        data["workplan"] = None
        data["part"] = None
        data["workingsteps"] = []

    # 2. 장비 요약 로그 (MachineLog)
    data["machine_log"] = model_to_dict(job.machine_log) if job.machine_log else None

    # 3. 표면 조도 측정값 목록 (SurfaceRoughness)
    data["surface_roughness"] = [model_to_dict(r) for r in (job.surface_roughnesses or [])]

    # 4. 품질 검사 결과 (Inspection)
    data["inspection"] = model_to_dict(job.inspection) if job.inspection else None

    # 5. 환경 및 작업자 메모 (EnvMemo)
    data["env_memo"] = model_to_dict(job.env_memo) if job.env_memo else None

    # 6. 파일 아카이브 경로 메타데이터 (JobFileArchive)
    if hasattr(job, "file_archive") and job.file_archive:
        fa = job.file_archive
        data["files"] = {
            "tdms_raw_path": fa.tdms_raw_path,
            "tdms_parquet_raw_path": fa.tdms_parquet_raw_path,
            "tdms_fft_parquet_raw_path": fa.tdms_fft_parquet_raw_path,
            "xml_sha256": fa.xml_file_sha256,
        }
    else:
        data["files"] = None

    return data
