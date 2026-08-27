import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# .env 파일 환경변수 로드
load_dotenv()

# 데이터베이스 연결 정보 설정 (.env에서 값 가져오기)
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME")

# pymysql 드라이버를 사용한 MySQL 연결 URL
SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Engine 생성
# pool_recycle: MySQL의 wait_timeout보다 작은 값으로 설정하여 Connection이 끊기는 것을 방지
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=False,         # 실행되는 SQL 쿼리 출력 (디버깅용)
    pool_recycle=3600
)

# Session 생성을 위한 factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# SQLAlchemy 모델을 정의하기 위한 Base 클래스
Base = declarative_base()

def get_db():
    """
    데이터베이스 세션을 생성하고 반환하는 제너레이터 함수입니다.
    세션을 사용한 후에는 항상 안전하게 닫히도록 관리합니다.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
