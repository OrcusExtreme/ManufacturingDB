# 공작기계지능화실험실 통합 제조 데이터베이스 시스템 (ManufacturingDB)

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Database](https://img.shields.io/badge/MySQL-8.0%2B-orange.svg)](https://www.mysql.com/)
[![ORM](https://img.shields.io/badge/SQLAlchemy-2.0%2B-red.svg)](https://www.sqlalchemy.org/)
[![Frontend](https://img.shields.io/badge/Streamlit-1.61%2B-FF4B4B.svg)](https://streamlit.io/)
[![Standard](https://img.shields.io/badge/Standard-ISO%2014649%20(STEP--NC)-green.svg)](https://www.iso.org/)
[![Release](https://img.shields.io/badge/Release-V2.0.2-brightgreen.svg)](https://github.com/OrcusExtreme/ManufacturingDB)

공작기계지능화실험실(Machine Tool Intelligence Lab)의 **통합 스마트 제조 데이터베이스 및 실시간 분석 플랫폼**입니다.  
공작기계(CNC)에서 생성되는 다양한 이기종 데이터(XML 메타데이터, NC 프로그램, 100kHz+ 고주파 NI TDMS 진동 센서, 1Hz CNC 상태 로그, 표면 조도 측정 CSV, 3D CAD 도면)를 **Watchdog 기반으로 자동 감시·수집·파싱**하여 **ISO 14649(STEP-NC) 표준 기반 RDBMS**에 정규화 적재하고, 연구원 및 관리자에게 고성능 웹 대시보드를 제공합니다.

---

## 🌟 주요 특징 (Key Highlights)

### 1. ISO 14649(STEP-NC) 표준 기반 데이터 모델링
- **정적 공정 계획(Static Planning)**과 **동적 가공 실행(Dynamic Execution)**의 분리
- `Part(부품)` ➔ `Workplan(NC 가공계획)` ➔ `Workingstep(단위공정/공구호출)` ↔ `Tool(공구 마스터)`의 객체 지향 공정 계층 구조 구축
- 단일 Workplan 하에서 반복 수행되는 실험 가공(`Job`)의 1:N 이력 관리

### 2. 하이브리드 4계층 스토리지 & 재난 복구(DR) 체계
- **MySQL RDBMS**: B-Tree 인덱스 기반의 초고속 조건 필터링, 정렬, 다차원 조인 및 통계 쿼리
- **Apache Parquet**: 100kHz+ 초고주파 센서 시계열 및 마이크로미터($\mu m$) 단면 조도 곡선을 Snappy 컬럼형 압축 포맷으로 초고속 렌더링
- **File Vault**: SHA 해시 기반의 불변 원본 파일 안전 보관 디렉터리
- **LONGBLOB 백업 미러링**: MySQL 덤프 파일 단독으로도 `recovery_engine.py`를 통해 모든 원본 파일과 디렉터리 트리를 100% 원복 가능

### 3. 무중단 실시간 자동 파이프라인 (Automated Pipeline)
- `machining_raw_data/` 모니터링 디렉터리에 폴더째 파일 투입 시 자동 감지(Watchdog)
- 파일 전송 완료(Lock 해제) 감지 후 확장자별 병렬 파서 자동 구동 (`xml`, `tdms`, `nc`, `log`, `csv`, `step`/`stl`)
- 비동기 백그라운드 데몬(`tdms_visualizer.py`)을 통한 시간 도메인 & FFT 주파수 스펙트럼 Parquet 자동 생성

### 4. 이원화된 Streamlit 웹 인터페이스
- **관리자 대시보드 (Port 8501)**: Job 메타데이터/환경/품질 점검 CUD 폼, CASCADE 삭제, ZIP 재난 복구, 수동 웹 업로더
- **사용자/연구원 대시보드 (Port 8502)**: ISO 14649 트리뷰, Plotly 기반 고주파 진동 Envelope & FFT 스펙트럼 분석, 2D 표면조도 프로파일 시각화, 선택적 데이터 다운로드

---

## 🏗️ 시스템 아키텍처 (System Architecture)

```text
[Raw Data / Network Drive]
       │ (File Drop: XML, NC, TDMS, LOG, CAD, CSV)
       ▼
[Watchdog Observer (data_insert_recognization.py)]
       │ (Queue & Stability Check: 파일 접근 권한 및 무변동 검사)
       ▼
[Parsing Engine (parsers/)]
  ├─ xml_parser.py       : 메타데이터 추출, Part/Workplan/Job 구조 매핑, 공구 런타임 상태 추출
  ├─ tdms_parser.py      : 초고속 메타데이터(nptdms) 추출 및 가공 시작 시간 보완
  ├─ log_parser.py       : CNC 1Hz 로그 통계(Max/Avg Load, RPM, Feed) 요약 및 알람 수집
  ├─ roughness_parser.py : 조도 측정 결과(Ra, Rq, Rz) 자동 계산 및 2D 단면 Parquet 변환
  ├─ cad_parser.py       : 3D 모델(STEP, STL) 파트 매핑 및 원본 아카이빙
  └─ nc_parser.py        : NC 프로그램 코드 추출 및 MD5 해시 식별자 생성
       │
       ▼
[MySQL Database (SQLAlchemy ORM)]  ◄═══►  [Vault System (vault_manager.py)]
       │
       ▼
[Streamlit Frontend UI]
  ├─ Admin Dashboard (Port 8501) : 데이터 관리, 품질/환경 수정, 원자적 삭제, 복구 ZIP 생성
  └─ User Dashboard  (Port 8502) : 가공 검색, Plotly 센서 분석, 조도 프로파일, 데이터 다운로드
```

---

## 📊 데이터베이스 스키마 구조 (ER Diagram)

```mermaid
erDiagram
    Part ||--o{ Workplan : "1:N"
    Part ||--o{ CadFileArchive : "1:N"
    Workplan ||--o{ Workingstep : "1:N"
    Workplan ||--o{ Job : "1:N"
    Workplan ||--o| WorkplanFileArchive : "1:1"
    Tool ||--o{ Workingstep : "1:N"
    Job ||--o{ MachineLog : "1:N"
    Job ||--o| Inspection : "1:1"
    Job ||--o| EnvMemo : "1:1"
    Job ||--o{ SurfaceRoughness : "1:N"
    Job ||--o| JobFileArchive : "1:1"
    SurfaceRoughness ||--o| SurfaceRoughnessArchive : "1:1"
    MachineLog ||--o| LogFileArchive : "1:1"
```

| 도메인 | 테이블명 | 주요 역할 |
| :--- | :--- | :--- |
| **마스터 & 계획** | `part`, `workplan`, `tool`, `workingstep`, `cad_file_archive` | 가공 대상 부품, NC 공정 계획, 공구 제원, 단위 공정 순서, 3D 도면 관리 |
| **가공 실행 & 품질** | `job`, `machine_log`, `surface_roughness`, `inspection`, `env_memo` | 가공 이력(시간,거리,에러), CNC 1Hz 부하 요약, 다지점 표면조도, 공차/합부(PASS/FAIL), 온습도/메모 |
| **아카이브 & 백업** | `workplan_file_archive`, `job_file_archive`, `surface_roughness_archive`, `log_file_archive` | 원본 파일 Vault 저장 경로 및 LONGBLOB 이중화 백업 데이터 |

---

## 📁 디렉터리 구조 (Directory Structure)

```text
ManufacturingDB/
├── .env                              # 데이터베이스 및 시스템 환경변수 설정
├── requirements.txt                  # 런타임 종속 파이썬 패키지 목록
├── run_system.py                     # 전체 서비스 원클릭 통합 기동 관리자
├── gemini.md                         # 전체 시스템 복원 및 상세 기술 명세서
├── machining_raw_data/               # 외부 장비 데이터 유입 모니터링 폴더
│   └── {ProjectName}/{PartName}/{JobID}/
├── archive_vault/                    # SHA 해시 기반 원본 파일 안전 보관소
├── processed_data/                   # 변환된 시계열 및 FFT Parquet 저장소
├── failed_data/                      # 파싱 실패 격리 보관소 (DLQ)
├── backend/                          # 백엔드 파이프라인 모듈
│   ├── data_insert_recognization.py  # Watchdog 파일 감시 및 큐 분배기
│   ├── job_manager.py                # Job 생성 및 중복 확인 유틸리티
│   ├── recovery_engine.py            # Vault/DB 기반 재난 복구 ZIP 생성기
│   ├── tool_inserter.py              # 공구 마스터 Excel 파서
│   ├── tdms_visualizer.py            # 백그라운드 TDMS Parquet/FFT 변환 데몬
│   ├── vault_manager.py              # 파일 SHA 해시 기반 Vault 아카이빙
│   ├── DB/                           
│   │   ├── database.py               # SQLAlchemy 커넥션 풀링 및 세션 팩토리
│   │   └── models.py                 # 14개 테이블 DDL 및 ORM 정의
│   └── parsers/                      # 확장자별 전용 파싱 엔진
│       ├── xml_parser.py             # XML 메타데이터 및 공구 상태 파서
│       ├── tdms_parser.py            # NI TDMS 고속 헤더 파서
│       ├── log_parser.py             # CNC 컨트롤러 1Hz 상태 로그 파서
│       ├── roughness_parser.py       # 표면조도(Ra/Rq/Rz) 및 평가곡선 파서
│       ├── cad_parser.py             # STEP/STL 도면 파서
│       └── nc_parser.py              # NC 프로그램 G코드 파서
└── frontend/                         # Streamlit 대시보드
    ├── admin_dashboard.py            # 관리자 대시보드 (Port 8501)
    └── user_dashboard.py             # 사용자 분석 대시보드 (Port 8502)
```

---

## 🚀 빠른 시작 (Quick Start)

### 1. 사전 요구사항 (Prerequisites)
- **Python**: 3.10 이상
- **MySQL**: 8.0 이상 (인스턴스 구동 필요)

### 2. 저장소 복제 (Clone Repository)
```bash
git clone https://github.com/OrcusExtreme/ManufacturingDB.git
cd ManufacturingDB
```

### 3. 환경 변수 설정 (`.env`)
프로젝트 루트 디렉터리에 `.env` 파일을 생성하고 데이터베이스 정보를 입력합니다.
```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=orcus
PYTHONPATH=backend
```

### 4. 원클릭 시스템 실행 (Run System)
`run_system.py`를 실행하면 필수 패키지 설치 확인, DB 스키마 자동 초기화, 파이프라인 및 두 대시보드가 서브프로세스로 동시 기동됩니다.
```bash
python run_system.py
```

### 5. 서비스 접속
- **관리자 대시보드 (Admin Dashboard)**: [http://localhost:8501](http://localhost:8501)
- **사용자 분석 대시보드 (User Dashboard)**: [http://localhost:8502](http://localhost:8502)

---

## 📈 대시보드 주요 기능 안내 (UI Features)

### 🛠️ 관리자 대시보드 (Port 8501)
1. **데이터 수정**:
   - 연구 프로젝트명, Part명, 가공차수, 실험일자 범위 기반의 5단계 동적 계층 필터
   - Tab 1(메타데이터), Tab 2(온습도/작업자/칩형태 환경메모), Tab 3(치수공차/형상/PASS·FAIL 품질점검) 통합 편집
   - 원자적 연쇄 삭제(`CASCADE`) 모달 지원
   - `복구 파일(ZIP) 생성`: Vault에서 원본 파일들을 역추적하여 폴더 구조 그대로 압축 다운로드
2. **가공 검색**: 엑셀 형태의 17개 종합 지표 테이블, 고유 범위 슬라이더 고급 필터
3. **데이터 삽입**: CAD 도면, XML, NC, TDMS, Log, 조도 CSV 웹 수동 업로드

### 🔬 사용자 및 분석 대시보드 (Port 8502)
1. **계층형 마스터 데이터**: ISO 14649 공정 트리(Part ➔ Workplan ➔ Workingstep ➔ Tool) 및 CAD 모델 조회
2. **가공 이력 & 센서 분석**:
   - 좌측: 가공 요약 메타, CNC 1Hz 통계 지표(`cls`, `crpm`, `cfr`), 조도 파라미터, 품질 합부 태그
   - 우측: 고주파 TDMS 센서 Line 차트, DAQ 진동 Envelope 밴드 차트, 주파수(FFT) PSD 로그 스펙트럼 차트, 마이크로미터 표면조도 단면 곡선 시각화
3. **데이터 다운로드**: 대상 Job의 전체 폴더 ZIP 다운로드 또는 확장자별 맞춤형 선택 다운로드
4. **DB 테이블 조회**: RDBMS 내 14개 테이블의 Raw 데이터를 실시간 쿼리하여 데이터 정합성 검증

---

## 📜 릴리즈 노트 (Release Notes)

### [V2.0.2] - 2026-09-01
- **포트 이원화 배포**: 관리자(8501), 사용자(8502) 대시보드 포트 분리 및 Python UI 안정화
- **STP 시각화 모듈**: 관리자 대시보드 및 사용자 대시보드에서 STP 파일의 3D 모델을 시각화하는 기능 추가
- **백그라운드 처리 최적화**: 파싱 및 데이터 삽입 과정의 백그라운드 데몬 및 프로세스 처리 개선

### [V2.0.1] - 2026-08-31
- **DB 스키마 명세 고도화**: ISO 14649 표준 준수 14개 테이블의 완벽한 컬럼 정의 및 제약조건/CASCADE 정책 문서화
- **인코딩 & 서브프로세스 안정화**: Windows 환경 UTF-8 인코딩(`-X utf8`, `PYTHONUTF8=1`) 전면 적용
- **에디터 편의성 개선**: 웹 코드 작성란 Tab 키 들여쓰기(4 spaces) 및 Shift+Tab 내어쓰기 지원
- **데이터베이스 이중화 강화**: LONGBLOB 원본 미러링 및 재난 복구(DR) 엔진 안정화

---

## 👥 기여 및 문의 (Contact)
- **개발 및 관리**: 공작기계지능화실험실 (Machine Tool Intelligence Lab)
- **GitHub**: [@OrcusExtreme](https://github.com/OrcusExtreme)
- **Issues**: 버그 리포트 및 기능 제안은 [GitHub Issues](https://github.com/OrcusExtreme/ManufacturingDB/issues)를 이용해 주세요.
