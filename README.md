<p align="center">
  <img src="assets/logo_card.png" width="140" alt="Orcus ManufacturingDB Logo" />
</p>

<h1 align="center">공작기계지능화실험실 통합 제조 데이터베이스 시스템 (ManufacturingDB)</h1>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-blue.svg" alt="Python Version" /></a>
  <a href="https://www.mysql.com/"><img src="https://img.shields.io/badge/MySQL-8.0%2B-orange.svg" alt="Database" /></a>
  <a href="https://www.sqlalchemy.org/"><img src="https://img.shields.io/badge/SQLAlchemy-2.0%2B-red.svg" alt="ORM" /></a>
  <a href="https://streamlit.io/"><img src="https://img.shields.io/badge/Streamlit-1.61%2B-FF4B4B.svg" alt="Frontend" /></a>
  <a href="https://www.iso.org/"><img src="https://img.shields.io/badge/Standard-ISO%2014649%20(STEP--NC)-green.svg" alt="Standard" /></a>
  <a href="https://github.com/OrcusExtreme/ManufacturingDB"><img src="https://img.shields.io/badge/Release-V3.1.0-brightgreen.svg" alt="Release" /></a>
</p>

공작기계지능화실험실(Machine Tool Intelligence Lab)의 **통합 스마트 제조 데이터베이스 및 실시간 분석 플랫폼**입니다.  
공작기계(CNC)에서 생성되는 다양한 이기종 데이터(XML 메타데이터, NC 프로그램, 고주파 NI TDMS 센서(실측 12.8kHz · 스핀들 전류/진동/음향), 1Hz CNC 상태 로그, 표면 조도 측정 CSV, 3D CAD 도면)를 **Watchdog 기반으로 자동 감시·수집·파싱**하여 **ISO 14649(STEP-NC) 표준 기반 RDBMS**에 정규화 적재하고, 연구원 및 관리자에게 고성능 웹 대시보드를 제공합니다.

---

## 🌟 주요 특징 (Key Highlights)

### 1. ISO 14649(STEP-NC) 표준 기반 데이터 모델링
- **정적 공정 계획(Static Planning)**과 **동적 가공 실행(Dynamic Execution)**의 분리
- `Part(부품)` ➔ `Workplan(NC 가공계획)` ➔ `Workingstep(단위공정/공구호출)` ↔ `Tool(공구 마스터)`의 객체 지향 공정 계층 구조 구축
- 단일 Workplan 하에서 반복 수행되는 실험 가공(`Job`)의 1:N 이력 관리

### 2. 하이브리드 4계층 스토리지 & 재난 복구(DR) 체계
- **MySQL RDBMS**: B-Tree 인덱스 기반의 초고속 조건 필터링, 정렬, 다차원 조인 및 통계 쿼리
- **Apache Parquet**: 수백만 행 고주파 센서 시계열(12.8kHz)과 마이크로미터($\mu m$) 단면 조도 곡선을 Snappy 컬럼형 압축 포맷으로 초고속 렌더링
- **File Vault**: SHA 해시 기반의 불변 원본 파일 안전 보관 디렉터리 (프로젝트 외부 경로 지정 지원으로 프로젝트 삭제 시에도 백업 영구 보존)
- **LONGBLOB 백업 미러링**: MySQL 덤프 파일 단독으로도 `recovery_engine.py`를 통해 모든 원본 파일과 디렉터리 트리를 100% 원복 가능
- **스토리지 초기화 시 보관소 영구 보호**: DB 및 런타임 데이터 초기화 시에도 보관소(`archive_vault`)는 절대 삭제되지 않도록 이중 격리 보호

### 3. 무중단 실시간 자동 파이프라인 (Automated Pipeline)
- `machining_raw_data/` 모니터링 디렉터리에 폴더째 파일 투입 시 자동 감지(Watchdog)
- 파일 전송 완료(Lock 해제) 감지 후 자료 종류별 파서 자동 구동 (`xml`, `tdms`, `nc`, `log`, `csv`, `step`/`stl`)
- **Job 폴더 자동 정리**: 파일이 Job 폴더에 섞여 들어와도 확장자를 보고
  `XML/` · `Log/` · `TDMS/` · `NC/` 하위 폴더로 옮긴 뒤 처리 (규칙은 `backend/job_layout.py` 단일 출처)
- 비동기 백그라운드 데몬(`tdms_visualizer.py`)을 통한 시간 도메인 & FFT 주파수 스펙트럼 Parquet 자동 생성
- **단일 패스 TDMS 읽기**: 여유 메모리를 보고 파일 전체를 한 번에 훑어 채널별 재스캔을 제거
  (464MB 실측 54.9초 → 10.1초). 메모리가 부족하면 지연 로딩으로 자동 전환

### 4. 제조 DB Controller (C++ 네이티브 GUI)
`system_controller.exe` 하나로 Streamlit UI · Watchdog 수집기 · TDMS 변환기 · **REST API 서버**를 개별 제어하고,
네 프로세스의 로그를 한 창에서 실시간으로 봅니다. 파서 6종과 파이프라인 일시정지를 **재시작 없이** 토글할 수
있으며(`backend/pipeline_config.json` 공유), Job Object 로 묶어 제어기가 비정상 종료해도 자식 프로세스가
남지 않습니다. 파이썬 코드를 품지 않고 디스크의 `.py` 를 그대로 실행하므로 **파이썬을 고쳐도 재빌드가 필요 없습니다.**
대시보드 계정 관리(가입 승인·거절, 멤버 조회·삭제)도 **여기서** 합니다(관리 유틸리티 › `유저 관리`).
`DB · 스토리지 초기화` 는 지울 항목(가공 데이터 DB · 스토리지 · 공구 목록)을 골라서 하며, 백업 보관소와
로그인 계정은 어떤 항목을 골라도 남습니다.

### 5. 단일 통합 Streamlit 웹 대시보드 (Port 8501)
Job 검색부터 ISO 14649 트리뷰, 센서/조도 분석(Plotly Envelope & FFT), 데이터 삽입(가공 기록 입력 포함),
DB 테이블 조회/편집, 재난 복구(ZIP)까지 하나의 화면 흐름에서 처리합니다.

**로그인해야 화면이 열립니다.** 회원가입은 아이디·이름·비밀번호로 **신청**만 하고, 관리자가
제조 DB Controller 의 `유저 관리` 창에서 승인해야 계정이 생깁니다(이메일 인증 없음). 멤버 삭제도
그 창에서 하며, 삭제하면 열려 있던 대시보드도 다음 동작에서 바로 로그아웃됩니다.
가입 시 받는 작업자 이름이 데이터 삽입 시 그 Job 의
`env_memo.worker_name` 으로 자동 기록됩니다. 비밀번호는 PBKDF2-HMAC-SHA256(계정별 소금,
600,000회)으로 해싱해 저장하며 평문으로 남기지 않습니다. 역할 구분(관리자/일반)은 없고,
Job 삭제나 DB 테이블 직접 편집처럼 파괴적인 작업에는 확인 절차(삭제 확인 다이얼로그,
Root 계정 재인증)가 남아 있습니다.

> 내부망 localhost 를 전제로 한 앱 수준 인증입니다. Streamlit 은 HTTP 로 서비스되므로
> 외부에 열어야 한다면 리버스 프록시로 HTTPS 를 씌우세요.

### 6. 제조 DB REST API 서버 (Port 8000 & OpenAPI / Swagger)
외부 시스템, 타 연구실 PC, AI 모델 및 LLM(대형 언어 모델) 연동을 위한 **FastAPI 기반 RESTful API 서비스**를 제공합니다.
- **전체 DB 일괄 덤프 (`GET /api/v1/export/all`)**: 15개 전체 테이블의 모든 가공·공정·계측 데이터를 단일 JSON으로 한 번에 수신
- **LLM/RAG 최적화 계층형 프로필 (`GET /api/v1/jobs/{job_id}/full`)**: 특정 Job의 공정 계획부터 공구, 장비로그, 표면조도, 품질검사, 환경메모까지 단일 JSON 컨텍스트로 바인딩하여 LLM 환각(Hallucination) 방지
- **대화형 Swagger UI (`http://localhost:8000/docs`) & ReDoc (`/redoc`)**: 웹 브라우저에서 모든 엔드포인트 실시간 테스트 및 데이터 다운로드 지원
- **LLM Tool / Function Calling 자동 등록 (`http://localhost:8000/openapi.json`)**: LangChain, LlamaIndex, OpenAI/Gemini Agent에 스키마 URL 하나로 자동 도구 바인딩 지원
- **CORS 전체 허용 & 보안 직렬화**: 외부 웹 프론트엔드 연동을 위한 CORS 지원 및 비밀번호 해시/대용량 바이너리 자동 필터링

---

## 🏗️ 시스템 아키텍처 (System Architecture)

사람이 켜는 창은 **제조 DB Controller 하나**이고, 그 아래 **4개의 독립 프로세스**가 돕니다.
네 프로세스는 서로 직접 통신하지 않고 **MySQL · 파일시스템 · `pipeline_config.json`** 으로만 이어집니다.

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  제조 DB Controller  (system_controller.exe · C++ Win32)                                     │
│  · 4개 프로세스 기동/중지 · 실시간 로그 통합 · 파서 6종 On/Off · 일시정지                       │
│  · backend/pipeline_config.json (파일 기반 전달, IPC 없음)                                   │
│  · Job Object 로 비정상 종료 시 자식 프로세스 일괄 회수                                       │
└────────┬─────────────────────────┬──────────────────────────┬───────────────────────────────┤
         │ 기동·감시                │ 기동·감시                 │ 기동·감시                     │ 기동·감시
         ▼                         ▼                          ▼                               ▼
  ① Watchdog 수집기         ② TDMS 변환 데몬            ③ Streamlit UI                 ④ REST API 서버
                              (5초 폴링)                   (localhost:8501)               (0.0.0.0:8000)

[장비 / 계측 PC] ──폴더째 복사(File Drop)──┐
                                          ▼
   data/machining_raw_data/{Project}/{Part}/
        ├─ CAD_Files/                      ← Part 레벨 (Job 폴더와 같은 깊이)
        └─ {JobID}/ XML · Log · TDMS · NC · Surface_Roughness · etc · processed_parquet
                                          │
                                          ▼
 ① Watchdog 수집기  (data_insert_recognization.py)
    ├─ Queue & Stability Check — 파일 전송 완료(Lock 해제) 검사
    ├─ job_layout.py — 확장자 보고 하위 폴더 자동 정리 / 부수 파일 무시
    ├─ pipeline_control.py — 파서 On/Off · 일시정지 확인
    ├─ Parsing Engine [parsers/]
    │    xml · nc · tdms · log · roughness · cad  (+ job_manager · tool_inserter)
    ├─ integrity_monitor — 데몬 스레드, 30분 주기 SHA-256 전수 재검증(3-상태)
    └─ 파싱 실패 ─▶ data/failed_data (DLQ)
         │
         ├──▶ MySQL DB (SQLAlchemy ORM)     정규화 적재
         ├──▶ Archive Vault                 원본 사본 (프로젝트 밖)
         └──▶ MySQL LONGBLOB                XML·NC·CAD 원본 (15MB 이하)

 ② TDMS 변환 데몬  (tdms_visualizer.py)
    └─ tdms_alignment.py — NC 대조 실가공 구간 판별 · CNC/DAQ 시간축 정렬 · FFT
         ├──▶ Parquet 저장 계층 (3중 저장)
         │      data/processed_data → Job폴더/processed_parquet → Vault/processed_parquet
         └──▶ MySQL  (tdms_parquet_path · machining_window)

 ③ Streamlit UI (:8501)  — 사이드바 8개 메뉴 + ERD 모달
    ├─ Read  ◀── MySQL · Parquet 저장 계층 · Archive Vault · 원본 폴더
    ├─ Write ──▶ MySQL     (메타데이터 편집 · Cascade 삭제 · 공구 마스터 전량 교체)
    ├─ Write ──▶ raw_data  (데이터 삽입 → ① 수집기로 재진입)        ◀ 순환
    └─ recovery_engine.py ──▶ 복구 ZIP 생성 / Job 단위 로컬 복원

  ※ MySQL 과 Archive Vault 는 직접 주고받지 않습니다.
     DB 에는 보관소 루트 기준 **상대경로 문자열만** 저장되고, 실제 파일 입출력은
     파서 · recovery_engine · Streamlit 이 수행합니다.
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
    Job ||--o| MachineLog : "1:1"
    Job ||--o| Inspection : "1:1"
    Job ||--o| EnvMemo : "1:1"
    Job ||--o{ SurfaceRoughness : "1:N"
    Job ||--o| JobFileArchive : "1:1"
    SurfaceRoughness ||--o| SurfaceRoughnessArchive : "1:1"
    MachineLog ||--o| LogFileArchive : "1:1"
```

| 도메인 | 테이블명 | 주요 역할 |
| :--- | :--- | :--- |
| **마스터 & 계획** | `part`, `workplan`, `tool`, `workingstep`, `cad_file_archive` | 가공 대상 부품(숫자 PK + `part_name`), NC 공정 계획(숫자 PK + 부품·프로그램·NC해시 유일 제약), 공구 제원(ISO 14649 분류·치수, 매거진 장착 공구 + 미장착 재고), 단위 공정 순서 및 절삭 가공조건(Feed/Spindle), 3D 도면 관리 |
| **가공 실행 & 품질** | `job`, `machine_log`, `surface_roughness`, `inspection`, `env_memo` | 가공 이력(시간,거리,에러), CNC 1Hz 부하 요약, 다지점 표면조도, 공차/합부(PASS/FAIL), 온습도/메모 |
| **아카이브 & 백업** | `workplan_file_archive`, `job_file_archive`, `surface_roughness_archive`, `log_file_archive` | 장비 원본 경로(`*_raw_path`) 및 LONGBLOB 이중화 백업 데이터 |

> **파일 경로는 전부 아카이브 테이블에 둡니다.** 주 테이블(`job`·`workplan`·`surface_roughness`)에는
> 경로 컬럼이 없습니다. 그리고 **보관소(Vault) 안의 경로는 아예 저장하지 않습니다** — 보관소가
> `machining_raw_data` 트리를 그대로 비추므로 **보관소 상대경로 = 원본 상대경로**이기 때문입니다.
> 규칙은 `backend/vault_layout.py` 한 곳에만 있습니다.
> 다운로드·복원의 **원본 폴더 → 보관소 → LONGBLOB** 폴백은 그대로이며, 가운데 단계의 경로를
> 읽는 대신 계산할 뿐입니다.

---

## 📁 디렉터리 구조 (Directory Structure)

루트에는 **실행 파일과 최소한의 설정만** 둡니다. 코드·데이터·도구·문서는 모두 폴더로 분리되어 있습니다.

```text
ManufacturingDB/
├── system_controller.exe             # ★ 실행은 이것 하나 (제조 DB Controller)
├── README.md
├── .env                              # DB 접속 정보 (dotenv 가 cwd 기준으로 읽어 루트 고정)
├── .streamlit/config.toml            # Streamlit 설정 (streamlit 이 $CWD 에서만 읽어 루트 고정)
├── pyrefly.toml                      # 타입 체커 설정
│
├── assets/                           # 브랜드 자산 (컨트롤러·대시보드가 공유)
│   ├── logo_card.png                 #   컨트롤러 헤더 · 사이드바 카드
│   ├── logo.png / logo.jpg           #   홈 헤더 · 일반 용도
│   └── logo.ico / favicon.ico        #   앱 아이콘 · 브라우저 탭
│
├── controller/                       # 제조 DB Controller C++ 소스
│   ├── system_controller.cpp         #   Win32 오너드로우 UI + 프로세스 감독
│   ├── theme.h                       #   Google Cloud 콘솔풍 팔레트 및 그리기 도우미
│   ├── app_config.h                  #   설정 JSON 읽기/쓰기 + 파이썬 인터프리터 탐색
│   └── system_controller.rc          #   실행 파일 아이콘 및 버전 정보
│
├── backend/                          # 수집 파이프라인
│   ├── data_insert_recognization.py  #   Watchdog 파일 감시 및 큐 분배기
│   ├── job_layout.py                 #   Job 폴더 하위 분류(XML/Log/TDMS/NC) 규칙 단일 출처
│   ├── vault_manager.py              #   경로 기준(PROJECT_ROOT·DATA_ROOT 등) 및 Vault 아카이빙
│   ├── vault_layout.py               #   보관소 안의 파일 배치 규칙 단일 출처 (경로를 DB 에 저장하지 않고 계산)
│   ├── file_archive.py               #   아카이브 행 조회/생성 및 경로 읽기 헬퍼
│   ├── job_manager.py                #   Job 생성 및 중복 확인 유틸리티
│   ├── recovery_engine.py            #   Vault/DB 기반 복원 및 재난 복구 ZIP 생성
│   ├── tdms_visualizer.py            #   백그라운드 TDMS Parquet/FFT 변환 데몬
│   ├── tdms_alignment.py             #   NC 대조 실가공 구간 판별 + CNC/DAQ 시간축 정렬 (단독 CLI)
│   ├── tool_inserter.py              #   공구 마스터 전체 교체 (ISO 14649 형식 CSV·XLSX 검사 → 교체 → 보관)
│   ├── pipeline_control.py           #   파서 토글/일시정지 상태 (컨트롤러와 JSON 공유)
│   ├── pipeline_config.json          #   ↑ 그 상태 파일
│   ├── integrity.py                  #   SHA-256 원본 복원 검증 단일 로직 (3-상태)
│   ├── integrity_monitor.py          #   검증 백그라운드 감시자 (30분 주기)
│   ├── auth.py                       #   로그인 계정 · 세션 · 가입 신청/승인 (PBKDF2)
│   ├── user_manager.py               #   유저 관리 창: 가입 승인·멤버 조회·삭제 (tkinter, 단독 실행 가능)
│   ├── clean_dummy_data.py           #   고아 Workplan·중복 Workingstep 자동 정리
│   ├── reset_database_and_storage.py #   항목별 초기화: 가공 DB · 스토리지 · 공구 목록 (보관소·계정 보존)
│   ├── reset_manager.py              #   초기화 창: 항목 선택 · 모듈 실행 확인 · 진행 기록 (tkinter)
│   ├── admin_ui.py                   #   관리 창 공통 (팔레트 · 버튼 · 창 하나만 · DPI)
│   ├── migrate_keys_v3.py            #   키 정규화 마이그레이션 (숫자 PK 전환)
│   ├── migrate_job_folder_layout.py  #   Job 폴더를 자료 종류별 하위 폴더로 이전
│   ├── migrate_vault_location.py     #   원본 보관소 위치 이전 도구 (미리보기 / --apply)
│   ├── migrate_tool_iso14649.py      #   tool 테이블 ISO 14649 구조 전환 + CSV 적재 (미리보기 / --apply)
│   ├── normalize_name_casing.py      #   보관소 폴더 대소문자 표기를 DB 기준으로 통일
│   ├── backfill_workingstep_conditions.py  # 기존 Workplan 절삭조건 소급 갱신
│   ├── DB/
│   │   ├── database.py               #   SQLAlchemy 커넥션 풀링 및 세션 팩토리
│   │   ├── models.py                 #   14개 테이블 ORM 정의
│   │   └── schema_patch.py           #   기동 시 누락 컬럼만 채우는 경량 보정
│   ├── parsers/                      #   자료 종류별 파싱 엔진
│   │   ├── xml_parser.py  nc_parser.py  tdms_parser.py
│   │   └── log_parser.py  roughness_parser.py  cad_parser.py
│   └── tests/
│       ├── test_nc_parser.py                  # pytest
│       ├── test_tool_master_parse.py          # pytest (공구 마스터 파일 검사)
│       ├── test_reset_options.py              # pytest (초기화 항목 선택)
│       ├── test_setup_target.py               # pytest (설치 마무리: .env · my.ini · 입력 검사)
│       ├── test_user_management.py            # pytest (가입 승인·멤버 관리)
│       ├── test_controller_integration.py     # pytest
│       ├── test_tdms_alignment.py             # 단독 실행 (합성 TDMS 31개 검사)
│       ├── verify_e2e_cutting_conditions.py   # 단독 실행 (실 DB 연동 검증)
│       ├── verify_tool_csv.py                 # 단독 실행 (공구 CSV·모델·실제 tool 테이블 대조)
│       └── verify_reset.py                    # 단독 실행 (초기화를 시험용 스키마에서 실제로 돌려 봄)
│
├── frontend/                         # Streamlit 통합 대시보드 (Port 8501)
│   ├── dashboard.py                  #   단일 엔트리포인트: 사이드바 내비게이션
│   ├── cad_viewer_component.py       #   3D CAD(STEP/STL) 뷰어 (Z-up, 축 gizmo)
│   ├── erd_component.py              #   인터랙티브 ERD 다이어그램
│   └── components/
│       ├── common.py                 #   경로 설정 · 캐싱 쿼리
│       ├── home_view.py              #   홈 화면 및 Quick Access 카드
│       ├── filters.py                #   5단계 캐스케이딩 Job 검색 필터
│       ├── job_workspace.py          #   Job 검색·분석·수정 통합 화면
│       ├── master_tree.py            #   계층형 마스터 데이터 (ISO 14649 트리)
│       ├── tool_master.py            #   공구 마스터 파일 교체 · CSV 내려받기 · 장착/미장착 조회
│       ├── data_upload.py            #   가공 데이터 수동 업로드
│       ├── db_explorer.py            #   DB 테이블 조회·필터·편집·CSV
│       ├── download_center.py        #   프로젝트 ▸ Part ▸ Job 드릴다운 다운로드
│       ├── native_picker.py          #   파일 선택창 형식 필터 이름 지정
│       └── recovery.py               #   시스템 백업/복구
│
├── data/                             # 런타임 데이터 (코드와 분리 · 전부 gitignore)
│   ├── machining_raw_data/           #   장비 데이터 유입 감시 폴더
│   │   └── {Project}/{Part}/
│   │       ├── CAD_Files/            #     3D 도면 (Part 단위)
│   │       └── {JobID}/              #     가공차수 1건
│   │           ├── XML/  Log/  TDMS/  NC/
│   │           ├── Surface_Roughness/
│   │           ├── etc/
│   │           └── processed_parquet/
│   ├── processed_data/               #   변환된 시계열·FFT Parquet
│   └── failed_data/                  #   파싱 실패 격리 (DLQ)
│
│   ※ 원본 백업 보관소(archive_vault)는 프로젝트 밖에 둡니다.
│     .env 의 ORCUS_VAULT_ROOT 로 프로젝트 밖을 가리키면
│     프로젝트 폴더가 사라져도 백업은 남습니다.
│
├── tools/                            # 개발·운영 도구
│   ├── run_system.py                 #   CLI 일괄 실행 (컨트롤러 대안)
│   ├── compile_controller.bat        #   컨트롤러 빌드 (g++ + windres, 인자로 출력 경로 지정 가능)
│   ├── run_controller.bat            #   컨트롤러 실행 (없으면 자동 빌드)
│   ├── run_windows.bat               #   venv 생성 + 의존성 설치 + CLI 실행
│   └── requirements.txt              #   런타임 의존 패키지
│
├── installer/                        # 배포용 설치 파일(Setup.exe) 만들기 — 결과물은 dist\ (gitignore)
│   ├── build_installer.py            #   런타임 · MySQL · VC++ 런타임 · 프로젝트 파일을 모아 컴파일
│   ├── ManufacturingDB.iss           #   Inno Setup 스크립트 (마법사 화면 · 업그레이드 · 방화벽 · 제거)
│   ├── setup_target.py               #   설치 마무리 (설치한 PC 에서 실행): MySQL · 테이블 · 공구 목록 · 확인
│   ├── requirements-runtime.txt      #   동봉 파이썬 런타임의 패키지 (판 고정)
│   └── install_info.txt              #   설치 화면 안내문
│
└── docs/
    ├── gemini.md                     # 전체 시스템 상세 명세서
    ├── tool_iso14649.csv             # 공구 마스터 데이터 (장착 18 + 미장착 32)
    ├── tool_iso14649_schema.sql      # tool 테이블 DDL (models.Tool 과 같음)
    └── tool_iso14649_sources.md      # 공구 사양의 출처 · 실물 확인 목록
```


---

## 🚀 빠른 시작 (Quick Start)

> **다른 PC(서버 PC 등)에 설치만 하려면** 아래 [배포용 설치 파일](#-배포용-설치-파일-setupexe) 하나로 끝납니다.
> 그 PC 에 파이썬 · MySQL 을 따로 설치하지 않아도 되고, 인터넷도 필요 없습니다. 아래 1~5 는 개발 PC 기준입니다.

### 1. 사전 요구사항 (Prerequisites)
- **Python**: 3.10 이상
- **MySQL**: 8.0 이상 (인스턴스 구동 필요)
- **OS**: Windows 10/11 — 제조 DB Controller 가 Win32 API 를 직접 사용합니다.
  다른 OS 에서는 CLI 실행(`python tools/run_system.py`)을 쓰세요
- **g++ (MinGW-w64 / w64devkit)**: 컨트롤러를 **수정해 다시 빌드할 때만** 필요합니다

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

# 원본 백업 보관소(archive_vault)를 프로젝트 밖에 둡니다.
# MySQL 스키마 폴더 안은 피하세요 (DROP DATABASE 시 함께 삭제됨).
ORCUS_VAULT_ROOT=D:\Orcus\archive_vault
```

#### 백업 보관소를 프로젝트 밖에 두는 이유

보관소가 프로젝트 안에 있으면 **프로젝트 폴더를 지우는 순간 원본 백업도 함께 사라집니다.**
백업의 존재 이유가 "원본이 없어져도 복원할 수 있다"는 것이므로, 보관소는 프로젝트와
수명이 다른 곳(예: DB 가 설치된 폴더)에 두어야 합니다.

| 우선순위 | 환경변수 | 의미 |
| :--- | :--- | :--- |
| 1 | `ORCUS_VAULT_ROOT` | 보관소 절대경로를 직접 지정 |
| 2 | `ORCUS_DB_DATA_DIR` | DB 설치 폴더. 그 아래 `archive_vault/` 를 만들어 사용 |
| 3 | (설정 없음) | 예전 위치 `data/archive_vault` 를 그대로 사용 |

DB 에는 보관소 루트 기준의 **상대경로만** 저장되므로, 위 값을 바꾸고 폴더만 옮기면
기존 레코드를 손대지 않아도 복원 기능이 그대로 동작합니다. 이미 쌓인 보관소를 옮길 때는
아래 도구를 사용하세요.

```bash
python backend\migrate_vault_location.py            # 무엇이 옮겨질지 미리보기
python backend\migrate_vault_location.py --apply    # 실제 이동 (복사 → 검증 → 원본 삭제)
```

#### 폴더 이름 대소문자 표기

Windows 파일시스템과 MySQL 기본 콜레이션은 **둘 다 대소문자를 구분하지 않습니다.** 그래서
같은 부품을 `DB_Test` 로도 `DB_TEST` 로도 넣을 수 있고 DB 조회는 어느 쪽으로도 같은 행을
찾습니다(중복 Part 는 생기지 않습니다). 파서는 **먼저 등록된 표기**를 기준으로 경로를 만들어
보관소 폴더가 갈라지지 않게 합니다. 이미 갈라진 폴더가 있다면 아래 도구로 정리하세요.

```bash
python backend\normalize_name_casing.py             # 무엇이 바뀔지 미리보기
python backend\normalize_name_casing.py --apply     # DB 표기 기준으로 폴더 이름 통일
```

> Windows 에서는 대소문자가 달라도 파일이 열려 당장은 문제가 없지만, 보관소를 리눅스/NAS 로
> 옮기거나 다른 PC 에서 복원하면 대소문자를 구분하는 파일시스템에서 경로가 어긋납니다.
> DB 가 참조하지 않는 고아 폴더는 **삭제하지 않고 보고만** 합니다.

**초기화는 지울 항목을 골라서 합니다.** 컨트롤러의 `DB · 스토리지 초기화` 는 초기화 창
(`backend/reset_manager.py`)을 띄우고, 거기서 항목마다 지워질 양(테이블 · 행 · 파일 · 공구 수)을 보며
고릅니다. 수집기 · 대시보드 · TDMS 변환기가 실행 중이면 실행 버튼이 잠깁니다 — 수집기가 켜져
있으면 지운 Job 폴더를 '유실'로 보고 보관소에서 자동 복원해 초기화가 무효가 되기 때문입니다.

| 항목 | 지우는 것 | 남는 것 |
| :--- | :--- | :--- |
| 가공 데이터 DB | 공구 · 계정을 뺀 모든 테이블 (DROP 후 `models.py` 로 다시 만듦) | 공구 목록 · 로그인 계정 |
| 스토리지 | `data/machining_raw_data` · `data/processed_data` · `data/failed_data` (폴더는 남김) | 원본 백업 보관소 |
| 공구 목록 | `tool` 테이블 + 감시 폴더 루트의 공구 마스터 파일 | 보관소의 `공구 마스터.csv` |

**백업 보관소와 로그인 계정은 어떤 항목을 골라도 지우지 않습니다.** 보관소는 삭제 대상 목록에서
제외하는 것에 더해, 실제 삭제 직전에 보관소 안쪽 경로인지 한 번 더 확인해 막습니다. 계정은
`유저 관리` 에서 관리합니다. 명령줄에서는 `python backend/reset_database_and_storage.py --db --storage --tools`
(또는 `--all`)로 같은 일을 하며, 항목을 하나도 주지 않으면 아무것도 지우지 않습니다.

**되돌리는 길도 버튼 하나입니다.** 관리 유틸리티의 `보관소에서 원본 복원` 은 보관소의 원본을
`data/machining_raw_data` 로 되돌려 놓습니다. 보관소가 원본 트리를 그대로 비추므로 복원은
**폴더 복사 한 번**이고, 파일 이름도 자리도 원본과 같습니다. 파일이 제자리에 돌아가면 수집기가
그것을 다시 읽으므로 **DB 까지 함께 복구**됩니다. 이미 있는 파일은 건드리지 않고 없는 것만
채우므로 여러 번 눌러도 결과가 같고, 살아남은 원본을 사본으로 덮어쓰지 않습니다.

**DB 가 통째로 비어 있어도 복원됩니다.** 경로에 `job_id` 가 전혀 들어가지 않으므로 DB 를 볼
이유가 없습니다.

> 옛 보관소(`jobs/` · `tdms_files/Job_{id}/` 같은 id 기반 배치)는
> `python backend/migrate_vault_to_mirror_layout.py --apply` 로 한 번 옮기면 됩니다
> (미리보기 기본, 복사 → 검증 → 삭제, 검증 실패 시 아무것도 지우지 않음).
> **실행 전 컨트롤러에서 「전체 중지」** 를 하세요.

> ⚠️ **보관소를 MySQL 스키마 폴더(`...\Data\orcus`) 안에 두지 마세요.**
> `DROP DATABASE orcus` 는 스키마 폴더를 **통째로 삭제**하므로 그 안의 `archive_vault` 까지
> 함께 사라집니다. 이 프로젝트의 초기화 버튼은 `DROP TABLE` 만 쓰고 보관소를 이중으로
>보호하지만, MySQL Workbench 등에서 스키마를 직접 재생성하면 보호 장치가 소용없습니다.
> **2026-09-15 에 실제로 이 경로에서 보관소가 통째로 소실된 사례가 있습니다.**
> 반드시 스키마 폴더 **바깥**을 가리키세요 (예: `D:\Orcus\archive_vault`).

> **디스크 분리 권장** — 원본(`machining_raw_data`)과 보관소가 같은 물리 디스크에 있으면
> 디스크 고장 시 둘 다 잃습니다. 가능하면 서로 다른 디스크에 두세요.

### 4. 시스템 실행 (Run System)

#### 방법 1: 제조 DB Controller (권장 - 실시간 로그 모니터 & 모듈/파서 토글 지원)
C++ Win32 네이티브 컨트롤러로 Streamlit UI, Watchdog 파일 감시, TDMS 변환기, REST API 서버를 개별 제어하고,
실시간 로그 스트리밍과 파서별 On/Off 토글을 수행합니다. Google Cloud 콘솔풍의 밝은 화면을
오너드로우로 직접 그렸고, 강조색은 대시보드와 같은 `#1A73E8` 입니다.
```bash
system_controller.exe          # 실행 파일 직접 실행
tools\run_controller.bat       # 없으면 자동 빌드 후 실행
```

**동작 방식** — 컨트롤러는 파이썬 코드를 품고 있지 않고 디스크의 `.py` 를 그대로 실행하는 감독자입니다.
따라서 `backend/`, `frontend/` 를 수정하면 **컨트롤러를 다시 빌드하지 않아도** 다음 실행부터 반영됩니다.

**유저 관리** — 관리 유틸리티의 `유저 관리` 창에서 대시보드 계정을 관리합니다.
`가입 신청` 탭에서 신청을 승인·거절하고, `가입 멤버` 탭에서 멤버(아이디·이름·가입 시각·마지막 로그인)를
조회하거나 삭제합니다. 버튼은 `backend/user_manager.py`(tkinter 창)를 띄우기만 하고, 모든 처리는 그
파이썬 창이 DB 에 직접 합니다. 그래서 컨트롤러 없이 `python backend/user_manager.py` 로도 똑같이
동작합니다. 새 신청·승인·거절·삭제는 실시간 로그에 `[가입 신청] 아이디 (이름)` 처럼 한 줄씩 남습니다.
멤버를 삭제해도 가공 이력에 기록된 작업자 이름은 그대로 남습니다.

**파이썬 인터프리터 탐색 순서** (`controller/app_config.h`)
1. `runtime\python.exe` — 배포용 설치 파일이 넣는 동봉 런타임 (파이썬 미설치 PC 대응. 개발 PC 에는 없음)
2. `py -3` → 실제 `sys.executable` 경로를 조회해 사용
3. PATH 의 `python` → 마찬가지로 실제 경로를 조회해 사용

> PATH 의 `python.exe` / `py.exe` 는 Microsoft Store 파이썬에서 0바이트 **앱 실행 별칭**인 경우가 많고,
> 표준 출력을 파이프로 받는 실행에서는 `ERROR_CANT_ACCESS_FILE(1920)` 로 실패합니다.
> 그래서 별칭을 한 번 실행해 실제 exe 경로를 받아낸 뒤, 이후에는 그 절대 경로만 씁니다.

**컨트롤러 빌드** (수정했을 때만 필요, MinGW-w64 / w64devkit 의 g++ 사용)
```bash
tools\compile_controller.bat
```

#### 방법 2: CLI 터미널 일괄 실행
`tools/run_system.py`를 실행하면 필수 패키지 설치 확인, DB 스키마 자동 초기화, 파이프라인, 통합 대시보드 및 REST API 서버가 서브프로세스로 동시 기동됩니다.
```bash
python tools/run_system.py
```

*개별 서비스 단독 실행:*
```bash
# REST API 서버만 단독 실행 (Port 8000)
tools\run_api.bat              # 또는 python tools/run_api.py
```

### 5. 서비스 접속
- **통합 대시보드**: [http://localhost:8501](http://localhost:8501)
- **제조 DB REST API (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs) (ReDoc: `/redoc`, OpenAPI 스펙: `/openapi.json`)
- **컨트롤러 모니터**: `system_controller.exe` 창에서 4대 모듈 상태 및 실시간 로그 확인 가능

---

## 📦 배포용 설치 파일 (Setup.exe)

다른 PC 에는 설치 파일 하나로 설치합니다. 설치 파일 안에 프로젝트, 전용 파이썬 3.14 런타임(패키지 포함),
MySQL 8.0, VC++ 런타임이 모두 들어 있어서 **그 PC 에 파이썬 · MySQL 이 없어도, 인터넷이 없어도** 됩니다.

### 설치하기 (설치할 PC)

1. `Setup_ManufacturingDB_v3.1.0.exe` 를 실행합니다 (관리자 권한 필요. 서명되지 않은 파일이라 SmartScreen 이
   경고하면 '추가 정보' → '실행').
2. **설치 폴더**(기본 `D:\ManufacturingDB`)와 **데이터 폴더**(기본 `D:\Orcus` — 원본 백업 보관소 `archive_vault` 와
   DB 파일 `mysql_data` 가 생기는 곳)를 고릅니다. D 드라이브가 없으면 C 드라이브가 기본값이고, 둘 다 영문 경로여야 합니다.
3. **데이터베이스**를 고릅니다. 설치 프로그램이 이 PC 의 MySQL 서비스와 3306 포트를 먼저 살펴 기본값을 정합니다.
   - **동봉된 MySQL 8.0.46 설치 (권장)** — `OrcusMySQL` 서비스로 등록되어 부팅 때 켜지고, 이 PC 안에서만 접속됩니다.
     3306 을 이미 다른 MySQL 이 쓰면 비어 있는 포트(3307~)를 제안합니다.
   - **이 PC 에 이미 있는 MySQL 사용** — 8.0.16 이상. root 비밀번호를 넣으면 설치를 시작하기 전에 실제로 접속해 확인합니다.
     같은 이름의 데이터베이스가 이미 있으면 지우지 않고 이어서 씁니다.
   - root 비밀번호 기본값은 프로젝트 기본값 `610610610` 이고, 바꾸면 설치 폴더의 `.env` 에 저장됩니다.
4. 끝나면 '제조 DB Controller' 를 실행해 **[전체 시작]** → 웹 대시보드 <http://localhost:8501>.
   처음 가입한 계정은 컨트롤러의 **[유저 관리]** 에서 승인합니다.

| 설치 프로그램이 하는 일 | 내용 |
| :--- | :--- |
| 파일 | 프로젝트(`backend` · `frontend` · `tools` · `docs` · 컨트롤러) · `runtime\` (파이썬) · `mysql\` |
| VC++ 런타임 | 없거나 동봉판보다 오래됐을 때만 Microsoft 재배포 패키지를 조용히 설치 (MySQL 이 사용) |
| MySQL | (동봉) `my.ini` 작성 → 데이터 폴더 초기화 → `OrcusMySQL` 서비스 등록·시작 → root 비밀번호 지정 |
| 데이터베이스 | `orcus`(utf8mb4) · `models.py` 기준 테이블 · 기본 공구 목록(설치 파일을 만들 때 DB 에 있던 공구 마스터) |
| 설정 · 폴더 | `.env`(DB 접속 · `ORCUS_VAULT_ROOT`) · 원본 백업 보관소 · `data\` 폴더 |
| 방화벽 | 대시보드 8501 (기본 선택) · REST API 8000 (기본 해제 — 로그인 없이 DB 를 읽을 수 있어서) |
| 바로 가기 | 바탕 화면 · 시작 메뉴 (컨트롤러, 설치 폴더, 설치 기록, 제거) |

- **다시 실행 = 업그레이드** — 같은 판이든 새 판이든, 이미 설치된 폴더에 다시 설치하면 설정 화면 없이 `.env` · DB ·
  데이터 · 파서 On/Off 설정을 그대로 두고 프로그램만 바꿉니다. DB 는 빠진 테이블·컬럼만 채웁니다.
- **제거** — 서비스와 방화벽 규칙을 지우고, 데이터베이스 파일(`mysql_data`)은 지울지 묻습니다(기본: 남김).
  **원본 백업 보관소는 지우지 않습니다.** 남긴 데이터 폴더를 다시 고르면 그 DB 를 그대로 이어서 씁니다.
- **실패하면** — 프로그램 파일은 설치된 채로 원인을 보여 줍니다. 고친 뒤 설치 프로그램을 다시 실행하면 이어서 설정합니다.
  기록: `{설치 폴더}\logs\install.log`
- **기존 데이터 옮기기** — 원래 PC 의 보관소 폴더(`archive_vault`)를 새 PC 데이터 폴더 아래로 복사한 뒤 컨트롤러에서
  **[보관소에서 원본 복원]** → **[전체 시작]** 하면 수집기가 DB 를 다시 채웁니다. 계정은 새로 가입·승인합니다.

무인 설치(명령줄) — 화면 없이 같은 설정으로 설치합니다. 설정이 끝까지 되지 않으면 종료 코드 8 을 돌려줍니다.

```bat
Setup_ManufacturingDB_v3.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR=D:\ManufacturingDB /DATAROOT=D:\Orcus ^
    /DBMODE=bundled /DBPORT=3306 /DBPASSWORD=610610610 /TASKS="desktopicon,firewall"
rem 제거하면서 DB 파일까지 지우기: "D:\ManufacturingDB\unins000.exe" /VERYSILENT /DELETEDBDATA=1
```

### 설치 파일 만들기 (개발 PC)

필요한 것: [Inno Setup 6](https://jrsoftware.org/isdl.php) (`winget install JRSoftware.InnoSetup`), 이 PC 의
MySQL Server 8.0 설치본, w64devkit(g++), 처음 한 번 인터넷(파이썬 · VC++ 런타임 내려받기).

```bat
python installer\build_installer.py           & rem → dist\Setup_ManufacturingDB_v<버전>.exe
python installer\build_installer.py --test    & rem 시험용 판 (관리자 권한 없이 설치·제거, 서비스 대신 백그라운드 mysqld)
```

- 판 번호는 이 README 릴리즈 노트 맨 위(`### [Vx.y.z]`)에서 가져옵니다 (`--version` 으로 바꿀 수 있음).
- 빌드할 때의 작업 트리(커밋 전 새 파일 포함, `.gitignore` 대상 제외)를 그대로 담고, 컨트롤러는 소스에서 새로 빌드합니다.
  `.env` · `data\` · 보관소 · 테스트는 담지 않습니다.
- 동봉 런타임의 패키지는 `installer\requirements-runtime.txt` 에 판을 고정해 둡니다. **`tools\requirements.txt` 에
  패키지를 더하면 이 파일에도 더하세요** (안 그러면 설치한 PC 에서 그 기능만 import 오류가 납니다).
- 담은 MySQL 은 빌드 중에 임시 데이터 폴더로 실제로 초기화·기동해 보고, 런타임은 주요 패키지를 불러 써 봅니다.

---

## 📈 대시보드 주요 기능 안내 (UI Features)

대시보드는 **로그인 계정으로 접속**하며, 역할 구분(관리자/일반) 없이 모든 기능에 접근합니다.
가입 승인은 제조 DB Controller의 '유저 관리'에서 수행합니다. Job 삭제, DB 테이블 직접 편집처럼 되돌릴 수
없는 작업에는 확인 다이얼로그 또는 Root 계정 재인증이 남아 있습니다.

1. **홈**: 로고 헤더와 Quick Access 카드 8개. 자주 쓰는 화면으로 한 번에 이동합니다
2. **Job 워크스페이스**: 연구 프로젝트명·Part명·가공차수·실험일자·장비 기반 5단계 캐스케이딩 필터로 Job을
   검색한 뒤, 하나의 화면에서 탭으로 전환하며 처리합니다. 가공시간·조도 Ra·평균 RPM 범위로도 좁힐 수 있습니다.
   - `분석 보기`: Job 정보/품질/조도통계/CNC로그/환경 카드 + CNC·DAQ·FFT 탭 차트, CNC 로그 추이,
     마이크로미터 표면조도 단면 곡선. TDMS 카드 상단에 가공 구간 판별 근거를 한 줄로 표시합니다
   - 하단 `영구 삭제`: 원자적 연쇄 삭제(`CASCADE`) 확인 모달 지원 (가공 기록·환경·품질 입력은 업로드 시점인 `데이터 삽입` 화면으로 통합)
   - 원본 파일 다운로드는 `데이터 다운로드` 메뉴로 일원화되어 있습니다
3. **계층형 마스터 데이터**: ISO 14649 공정 트리(Part ➔ Workplan ➔ Workingstep ➔ Tool) 조회.
   Workingstep 표에 NC에서 파싱한 `주축회전수 (RPM)`·`이송속도 (mm/min)`가 함께 나오며,
   단위는 열 이름에만 표기하고 칸에는 숫자만 둡니다. Part별 3D CAD 모델 뷰어(Z-up, 축 gizmo) 제공
4. **공구 마스터**: ISO 14649 기반 공구 마스터(분류·치수·재질·재고)를 매거진 장착 공구와 미장착 재고로
   나눠 조회합니다. 고칠 때는 `현재 공구 마스터 CSV` 를 내려받아 수정한 뒤 CSV·XLSX 로 올리면 전체 교체되고,
   가공 이력의 공구 연결은 **T번호(공구 번호)** 기준으로 자동 재연결됩니다. 파일에 형식 오류가 한 곳이라도
   있으면 아무것도 바꾸지 않고 행·열 위치를 알려 줍니다. 반영된 내용은 보관소 루트에 `공구 마스터.csv` 로
   남아, 보관소에서 원본을 복원하면 수집기가 그 파일로 공구 마스터를 다시 채웁니다
   (형식·출처: `docs/tool_iso14649.csv`, `docs/tool_iso14649_sources.md`)
5. **데이터 삽입**: CAD 도면(`.stl`/`.stp`/`.step`), XML, NC, TDMS, Log, 조도 CSV 웹 수동 업로드.
   업로드와 동시에 가공 종류, 환경 정보(온습도·작업자·칩 형태·이상 소음·메모), 품질 점검(치수공차·합불 판정)을
   함께 입력합니다. 작업자는 로그인 계정 이름으로 자동 기록되며, 업로드한 파일은 자료 종류별 하위 폴더에 즉시 분류 저장됩니다
6. **DB 테이블**: 인터랙티브 ERD에서 테이블을 선택해 실시간 조회·검색, 속성별 정렬(오름/내림차순)과 속성 ▸
   비교 조건 ▸ 값 드롭다운 필터링, CSV 내보내기 지원. 표를 직접 편집한 뒤 저장하면 Root 인증을 거쳐 반영
7. **데이터 다운로드**: 프로젝트 ▸ Part ▸ Job 순으로 좁혀가며 선택 범위를 ZIP으로 내려받습니다.
   - **부품까지 선택**하면 `CAD 파일` 버튼이 나타납니다. 도면은 가공차수가 아니라 부품에 딸린 자료라
     따로 받으며, 원본 폴더 ▸ Vault ▸ DB BLOB 순으로 조달합니다
   - **Job까지 선택**하면 그 Job에 **실제로 존재하는 데이터 유형만** 버튼으로 나타나고(파일 개수 표시),
     유형별로 즉시 받거나 개별 파일 단위로 받을 수 있습니다
   - 원본 폴더가 삭제된 Job은 Vault/DB 아카이브에서 자동 조달되며, 로컬 디스크로 원본 복원도 가능합니다
8. **시스템 백업/복구**: Vault/DB에서 원본 파일들을 역추적하여 폴더 구조 그대로 압축(ZIP) 다운로드.
   단일 Job 복원과 **같은 코드**를 쓰므로 XML·NC·TDMS·로그·조도·etc·CAD·Parquet이 모두 포함됩니다

사이드바는 로고와 `DB 관계도(ERD) 보기` 버튼(전체 테이블 관계도 팝업) 아래에 데이터 입출력 메뉴
(`데이터 삽입`, `데이터 다운로드`)를 두고, 구분선 아래에 나머지 조회·관리 메뉴를 배치합니다.

### 🔒 원본 복원 검증은 백그라운드에서만 수행됩니다

XML/NC/CAD 원본이 저장 시점과 바이트 단위로 동일한지에 대한 SHA-256 검증은 화면에서 제거되었고,
데이터 수집 파이프라인이 기동 직후와 이후 30분 주기로 자동 수행합니다(`backend/integrity_monitor.py`).
판정은 `일치 / 불일치 / 검증 불가(기준 해시 미기록)` 3-상태를 그대로 유지하며, 실행 로그에 다음과 같이 남습니다.

```text
[원본 복원 검증] 일치 4건 / 불일치 0건 / 검증 불가 0건 / 원본 미보존 1건
```

불일치가 감지되면 해당 대상의 기준 해시와 재계산 해시가 `[경고]` 라인으로 함께 출력됩니다. 해시 재계산
로직은 여전히 `backend/integrity.py` 한 곳만 사용합니다.

---

## 🚀 릴리즈 노트 (Release Notes)

각 버전의 핵심만 적습니다. 세부 동작과 판단 근거는 [`docs/gemini.md`](docs/gemini.md)를 참고하세요.

### [V3.1.0] - 2026-10-09
- **제조 DB REST API 서버 신설 (Port 8000 & OpenAPI/Swagger)**:
  - 외부 연구자, 타 서버, AI 분석 모델 및 LLM 연동을 위한 FastAPI 기반 고성능 RESTful API 서비스 신설 ([`backend/api/`](backend/api))
  - **전체 DB 일괄 덤프 (`GET /api/v1/export/all`)**: 15개 전체 테이블의 모든 데이터를 단일 JSON 객체로 일괄 추출
  - **LLM/RAG 최적화 계층형 프로필 (`GET /api/v1/jobs/{job_id}/full`)**: 특정 Job의 공정 계획부터 공구, 장비로그, 표면조도, 품질검사, 환경메모까지 단일 JSON 컨텍스트로 바인딩하여 LLM 환각(Hallucination) 방지
  - 대화형 Swagger UI (`/docs`), ReDoc (`/redoc`), 및 LLM Tool/Function Calling 자동 등록용 OpenAPI 표준 스펙 (`/openapi.json`) 제공
  - CORS 전체 허용(`*`) 및 민감정보(비밀번호 해시, 대용량 바이너리) 자동 필터링 직렬화기([`backend/api/serializers.py`](backend/api/serializers.py)) 적용
- **제조 DB Controller (C++ GUI) 4대 프로세스 통합**:
  - `system_controller.exe`에 `REST API (Port 8000)`를 제4의 핵심 시스템 모듈로 추가
  - 8000번 포트 충돌 사전 감지, 개별 시작/중지 토글, `[전체 시작]` / `[전체 중지]` 일괄 제어 및 실시간 로그 스트리밍 통합
  - 모듈 카드 높이 및 메인 윈도우 크기 자동 조정 (1280x930)
- **ISO 14649 공구 마스터 및 스토리지 초기화 고도화**:
  - 공구 마스터 스키마를 ISO 14649 표준(분류, 절삭방향, 치수, 날 수, 재질, 재고)으로 전면 정규화 (`docs/tool_iso14649.csv`, `backend/tool_inserter.py`)
  - DB 및 스토리지 선택적 초기화 관리자 (`backend/reset_manager.py`) 신설
- **올인원 배포 인스톨러 (Inno Setup) 아키텍처 구축**:
  - `installer/build_installer.py`, `installer/ManufacturingDB.iss`를 통해 파이썬 3.14 런타임 및 MySQL 8.0 무인 자동 설치 지원
- **러너 및 패키지 의존성 최신화**:
  - `tools/run_api.py`, `tools/run_api.bat` 신설 및 `tools/run_system.py`에 REST API 서버 5번째 서비스로 자동 기동 연동
  - `tools/requirements.txt`에 `fastapi`, `uvicorn` 반영

### [V3.0.6] - 2026-09-30
- **웹 대시보드 로그인 인증 및 세션 영속화**:
  - `app_user` (계정 아이디, 작업자명, PBKDF2-HMAC-SHA256 해시, 600,000회 반복), `app_session` (토큰 해시, 7일 만료), `app_pending_signup` (승인 대기)
  - 대시보드에서 가입 신청 후 제조 DB Controller의 `유저 관리` 창에서 관리자가 승인/거절 및 회원 삭제 관리 (이메일 인증 불필요)
  - URL 질의문자열(`?sid=...`)과 브라우저 쿠키(`streamlit-cookies-controller`) 연동으로 브라우저 새로고침 및 새 탭에서도 로그인 상태 유지
  - 가입 시 등록한 작업자 이름이 데이터 삽입 시 `env_memo.worker_name`으로 자동 기록
- **원본 백업 보관소 미러 레이아웃(Mirror Layout) 전환 및 원클릭 복원**:
  - 보관소(`archive_vault`) 구조를 원본 트리(`machining_raw_data`)와 1:1 동일한 상대경로 미러 구조로 단순화 (`backend/vault_layout.py`)
  - 보관소 경로에서 DB 대리키(`job_id`, `workplan_id`) 의존성을 완전히 제거하여, DB가 비어 있거나 auto-increment가 밀려도 원본 파일명과 폴더 그대로 복원 가능
  - 아카이빙 쓰기 진입점을 `vault_manager.archive_file(raw_path)` 하나로 단일화
  - Controller 관리 유틸리티 및 스크립트(`backend/restore_from_vault.py`)에 `보관소에서 원본 복원` 추가: 보관소 원본을 `machining_raw_data`로 복원하여 수집기를 통한 DB 자동 재구축 지원 (기존 파일 보존 및 덮어쓰기 방지)
  - 기존 구형 보관소를 미러 구조로 변환하는 마이그레이션 도구 제공 (`backend/migrate_vault_to_mirror_layout.py`)
- **공구 코드 표준 표기(`T01`~`T99`) 및 공구 번호 기반 매칭 (`backend/tool_codes.py`)**:
  - 공구 코드를 두 자리 0 채움 표준 표기(`T01`~`T99`)로 정규화하여 정렬 순서 및 화면 일관성 확보
  - NC 코드(`M6T6`), XML(`T6_10`), 엑셀 마스터 간의 연결을 문자열 표기가 아닌 **공구 번호(정수 `tool_number`)** 기준으로 일원화하여 연결 누락 문제 해결
- **가공 환경 및 품질 데이터 입력/관리 체계 개선**:
  - 메타데이터(가공 종류), 환경 정보(온습도·작업자·칩 형태·이상 소음·메모), 품질 점검(치수공차·합불 판정) 입력을 **'데이터 삽입' 업로드 화면으로 일원화**
  - Job 워크스페이스의 복잡한 편집 모드를 제거하고 직관적인 '영구 삭제' 기능만 유지
  - `job` 생성 시 `env_memo` 빈 레코드 동시 생성 보장 (`job_manager.ensure_env_memo`)
  - `job.start_time` 기반으로 `day_of_week` (영어: `Monday`~`Sunday`)를 세션 flush 이벤트에서 자동 계산 및 동기화 (`DB/models.py`)
  - 조도 CSV 파서(`roughness_parser`) 측정 평균 Ra/Rz 보존 및 수동 입력 우선 적용, `inspection`에서 미사용 `shape_accuracy` 컬럼 제거
- **제조 DB Controller (C++ GUI) 기능 확장 및 UI 편의성 개선**:
  - 관리 유틸리티 버튼 추가: `유저 관리` (신청 승인/거절, 회원 목록/삭제 GUI 창) 및 `보관소에서 원본 복원`
  - 창 생성 시 작업표시줄을 제외한 작업 영역 중앙에 배치하여 하단 버튼 잘림 현상 방지
  - 유저 신청/승인/거절/삭제 이벤트를 Controller 실시간 로그 패널에 즉각 표시
  - C++ 컨트롤러 재컴파일 완료 (`system_controller.exe`)

### [V3.0.5] - 2026-09-16
- **보관소를 MySQL 스키마 폴더 밖으로 이전** (`ORCUS_VAULT_ROOT=D:\Orcus\archive_vault`):
  - 기존 위치(`...\Data\orcus\archive_vault`)는 `DROP DATABASE orcus` 실행 시 스키마 폴더와 함께
    **통째로 삭제**됩니다. 초기화 버튼의 이중 보호는 스키마 직접 재생성에는 소용이 없습니다.
  - 2026-09-15 해당 경로에서 보관소가 소실된 사례가 있어 경로를 옮기고 README·gemini.md 에 경고 강화
  - 원본과 보관소를 서로 다른 물리 디스크에 두도록 권장 문구 추가
- **시스템 아키텍처 다이어그램 전면 수정**: 실제 구조와 어긋나 있던 부분을 바로잡음
  - 3개 독립 프로세스(Watchdog 수집기 / TDMS 변환 데몬 / Streamlit)를 Controller 하위에 명시.
    TDMS 변환은 수집기의 일부가 아니라 **별도 프로세스**(5초 폴링)
  - **Parquet 저장 계층**(3중 저장: processed_data → Job폴더 → Vault) 추가 —
    시계열 시각화는 MySQL 이 아니라 Parquet 에서 읽음
  - `MySQL ◄═► Vault` 직접 연결선 **삭제** — DB 에는 보관소 상대경로 문자열만 저장되며
    실제 파일 입출력은 파서·recovery_engine·Streamlit 이 수행
  - `job_layout` · `pipeline_control` · `integrity_monitor` · `failed_data(DLQ)` ·
    `tdms_alignment` 표기 추가, Streamlit → raw_data 순환(데이터 삽입) 경로 추가

### [V3.0.4] - 2026-09-15
- **TDMS 변환 성능 5.4배 개선** (464MB 파일 실측 54.9초 → 10.1초, 폴더 투입부터 UI 전체 노출까지 148초 → 43.2초):
  - 채널별 지연 로딩이 채널 수만큼 파일을 다시 스캔하던 문제를 단일 패스 읽기(`TdmsFile.read`)로 교체.
    CNC 34채널을 읽는 데만 45초(전체의 79%)가 소모되던 병목 제거
  - 손상 파일 꼬리를 잘라내는 `_TruncatedFile` 래퍼가 파일 하나당 400만 회 호출되던 문제를
    읽기 위치 직접 추적 + `BufferedReader`(1MB) 도입으로 수백 회 수준까지 축소
  - 여유 메모리를 보고 단일 패스와 지연 로딩을 자동 선택 (`open_tdms(eager=None)`)하여
    대용량 파일에서도 안전하게 동작
- **부품·프로젝트 표기 통일**: Windows·MySQL 모두 대소문자를 구분하지 않아 같은 부품이
  `DB_Test` / `DB_TEST` 등 서로 다른 Vault 폴더로 갈라지던 문제 해결
  - 파서가 들어온 이름 대신 **DB에 등록된 표기**로 경로를 생성하도록 수정 (`canonical_names`)
  - 기존 보관소 폴더 표기를 일괄 정리하는 `backend/normalize_name_casing.py` 신설
    (미리보기 기본, `--apply` 적용, 고아 폴더는 삭제하지 않고 보고만)
- **Watchdog 삭제 감지 안정화**:
  - 탐색기가 폴더 복사 중 생성·삭제하는 `desktop.ini`, `Thumbs.db`, 오피스 임시 파일 등을
    수집·삭제 양쪽에서 동일 기준으로 무시 (`job_layout.is_ignored_file`)
  - 삭제 이벤트마다 스레드를 새로 띄우던 구조를 큐 + 단일 워커로 교체.
    파일 14개 폴더 삭제 시 15개 스레드가 동시에 DB 세션을 잡아 커넥션 풀이 고갈되던
    (`QueuePool limit ... timed out`) 문제 해소, 자동 복원 트리거 15회 → 1회
- **Streamlit 1.61 지원**: 폐기 예정 경고를 발생시키던 `use_container_width`를
  `width="stretch"`로 전면 교체 (11곳)

### [V3.0.3] - 2026-09-15
- **문서화 및 가이드 최신화**:
  - `tools/`, `controller/`, `docs/`, `assets/` 디렉터리 분리에 따른 README 실행·빌드 가이드 및 스크립트 경로 전면 갱신
  - V3.0.2 원본 백업 보관소 외부 분리(`ORCUS_VAULT_ROOT` / `ORCUS_DB_DATA_DIR`) 가이드 및 `migrate_vault_location.py` 사용법 정리
  - 스토리지 초기화 시 보관소 영구 보존 정책 및 이중 보호 체계 문서 반영

### [V3.0.2] - 2026-09-15
- **원본 백업 보관소(Vault) 외부 분리 및 영구 보호**:
  - 프로젝트 폴더 삭제나 재설치 시에도 원본 백업이 함께 유실되지 않도록 보관소 경로를 프로젝트 외부(예: MySQL 데이터 디렉터리 등)로 지정 지원 (`ORCUS_VAULT_ROOT` / `ORCUS_DB_DATA_DIR` 환경변수)
  - `backend/migrate_vault_location.py` 마이그레이션 도구 신설 (기본 드라이런 미리보기 및 `--apply` 옵션을 통한 안전한 원본 복사·검증·삭제)
  - DB 및 캐시 초기화(`reset_database_and_storage.py`) 시 원본 백업 보관소(`archive_vault`)를 영구 보존하는 이중 보호 장치(`is_inside_vault`) 적용
  - C++ 컨트롤러 다이얼로그에서 실제 보관소 위치 표시 및 초기화 대상(DB, raw_data, processed_data, failed_data)과 보존 대상(보관소) 분리 안내

### [V3.0.1] - 2026-09-15
- **프로젝트 디렉터리 구조 개편 및 정돈**:
  - 루트의 중복 로고 및 배치 스크립트를 정리하고 `assets/`, `docs/`, `tools/`, `controller/`(구 `src/`) 하위로 기능별 분리
  - 빌드 및 실행 도구(`tools/`), 마크다운 상세 문서(`docs/`) 경로 표준화 및 README 내 안내 경로 갱신

### [V3.0.0] - 2026-09-14
- **전용 브랜드 로고 도입**: 엔드밀 공구·클라우드 회로 루프·스파크를 형상화한 심볼을
  JPG / 멀티해상도 ICO / 투명 PNG / 카드형 PNG 4종으로 제작해 `assets/` 한 곳에 보관.
  컨트롤러 헤더·앱 아이콘, 대시보드 사이드바·파비콘이 모두 같은 파일을 씁니다
- **대시보드 홈 화면 신설**: 브랜드 헤더 + Quick Access 카드 8개 그리드
- **프로젝트 구조 정리**: 루트에 흩어져 있던 로고 중복본을 제거하고
  `assets/` · `docs/` · `tools/` · `controller/`(구 `src/`)로 분리.
  루트에는 `system_controller.exe`와 README, 설정 파일만 남습니다

### [V2.3.5] - 2026-09-14
- **Workingstep 절삭조건 자동 파싱**: NC 코드에서 공구별 `Feed Rate`/`Spindle Speed`를 추출해
  `workingstep`에 반영. 컴팩트 표기(`S3800M3`, `G1Z-2F710`)와 공백 분리형을 모두 지원
- **Job 폴더 구조 개편**: 원본을 `XML/`·`Log/`·`TDMS/`·`NC/` 하위 폴더로 분리.
  규칙은 `backend/job_layout.py` 단일 출처이며, Job 루트에 떨어진 파일은 자동으로 옮겨 처리
- **제조 DB Controller 신설**: C++ Win32 네이티브 GUI로 모듈 제어·실시간 로그·파서 토글 제공.
  Google Cloud 콘솔풍 라이트 테마, 브랜드 로고, 파이썬 인터프리터 자동 탐색(동봉 런타임 지원)
- **전체 복구 ZIP 누락 수정**: 전체 복구가 단일 Job 복원과 다른 코드를 써서 TDMS·Parquet·etc·CAD가
  빠져 있던 문제를 공용 함수로 통합해 해결. 복원이 멱등해져 반복 실행해도 파일이 중복되지 않음
- **데이터 정합성 보강**: Workingstep 중복 생성(세션 flush 누락), CNC 로그가 아닌 `.log`가 통계를
  0으로 덮어쓰던 문제, 해시 미확보로 생기던 고아 Workplan, 사용자 입력의 SQL 직접 보간 수정
- **정리**: 중복·미사용 모듈(`reset_db.py`, `restore_utility.py`) 삭제, 경로 기준을
  `vault_manager.PROJECT_ROOT` 단일 출처로 통합

### [V2.3.4] - 2026-09-11
- NC 코드와 `CNC-CurrentBlock`을 대조해 **실가공 구간만 자동 판별** (`backend/tdms_alignment.py` 신규)
- CNC(27Hz)와 DAQ(12.8kHz)를 **공통 경과시간 축**으로 통일하고, 상호상관으로 시계 지연 실측·보정
- 손상된 TDMS(비정상 종료로 꼬리가 깨진 파일)도 유효 지점까지만 읽어 처리
- 표면조도 평가곡선 인식을 파일명 → **내용 기반**으로 변경해 이름이 달라도 적재
- 3D 형상 뷰어를 CAD 표준 **Z-up**으로 정렬하고 축 gizmo 추가

### [V2.3.3] - 2026-09-10
- 공구 마스터의 단일 출처를 **엑셀로 고정**. `xml_parser`는 공구 행을 만들 수 없도록 차단
- 업로드 방식을 업서트 → **전체 교체**로 변경하고, 유효 공구 0건이면 취소해 사고 방지
- 공구 코드 형식 검증 및 표준 표기(`T01`~`T99`) 정규화, `tool_id` 연속 재부여, 원본 엑셀 보관
  (표기는 보여주기용이고 공구 연결은 번호로 한다 — NC 는 `T6` 으로 적기 때문. `backend/tool_codes.py`)

### [V2.3.2] - 2026-09-09
- **DB 키 정규화**: 사람이 읽는 이름을 PK로 쓰던 구조를 숫자 대리키로 전환.
  `part_name` 분리, `UNIQUE(part_code, program_code, nc_hash)` 제약으로 중복 차단 규칙 이관
- 데이터 보존 마이그레이션 도구 신설 (`backend/migrate_keys_v3.py`, `--check` / `--run`)

### [V2.3.1] - 2026-09-08
- 관리자/사용자 대시보드를 **단일 대시보드**로 통합 (Port 8501)
- 백그라운드 무결성 검증 도입, 다운로드 센터 신설

### [V2.0.x ~ V2.2.0] - 2026-08-31 ~ 09-07
- ISO 14649 기반 스키마 확립, Watchdog 수집 파이프라인, Vault + LONGBLOB 이중화
- TDMS Parquet 변환, 표면조도 파서, 3D CAD 뷰어, 재난 복구 ZIP

---


## 👥 기여 및 문의 (Contact)
- **개발 및 관리**: 공작기계지능화실험실 (Machine Tool Intelligence Lab)
- **GitHub**: [@OrcusExtreme](https://github.com/OrcusExtreme)
- **Issues**: 버그 리포트 및 기능 제안은 [GitHub Issues](https://github.com/OrcusExtreme/ManufacturingDB/issues)를 이용해 주세요.
