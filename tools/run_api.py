import os
import sys

# Windows cp949 인코딩 콘솔 오류 방지
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 프로젝트 루트 및 backend 경로 설정
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.main import app
import uvicorn

if __name__ == "__main__":
    print("============================================================")
    print("[API] 공작기계지능화실험실 제조 DB REST API 서버 시작")
    print("[API] 로컬 접속: http://localhost:8000")
    print("[API] 대화형 Swagger 문서: http://localhost:8000/docs")
    print("[API] 외부/LAN 접속: http://0.0.0.0:8000")
    print("============================================================")
    
    # 0.0.0.0 바인딩
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
