import os
import sys

# backend 디렉터리를 sys.path에 추가하여 DB 및 유틸리티 모듈 정상 임포트 보장
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import overview, parts, tools, jobs, export

app = FastAPI(
    title="공작기계지능화실험실 제조 DB REST API",
    description=(
        "공작기계지능화실험실(Machine Tool Intelligence Lab)의 통합 제조 데이터베이스(ISO 14649 STEP-NC) "
        "외부 연동용 RESTful API 서비스입니다.\n\n"
        "부품, 공정계획(Workplan), 단위공정(Workingstep), 공구마스터, 가공이력(Job), 장비로그, "
        "표면조도, 품질검사, 작업자환경 데이터 및 전체 일괄 추출(Bulk Export)을 지원합니다."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 외부 웹 클라이언트 / 타 서버 연동을 위한 CORS 전체 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록 (/api/v1)
API_V1_PREFIX = "/api/v1"
app.include_router(overview.router, prefix=API_V1_PREFIX)
app.include_router(parts.router, prefix=API_V1_PREFIX)
app.include_router(tools.router, prefix=API_V1_PREFIX)
app.include_router(jobs.router, prefix=API_V1_PREFIX)
app.include_router(export.router, prefix=API_V1_PREFIX)

@app.get("/", tags=["Root"])
def root():
    """API 서버 기본 안내 및 문서 링크"""
    return {
        "service": "Manufacturing DB REST API",
        "version": "1.0.0",
        "status": "online",
        "documentation": "/docs",
        "endpoints": {
            "overview": f"{API_V1_PREFIX}/overview",
            "parts": f"{API_V1_PREFIX}/parts",
            "workplans": f"{API_V1_PREFIX}/workplans",
            "workingsteps": f"{API_V1_PREFIX}/workingsteps",
            "tools": f"{API_V1_PREFIX}/tools",
            "jobs": f"{API_V1_PREFIX}/jobs",
            "export_all": f"{API_V1_PREFIX}/export/all",
            "export_tables": f"{API_V1_PREFIX}/export/tables"
        }
    }

if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 바인딩으로 로컬뿐 아니라 같은 네트워크(LAN)의 다른 외부 PC에서도 접속 허용
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
