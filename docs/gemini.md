# 공작기계지능화실험실 제조 DB — 시스템 명세서

> **이 문서의 목적**
> 소스 코드가 없는 상태에서도 이 시스템을 다시 만들 수 있도록, 무엇을 왜 그렇게 만들었는지까지
> 기록한 단일 사양서입니다. 코드를 그대로 옮겨 적는 대신 **동작 규칙·자료 구조·판단 근거**를
> 서술합니다. 실제 코드와 어긋나는 부분이 보이면 코드가 정답이며, 이 문서를 고쳐 주세요.
>
> **기준 버전** V2.3.5 · **대상 독자** 이 시스템을 인수인계받거나 재구축하는 개발자

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [환경 및 설치](#2-환경-및-설치)
3. [파일 레이아웃 규칙](#3-파일-레이아웃-규칙)
4. [데이터베이스 명세](#4-데이터베이스-명세)
5. [수집 파이프라인](#5-수집-파이프라인)
6. [파서 상세](#6-파서-상세)
7. [TDMS 정렬 엔진](#7-tdms-정렬-엔진)
8. [보관소와 무결성](#8-보관소와-무결성)
9. [복구 엔진](#9-복구-엔진)
10. [제조 DB Controller (C++)](#10-제조-db-controller-c)
11. [웹 대시보드](#11-웹-대시보드)
12. [엔드투엔드 흐름](#12-엔드투엔드-흐름)
13. [운영 가이드](#13-운영-가이드)
14. [설계 판단과 알려진 제약](#14-설계-판단과-알려진-제약)
15. [배포용 설치 파일](#15-배포용-설치-파일)

---

## 1. 시스템 개요

### 1.1 무엇을 하는 시스템인가

CNC 공작기계 한 번의 가공에서 쏟아지는 **서로 다른 여섯 종류의 데이터**를 사람이 손대지 않고
한 데이터베이스로 모으고, 웹에서 함께 들여다볼 수 있게 하는 시스템입니다.

| 자료 | 형식 | 규모 | 나오는 곳 |
| :--- | :--- | :--- | :--- |
| 가공 메타데이터 | XML | ~8KB | 장비 제어 PC |
| NC 프로그램 | G코드 텍스트 | ~1KB | CAM / 작업자 |
| 고주파 센서 | NI TDMS | 수백 MB | DAQ (12.8kHz) |
| CNC 상태 로그 | 탭 구분 텍스트 | ~200KB | 컨트롤러 (1Hz) |
| 표면 조도 | CSV | ~30KB × N | 조도 측정기 |
| 3D 도면 | STEP / STL | ~30KB~15MB | CAD |

### 1.2 세 개의 축

이 시스템을 이해하는 가장 빠른 길은 아래 세 축이 **왜 분리되어 있는지**를 보는 것입니다.

**① 정적 계획과 동적 실행의 분리 (ISO 14649 / STEP-NC)**

같은 NC 프로그램으로 부품을 열 번 깎으면, *계획*은 하나이고 *실행 이력*이 열 개입니다.
계획을 열 번 복사해 저장하면 "이 프로그램으로 만든 결과들을 비교"하는 질문에 답할 수 없습니다.

```
Part (부품)
 └─ Workplan (NC 프로그램 단위의 가공 계획)       ← 정적, 1개
     ├─ Workingstep (공구 호출 순서 · 절삭조건)    ← 정적
     └─ Job (1회 가공 실행 이력)                   ← 동적, N개
         ├─ MachineLog / Inspection / EnvMemo      (각 1:1)
         └─ SurfaceRoughness                        (1:N)
```

**② 저장 계층의 분리**

한 저장소에 다 넣으면 어느 한쪽이 반드시 불행해집니다.

| 계층 | 담는 것 | 이유 |
| :--- | :--- | :--- |
| MySQL | 스칼라 값, 관계, 요약 통계 | 인덱스 기반 조건 검색·조인·정렬 |
| Parquet | 시계열·주파수·조도 곡선 | 수백만 행을 컬럼 압축으로 즉시 그림 |
| File Vault | 원본 파일 사본 | 원본을 절대 손실하지 않기 위한 불변 보관 |
| LONGBLOB | 15MB 이하 원본 바이너리 | **DB 덤프 하나만으로** 파일 트리까지 복원 |

**③ 실행 주체의 분리**

```
[제조 DB Controller (system_controller.exe)]   ← 사람이 켜고 끄는 유일한 창
        │  프로세스 감독 + 실시간 로그 + 파서 토글
        ├─ Watchdog 수집기 (data_insert_recognization.py)
        ├─ TDMS 변환 데몬 (tdms_visualizer.py)
        └─ Streamlit 대시보드 (frontend/dashboard.py, :8501)
```

컨트롤러는 파이썬 코드를 품지 않고 **디스크의 `.py` 를 그대로 실행**합니다.
따라서 파이썬을 고쳐도 컨트롤러를 다시 빌드할 필요가 없습니다.

### 1.3 전체 흐름

```text
[장비 / 계측 PC]
      │ 폴더째 복사
      ▼
data/machining_raw_data/{프로젝트}/{부품}/{가공차수}/
      │ Watchdog 감지 → 확장자로 하위 폴더 자동 정리 → 큐 적재
      ▼
[파서 6종] xml · nc · tdms · log · roughness · cad
      │
      ├─▶ MySQL (정규화 적재)
      ├─▶ {보관소 루트}/archive_vault (원본 사본 · 프로젝트 밖 가능)
      └─▶ LONGBLOB (15MB 이하 원본)
      │
      ▼
[TDMS 변환 데몬] NC 대조로 실가공 구간만 잘라 Parquet 생성
      │
      ▼
[Streamlit 대시보드 :8501]  검색 · 분석 · 편집 · 다운로드 · 복구
```

---

## 2. 환경 및 설치

### 2.1 요구사항

| 항목 | 버전 | 비고 |
| :--- | :--- | :--- |
| Python | 3.10+ | 3.13/3.14에서 동작 확인 |
| MySQL | 8.0+ | 기본 DB 이름 `orcus` |
| OS | Windows 10/11 | 컨트롤러가 Win32 API 사용 |
| g++ | MinGW-w64 / w64devkit | 컨트롤러를 **고칠 때만** 필요 |

> 다른 PC 에 **설치만** 할 때는 [15. 배포용 설치 파일](#15-배포용-설치-파일)을 씁니다. 파이썬 · MySQL 을
> 따로 설치하지 않아도 되고 `.env` 도 설치 프로그램이 만듭니다. 이 절의 내용은 개발 PC 기준입니다.

### 2.2 `.env`

프로젝트 루트에 둡니다. `backend/DB/database.py` 가 `python-dotenv` 로 읽습니다.

```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=orcus
PYTHONPATH=backend
```

### 2.3 패키지 (`requirements.txt`)

```
pandas>=2.2.0          numpy>=1.26.0        openpyxl==3.1.5
SQLAlchemy==2.0.51     PyMySQL==1.2.0       cryptography>=42.0.0
python-dotenv==1.2.2   watchdog==6.0.0      streamlit==1.61.1
streamlit-option-menu>=0.4.0                streamlit-cookies-controller>=0.0.4
plotly>=5.20.0         pyarrow>=14.0.1      scipy>=1.12.0
nptdms>=1.9.0          psutil>=6.0.0        fastapi>=0.110.0     uvicorn>=0.28.0
```

- **`cryptography` 는 빠지면 안 됩니다.** MySQL 8 의 기본 인증(`caching_sha2_password`)은 서버가 재시작된 뒤
  첫 접속에서 비밀번호를 RSA 로 암호화해 보내야 하고, PyMySQL 은 그 계산을 `cryptography` 에 맡깁니다.
  없으면 PC 를 재부팅한 직후 `'cryptography' package is required ...` 로 대시보드·수집기가 DB 에 붙지 못합니다
  (다른 프로그램이 먼저 접속해 서버 쪽 인증 캐시가 차 있으면 우연히 지나가서 개발 PC 에서는 잘 드러나지 않았습니다).
- 이 파일은 사람이 `pip install` 할 때 쓰는 목록입니다. 배포용 설치 파일의 동봉 런타임은
  `installer/requirements-runtime.txt` 에 판을 고정해 따로 두므로, **패키지를 더하면 두 곳 모두** 고칩니다.
- 둘 다 ASCII 로만 씁니다. pip 은 requirements 파일을 시스템 코드페이지(한국어 Windows 는 cp949)로 읽습니다.

### 2.4 DB 연결 (`backend/DB/database.py`)

```python
SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{USER}:{PW}@{HOST}:{PORT}/{NAME}"
engine = create_engine(URL, echo=False, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

> **`autoflush=False` 는 중요한 함정입니다.** 같은 세션에서 `db.add()` 한 객체는
> `db.flush()` 를 부르기 전까지 `db.query()` 에 **잡히지 않습니다**. 이 때문에 실제로
> Workingstep 이 두 배로 생성되는 버그가 있었습니다([6.2](#62-nc-파서-parsersnc_parserpy) 참고).
> 같은 트랜잭션 안에서 방금 추가한 행을 조회해야 한다면 **반드시 먼저 flush** 하세요.

### 2.5 스키마 생성과 보정

- **생성** — `backend/reset_database_and_storage.py` 가 `Base.metadata.create_all()` 로 `models.py` 의
  테이블 중 **없는 것만** 만듭니다(가공 데이터 DB 초기화는 지운 테이블을 이것으로 다시 만들고, 남긴
  공구 · 계정 테이블은 그대로 둡니다). 이 파일의 `from DB import models` 는 이름을 직접 쓰지 않지만
  **지우면 안 됩니다.** 이 import 가 있어야 모델이 `Base.metadata` 에 등록됩니다.
- **기동 시 보정** — `backend/DB/schema_patch.py` 의 `ensure_schema()` 가 `PENDING_COLUMNS` 목록을
  보고 **빠진 컬럼만** `ALTER TABLE ADD COLUMN` 합니다. 몇 번을 실행해도 안전하며,
  수집 파이프라인이 시작할 때 자동으로 호출됩니다.

---

## 3. 파일 레이아웃 규칙

### 3.1 감시 폴더 구조

`backend/job_layout.py` 가 **폴더 이름과 분류 규칙의 단일 출처**입니다.
수집·복원·업로드·다운로드가 모두 이 모듈을 참조하므로, 폴더 이름을 바꾸려면 여기만 고치면 됩니다.

```text
data/machining_raw_data/{프로젝트}/{부품}/
  ├─ CAD_Files/                  ← Part 레벨 (도면은 가공차수와 무관)
  └─ {가공차수}/                  ← Job 1건
       ├─ XML/                   장비 메타데이터
       ├─ Log/                   CNC 1Hz 상태 로그 (.log / .csv)
       ├─ TDMS/                  고주파 센서 원본
       ├─ NC/                    NC 프로그램
       ├─ Surface_Roughness/     조도 측정 결과
       ├─ etc/                   참고용 기타 파일
       └─ processed_parquet/     변환 산출물 (시스템이 생성)
```

`{가공차수}` 는 보통 DB의 `job_id` 와 같은 숫자입니다. 숫자가 아니어도 동작하지만,
같은 값으로 맞춰두면 폴더만 보고 Job 을 찾을 수 있습니다.

### 3.2 확장자 → 폴더 매핑

| 확장자 | 폴더 |
| :--- | :--- |
| `.xml` | `XML` |
| `.log`, `.csv` | `Log` |
| `.tdms`, `.tdms_index` | `TDMS` |
| `.nc` | `NC` |

> `.csv` 는 `Surface_Roughness/` **안에 있으면** 조도 데이터, Job 폴더 루트에 떨어지면 CNC 로그로
> 봅니다. 즉 자료 종류는 **어느 폴더에 있느냐**가 1차 기준이고 확장자는 2차 확인입니다.

### 3.3 자동 정리

장비가 예전 방식대로 Job 폴더에 파일을 한꺼번에 쏟아내도 됩니다.
수집기가 Job 폴더 **바로 아래**에서 파일을 발견하면 `job_layout.relocate_into_subdir()` 로
확장자에 맞는 하위 폴더를 만들어 옮긴 뒤 처리합니다.

```
[정리] DNM200__....xml  -> XML/ 로 이동
[정리] DNM200__....log  -> Log/ 로 이동
[정리] DNM200__....tdms -> TDMS/ 로 이동
[정리] O0911.nc         -> NC/ 로 이동
```

TDMS 를 옮길 때는 nptdms 가 만드는 `*.tdms_index` 사이드카도 함께 옮겨 짝을 맞춥니다.

### 3.4 읽을 때의 규칙

`job_layout.find_files(job_dir, kind, extensions)` 는 **하위 폴더를 먼저 보고, 없으면 Job 루트도**
봅니다. 예전 구조로 남아 있는 폴더도 그대로 읽히게 하기 위한 폴백입니다.

### 3.5 기존 데이터 이전

`backend/migrate_job_folder_layout.py` 가 Job 루트에 흩어진 파일을 하위 폴더로 옮기고,
DB에 저장된 경로 참조(각 아카이브 테이블의 `*_raw_path`)까지 함께 고칩니다.
보관소 기준 경로(`*_vault_path`)는 Vault 안에서 움직이지 않으므로 그대로 둡니다.

```bash
python backend/migrate_job_folder_layout.py          # 무엇을 옮길지 확인만
python backend/migrate_job_folder_layout.py --apply  # 실제 이동 + DB 갱신
```

> 수집 파이프라인을 **멈춘 상태에서** 실행하세요. 켜져 있으면 폴더 삭제를 유실로 보고
> 자동 복원을 걸 수 있습니다.

---

## 4. 데이터베이스 명세

### 4.1 관계도

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

### 4.2 키 설계 원칙

**사람이 읽는 이름을 기본키로 쓰지 않습니다.** V2.3.2 이전에는 `part.part_code` 가 부품 이름
문자열이었고 `workplan_id` 는 `"부품명_프로그램_해시"` 였습니다. 그 결과 이름을 바꾸면 자식
테이블 전체에 전파되고, 한글·공백이 그대로 키에 들어갔습니다.

지금은 모든 PK가 **의미 없는 숫자 대리키**이고, 사람이 읽는 값은 속성으로 분리되어 있습니다.
중복 차단은 유일 제약으로 옮겼습니다.

```
part.part_code      INT AUTO_INCREMENT   ← 이름은 part_name 속성
workplan.workplan_id INT AUTO_INCREMENT  ← UNIQUE(part_code, program_code, nc_hash)
```

### 4.3 테이블 상세

#### `part` — 가공 대상 부품

| 컬럼 | 타입 | 설명 |
| :--- | :--- | :--- |
| `part_code` **PK** | INT AI | 부품 고유 번호 |
| `part_name` | VARCHAR(100) NOT NULL UNIQUE | 부품 이름 (XML `PartCode` / 폴더명) |
| `project_code` | VARCHAR(50) | XML `ProjectCode` |
| `material_code` | VARCHAR(50) | XML `MaterialCode` |

#### `workplan` — NC 프로그램 단위 가공 계획

| 컬럼 | 타입 | 설명 |
| :--- | :--- | :--- |
| `workplan_id` **PK** | INT AI | |
| `part_code` **FK** | INT | → `part`, ON DELETE CASCADE |
| `program_code` | VARCHAR(50) | XML `ProgramCode` / `ProgramName` |
| `nc_hash` | VARCHAR(32) | NC 내용 MD5 앞 8자리 |

NC 파일 경로는 `workplan_file_archive` 로 옮겼습니다. `nc_hash` 는 경로가 아니라
아래 유일 제약을 이루는 **신원 값**이라 여기 남습니다.

제약: `UNIQUE(part_code, program_code, nc_hash)`

> `nc_hash` 는 **같은 이름의 다른 NC** 를 구분하는 장치입니다. NC 파일이 XML 보다 늦게 도착하면
> 해시를 모르는 상태라 `'NOHASH'` 로 들어가고, 나중에 NC 를 읽을 때 `nc_parser` 가 채웁니다.
> 채우지 않으면 같은 부품에 Workplan 이 두 개 생기고 앞의 것이 고아가 됩니다.

#### `workingstep` — 공구 호출 단위 공정

| 컬럼 | 타입 | 설명 |
| :--- | :--- | :--- |
| `step_id` **PK** | INT AI | |
| `workplan_id` **FK** | INT | → `workplan`, CASCADE |
| `tool_id` **FK** | INT | → `tool`, ON DELETE SET NULL |
| `operation_type` | VARCHAR(50) | 가공 방식 |
| `step_order` | INT NOT NULL | 공구 호출 순서 |
| `tool_number` | INT NOT NULL | 호출된 공구 번호 |
| `xml_tool_code` | VARCHAR(50) | 표준 표기 공구 코드 (`T06`). 매칭은 `tool_number` 로 한다 |
| `feed_rate` | FLOAT | 이송속도 mm/min (NC 파싱) |
| `spindle_speed` | FLOAT | 주축 회전수 RPM (NC 파싱) |

#### `tool` — 공구 마스터

ISO 14649 기반 공구 마스터입니다. **매거진에 장착된 공구와 미장착 재고를 함께** 담습니다.
DDL 은 `docs/tool_iso14649_schema.sql`, 데이터는 `docs/tool_iso14649.csv`(장착 18 + 미장착 32)이며
`backend/tests/verify_tool_csv.py` 가 CSV · `models.Tool` · 실제 테이블을 서로 대조합니다.

| 컬럼 | 타입 | 설명 |
| :--- | :--- | :--- |
| `tool_id` **PK** | INT AI | 1부터 빈 번호 없이 (교체할 때마다 다시 매김) |
| `tool_code` | VARCHAR(20) NULL **UNIQUE** | 매거진 포트 T번호 `T01`~`T99`. **미장착 재고는 NULL** |
| `manufacturer` / `model_code` | VARCHAR(50) / (100) | 제조사 / 카탈로그 형번 |
| `iso14649_class` | VARCHAR(50) | ISO 14649-111:2010 엔티티 대문자 (`ENDMILL`, `BALLNOSE_ENDMILL`, `TWIST_DRILL` …) |
| `hand_of_cut` | VARCHAR(10) DEFAULT 'RIGHT' | `RIGHT` / `LEFT` / `NEUTRAL` |
| `cutter_diameter` · `shank_diameter` | DECIMAL(6,3) | 직경 Dc · 생크 Ds |
| `cutting_edge_length` · `overall_length` · `usable_length` | DECIMAL(6,2) | 날장 · 전장 · 유효장 |
| `corner_radius` | DECIMAL(5,3) DEFAULT 0 | 코너 R / 볼 반경 |
| `point_angle` · `approach_angle` · `helix_angle` | DECIMAL | 선단각 · 진입각 · 비틀림각 |
| `number_of_teeth` | INT DEFAULT 1 | 날 수 (프로브·마스터 툴은 0) |
| `tool_material` · `coating_type` | VARCHAR | 재질 · 코팅 |
| `coolant_through` | BOOLEAN DEFAULT FALSE | 내부 급유 |
| `stock_count` · `gauge_length` · `memo` | INT · DECIMAL(6,3) · TEXT | 재고 · 게이지 길이 · 비고 |
| `created_at` · `updated_at` | DATETIME | 등록 · 수정 시각 (DB 가 채움) |

**CHECK `chk_tool_mounted_complete`** — 장착 공구(`tool_code` 있음)는 `iso14649_class` ·
`cutter_diameter` · `number_of_teeth` 가 반드시 있어야 합니다. NC/XML 이 T번호로 부르는 공구라서입니다.
미장착 재고에는 실험실 시트에 '?' 로만 적힌 행이나 인서트처럼 그 셋이 없는 것이 있어 강제하지
않습니다. 파서(`nc_parser` · `xml_parser`)는 `WHERE tool_code IS NOT NULL` 로 공구를 찾으므로
미장착 재고는 T번호 매칭에 끼어들지 않습니다.

**빈 칸은 NULL 입니다.** 입력기는 빈 칸을 DEFAULT 로 채우지 않습니다. 모르는 코너 R 이 0, 모르는
날 수가 1 로 저장되면 거짓이 되기 때문입니다(DEFAULT 는 손으로 행을 넣을 때만 쓰입니다).

> 이 테이블을 **통째로 교체하는 경로는 `tool_inserter` 하나뿐**입니다(대시보드 업로드 · 수집기 ·
> 마이그레이션이 모두 그 코드를 씁니다). `xml_parser` 는 `Tool` 모델을 import 조차 하지 않아,
> 실수로 공구 행을 만들 수 없습니다. DB 테이블 화면의 행 단위 편집은 CHECK 가 지킵니다.

#### `job` — 1회 가공 실행 이력

| 컬럼 | 타입 | 설명 |
| :--- | :--- | :--- |
| `job_id` **PK** | INT AI | |
| `source_folder` | VARCHAR(255) UNIQUE | `{프로젝트}/{부품}/{차수}` |
| `workplan_id` **FK** | INT NOT NULL | → `workplan`, CASCADE |
| `work_id` | VARCHAR(50) NOT NULL | XML `WorkID` |
| `machine_code` / `machine_ip` | VARCHAR | 장비 식별 |
| `start_time` / `end_time` | DATETIME | 가공 시작·종료 |
| `cutting_seconds` | DOUBLE | 실절삭 시간 |
| `moving_distance` / `cutting_moving_distance` | DOUBLE | 총·절삭 이동거리 |
| `is_finish` / `is_error` | BOOLEAN | 완주 / 에러 |
| `machining_type` | VARCHAR(100) | 가공 종류 (수기 입력) |
| `machining_window` | JSON | 실가공 구간 판별 결과 ([7장](#7-tdms-정렬-엔진)) |
| `tool_conditions` | JSON | 런타임 공구 상태 (사용횟수·오프셋) |

#### `machine_log` — CNC 로그 요약 (Job과 1:1)

`log_id` PK, `job_id` FK **UNIQUE**, `max_spindle_load`, `max_spindle_rpm`,
`max_feed_rate`, `avg_spindle_rpm`, `alarm_count`, `critical_alarm_msg`

#### `surface_roughness` — 조도 측정 (Job과 1:N)

`roughness_id` PK, `job_id` FK, `measure_name`, `ra`, `rq`, `rz`,
`profile_parquet_path`

#### `inspection` — 품질 점검 (Job과 1:1)

`inspection_id` PK, `job_id` FK UNIQUE, `dimension_tolerance`,
`surface_roughness_ra`, `surface_roughness_rz`, `pass_fail`

`surface_roughness_ra/rz` 는 **조도 CSV 에서 파서가 평균 내 채우는 측정값**입니다
(`roughness_parser`). 데이터 삽입 화면에서 값을 적으면 그 값이 우선하고, 비워 두면
측정값이 유지됩니다.

`shape_accuracy`(형상정밀도)는 쓰지 않기로 하여 제거했습니다
(`backend/migrate_auth_and_inspection.py`).

#### 공구 코드 표기와 매칭 (`backend/tool_codes.py`)

저장 표기는 **두 자리 0 채움**입니다: `T1` → `T01`, `T6` → `T06`, `T29` → `T29`.
문자열로 정렬해도 번호 순과 일치하고(`T01 T02 T06 T11 T29`, 예전에는 `T1 T11 T2 T29 T6`),
화면·엑셀·NC 어디에 나와도 폭이 같습니다.

**매칭은 표기가 아니라 번호로 합니다.** NC 코드는 `M6T6` 처럼 0 을 채우지 않고 적으므로,
표기를 그대로 비교하면 마스터의 `T06` 과 연결되지 않습니다. 예전에는 세 곳에서
`f"T{번호}"` 로 문자열을 만들어 `tool.tool_code` 와 비교했는데(`tool_inserter` 의 relink SQL,
`nc_parser`, `xml_parser`), 그 방식은 양쪽 표기가 한 글자라도 다르면 **조용히 연결이 끊깁니다**
— `tool_id` 가 NULL 로 남고 화면에서는 공구가 '미연결' 로 보입니다.

공구를 가리키는 진짜 값은 정수 `workingstep.tool_number` 입니다. 표기는 사람에게 보여줄
때만 쓰고, 연결은 언제나 번호로 합니다. 덕분에 옛 표기(`T6`)가 남아 있는 DB 에서도
그대로 동작합니다.

| 함수 | 쓰는 곳 | 엄격도 |
| :--- | :--- | :--- |
| `canonical_tool_code(v)` | 저장·표시 표기 만들기 (`T6` → `T06`) | — |
| `tool_code_number(v)` | "이 칸이 마스터의 정식 코드인가" | 엄격 — `T19-TIP` 은 None |
| `tool_number_from_label(v)` | "이 이름표가 어느 공구를 가리키는가" | 느슨 — `T6_10` → 6 |
| `index_by_number(pairs)` | 마스터를 번호 기준으로 색인 | — |

두 엄격도가 나뉘어 있는 이유가 있습니다. 공구 마스터(엑셀)에서는 엄격해야 합니다 —
`T19-TIP` 같은 변형 행을 받아들이면 공구 19 와 별개인 가짜 공구가 생깁니다. 반대로 '어느
공구를 가리키는가' 를 물을 때는 느슨해야 합니다. 실측 XML 의 `<Tools>` 는 `toolCode` 를
**`T6_10`** 으로 적는데, 예전에는 그 문자열을 키로 담아 두고 `'T6'` 으로 찾아서 XML 의 공구
제원(`toolUsedCount`·오프셋 9종)이 **한 번도 붙지 않았습니다**. 이름표에서 번호만 뽑아
색인하도록 바꿔 해결했습니다.

#### `env_memo` — 작업 환경 (Job과 1:1)

`env_id` PK, `job_id` FK UNIQUE, `worker_name`, `temperature`, `humidity`,
`day_of_week`, `chip_shape`, `abnormal_noise`, `free_memo`

**Job 이 생기면 빈 행도 같이 생깁니다.** 작업자·온습도처럼 사람이 나중에 적는 칸이라 예전에는
행 자체를 만들지 않았고, 화면에서 처음 저장할 때야 생겼습니다. 그러면 "Job 은 있는데 env_memo 는
없는" 상태가 정상이 되어 1:1 이라는 약속이 데이터에서는 지켜지지 않았습니다. 지금은
`job_manager.ensure_env_memo()` 가 Job 생성 경로에서 빈 행을 만들고, Job 삭제 시 cascade 로
함께 사라집니다.

**`day_of_week` 는 자동으로 채워집니다** (영어: `Monday` ~ `Sunday`).
`job.start_time` 에서 계산되는 파생값이라 사람이 입력하지 않습니다 — 화면의 입력칸도 읽기
전용입니다.

계산은 `DB/models.py` 의 `english_day_of_week()` 가 하고, **고정 표를 `weekday()` 로
색인**합니다. `strftime('%A')` 나 `calendar.day_name` 은 쓰지 않습니다 — 둘 다 로케일을 따라서
한국어 Windows 에서는 `'금요일'` 이 나오고(실측), 장비 PC 의 지역 설정에 따라 같은 컬럼에
영어와 한국어가 섞입니다. `weekday()` 는 로케일과 무관합니다.

채우는 자리는 **한 곳**입니다. `job.start_time` 을 쓰는 코드가 8군데(xml/log/tdms 파서, 복구
backfill, tdms_visualizer)라 그 자리마다 요일 계산을 끼워 넣으면 언젠가 한 곳이 빠집니다.
그래서 어디서 채우든 반드시 지나가는 **세션 flush 직전**에 한 번만 맞춥니다
(`models.py` 의 `_sync_day_of_week`, `before_flush` 이벤트). `start_time` 이 바뀌면 요일도
따라 바뀝니다.

이전에 쌓인 행은 `python backend/backfill_day_of_week.py --apply` 로 정리합니다
(미리보기 기본. 파생값이므로 기존 값이 있어도 날짜 기준으로 덮어씁니다).

#### 아카이브 4종

| 테이블 | 키 | 내용 |
| :--- | :--- | :--- |
| `workplan_file_archive` | `workplan_id` PK/FK | `nc_raw_path` + LONGBLOB + SHA-256 |
| `job_file_archive` | `job_id` PK/FK | LONGBLOB + SHA-256, `tdms_raw_path`, `tdms_parquet_raw_path`, `tdms_fft_parquet_raw_path` |
| `cad_file_archive` | `cad_id` PK, `part_code` FK | 파일명/확장자 + LONGBLOB + SHA-256 |
| `surface_roughness_archive` | `roughness_id` PK/FK | `profile_parquet_raw_path` |
| `log_file_archive` | `log_id` PK/FK | `log_raw_path` |

#### 파일 경로는 전부 아카이브 테이블에 둔다

예전에는 같은 파일을 가리키는 칸이 주 테이블과 아카이브 테이블에 나뉘어 있었습니다.
이름만 보면 중복 같지만 담긴 값은 서로 달랐습니다.

```
job.tdms_parquet_path            Alchemist/DB_test/1/processed_parquet/job_1_viz.parquet
job_file_archive.tdms_parquet_*  processed_parquet/job_1_viz.parquet
```

앞은 `RAW_DATA_ROOT` 기준(장비가 떨군 원본이 놓인 자리), 뒤는 `VAULT_ROOT` 기준(보관소
사본)이었습니다. 지금은 **원본 경로만 아카이브 테이블에 남깁니다.**

| 접미사 | 기준 | 뜻 | DB 저장 |
| :--- | :--- | :--- | :--- |
| `*_raw_path` | `RAW_DATA_ROOT` | 장비가 원본을 떨군 자리 — **사실** | 저장함 |
| (보관소 경로) | `VAULT_ROOT` | 원본 상대경로와 **같음** | 저장하지 않고 계산 |

#### 보관소는 원본 트리를 그대로 비춘다

규칙은 한 줄입니다 — **보관소 상대경로 = `machining_raw_data` 상대경로.**

```
machining_raw_data/Alchemist/DB_test/1/TDMS/foo.tdms
archive_vault/     Alchemist/DB_test/1/TDMS/foo.tdms
```

```
{보관소 루트}/
  {Project}/{Part}/{JobID}/XML · NC · TDMS · Log · Surface_Roughness
                          /processed_parquet · etc
  {Project}/{Part}/CAD_Files/
  공구 마스터.csv
```

**왜 이렇게 바꿨나.** 예전에는 보관소가 파일을 합성한 이름과 id 기반 폴더에 담았습니다.

```
jobs/{source_folder}/metadata.xml     <- 원래 파일명을 버림
workplan_nc/WP{workplan_id}.nc        <- 원래 파일명을 버림
tdms_files/Job_{job_id}/{파일명}       <- job_id 에 의존
```

`job_id`·`workplan_id` 는 **대리키(surrogate key)** 라 재구축·수집순서·실패에 따라 값이
달라집니다. 실제로 auto-increment 가 한 번 밀리자 보관소의 `Job_1` 이 job2 로 복원됐고,
이름을 잃은 `WP2.nc` 가 다시 `program_code` 로 읽히며 신원이 또 어긋났습니다. 복원이
왕복하지 않았습니다.

원본 경로를 그대로 쓰면 경로에 id 가 전혀 없으므로 id 가 어떻게 바뀌든 보관소는 멀쩡하고,
복원은 **폴더 복사 한 번**이 되어 파일명까지 원래대로 돌아옵니다.

```python
import vault_layout
vault_layout.find_job_xml(job)                  # {sf}/XML/*.xml
vault_layout.find_job_nc(job, raw_path)         # {sf}/NC/*.nc
vault_layout.find_job_tdms(job, raw_path)       # {sf}/TDMS/*.tdms
vault_layout.find_job_parquet(job, "viz")       # {sf}/processed_parquet/viz.parquet
vault_layout.find_part_cad(project, part, name) # {Project}/{Part}/CAD_Files/*
vault_layout.list_job_roughness(job)
```

**쓰기는 `vault_manager.archive_file(원본 절대경로)` 하나뿐입니다.** 예전에는 파서마다
폴더 이름을 문자열로 직접 들고 있어(`"tdms_files", f"Job_{id}"` 같은 식) 같은 규칙이 11곳에
복제돼 있었고, 읽는 쪽과 쓰는 쪽이 조용히 어긋났습니다. 이제 배치를 아는 쓰기 함수는 하나입니다.

다운로드·복원의 **원본 폴더 → 보관소 → LONGBLOB** 폴백은 그대로입니다.

읽고 쓸 때는 `backend/file_archive.py` 의 헬퍼를 씁니다.

```python
from file_archive import archive_path, set_job_paths
set_job_paths(db, job.job_id, tdms_raw_path=rel)   # 행이 없으면 만들어 준다
tdms = archive_path(job, "tdms_raw_path")          # job.file_archive 관계를 타고 읽는다
```

부품명·프로젝트명은 사정이 다릅니다. `job.custom_part_name` / `job.research_project` 는
`part.part_name` / `part.project_code` 와 **같은 사실을 두 곳에 적어 둔 진짜 중복**이라
제거했습니다. 조회는 `Job → Workplan → Part` 조인으로 한 곳에서만 읽습니다.

이전 DB 는 `backend/migrate_paths_to_archive.py` 로 옮깁니다(기본 미리보기, `--apply` 로 실행).
새 컬럼 추가 → 값 복사 → 검증 → 옛 컬럼 삭제 순서이고, 검증에 실패하면 삭제하지 않습니다.

LONGBLOB 컬럼은 `LargeBinary(length=(2**32)-1)` 로 선언되어 최대 4GB까지 담을 수 있지만,
파서는 **15MB를 넘으면 저장을 생략**합니다. 그래도 보관소에는 사본이 있고 그 자리는
`vault_layout` 으로 계산되므로 복원에는 지장이 없습니다.

### 4.4 연쇄 삭제

```
Part 삭제   → Workplan → Workingstep, Job → (MachineLog, Inspection, EnvMemo,
                                              SurfaceRoughness, JobFileArchive)
Job 삭제    → 위 자식들만. Workplan 은 남는다 (다른 Job 이 공유할 수 있으므로)
```

> Job 을 지워도 Workplan 은 남습니다. 그 Workplan 을 쓰는 Job 이 하나도 없어지면
> `clean_dummy_data.py` 가 기동 시 정리합니다([13.3](#133-정리-스크립트)).

---

## 5. 수집 파이프라인

### 5.1 구조 (`backend/data_insert_recognization.py`)

```
watchdog Observer (recursive)
   │ on_created / on_modified  → file_queue.put()
   │ on_deleted                → 유실 검사 후 자동 복원
   ▼
단일 워커 스레드 (_process_queue)
   │
   ▼
handle_file()
```

**큐 + 단일 워커**를 쓰는 이유: 파서가 같은 Job 행을 동시에 건드리면 경합이 생깁니다.
순서대로 하나씩 처리해 그 문제를 없앴습니다. 대신 무거운 파일 하나가 뒤를 막을 수 있어,
TDMS 변환은 워커가 아니라 **별도 데몬**이 맡습니다([7.6](#76-변환-데몬-tdms_visualizerpy)).

### 5.2 `handle_file()` 처리 순서

1. **이미 처리한 경로인가** — `processed_files`(LRU 10000) 에 있으면 종료
2. **경로가 아직 존재하는가** — 없으면 즉시 종료
   > 하위 폴더로 옮긴 뒤 도착하는 **뒤늦은 이벤트**를 걸러냅니다. 이 검사가 없으면
   > `wait_for_file_ready` 가 10초를 기다린 뒤에야 포기해서, 단일 워커가 그동안 멈춥니다.
   > (실측: 7회 발생 시 NC 처리가 70초 지연)
3. **공구 마스터 파일인가** — 이름에 `공구 마스터` · `tool_iso14649` · `실험실 공구 정리` 가 있는
   `.csv` / `.xlsx` 면 `tool_inserter.sync_from_watch_folder` 로 (일반 `.csv` 인 조도 데이터보다 먼저
   거른다). 옛 실험실 시트는 형식을 보고, 이미 반영된 파일 · 보관본보다 오래된 파일은 내용과 수정
   시각을 보고 반영하지 않는다 ([6.7](#67-공구-마스터-tool_inserterpy))
4. **지원 확장자인가** — `.step .stp .stl .txt .csv .fpk .xml .tdms .log .nc` 외에는 무시
5. **전송이 끝났는가** — `wait_for_file_ready()` 가 파일을 열어 보고 **크기가 변하지 않을 때까지**
   0.5초 간격 최대 20회 대기. NAS/SMB 복사 지연 중 파싱하는 사고를 막습니다.
6. **Job 루트 파일이면 하위 폴더로 이동** ([3.3](#33-자동-정리))
7. **경로에서 자료 종류 판정** → 파서 호출
8. **실패 시 DLQ** — 예외가 나면 파일을 `failed_data/{타임스탬프}_{원본명}` 으로 격리

### 5.3 파서 토글 반영

`backend/pipeline_control.py` 가 `backend/pipeline_config.json` 을 통해 컨트롤러와 상태를 공유합니다.

```json
{
  "enable_xml": true, "enable_nc": true, "enable_tdms": true,
  "enable_log": true, "enable_roughness": true, "enable_cad": true,
  "paused": false
}
```

`handle_file()` 은 파서를 부르기 **직전에** 확인합니다.

```
paused=true            → 모든 처리를 건너뛴다
enable_{종류}=false    → 그 종류만 건너뛴다
```

토글은 파일에 즉시 기록되고 수집기가 매번 읽으므로, **재시작 없이** 반영됩니다.

### 5.4 자동 복원

`on_deleted` 는 지워진 경로의 깊이로 범위를 판단해 백그라운드 스레드에서 복원합니다.

| 지워진 깊이 | 복원 범위 |
| :--- | :--- |
| `machining_raw_data` 자체 | 전체 Job |
| `{프로젝트}` | 그 프로젝트의 Job |
| `{프로젝트}/{부품}` | 그 부품의 Job |
| `{...}/CAD_Files` | 그 부품의 Job (CAD 복원용) |
| `{프로젝트}/{부품}/{차수}` | 해당 Job |

1.5초 디바운스 후, **폴더가 없거나 비었을 때만** 복원합니다. 복원한 파일은
`_mark_processed()` 로 표시해 다시 파싱되지 않게 합니다.

---

## 6. 파서 상세

### 6.1 XML 파서 (`parsers/xml_parser.py`)

가공 메타데이터를 읽어 **Part → Workplan → Workingstep → Job** 계층 전체를 만듭니다.

**처리 순서**

1. 루트 태그가 `WorkModel` 이 아니면 건너뜀
2. 같은 Job 폴더의 NC 파일을 `job_layout.find_files(job_dir, NC, ('.nc',))` 로 탐색
   (`backup`, `old` 가 이름에 든 파일은 제외) → MD5 앞 8자리를 `nc_hash` 로
3. **Part upsert** — 이름으로 찾고 없으면 생성.
   프로젝트·부품 이름은 XML 내부 값보다 **폴더 구조를 우선**합니다. XML 내부 값이 부정확한
   경우가 많아서입니다.
4. **Workplan upsert** — `(part_code, program_code, nc_hash)` 로 조회.
   없고 해시를 아는 경우, 같은 부품·프로그램의 `NOHASH` 행이 있으면 **그 행의 해시를 채워
   재사용**합니다(고아 Workplan 방지).
5. **Workingstep 생성** — `ToolNumbers/int` 목록을 순회. 새 Workplan 일 때만 생성합니다
   (정적 계획이므로 한 번만).
6. **NC 절삭조건 동기화** — `sync_workplan_nc_cutting_conditions()` 호출
7. **Job upsert** — `job_id` PK 또는 `source_folder` 로 조회.
   `source_folder` 는 **반드시 실제 디스크 폴더와 일치**해야 합니다. 어긋나면 같은 폴더의 다른
   파일이 Job 을 못 찾아 중복 Job 을 만들고, 다운로드·복구가 폴더를 "유실됨"으로 오판합니다.
8. **XML 원본 보관** — Vault(`jobs/{source_folder}/metadata.xml`) + LONGBLOB + SHA-256

**추출 필드**: `WorkID`, `MachineCode`, `MachineIpAddress`, `StartTime`, `FinishTime`,
`CuttingSeconds`, `MovingDistance`, `CuttingMovingDistance`, `IsFinish`, `IsError`,
`ToolNumbers`, 공구별 `toolUsedCount` 및 `offset0~8` → `job.tool_conditions` (JSON)

### 6.2 NC 파서 (`parsers/nc_parser.py`)

**`parse_nc_cutting_conditions(nc_input)`** — G코드에서 공구 호출 단위로 `(T, S, F)` 를 뽑습니다.
경로·바이트열·문자열을 모두 받으며, 인코딩은 `utf-8 → cp949 → euc-kr → latin1` 순으로 시도합니다.

파싱 규칙:

| 대상 | 정규식 | 비고 |
| :--- | :--- | :--- |
| 공구 교체 | `(?<![#A-Z])M0?6(?![0-9])` + `T(\d+)` | M6 + T 가 같은 줄이면 새 스텝 시작 |
| 주축 회전수 | `(?<![#A-Z])S(\d+(?:\.\d*)?)` | 마지막 값 채택 |
| 이송속도 | `(?<![#A-Z])F(\d+(?:\.\d*)?)` | 절삭(G1/G2/G3 또는 축 이동) 값 중 **최빈값** |

`(?<![#A-Z])` 는 `G43H6` 의 `H6`, `#100` 같은 변수를 공구 번호로 오인하지 않기 위한 것입니다.
주석 `( ... )`, `< ... >`, `; ...` 과 `%` 줄은 먼저 제거합니다.

`compact` 포맷(`S3800M3`, `G1Z-2F710`, `M6T6`)과 공백 구분 포맷을 모두 지원합니다.

**`sync_workplan_nc_cutting_conditions(db, workplan_id, ...)`**

```python
db.flush()   # ← 반드시 먼저. 이유는 아래
existing_steps = db.query(Workingstep).filter(...).all()
if existing_steps:   # 기존 스텝에 조건만 채운다 (공구번호 매칭 → 실패 시 step_order)
else:                # 스텝이 없으면 NC 기준으로 새로 만든다
```

> **`db.flush()` 가 없으면 스텝이 두 배로 생깁니다.** `xml_parser` 는 Workingstep 을
> `db.add()` 로 세션에 담아두기만 한 채 이 함수를 부릅니다. 세션이 `autoflush=False` 라
> flush 없이 조회하면 그 행들이 보이지 않아 "스텝이 없다"고 판단하고 같은 스텝을 또 만듭니다.
> 실측 재현: 스텝 2개가 있어야 할 곳에 **4개** 생성, `step_order` 가 `[1,1,2,2]`.

**`parse_nc(file_path, job_id_str)`** — NC 단독 업로드 처리.
Workplan 의 `nc_file_path` 갱신, `nc_hash` 가 `NOHASH` 면 채우기, Vault + LONGBLOB 보관,
절삭조건 동기화를 수행합니다.

### 6.3 TDMS 파서 (`parsers/tdms_parser.py`)

수백 MB 파일을 **메타데이터만** 열어 빠르게 처리합니다.

1. `open_tdms(path, metadata_only=True)` 로 헤더만 읽기
2. 파일명에서 프로그램명·타임스탬프 추출 → `start_time` 보완
3. `job.tdms_file_path` 갱신, Vault(`tdms_files/Job_{id}/`) 백업

> **Parquet 변환을 여기서 하지 않습니다.** 예전에는 인라인으로 변환했는데, TDMS 변환 데몬도
> 같은 Job 을 5초마다 집어가서 **같은 464MB 파일을 두 프로세스가 동시에 변환**했습니다
> (76초 → 110초로 악화, 게다가 한쪽은 절대경로·다른 쪽은 상대경로를 저장). 변환 주체를
> 데몬 하나로 모아 중복과 경로 불일치를 함께 없앴습니다.

### 6.4 로그 파서 (`parsers/log_parser.py`)

탭 구분 CNC 1Hz 로그에서 **요약 통계만** 뽑습니다(시계열 전체를 DB에 넣지 않습니다).

**형식 검증이 먼저입니다.**

```python
CNC_LOG_REQUIRED_COLUMNS  = {'time'}
CNC_LOG_SIGNATURE_COLUMNS = {'cls', 'crpm', 'cfr', 'ctime', 'cut'}
```

헤더에 `time` 이 있고 위 계측 열 중 하나 이상이 있어야 CNC 로그로 인정합니다.

> 이 검사가 없으면 **엉뚱한 `.log` 가 통계를 0으로 덮어씁니다.** 실제로 NI TDMS 라이브러리가
> `TDMS: ERROR: TDS Exception in Initialize...` 라는 UTF-16 오류 로그를 TDMS 옆에 남기는데,
> 이걸 CNC 로그로 받아들여 `job.log_file_path` 가 그 파일을 가리키고 `machine_log` 가
> 전부 `0.0` 이 된 사고가 있었습니다(max_spindle_load 192.0 → 0.0).

**집계 항목**

| 항목 | 산출 |
| :--- | :--- |
| `max_spindle_load` | `cls` 최대 |
| `max_spindle_rpm` / `avg_spindle_rpm` | `crpm` 최대 / 평균 |
| `max_feed_rate` | `cfr` 최대 |
| `alarm_count` / `critical_alarm_msg` | `alarm_msg` 중복 제거 집합 |
| `cutting_seconds` | `ctime` 최대, 없으면 절삭 행 수 × 0.1 |
| `moving_distance` | `cpx/cpy/cpz` 3D 궤적 적분 |
| `cutting_moving_distance` | 절삭 상태(`cut=1` 또는 rpm>100 & feed>0)만 적분 |
| `start_time` / `end_time` | 파일명 `__YYMMDDHHMMSS` → `time` 열 순으로 폴백 |

### 6.5 조도 파서 (`parsers/roughness_parser.py`)

**파일 판별을 이름이 아니라 내용으로 합니다.** 헤더에 `DATANUM:` 이 있고
`DATAAXIS:` / `XPITCH:` / `PROFILENO:` 중 하나가 있으면 평가곡선 파일입니다.

> 예전에는 `*평가곡선*.CSV` 패턴으로 찾아서, 측정 담당자나 장비 설정에 따라 이름이 달라진 파일
> (예: `260911_1.CSV`)은 업로드해도 조도 값이 하나도 들어오지 않았습니다.

**처리**

1. `Surface_Roughness/` 에서 평가곡선 CSV 를 이름순으로 수집
2. `DATANUM:` 뒤부터 Z 값을 읽어 평균선을 뺀 뒤(`z_centered = z − mean(z)`) 계산

   | 값 | 산출 |
   | :--- | :--- |
   | `Ra` | 편차 절대값의 평균 — `mean(|z_centered|)` |
   | `Rq` | 편차 제곱 평균의 제곱근 — `sqrt(mean(z_centered²))` |
   | `Rz` | 프로파일을 **5구간으로 나눠** 각 구간의 `max − min` 을 구한 뒤 평균 (ISO 4287) |
3. `(X, Z)` 2열 Parquet 생성 → `data/processed_data/` 와 Job 폴더 `processed_parquet/` 양쪽에 저장
4. `measure_name` 은 파일명(확장자 제외)
5. 모든 측정의 `Ra`/`Rz` 평균을 `inspection` 에 자동 기록(소수 3자리)

### 6.6 CAD 파서 (`parsers/cad_parser.py`)

`CAD_Files/` 의 `.step` / `.stp` / `.stl` 을 Part 에 연결합니다.
Vault(`cad_models/Part_{부품명}/`) 보관 + 15MB 이하면 LONGBLOB + SHA-256.
같은 파일명이 이미 등록되어 있으면 건너뜁니다.

### 6.7 공구 마스터 (`tool_inserter.py`)

공구 마스터를 바꾸는 **유일한 경로**이며, 업서트가 아니라 **전체 교체**입니다. 입력 형식은
`docs/tool_iso14649.csv` 와 같은 **ISO 14649 형식 CSV · XLSX** 입니다(첫 줄 = 테이블 열 이름).
대시보드 업로드, 수집기(감시 폴더의 `공구 마스터.csv`), 마이그레이션이 모두 이 코드를 씁니다.

1. **파일 전체를 먼저 검사**하고, 한 칸이라도 틀리면 DB 를 건드리지 않고 행·열 위치와 이유를 돌려준다.
   - 열 구성: 빠진 열 · 모르는 열 · 이름 없는 열의 값 (`tool_id` · `created_at` · `updated_at` 은 무시)
   - `tool_code`: **두 자리 0 채움 표준 표기**로 정규화(`t4` / `T 4` → `T04`). `T19-TIP` 같은 변형은
     오류. 번호가 같은 행이 둘이면(`T06` · `T6`) 오류. 빈 칸은 미장착 재고
   - `iso14649_class`: 정해 둔 이름만(ISO 14649-111 · -10 · -121 + `REFERENCE_TOOL`). 오타 방지
   - 숫자: DECIMAL 자릿수 · 0 이상 · 정수 칸, `coolant_through` 는 1/0/TRUE/FALSE
   - 장착 공구의 분류 · 직경 · 날 수 누락 (DB CHECK 와 같은 규칙을 미리)
2. **옛 실험실 시트(`실험실 공구 정리.xlsx`)는 받지 않는다.** ISO 분류와 치수가 없어서 받아들이면
   조사해 채운 마스터를 덮어쓴다. 보관소 복원이 옛 시트를 감시 폴더로 되돌려도 마스터는 그대로다
3. CSV 는 UTF-8 과 **CP949**(엑셀에서 'CSV' 로 저장한 한국어 Windows 기본) 둘 다 읽는다
4. 기존 전체 삭제 → 파일 내용 등록(빈 칸은 명시적 NULL) → `relink_workingsteps()` 가 **공구 번호**
   기준으로 가공 이력의 공구 연결을 다시 맺음 (한 트랜잭션). 장착 공구에 없는 번호는 `tool_id` 만
   비우고 호출 번호는 보존 → `tool_id` 를 1부터 연속 재부여
5. DB 내용을 그대로 내보낸 CSV 를 `{보관소 루트}/공구 마스터.csv` 로 보관 (무엇을 올렸든 이 한 형식,
   임시 파일에 쓴 뒤 바꿔 끼워 보관본이 비는 순간이 없다).
   대시보드의 `현재 공구 마스터 CSV` 내려받기도 같은 내보내기라, **올린 CSV 를 다시 내려받으면
   바이트까지 같다**(verify_tool_csv.py 가 이것으로 실제 테이블을 대조한다)

**수집기 경로는 두 경우를 건너뛴다** (`sync_from_watch_folder`). 수집기는 기동할 때 감시 폴더의
기존 파일을 모두 다시 처리하는데, 보관소 복원으로 놓인 `공구 마스터.csv` 가 감시 폴더에 남아 있으면
그 뒤 대시보드에서 올린 새 마스터가 재시작마다 옛 파일로 조용히 되돌아가기 때문이다.

| 경우 | 처리 |
| :--- | :--- |
| 파일 내용 = 지금 DB | 건너뜀 (재시작마다 다시 쓰지 않음) |
| 내용이 다르고, 보관본보다 **오래된** 파일 | 반영 안 함 + 경고 ("대시보드에서 올리세요") |
| 내용이 다르고, 보관본보다 새것이거나 보관본이 없음 | 반영 |

DB 를 초기화한 뒤 보관소에서 복원하면 DB 가 비어 있고 파일은 보관본보다 오래되지 않으므로 다시 채워진다.
대시보드 업로드는 사람이 고른 파일이라 이 판단 없이 항상 반영한다.

`backend/migrate_tool_iso14649.py` 는 옛 구조(`company_name` · `tool_type` · `specification` …)를 이
구조로 바꾼 일회성 도구다. 옛 내용과 가공 이력 연결을 `backups/` 에 남기고, 외래키를 잠시 풀어
테이블을 `models.Tool` 로 다시 만든 뒤 같은 이름 · `ON DELETE SET NULL` 로 다시 걸고, 위 1~5 로
`docs/tool_iso14649.csv` 를 넣는다. 이미 새 구조면 내용만 다시 넣는다.

---

## 7. TDMS 정렬 엔진

`backend/tdms_alignment.py` (약 1,100줄) — 이 시스템에서 가장 복잡한 부분입니다.

### 7.1 풀려는 문제

TDMS 한 파일에는 **가공 한 건이 아니라 장비 모니터링이 켜져 있던 구간 전체**가 들어 있습니다.
예를 들어 12분짜리 파일에 실제 가공은 53초뿐일 수 있습니다. 게다가

- 파일명의 프로그램명이 실제 가공한 NC 와 **다를 수 있습니다** (`O2202.NC` 파일에 `O0911.nc` 가공)
- 한 파일에 **중단된 시도와 완주한 시도**가 같이 들어있을 수 있습니다
- CNC(약 27Hz, 수천 행)와 DAQ(12.8kHz, 수백만 행)는 **행 수가 600배** 차이납니다
- 두 그룹은 수집 경로가 달라 선언된 시작 시각이 같아도 실제로는 **몇 초씩 어긋나** 있습니다
- XML `StartTime` 은 로컬시간(+09:00), TDMS 파형 시각은 UTC 라 그대로 비교하면 **9시간** 차이

### 7.2 NC 블록 대조로 구간 찾기

업로드된 NC 원본을 정규화해 블록 목록을 만들고, CNC 의 `CNC-CurrentBlock` 열과 대조합니다.

```
NC 정규화: 주석 제거 → 공백 제거 → 대문자 → 헤더/끝 표식(%, <...>, O1234, :10) 제외
```

각 CNC 행을 "NC 프로그램의 몇 번째 블록인지"로 바꾸고(`map_block_positions`), 위치가
순서대로 증가하는 구간(run)을 찾습니다.

- **같은 블록이 여러 번 나오는 경우** (`G1Y-15` 가 2회 등) 진행 위치를 **단조 증가**로 매핑해
  한 번의 실행이 여러 조각으로 쪼개지지 않게 합니다.
- **후보가 여럿이면** 실행된 **블록 종류 수(커버리지)** 로 점수를 매겨 완주한 쪽을 고릅니다.
  XML 힌트와 겹치는 run 을 우선합니다.
- 커버리지가 `min_coverage`(기본 0.25) 미만이면 NC 대조를 포기하고 활동 기반으로 전환합니다.

### 7.3 앞뒤 대기 시간 잘라내기

고른 구간 앞뒤에는 공구 교체·대기 시간이 붙어 있습니다.
스핀들 RPM(`rpm_min` 기본 1.0)·이송(`feed_min` 기본 0.5)·블록 전환 간격(`idle_gap` 기본 15초)으로
활동 구간만 남기고, `merge_gap`(기본 5초) 이내 간격은 하나로 잇습니다.

### 7.4 판별 방법 4단계 폴백

| `method` | 조건 |
| :--- | :--- |
| `nc_block` | NC 대조 성공 (가장 신뢰) |
| `activity` | NC 가 없거나 커버리지 미달 → 스핀들/이송 활동으로 판별 |
| `hint` | 활동도 못 찾음 → XML 가공시간 사용 |
| `full` | 아무 근거도 없음 → 기록 전체 |

### 7.5 CNC ↔ DAQ 시간축 정렬

**공통 경과시간 축** — 가공 시작을 0초로 하는 `time_s`(CNC) / `daq_time_s`(DAQ) 열을 붙여
두 그룹을 같은 x축에 올립니다.

**DAQ 구간 잘라내기** — 비율 스케일이 아니라 파형 속성
(`wf_start_time` + `wf_start_offset` + `wf_increment`)으로 **절대 시각을 복원**해 자릅니다.
파형 속성이 없는 장비 데이터만 "두 그룹의 전체 기록 구간이 같다"는 가정으로 비율 환산합니다.

**시계 지연 실측** — 같은 물리량을 보는 두 채널의 **정규화 상호상관**으로 지연을 측정합니다.

```
기준 후보: CNC-Z-SpindleLoad → CNC-W-SpindleLoad → CNC-Z-Current → CNC-Z-SpindleSpeed
대조 후보: DAQ 스핀들 3상 전류 합 (DAQ-Spindle-C-R + -S + -T)
```

실측 예: 상관 0.963, 지연 0일 때 −0.141 → **DAQ 를 +3.05초 이동**.
상관이 충분하지 않으면 보정하지 않고 진단값만 남깁니다.

**시간대 정렬** — XML 힌트와 TDMS 기록 구간이 정수 시간 단위로 어긋나 있으면 맞춘 뒤
힌트로 사용하고, 보정해도 겹치지 않으면(다른 세션의 XML 등) 힌트를 버립니다.

### 7.6 변환 데몬 (`tdms_visualizer.py`)

5초 주기로 `job_file_archive.tdms_raw_path` 가 있고 변환이 필요한 Job 을 찾아 처리합니다.

**읽기 전략 — 채널별 지연 로딩이 아니라 단일 패스**

`TdmsFile.open()` 은 채널을 필요할 때 읽습니다. 그런데 채널 하나를 읽을 때마다 파일 전체의
세그먼트를 훑어야 해서, **채널 수만큼 파일을 다시 스캔**합니다. CNC 그룹은 채널이 34개라
464MB 파일을 34번 훑었고 이것만 45~51초가 걸렸습니다(전체 변환의 79%). 정작 데이터량이
100배인 DAQ 6채널(5,560만 샘플)은 0.75초였습니다. 병목은 데이터량이 아니라 **탐색 횟수**입니다.

`TdmsFile.read()` 는 순차로 한 번만 훑어 모든 채널을 채웁니다.

**두 번째 병목 — 잘게 쪼개진 읽기 호출.** 이 파일은 세그먼트가 약 56만 개여서 nptdms 가
아주 잘게 읽습니다. 그 호출이 전부 파이썬으로 짠 `_TruncatedFile` 로 들어오면 464MB 하나에
**400만 번** 넘게 호출됩니다(cProfile 실측). 두 가지로 줄였습니다.

1. 읽을 때마다 `tell()` 을 부르지 않고 위치를 직접 들고 있는다 (15.0s -> 11.1s)
2. `io.BufferedReader(buffer_size=1MB)` 로 한 번 더 감싸 파이썬 호출을 수백 번으로 줄인다 (-> 9.2s)

실측(464MB, 출력 동일 — CNC 1,423행 / DAQ 10,000행 / FFT 2,049행):

| 방식 | 소요 | 메모리 |
| :--- | ---: | ---: |
| 채널별 지연 로딩 (최초) | 54.9s | +445 MB |
| 단일 패스만 적용 | 16.4s | +560 MB |
| **단일 패스 + 버퍼링 (현재)** | **10.1s** | +561 MB |
| 지연 로딩 + 버퍼링 (메모리 부족 시 대체 경로) | 29.3s | +446 MB |

전부 메모리에 올리므로(파일 크기의 약 1.3배) `open_tdms(eager=None)` 이 여유 메모리를 보고
자동으로 고릅니다. 여유의 절반을 넘길 것 같으면 예전처럼 지연 로딩으로 돌아갑니다.

**남은 병목은 CPU 한 코어**입니다. 단일 패스로 바꾼 뒤에는 HDD 와 SSD 의 차이가 0.11초로
사라졌고(디스크 무관), 프로세스 CPU 점유율이 한 코어 기준 99.7% 로 붙습니다. 즉 nptdms 의
세그먼트 파싱이 한 코어를 포화시키는 구조입니다. 한 파일을 여러 코어로 쪼개는 것은
순차 파싱이라 어렵고, **여러 Job 을 동시에 돌리면** 처리량이 늘어납니다(4개 동시 = 2.6배,
다만 파일당 약 560MB 메모리가 필요).

**재처리 판단** (`_needs_processing`)
- Parquet 경로가 없거나 파일이 유실됨
- `machining_window` 가 없음
- NC 없이 만든 구간인데 이제 NC 가 생김 (단 1회만 재판별 — 무한 루프 방지)

**중복 변환 방지** — `job_conversion_lock()` 이 `data/processed_data/.job_{id}.converting` 을
`O_CREAT|O_EXCL` 로 만들어 선점합니다. `run_system.py` 를 실수로 여러 번 띄워도
같은 Job 을 두 번 변환하지 않습니다. 1시간이 지난 잠금은 죽은 프로세스가 남긴 것으로 보고 회수합니다.

**산출물**

| 파일 | 내용 |
| :--- | :--- |
| `job_{id}_viz.parquet` | 가공 구간만, 목표 10,000행. CNC 전 채널 + DAQ 포락선(max/min) + `time_s`/`daq_time_s` |
| `job_{id}_fft.parquet` | DAQ 채널별 주파수 스펙트럼 (`Frequency` + 채널별 PSD) |
| `job_{id}_window.json` | 구간·판별방법·NC 커버리지·DAQ 보정값·판단 메모 |

같은 내용이 `job.machining_window`(JSON) 에도 저장되고, Parquet 은 Job 폴더
`processed_parquet/` 로도 복사됩니다.

### 7.7 손상된 TDMS 내성

수집 프로그램이 비정상 종료하면 파일 끝과 `*.tdms_index` 에 0으로 채워진 조각이 남아
nptdms 가 `ValueError` 로 실패합니다(실측 파일에서 발생). `find_valid_data_end()` 가
세그먼트 lead-in(28바이트, 태그 `TDSm`/`TDSh`)을 따라가 유효 지점을 찾고,
`_TruncatedFile` 로 거기까지만 읽도록 우회합니다.

```
[알림] 파일 끝 277 바이트가 손상되어 있어 제외하고 읽었습니다.
```

### 7.8 단독 CLI

DB 없이 판별 결과를 확인할 수 있습니다.

```bash
python backend/tdms_alignment.py --tdms <파일> --nc <파일> [--xml <파일>] --out <폴더>
```

---

## 8. 보관소와 무결성

### 8.1 Vault (`backend/vault_manager.py`)

경로 기준의 **단일 출처**입니다. 다른 모듈은 여기서 `PROJECT_ROOT` 를 가져다 씁니다.

```python
PROJECT_ROOT   = <프로젝트 루트>
DATA_ROOT      = PROJECT_ROOT/data
RAW_DATA_ROOT  = DATA_ROOT/machining_raw_data
PROCESSED_ROOT = DATA_ROOT/processed_data
FAILED_ROOT    = DATA_ROOT/failed_data
VAULT_ROOT     = resolve_vault_root()   # 프로젝트 밖으로 뺄 수 있다
```

**보관소 위치는 환경변수로 정합니다.**

보관소가 프로젝트 안에 있으면 프로젝트 폴더를 지우는 순간 원본 백업까지 같이 사라집니다.
백업의 존재 이유가 "원본이 없어져도 복원할 수 있다"는 것이라, 보관소는 프로젝트와 수명이
다른 곳(예: DB 가 설치된 폴더)에 두어야 합니다. `.env` 에서 지정합니다.

| 우선순위 | 환경변수 | 의미 |
| :--- | :--- | :--- |
| 1 | `ORCUS_VAULT_ROOT` | 보관소 절대경로를 직접 지정 |
| 2 | `ORCUS_DB_DATA_DIR` | DB 설치 폴더. 그 아래 `archive_vault/` 를 만들어 사용 |
| 3 | (없음) | 예전 위치 `data/archive_vault` 를 그대로 사용 |

```ini
# .env
# MySQL 스키마 폴더 안은 피한다 (DROP DATABASE 시 함께 삭제됨)
ORCUS_VAULT_ROOT=D:\Orcus\archive_vault
```

OS 환경변수가 이미 있으면 `.env` 보다 그쪽이 우선합니다(`load_dotenv` 기본 동작).

DB 에 저장되는 Vault 경로는 **전부 `VAULT_ROOT` 기준 상대경로**라, 값을 바꾸고 폴더만
옮기면 기존 레코드는 손대지 않아도 그대로 살아납니다. 이전은
`backend/migrate_vault_location.py` 가 담당합니다(기본 미리보기, `--apply` 로 실행).

| 함수/상수 | 용도 |
| :--- | :--- |
| `resolve_vault_root()` | 위 우선순위대로 보관소 위치를 결정 |
| `VAULT_IS_EXTERNAL` | 보관소가 프로젝트 밖인지 (초기화 경고 문구를 가름) |
| `ensure_vault_root()` | 폴더 생성 + 쓰기 가능 여부 확인 → `(성공여부, 설명)` |

| 함수 | 용도 |
| :--- | :--- |
| `get_rel_raw_data_path(abs)` | 절대경로 → DB 저장용 상대경로(슬래시) |
| `get_abs_raw_data_path(rel)` | 상대경로 → 절대경로 (절대경로가 들어와도 그대로 통과) |
| `save_to_vault(src, *subpaths)` | Vault 에 복사하고 상대경로 반환 |
| `get_abs_vault_path(rel)` / `read_from_vault(rel)` | Vault 읽기 |

**Vault 구조**

```
{보관소 루트}/          # 기본: data/archive_vault, 설정 시 DB 설치 폴더 아래
  ├─ {프로젝트}/{부품}/{차수}/XML · NC · TDMS · Log
  │                        /Surface_Roughness · processed_parquet · etc
  ├─ {프로젝트}/{부품}/CAD_Files/
  └─ 공구 마스터.csv                 # 공구 마스터 보관본 (반영할 때마다 DB 내용으로 덮어씀)

  (machining_raw_data 트리와 파일 단위로 동일 — 경로에 job_id 가 들어가지 않는다)
```

### 8.2 무결성 검증 (`integrity.py` / `integrity_monitor.py`)

DB의 LONGBLOB 이 삽입 당시와 바이트 단위로 같은지 SHA-256 으로 재계산합니다.

**판정은 반드시 3-상태입니다.**

| 값 | 뜻 |
| :--- | :--- |
| `True` | 일치 (복원 신뢰 가능) |
| `False` | 불일치 (변조/손상) |
| `None` | **검증 불가** (기준 해시 미기록) — "실패"가 아니라 "모름" |

> `if ok:` 로 분기하면 `None` 이 실패로, `if ok is not False:` 로 분기하면 성공으로 취급됩니다.
> 둘 다 오해를 부르므로 값 자체를 그대로 노출해야 합니다.

해시 재계산 로직은 `integrity.py` **한 곳만** 씁니다. 화면 쪽에서 다시 구현하면
"ETL과 동일한 계산"이라는 검증의 근거가 깨집니다.

`integrity_monitor` 가 파이프라인 기동 직후와 **30분 주기**로 XML·NC·CAD 전부를 검사해
로그에 남깁니다.

```
[원본 복원 검증] 일치 4건 / 불일치 0건 / 검증 불가 0건 / 원본 미보존 0건
```

---

## 9. 복구 엔진

`backend/recovery_engine.py` — Vault 와 DB BLOB 에서 원본 파일 트리를 되살립니다.

### 9.1 공용 복원 함수

```python
_restore_job_files(session, job, dest_dir, raw_root, folder_name) -> 복원한 파일 수
```

**단일 Job 복원과 전체 복구 ZIP 이 같은 함수를 씁니다.**

> 예전에는 두 경로가 각자 구현되어 있었고, 전체 복구 쪽은 XML·NC·로그·조도만 복원했습니다.
> 즉 재난 복구 ZIP 에 **TDMS·Parquet·etc·CAD 가 통째로 빠져** 있었습니다. 같은 일을 두 번
> 구현하면 반드시 갈라집니다.

**복원 항목과 순서**

1. XML → `XML/metadata.xml`
2. NC → `NC/{프로그램명}.nc`
3. TDMS → `TDMS/`
4. 로그 → `Log/`
5. 조도 → `Surface_Roughness/` (DB 아카이브 + Vault 폴더 양쪽)
6. etc → `etc/`
7. Part 레벨 CAD → `{raw_root}/{프로젝트}/{부품}/CAD_Files/`
8. Parquet → `processed_parquet/`

각 항목은 Vault 경로를 먼저 보고, 없으면 **DB BLOB 에서 복원**합니다.

### 9.2 멱등성

```python
_already_has(dest_dir, kind, extensions)   # 그 종류가 이미 있으면 복원 생략
```

> Vault 는 XML 을 항상 `metadata.xml` 이라는 표준 이름으로 보관합니다. 파일 이름으로만
> 판단하면, 원본이 멀쩡한 폴더에 복원을 한 번 더 돌렸을 때 원래 파일 옆에 `metadata.xml` 이
> 하나 더 생깁니다. **이름이 아니라 종류로** 판단해야 몇 번을 돌려도 같은 결과가 됩니다.

### 9.3 공개 API

| 함수 | 용도 |
| :--- | :--- |
| `get_job_archive_files(job_id)` | 종류별 아카이브 목록 (다운로드 센터용) |
| `restore_single_job_to_raw_data(job_id\|source_folder)` | 원본 폴더 원상 복구 |
| `recover_all_jobs(base_output_dir)` | 전체 Job + `metadata_export.json` |
| `create_recovery_zip()` | 전체 복구 ZIP |
| `create_single_job_zip(job_id, categories)` | Job 1건 ZIP |
| `backfill_missing_job_metadata()` | 파일명·Vault 로 빠진 메타데이터 보완 |

---

## 10. 제조 DB Controller (C++)

`controller/system_controller.cpp` + `theme.h` + `app_config.h` + `system_controller.rc`
→ `system_controller.exe` (약 2.7MB, 정적 링크)

### 10.1 성격

**파이썬 코드를 품지 않는 감독자(supervisor)** 입니다.
디스크의 `.py` 를 그대로 실행하므로 파이썬을 고쳐도 다시 빌드할 필요가 없습니다.

### 10.2 인터프리터 탐색 (`app_config.h`)

```
1) {프로젝트}/runtime/python.exe   ← 배포용 설치 파일이 넣는 동봉 런타임 (개발 PC 에는 없음, 15절)
2) py -3    → sys.executable 을 물어 실제 exe 경로를 얻음
3) python   → 같은 방식
```

> **왜 "실제 경로를 물어보는가"** — PATH 의 `python.exe` / `py.exe` 는 Microsoft Store 파이썬에서
> **0바이트 앱 실행 별칭**(reparse point)인 경우가 많습니다. 그런 별칭은 표준 출력을 파이프로
> 받고 핸들을 상속시키는 `CreateProcess` 에서 `ERROR_CANT_ACCESS_FILE(1920)` 로 실패합니다.
> 모듈 로그를 파이프로 받아야 하는 이 프로그램에서는 치명적이라, 별칭을 한 번 실행해
> `sys.executable` 을 받아낸 뒤 그 절대 경로만 씁니다.

### 10.3 프로세스 감독

| 모듈 | 실행 |
| :--- | :--- |
| Streamlit UI | `-m streamlit run frontend\dashboard.py --server.port 8501` |
| Watchdog | `-u backend\data_insert_recognization.py` |
| TDMS Visualizer | `-u backend\tdms_visualizer.py` |

- **stdout/stderr 를 익명 파이프로 받아** 로그 패널에 실시간 표시 (UTF-8 → UTF-16 변환)
- **Job Object** (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) 에 묶어, 컨트롤러가 비정상 종료해도
  자식이 남지 않음 (검증: 강제 종료 후 고아 프로세스 0개)
- 중지는 `taskkill /F /T /PID` — 파이썬이 다시 자식(streamlit 등)을 띄우므로 트리째 정리
- **Streamlit 첫 실행 질문을 미리 답해 둠** (`EnsureStreamlitCredentials`) — Streamlit 은
  `%USERPROFILE%\.streamlit\credentials.toml` 이 없으면 처음 실행할 때 이메일을 묻습니다. 컨트롤러가 띄운
  대시보드에는 표준 입력이 없어 답할 수 없고, 그러면 Streamlit 이 대시보드를 띄우기 전에 종료됩니다
  (새 PC 에 설치한 직후 '대시보드가 켜지자마자 꺼지는' 증상). 컨트롤러가 기동할 때 빈 이메일로 답한 것과 같은
  파일을 만들어 둡니다. 이미 있으면 손대지 않습니다.

**핸들 수명은 전부 UI 스레드가 쥡니다.** 리더 스레드는 읽기 파이프만 소유하고, 끝나면
`WM_USER_PROC_EXITED` 를 보내 UI 스레드가 정리합니다.
(예전에는 리더 스레드가 `hProcess` 를 닫아서 `WM_TIMER` 의 상태 점검과 충돌했습니다.)

### 10.4 설정 파일 공유 (`app_config.h`)

`backend/pipeline_config.json` 을 파이썬과 공유합니다. 저장은 **있던 파일을 고쳐 쓰기**를
우선해, 파이썬이 나중에 키를 추가해도 토글 한 번에 그 키를 지우지 않습니다.

> 예전 구현은 `"paused"` 를 **항상 `false` 로 덮어써서** 일시정지 토글 자체가 동작하지
> 않았습니다. 지금은 파서를 끈 뒤 일시정지를 켜도 두 값이 모두 보존됩니다.

### 10.5 화면

Google Cloud 콘솔풍 밝은 화면을 Win32 오너드로우로 직접 그립니다(`theme.h`).

| 역할 | 값 |
| :--- | :--- |
| 바탕 · 카드 | `#FFFFFF` |
| 카드 테두리 / 구분선 | `#DADCE0` / `#E8EAED` |
| 본문 / 보조 | `#202124` / `#5F6368` |
| 강조 | `#1A73E8` (대시보드 사이드바와 동일) |
| 성공 / 위험 | `#1E8E3E` / `#D93025` |
| 로그 패널 | `#F8F9FA` |

- 헤더: `logo_card.png` (GDI+) + 제목 + 해석된 파이썬 경로
- 왼쪽: 모듈 3개(상태 점·PID·시작/중지) / 파서 토글 6개 + 일시정지 / 관리 유틸리티 4개
  (유저 관리 · 더미 데이터 청소 · 보관소에서 원본 복원 · DB·스토리지 초기화). `유저 관리` 와
  `DB·스토리지 초기화` 는 파이썬(tkinter) 창을 띄우기만 하고, 두 창은 `backend/admin_ui.py` 의
  팔레트 · 버튼 규칙 · 창 하나만 띄우기 · DPI 대응을 함께 씁니다
- 창은 작업 영역(작업표시줄 제외) 가운데에 뜹니다. `CW_USEDEFAULT` 는 계단식으로 아래로 밀어
  놓아, 높이가 화면에 들어가는데도 아래쪽 버튼이 작업표시줄에 가려졌습니다.
- 오른쪽: 실시간 로그 + 자동 스크롤·비우기·복사

**그리기 주의점 두 가지**

1. 오너드로우 버튼의 배경은 **GDI `FillRect`** 로 지웁니다. GDI+ 로 안티앨리어싱을 켠 채
   사각형을 채우면 가장자리 한 줄이 부분 커버라서, BUTTON 클래스가 먼저 칠한
   밝은 회색이 비쳐 선으로 남습니다(실측 `#878D96` = 흰색 47%).
2. 읽기 전용 `EDIT` 는 `WM_CTLCOLOREDIT` 가 아니라 **`WM_CTLCOLORSTATIC`** 을 받습니다.
   여기서 배경 모드를 `TRANSPARENT` 로 두면 새 줄을 그릴 때 이전 글자를 지우지 않아
   스크롤이 시작되는 순간부터 **글자가 겹쳐 그려집니다.** 로그 창만 `OPAQUE` 로 분기해야 합니다.

### 10.6 빌드

```bash
tools\compile_controller.bat                 # windres(아이콘/버전) + g++ → system_controller.exe
tools\compile_controller.bat build\x.exe     # 출력 경로를 주면 거기로 (프로젝트 루트 기준 상대 경로)
tools\run_controller.bat                     # 없으면 자동 빌드 후 실행
```

출력 경로 인자는 설치 파일 빌드가 씁니다. 루트의 `system_controller.exe` 가 실행 중이면 덮어쓸 수 없기 때문입니다.
**상대 경로로 주세요** — MinGW 의 g++/ld 는 경로를 시스템 코드페이지로 다뤄, 한글이 든 절대 경로(이 프로젝트
폴더)에는 결과를 쓰지 못합니다.

```
g++ -std=c++17 -O2 -municode -mwindows src\system_controller.cpp build\resources.o
    -o system_controller.exe
    -lcomctl32 -lshlwapi -lgdiplus -lgdi32 -luser32 -lole32
    -static -static-libgcc -static-libstdc++
```

---

## 11. 웹 대시보드

`frontend/dashboard.py` 단일 엔트리포인트, 포트 8501.
**로그인해야 화면이 열립니다.** 대시보드 안에는 역할 구분(관리자/일반)이 없고, 파괴적 작업에는
확인 절차가 따로 남아 있습니다. 가입 승인만은 대시보드가 아니라 제조 DB Controller 에서 합니다.

#### 로그인 계정 (`backend/auth.py` · `components/auth_view.py`)

계정은 `app_user` 테이블에 있습니다 (`user` 는 MySQL 시스템 테이블과 겹쳐 쓰지 않습니다).

| 컬럼 | 내용 |
| :--- | :--- |
| `username` | 로그인 아이디 (영문·숫자·`_.-`, 3~50자, 유일) |
| `password_hash` | PBKDF2-HMAC-SHA256, 계정마다 다른 소금, 600,000회 |
| `worker_name` | **작업자 이름 — 데이터 삽입 시 `env_memo.worker_name` 으로 기록** |

비밀번호는 평문으로 저장하지 않습니다. 저장 형식에 반복 횟수를 함께 적어 두어, 나중에
횟수를 올려도 기존 계정이 그대로 로그인되고 로그인 성공 시 현재 기준으로 다시 해싱합니다.
로그인 실패 메시지는 아이디 없음과 비밀번호 틀림을 **구분하지 않습니다**(존재하는 아이디를
알아내는 데 쓰이기 때문). 없는 아이디로 시도해도 해싱을 한 번 돌려 응답 시간을 맞춥니다.

#### 회원가입 — 관리자 승인

가입은 **신청 → 승인** 두 단계입니다. 이메일은 받지 않습니다. **승인하기 전까지 계정을 만들지
않습니다.**

```
① 대시보드 '회원가입' 탭: 아이디·이름·비밀번호  ->  app_pending_signup 에 대기
② 제조 DB Controller › 유저 관리 › 가입 신청: 승인  ->  app_user 에 계정 생성, 대기 줄에서 삭제
                                          거절  ->  대기 줄에서 삭제
```

미승인 계정을 먼저 만들어 두면 '계정은 있는데 로그인은 막힌' 상태가 생기고, 그 상태를 코드
곳곳에서 계속 구분해 다뤄야 합니다. 대기 줄을 따로 두면 승인을 통과한 것만 계정이 됩니다.
승인은 계정 생성과 신청 삭제를 **한 트랜잭션**에서 합니다.

| 상황 | 동작 |
| :--- | :--- |
| 같은 아이디로 다시 신청 | 거부 — 덮어쓰게 두면 남의 신청을 가로채 자기 비밀번호로 바꿔 둘 수 있음 |
| 이미 계정이 있는 아이디 | 신청 거부. 대기 중에 계정이 생겼다면 승인이 거부되고 거절만 가능 |
| 승인 전 로그인 시도 | **비밀번호까지 맞으면** "승인 대기 중" 안내, 아니면 일반 로그인 실패 문구 |
| 신청 · 승인 · 거절 | 한 줄씩 표준 출력 → 컨트롤러 실시간 로그에 `[가입 신청] 아이디 (이름)` 등으로 표시 |

#### 유저 관리 (제조 DB Controller › 관리 유틸리티 › `유저 관리`)

`backend/user_manager.py` (tkinter — 표준 라이브러리라 추가 의존성 없음). 탭 두 장입니다.

| 탭 | 보이는 것 | 동작 |
| :--- | :--- | :--- |
| 가입 신청 (N) | 신청 시각 · 아이디 · 이름 | 승인 · 거절 |
| 가입 멤버 (N) | 아이디 · 이름 · 가입 시각 · 마지막 로그인 | 삭제 |

**관리 화면을 대시보드에 두지 않는 이유.** 대시보드에는 권한 구분이 없어서, 거기 두면 로그인한
누구나 남을 들이거나 지울 수 있습니다. 유저 관리 창은 서버 PC 에서 `.env` 의 DB 계정으로 직접
붙으므로 **관리 권한 = 서버 PC 접근 권한** 입니다. 대시보드를 터널 등으로 외부에 열었을 때도
가입은 관리자가 승인해야만 끝납니다. 계정이 하나도 없어도 잠기지 않습니다 — 관리 권한이 계정이
아니라 서버 PC 에 있기 때문입니다.

**멤버 삭제** (`auth.delete_user`)

- 계정과 그 계정의 로그인 세션을 **한 트랜잭션**에서 지웁니다. MySQL 외래키 CASCADE 로도 지워지지만
  그 동작에 기대지 않고 명시합니다.
- 열려 있던 대시보드도 **다음 동작에서 바로** 로그인 화면으로 돌아가고 "계정이 삭제되어
  로그아웃되었습니다" 안내가 한 번 뜹니다. 대시보드가 그릴 때마다 서버 세션을 다시 확인하기
  때문입니다(`auth_view._session_still_valid`). 예전에는 `session_state` 의 계정 정보만 믿어서,
  지워도 그 탭은 새로고침 전까지 로그인된 채였습니다.
- 그 재확인은 **DB 오류면 로그인을 유지**합니다. `auth.session_account` 는 '세션 없음'이면 None,
  DB 오류면 예외를 내서 둘을 구분합니다. 같이 다루면 MySQL 이 한 번 재시작될 때 전원이 로그아웃됩니다.
- 가공 이력의 작업자 이름(`env_memo.worker_name`)은 계정을 가리키는 키가 아니라 그때 적어 둔
  이름이라 **그대로 남습니다**. `app_user` 를 참조하는 테이블은 `app_session` 뿐입니다.
- 되돌릴 수 없으므로 확인창이 경고 아이콘에 기본값 '아니오' 입니다. 지운 아이디로는 다시 가입
  신청할 수 있습니다.

**창의 동작**

- 컨트롤러는 `RunUtilityScript` 로 **띄우기만** 합니다. 조회·승인·거절·삭제는 전부 파이썬 쪽이 하므로
  `python backend/user_manager.py` 로 컨트롤러 없이도 똑같이 돕니다. 창은 Job Object 에 묶이지
  않아 컨트롤러를 닫아도 남습니다(다른 관리 유틸리티와 같은 방식).
- **창은 하나만** 뜹니다. 이름 있는 뮤텍스(`Local\OrcusUserManager`)로 판정하고, 이미 있으면
  창 제목으로 찾아 앞으로 가져온 뒤 새 프로세스는 코드 0 으로 끝납니다. 뮤텍스는 프로세스가
  죽으면 OS 가 거둬 가므로, 잠금 파일처럼 비정상 종료 후 남아 다음 실행을 막는 일이 없습니다.
- 열려 있는 동안 10초마다 두 목록을 함께 다시 읽습니다(F5 로 즉시, Ctrl+Tab 으로 탭 전환).
  승인하면 신청이 멤버로 옮겨 가므로 어느 탭에서 무엇을 하든 둘 다 읽습니다. 고르던 행은 유지됩니다.
- 여러 건을 골라 한 번에 처리할 수 있고, 모든 동작이 대상 목록을 보여 주는 확인창을 거칩니다.
  승인·거절·삭제는 한 줄씩 표준 출력으로 남아 컨트롤러 실시간 로그에 들어옵니다.
- DB 에 못 붙으면 **목록을 비우고** 이유를 표 가운데에 빨간 글씨로 보입니다. 예전 목록을 남겨
  두면 이미 처리된 항목을 보고 누를 수 있기 때문입니다.
- 색·버튼 규칙은 컨트롤러(`theme.h`)와 같습니다. ttk 기본 테마(vista)는 버튼 색 지정을 무시해서
  `clam` 테마를 쓰고, `relief=flat` 은 테두리를 지워 흰 버튼이 링크처럼 보이므로 쓰지 않습니다.
  탭은 `ttk.Notebook` 대신 직접 그립니다 — clam 의 Notebook 은 폴더 모양 테두리를 둘러 표의
  테두리와 두 겹으로 보여서, 글자 + 선택 밑줄만 있는 콘솔식 탭으로 바꿨습니다.

> **이메일 인증에서 바꾼 DB** 는 `backend/migrate_auth_and_inspection.py --apply` 로 정리합니다.
> 옛 형식의 `app_pending_signup`(이메일·인증 코드 컬럼)을 새로 만들고 `app_user.email` 을 지웁니다.
> 적용 전에 컨트롤러에서 「전체 중지」 하세요 — 옛 코드가 떠 있는 채로 email 을 지우면 그 코드가
> 없는 컬럼을 읽으려다 실패해 로그인이 막힙니다. `.env` 의 `SMTP_*` 항목은 더 이상 쓰지 않습니다.

#### 새로고침해도 로그인이 풀리지 않게 (`app_session`)

Streamlit 의 `session_state` 는 **웹소켓 세션에 묶여 있어 새로고침하면 사라집니다.**
쿠키로 해결할 수도 없습니다 — `st.context.cookies` 는 **읽기 전용**이라 파이썬에서 쿠키를
구울 수 없고, 위젯의 `persist_state` 는 세션 안에서 페이지를 옮길 때만 값을 남깁니다.

그래서 로그인 사실을 서버에 남기고, 그것을 가리키는 난수 토큰만 URL 질의문자열
(`?sid=...`)로 주고받습니다.

| 동작 | 처리 |
| :--- | :--- |
| 로그인 | 256비트 난수 토큰 발급 → `app_session` 에 **SHA-256 해시**만 저장 → **URL 에 먼저** 실음 |
| 로그인 후 첫 렌더링 | `sync_browser_token()` 이 **쿠키를 굽고**, 확인되면 URL 에서 토큰을 지움 |
| 새로고침·새 탭 | `session_state` 가 비어 있으면 쿠키(없으면 URL) 토큰으로 계정을 되살림 |
| 로그아웃 | 서버 세션 행 삭제 + 쿠키 제거 → **즉시 무효** |
| 만료 | 기본 7일(`auth.SESSION_DAYS`). 만료된 토큰은 조회 시 거부하고 그 자리에서 삭제 |

**쿠키를 로그인 처리에서 굽지 않고 다음 렌더링으로 미루는 이유가 있다.** 쿠키 쓰기는
컴포넌트가 실제로 렌더링돼야 브라우저까지 전달되는데, 로그인 직후에는 화면을 바꾸려고 곧바로
`st.rerun()` 을 부르므로 그 렌더링이 일어나지 않는다. 처음 구현에서 이 순서 때문에 쿠키가
**한 번도 구워지지 않았고**, 새로고침하면 그대로 로그아웃됐다(실제 브라우저로 확인).
그래서 URL 토큰으로 먼저 이어 붙이고, 쿠키는 로그인된 화면을 그릴 때 굽는다.

**쿠키를 쓰는 이유는 탭을 가리지 않기 때문입니다.** URL 토큰만으로는 같은 탭 새로고침은
버텨도, 새 창에서 주소를 직접 치면 토큰이 없어 다시 로그인해야 합니다.

굽기는 `streamlit-cookies-controller` 컴포넌트가, 읽기는 Streamlit 내장 `st.context.cookies`
가 합니다. 읽기를 내장 기능으로 하는 이유는 컴포넌트가 첫 렌더링에서 값을 못 돌려주는
경우가 있어서입니다 — 그러면 로그인했는데 한 번 튕기는 것처럼 보입니다.
(`st.context.cookies` 는 읽기 전용이라 굽는 일은 컴포넌트가 맡아야 합니다.)

토큰이 아니라 해시를 저장하는 이유는 비밀번호와 같습니다 — DB 를 들여다본 사람이 그대로
로그인할 수 있으면 안 됩니다. 토큰은 256비트 난수라 사전 공격이 통하지 않으므로 느린
해시까지는 필요 없고 SHA-256 으로 충분합니다.

> **감수한 대가.** JS 로 굽는 쿠키라 `HttpOnly` 가 아니어서 페이지의 스크립트가 읽을 수
> 있습니다. HTTPS 가 아니므로 `Secure` 도 걸지 않았습니다(걸면 localhost 에서 동작하지
> 않습니다). 쿠키를 못 쓰는 환경에서는 URL 토큰으로 내려가는데, 그 URL 을 복사해 넘기면
> 만료 전까지 남이 쓸 수 있습니다. 내부망 도구를 전제로 한 절충입니다.

> **추가된 의존성**: `streamlit-cookies-controller`. 폴더를 복사해 배포할 때
> `pip install streamlit-cookies-controller` 가 필요합니다. 설치되어 있지 않으면 쿠키 없이
> URL 토큰 방식으로만 동작합니다(코드가 ImportError 를 잡아 넘깁니다).

> **이 인증의 한계.** 내부망 localhost 대시보드를 전제로 한 **앱 수준 인증**입니다.
> Streamlit 은 HTTP 로 서비스되므로 다른 PC 에서 접속하면 비밀번호가 평문으로 오갑니다.
> 외부에 열어야 한다면 리버스 프록시로 HTTPS 를 씌워야 합니다. 세션은 `session_state` 에만
> 있어 탭을 닫으면 풀립니다. 적대적 침입을 막는 장치가 아니라, **누가 넣었는지 남기고
> 아무나 들어오지 못하게** 하는 용도입니다.

#### 가공 기록 입력 위치

메타데이터(가공 종류)·환경(온습도·칩 형태·소음·메모)·품질(치수공차·Ra/Rz·합불)은
**'데이터 삽입' 화면에서 업로드와 함께** 입력합니다. 값을 아는 시점이 가공을 막 끝낸
업로드 때이기 때문입니다. Job 워크스페이스의 편집 모드는 제거되었고, 그 자리에는 Job 영구
삭제만 남았습니다.

**빈 칸은 '변경하지 않음'으로 다룹니다.** `surface_roughness_ra/rz` 는 조도 CSV 에서 파서가
채우는 측정값이라, 빈 칸을 NULL 로 쓰면 측정 결과를 지워 버리기 때문입니다.
작업자 이름은 새 Job 이면 항상 로그인 계정으로 적고, 기존 Job 은 **비어 있을 때만** 채웁니다
(이미 다른 사람이 기록돼 있다면 그 기록이 사실이므로 덮어쓰지 않습니다).

### 11.1 공통 기반 (`components/common.py`)

`PROJECT_ROOT` 계산 + `backend`/`frontend` 를 `sys.path` 에 추가 + 캐싱 쿼리 헬퍼.

```python
@st.cache_data(ttl=60)
def load_data(query, params=None): ...
```

> **TTL 60초는 UI 지연의 원인입니다.** DB 가 갱신되어도 화면은 최대 60초 뒤에 반영됩니다.
> 화면을 조작하면(필터 변경·재실행) 즉시 갱신됩니다. 편집 후에는 `st.cache_data.clear()` 를
> 명시적으로 부릅니다.

### 11.2 화면 구성

사이드바: 로고 + `DB 관계도(ERD) 보기` 버튼 + 메뉴(데이터 입출력 / 구분선 / 조회·관리)

| 메뉴 | 모듈 | 기능 |
| :--- | :--- | :--- |
| 홈 | `home_view.py` | 브랜드 헤더 + Quick Access 카드 8개 |
| 데이터 삽입 | `data_upload.py` | CAD/XML/NC/TDMS/Log/조도 수동 업로드 |
| 데이터 다운로드 | `download_center.py` | 프로젝트▸Part▸Job 드릴다운 ZIP |
| Job 워크스페이스 | `job_workspace.py` + `filters.py` | 검색·분석·편집·삭제 |
| 계층형 마스터 데이터 | `master_tree.py` | ISO 14649 트리 + CAD 뷰어 |
| 공구 마스터 | `tool_master.py` | ISO 14649 형식 파일 전체 교체 · CSV 내려받기 · 장착/미장착 목록 |
| DB 테이블 | `db_explorer.py` | 14개 테이블 조회·편집·CSV |
| 시스템 백업/복구 | `recovery.py` | 전체 복구 ZIP |

### 11.3 Job 워크스페이스

**5단계 캐스케이딩 필터** (`filters.py`) — 앞 단계 선택이 뒤 단계 선택지를 좁힙니다.

```
프로젝트 → 부품 → Job → 실험일자 범위 → 장비
추가: 가공시간(초) / 표면조도 Ra(μm) / 평균 RPM 범위
```

**분석 보기** — 정보 카드 3열 + 그래프

| 카드 | 내용 |
| :--- | :--- |
| Job 정보 | 프로젝트·부품·차수·장비·시작/종료 |
| 가공 결과 · 품질 | 가공시간·이동거리·PASS/FAIL 배지 |
| 표면조도 측정 통계 | Ra/Rq/Rz 측정점별 |
| CNC 로그 요약 (1Hz) | `cls`/`crpm`/`cfr` 최대·평균 |
| 작업 환경 및 메모 | 온습도·작업자·칩형태 |

그래프: **TDMS**(CNC / DAQ / FFT 탭) · **CNC 로그 추이** · **표면조도 단면 곡선**
TDMS 카드 상단에 가공 구간 판별 요약을 한 줄로 표시합니다.

```
가공 구간 53.6초 · 판별: NC 블록 대조 · NC 블록 22/22종 실행 (100%)
· CNC 1,423/15,229행 · DAQ 시계 보정 +3.05초
```

**편집 모드** — 켜면 메타데이터/환경/품질 탭과 영구 삭제 버튼이 나타납니다.
삭제는 확인 다이얼로그를 거치며 `CASCADE` 로 자식까지 정리됩니다.

### 11.4 데이터 다운로드

프로젝트 ▸ 부품 ▸ Job 3단계로 좁히고, 각 단계 라벨은 `:material/counter_N:` 아이콘을 씁니다.

- **부품까지 선택**하면 `CAD 파일` 버튼이 나타납니다. 도면은 가공차수가 아니라 부품에 딸린
  자료라 별도로 받습니다. 원본 폴더 → Vault → **DB BLOB**(임시 파일로 해제) 순으로 조달합니다.
- **Job 까지 선택**하면 그 Job 에 **실제로 존재하는 유형만** 버튼으로 나타나고(파일 개수 표시),
  유형별 또는 개별 파일로 받을 수 있습니다.
- 원본 폴더가 없는 Job 은 Vault/DB 에서 자동 조달하며, 로컬 디스크로 원본 복원도 가능합니다.

유형 분류는 확장자 기준이며 위에서부터 먼저 맞는 것으로 정해집니다:
메타데이터(XML) → NC → TDMS → 표면조도 → 장비로그 → Parquet → CAD → 기타

### 11.5 DB 테이블

인터랙티브 ERD 에서 테이블을 골라 조회하고, 행 수 제한(100/300/500/1000/전체)·정렬·
속성▸비교조건▸값 필터·CSV 내보내기를 지원합니다.

표를 직접 편집해 저장하면 **Root 계정 재인증** 다이얼로그를 거칩니다. 인증된 자격으로
임시 엔진을 만들어 `UPDATE`/`INSERT`/`DELETE` 를 실행하며, **값은 모두 바인딩 파라미터**이고
식별자(테이블·컬럼)만 보간합니다.

### 11.6 3D CAD 뷰어 (`cad_viewer_component.py`)

Three.js 를 `components.html` 로 임베드합니다. STEP/STL 을 base64 로 전달해 렌더링하고,
360° 회전·메시 모드·등각/상면/정면/측면 프리셋을 제공합니다.

좌표계는 **CAD 표준 Z-up** 입니다. 바닥 그리드를 XY 평면에 두고 카메라 up 벡터를 Z축으로
고정하며(OrbitControls 생성 **전에** 적용해야 함), 화면 좌측 하단에 X(적)/Y(녹)/Z(청)
축 gizmo 를 별도 뷰포트로 겹쳐 그립니다.

---

## 12. 엔드투엔드 흐름

폴더 하나를 떨어뜨렸을 때 무슨 일이 일어나는지, 실측 시간과 함께 정리합니다.
(464MB TDMS 포함 9개 파일 기준)

| 경과 | 일어나는 일 |
| ---: | :--- |
| 0.0s | `data/machining_raw_data/{프로젝트}/{부품}/{차수}/` 에 폴더 복사 |
| 0.9s | CAD 파서 → `cad_file_archive` |
| 1.7s | 파일들이 확장자에 맞는 하위 폴더로 이동 → XML 파싱 → **Part·Workplan·Workingstep·Job 생성**, NC 절삭조건 반영, XML·NC 원본 BLOB 보관 |
| 2.5s | 로그 파싱 → `machine_log` 통계 + Vault |
| 20s | TDMS 메타데이터 파싱 → `job_file_archive.tdms_raw_path` 매핑 + Vault 백업 |
| 22s | 조도 CSV 4점 → `surface_roughness` + `inspection` 평균 + 곡선 Parquet |
| 103s | TDMS 변환 데몬 완료 → 구간 판별(78.6초 소요) + viz/fft Parquet + `machining_window` |
| +최대 60s | 대시보드 캐시 TTL 만료 → 화면 반영 |

**총 약 2분 45초** (가만히 두고 볼 때 최악값). 화면을 조작하면 DB 반영 즉시 보입니다.

병목은 전적으로 TDMS 변환이며, 이는 **워커 스레드가 아닌 별도 데몬**에서 돌기 때문에
나머지 파일 처리를 막지 않습니다.

---

## 13. 운영 가이드

### 13.1 실행

**방법 1 — 제조 DB Controller (권장)**

```bash
system_controller.exe          # 또는 tools\run_controller.bat
```

모듈을 개별로 켜고 끄며 실시간 로그를 보고 파서를 토글할 수 있습니다.

**방법 2 — CLI 일괄 실행**

```bash
python tools/run_system.py
```

패키지 확인 → 더미 청소 → Watchdog → 대시보드 → TDMS 데몬을 순서대로 띄우고
`Ctrl+C` 로 프로세스 트리까지 정리합니다.

접속: <http://localhost:8501>

### 13.2 마이그레이션

| 스크립트 | 용도 |
| :--- | :--- |
| `migrate_keys_v3.py` | 이름 PK → 숫자 PK 전환 (`--check` / `--run`) |
| `migrate_job_folder_layout.py` | Job 루트 파일 → 하위 폴더 이전 (`--apply`) |
| `backfill_workingstep_conditions.py` | 기존 Workplan 에 NC 절삭조건 일괄 반영 |

전부 **이미 적용된 일회성 도구**이지만, 예전 DB 덤프를 복원할 때 필요하므로 남겨둡니다.

### 13.3 정리 스크립트

`backend/clean_dummy_data.py` — 파이프라인 기동 시 자동 실행

1. 자리 채우기 Workplan (`program_code='Unknown'`) 중 Job 이 없는 것 삭제
2. 해시를 뒤늦게 확보해 **밀려난 `NOHASH` Workplan** 삭제
   (같은 부품·프로그램에 실제 해시를 가진 형제 행이 있을 때만 — NC 만 올려두고 아직 돌리지
   않은 정상 계획을 지우지 않기 위한 조건)
3. 같은 `(workplan_id, step_order)` 로 **중복 생성된 Workingstep 병합**
   (살아남는 행의 빈칸을 나머지 행에서 채운 뒤 삭제)

`backend/reset_database_and_storage.py` — 항목별 초기화. 컨트롤러의 `DB · 스토리지 초기화` 버튼은
초기화 창(`backend/reset_manager.py`)을 띄우고, 그 창이 `run_reset()` 을 부릅니다. **되돌릴 수 없습니다.**

| 항목 | 지우는 것 | 남는 것 |
| :--- | :--- | :--- |
| 가공 데이터 DB (`--db`) | 공구 · 계정을 뺀 모든 테이블과 뷰 DROP → `create_all` 로 다시 만듦 | 공구 목록 · 로그인 계정 |
| 스토리지 (`--storage`) | `data/machining_raw_data` · `data/processed_data` · `data/failed_data` 내용 (루트 폴더 유지) | 원본 백업 보관소 |
| 공구 목록 (`--tools`) | `tool` 행 전부(AUTO_INCREMENT 1 로) + 감시 폴더 루트의 공구 마스터 파일 | 보관소의 `공구 마스터.csv`, 가공 이력의 T번호 |

- **어떤 항목을 골라도 지우지 않는 것**: 원본 백업 보관소, 로그인 계정(`app_user` · `app_session` ·
  `app_pending_signup`). 예전에는 DB 초기화가 모든 테이블을 DROP 해 계정까지 사라졌고, 그러면 모두가
  다시 가입 신청 · 승인을 거쳐야 했다. 계정은 `유저 관리` 에서 다룬다.
- **공구 목록만 지우면** 가공 이력(workingstep)은 남고 `tool_id` 만 외래키(ON DELETE SET NULL)로
  비워진다. `tool_number` · `xml_tool_code` 가 남으므로 공구 마스터를 다시 넣으면 다시 연결된다.
  감시 폴더 루트의 `공구 마스터.csv` 를 함께 지우는 이유는, 남겨 두면 수집기가 다시 켜질 때
  '빈 공구 목록과 다른 파일'로 보고 공구 목록을 되살리기 때문이다([6.7](#67-공구-마스터-tool_inserterpy)).
- **DB 만 지우면** 감시 폴더의 원본이 남으므로, 수집기를 다시 켜면 기동 스캔이 그 파일로 DB 를 다시 채운다.
- **순서는 DB → 공구 목록 → 스토리지.** 도중에 실패해도 원본 파일이 남아 있으면 수집기로 다시 채울 수 있다.
- **실행 전 모듈 확인** (`running_modules()`): 수집기 · 대시보드 · TDMS 변환기가 돌고 있으면 시작하지
  않는다(psutil 로 프로세스 명령줄을 본다 — 컨트롤러로 띄웠든 `tools/run_system.py` 로 띄웠든 잡힌다).
  수집기가 켜진 채 Job 폴더를 지우면 `_auto_restore` 가 유실로 보고 보관소에서 되살려 초기화가 무효가 된다.
- 명령줄: `--db` · `--storage` · `--tools` · `--all`. 항목을 하나도 주지 않으면 도움말만 찍고 코드 2 로 끝난다.

초기화 창(`reset_manager.py`)은 항목마다 지워질 양과 남는 것(보관소 경로 · 계정 수)을 보여 주고, 모듈이
돌고 있으면 실행 버튼을 잠근다. '되돌릴 수 없음을 확인했습니다' 체크 + 마지막 확인창(기본값 '아니오')을
거쳐 백그라운드 스레드로 실행하며, 진행 기록은 창의 기록란과 컨트롤러 로그 양쪽에 나온다. 실행 중에는
창을 닫을 수 없고, 닫을 때 실제로 초기화했는지를 로그에 남긴다(컨트롤러가 붙이는 '… 창 완료' 는 창이
끝났다는 뜻일 뿐이다).

`backend/restore_from_vault.py` — 컨트롤러의 `보관소에서 원본 복원` 버튼이 부릅니다.
위 초기화의 **반대 방향**입니다. 보관소 → `data/machining_raw_data` 로 원본을 되돌리면
수집기가 그것을 다시 읽어 DB 까지 복구됩니다.

| 상황 | 하는 일 |
| :--- | :--- |
| DB 살아 있음 | `recovery_engine.restore_single_job_to_raw_data()` 로 Job 별 정확 복원 |
| DB 비어 있음 | 보관소 파일만 보고 복원 (경로에 id 가 없어 DB 가 필요 없음) |

덮어쓰지 않습니다. 이미 있는 파일은 건너뛰므로 여러 번 돌려도 결과가 같습니다.

**왜 `job_manifest.json` 이 필요한가.** 보관소 안의 자리는 두 종류입니다.

```
jobs/{source_folder}/metadata.xml     <- 경로에 폴더 이름이 들어 있다
tdms_files/Job_{job_id}/...           <- job_id 로만 묶여 있다
```

`job_id` 가 어느 `source_folder` 였는지는 **DB 에만 있던 사실**이라, DB 까지 잃으면 파일
이름만으로는 알 수 없습니다. 그래서 XML 을 보관할 때 옆에 쪽지를 같이 남깁니다
(`vault_layout.write_manifest`, `xml_parser` 가 커밋 직후 호출).

```json
{"source_folder": "Alchemist/DB_test/1", "job_id": 1, "workplan_id": 2,
 "part_name": "DB_test", "project_code": "Alchemist", "nc_file_name": "O0911.nc"}
```

경로를 DB 에 저장하던 것과는 다릅니다 — 계산 가능한 **규칙**이 아니라 '이 job_id 는 이
폴더였다'는 **사실**을 원본 옆에 두는 것입니다. `nc_file_name` 덕에 보관소가 `WP{id}.nc` 로
바꿔 담은 NC 도 원래 이름으로 되돌아갑니다.

쪽지가 없는 옛 보관소는 `backend/backfill_vault_manifests.py --apply` 로 DB 가 살아 있을 때
한 번 만들어 두면 됩니다.

보관소는 원본이 사라져도 복원할 수 있게 해 주는 마지막 방어선이고, 프로젝트와 함께
지워지지 않도록 일부러 밖으로 빼 둔 폴더입니다. 초기화가 그것까지 지우면 밖으로 뺀 의미가
없어지므로 **이중 안전장치**를 둡니다.

1. `reset_storage()` 의 삭제 대상 목록에서 보관소를 아예 제외
2. `clean_directory()` 가 실제 삭제 직전에 `vault_manager.is_inside_vault()` 로 확인하고,
   보관소 자신이거나 그 안쪽이면 `[보호]` 를 찍고 즉시 반환

경로 설정이 바뀌거나 호출이 잘못돼도 2번에서 막힙니다. 초기화가 끝나면 보관소에 남은
파일 수를 세어 보존됐음을 보고합니다. 이 상태에서 복원이 필요하면 `recovery_engine` 의
복구 기능으로 보관소에서 되살릴 수 있습니다.

### 13.4 테스트

| 파일 | 실행 방법 | 내용 |
| :--- | :--- | :--- |
| `test_nc_parser.py` | `pytest` | NC 절삭조건 파싱 4케이스 |
| `test_controller_integration.py` | `pytest` | 토글·파서 skip·프로세스 수명·exe 동작 |
| `test_tdms_alignment.py` | **단독 실행** | 합성 TDMS 로 31개 검사 |
| `verify_e2e_cutting_conditions.py` | **단독 실행** | 실제 DB 연동 E2E 검증 (테스트 행을 만들었다 지움) |
| `test_user_management.py` | `pytest` | 가입 신청 · 승인 · 멤버 삭제 (SQLite) |
| `test_tool_master_parse.py` | `pytest` | 공구 마스터 파일 검사 · 수집기 반영 판단 (DB 없음) |
| `test_reset_options.py` | `pytest` | 초기화 항목 조합 · 모듈 감지 · 보관소 보호 · 명령줄 (DB 없음) |
| `verify_tool_csv.py` | **단독 실행** | 공구 CSV · `models.Tool` · 실제 tool 테이블 대조 (임시 테이블, 실제는 읽기만) |
| `verify_reset.py` | **단독 실행** | 초기화를 시험용 스키마 `orcus_reset_selftest` 에서 실제로 돌려 봄 (실제 DB 는 읽기만, 끝나면 스키마 삭제) |
| `test_setup_target.py` | `pytest` | 설치 마무리: `.env` 쓰기 · `my.ini` · 입력 검사 · `sc` 출력 해석 · Setup 과의 약속 (MySQL 없음) |

```bash
python -m pytest backend/tests/test_nc_parser.py backend/tests/test_controller_integration.py -q
python backend/tests/test_tdms_alignment.py
python backend/tests/verify_e2e_cutting_conditions.py
```

> `test_tdms_alignment.py` 는 인자 이름이 `tmp` 라 pytest 가 픽스처로 오인합니다.
> 자체 `main()` 을 가진 **단독 실행용 스크립트**이므로 그대로 실행하세요.

`test_controller_integration.py` 는 끝날 때 파일뿐 아니라 **자신이 만들었을 수 있는 DB 행도**
지웁니다(`_purge_test_rows`). 파서 skip 가드가 어떤 이유로든 동작하지 않으면
실제 DB 에 테스트용 Job 이 남기 때문입니다.

---

## 14. 설계 판단과 알려진 제약

### 14.1 왜 이렇게 했는가

| 판단 | 이유 |
| :--- | :--- |
| 폴더 이름을 `job_layout.py` 한 곳에만 | 수집·복원·업로드·다운로드가 각자 문자열을 들면 한 곳만 바뀌어도 어긋남 |
| 경로 기준을 `vault_manager.PROJECT_ROOT` 한 곳에만 | 6개 모듈이 `dirname` 을 몇 번 거슬러 올라가는지 세어가며 같은 값을 계산하고 있었음 |
| 복원을 `_restore_job_files` 하나로 | 같은 일을 두 번 구현하면 반드시 갈라짐 (실제로 갈라져 TDMS 가 빠졌음) |
| 해시 재계산을 `integrity.py` 한 곳에만 | 화면에서 다시 구현하면 "ETL과 같은 계산"이라는 검증 근거가 무너짐 |
| 공구 마스터 교체를 `tool_inserter` 한 곳으로 | 여러 경로가 각자 쓰면 어느 것이 정답인지 알 수 없음 (업로드 · 수집기 · 마이그레이션이 같은 검사를 거침) |
| TDMS 변환을 데몬 하나로 | 인라인 + 데몬 병행 시 같은 파일을 동시에 변환 (76초 → 110초) |
| 단일 워커 스레드 | 파서가 같은 Job 행을 동시에 건드리는 경합 제거 |
| 파일 종류 판정을 폴더 우선으로 | 확장자만 보면 `.csv` 가 조도인지 로그인지 알 수 없음 |
| 로그 파서의 헤더 검증 | 아무 `.log` 나 받으면 통계가 0으로 덮어써짐 (실제 사고) |

### 14.2 알려진 제약

- **대시보드 캐시 60초** — DB 갱신이 화면에 최대 60초 늦게 반영됩니다.
- **`pipeline_config.json` 폴링** — 토글은 파일을 통해 전달되므로, 파일 시스템이
  느린 환경에서는 반영이 약간 지연될 수 있습니다.
- **컨트롤러는 Windows 전용** — Win32 API 와 Job Object 를 직접 씁니다.
  다른 OS 에서는 `run_system.py` 를 쓰세요.
- **`runtime/` 은 배포용 설치 파일로 설치한 PC 에만 있습니다** — 개발 PC 에서는 비어 있고 컨트롤러가 `py -3` 을
  씁니다. 만드는 방법과 이유는 [15. 배포용 설치 파일](#15-배포용-설치-파일).
- **배포용 설치 파일은 서명되어 있지 않습니다** — 처음 실행할 때 SmartScreen 경고가 뜹니다. 코드 서명 인증서가
  생기면 Inno Setup 의 `SignTool` 로 서명할 수 있습니다.
- **`.igs` 등 미지원 확장자는 조용히 무시됩니다** — `etc/` 에 넣으면 Vault 에 보관은 됩니다.
- **Job 을 여러 번 돌리면 Workplan 의 `nc_file_path` 는 마지막 Job 을 가리킵니다** —
  그 Job 을 지우면 참조가 깨집니다. 데이터 차원에서 고칠 수 있지만 코드 차원의 방어는 없습니다.

### 14.3 기능을 추가할 때 확인할 것

1. **새 파일 종류를 다루나?** → `job_layout.EXTENSION_DIRS` 와 `KNOWN_SUBDIRS` 에 추가하고,
   `data_insert_recognization.handle_file()` 의 분기와 `recovery_engine._restore_job_files()`
   양쪽에 반영하세요. 한쪽만 고치면 수집은 되는데 복구가 안 됩니다.
2. **DB 컬럼을 추가하나?** → `models.py` 와 `schema_patch.PENDING_COLUMNS` 둘 다 고치세요.
   후자를 빠뜨리면 기존 DB 에서 기동할 때 컬럼이 없어 터집니다.
3. **같은 세션에서 방금 추가한 행을 조회하나?** → `db.flush()` 를 먼저 부르세요.
4. **컨트롤러에 토글을 추가하나?** → `pipeline_control.DEFAULT_CONFIG`,
   `app_config.h` 의 키 목록, 컨트롤러 UI 세 곳을 맞추세요.
5. **파괴적 동작인가?** → 확인 다이얼로그 또는 Root 재인증을 붙이세요.
6. **파이썬 패키지를 추가하나?** → `tools/requirements.txt` 와 `installer/requirements-runtime.txt` 둘 다 고치고
   (`setup_target.REQUIRED_MODULES` 와 `build_installer.RUNTIME_SMOKE` 에도 넣으면 설치 때 검사됩니다),
   설치 파일을 다시 만드세요. 빠뜨리면 설치한 PC 에서만 그 기능이 import 오류로 죽습니다.

---

## 15. 배포용 설치 파일

`installer/` — 다른 PC 에 설치할 설치 파일 하나(`dist\Setup_ManufacturingDB_v<판>.exe`)를 만듭니다.
쓰는 법(설치 화면 · 무인 설치 · 빌드 명령)은 README 의 '배포용 설치 파일' 절에 있고, 여기에는 구조와 이유를 적습니다.

### 15.1 무엇을 담는가

| 담는 것 | 어디서 | 압축 전 |
| :--- | :--- | :--- |
| 프로젝트 | git 작업 트리(커밋 전 새 파일 포함, `.gitignore` 대상 · `backend/tests` 제외) + 소스에서 새로 빌드한 컨트롤러 | 약 10 MB |
| `runtime\` | python.org 공식 배포본 3.14.x zip(SHA-256 고정) + `installer/requirements-runtime.txt` | 약 560 MB |
| `mysql\` | 개발 PC 의 MySQL Server 8.0: `mysqld` · `mysql` · `mysqladmin` · `mysqldump` 와 그들이 쓰는 DLL, `share`, `lib/plugin`, `lib/private`(정규식 함수의 ICU 데이터) | 약 120 MB |
| VC++ 런타임 | `aka.ms/vc14/vc_redist.x64.exe` (Microsoft 서명 확인) | 18 MB |
| 기본 공구 목록 | 빌드할 때 DB 의 공구 마스터를 앱 코드(`tool_inserter.export_tool_master_csv`)로 내보낸 것 | — |

LZMA2 솔리드 압축 뒤 약 130 MB. `.env` · `data\` · 보관소는 담지 않습니다(`.env` 가 담기면 빌드를 멈춥니다 — DB 비밀번호).

### 15.2 빌드에서 정한 것 (`build_installer.py`)

- **embeddable 판이 아니라 전체 배포판을 쓴다** — 관리 창(유저 관리 · 초기화)은 tkinter 가 필요한데 embeddable 에는
  없습니다. 또 embeddable 의 `._pth` 는 격리 모드라 스크립트 폴더가 `sys.path` 에 들어가지 않아,
  `backend\*.py` 가 옆 모듈을 import 하는 방식이 깨집니다.
- **사용자 site-packages 차단 (`sitecustomize.py`)** — 파이썬은 `%APPDATA%\Python\Python314\site-packages`
  (`pip install --user` 자리)를 런타임의 site-packages 보다 **앞에** 둡니다. 같은 PC 에서 파이썬 3.14 를 따로 쓰는
  사람이 있으면 동봉 판 대신 그 판을 불러옵니다(개발 PC 에서 실제로 PIL 이 그쪽에서 불려 왔습니다). 런타임의
  `sitecustomize.py` 가 그 경로를 빼고, 빌드 때 `-s` 없이 불러 차단을 확인합니다.
- **MySQL DLL 은 import 표를 따라가며 모은다** (`pe_imports`) — 손으로 적은 목록에서 `libprotobuf-lite.dll` 이 다시
  쓰는 `abseil_dll.dll` 이 빠져, 담은 `mysqld` 가 DLL 을 못 찾고(0xC0000135) 시작하지 못한 일이 있었습니다.
- **담은 MySQL 을 실제로 띄워 본다** — 영문 임시 폴더에 복사해 `--initialize-insecure` → 기동 → 질의 → 종료.
  빠진 파일은 설치한 PC 가 아니라 여기서 걸립니다 (`--skip-mysql-selftest` 로 건너뜀).
- **패키지 자체 시험 코드(`tests/`)는 뺀다** — numpy · pandas · scipy · pyarrow. 실행에 쓰이지 않습니다.
- **런타임은 파이썬 판 + 패키지 목록이 같으면 다시 만들지 않는다** (`.build-stamp`, `--rebuild-runtime`).

### 15.3 설치 순서

```
마법사(데이터 폴더 · MySQL 선택) → 파일 복사 → [설치 마무리]
  VC++ 런타임(없거나 오래됐을 때) → setup_target.py mysql → schema → verify → 방화벽 → 바로 가기
```

설치 마무리는 Inno Setup 의 Pascal 코드가 아니라 **설치된 파이썬 런타임으로 `installer/setup_target.py`** 를
단계마다 부릅니다. 앱의 `models.py` · `schema_patch` · `tool_inserter` · `vault_manager` 를 그대로 쓰므로 테이블과
공구 목록이 앱이 기대하는 모양과 어긋날 수 없고, 단위 시험도 파이썬으로 할 수 있습니다.

| 주고받는 것 | 방식 |
| :--- | :--- |
| 설정 (Setup → 파이썬) | `{tmp}\setup_options.json` — 비밀번호를 명령줄에 싣지 않으려고 파일로 넘기고, 끝나면 지웁니다 |
| 진행 상황 | 표준 출력의 `##STEP <키>` 줄 (ASCII). `ExecAndLogOutput` 콜백이 받아 설치 화면 문구를 바꿉니다 |
| 실패 설명 | `logs\install_error.txt` (UTF-8) — Setup 이 읽어 메시지 창에 그대로 띄웁니다 |
| 기록 | `logs\install.log` — 모든 단계의 출력 (사람용) |
| 설치 상태 | `installer\install_state.ini` — MySQL 방식 · 서비스 · 데이터 폴더. 업그레이드 · 제거가 읽습니다 (`GetIniString`) |
| 종료 코드 | 0 성공 · 1 실패 · 2 root 비밀번호 틀림. 하나라도 실패하면 Setup 자체가 8 로 끝납니다 |

- **모든 단계는 다시 실행해도 같다** — 초기화된 데이터 폴더는 재사용, `CREATE DATABASE IF NOT EXISTS`,
  `create_all`(없는 테이블만), 공구 목록은 비어 있을 때만 넣습니다. 지우는 일은 하지 않습니다. 그래서 실패한 뒤
  설치 프로그램을 다시 실행하면 이어서 설정되고, 업그레이드도 같은 단계를 그대로 부릅니다.
- **설치 단계는 방금 쓴 `.env` 만 본다** — Setup 을 띄운 셸에 `DB_*` 가 남아 있으면(개발 PC 의 터미널 등)
  dotenv 는 그 값을 덮어쓰지 않습니다(`override=False`). 실제로 시험 중에 설치 단계가 개발 PC 의 실제 DB 를
  바라본 일이 있어(쓰기는 없었음), `.env` 의 값으로 덮어쓰고 `.env` 에 없는 키는 지우게 했습니다. 거꾸로 Windows
  '영구' 환경 변수(`DB_*` · `ORCUS_*`)가 `.env` 와 다르면 설치한 앱이 그 값을 쓰게 되므로 `verify` 가 실패로 알립니다.

### 15.4 MySQL

- **감지** — 서비스 목록에서 이름에 `mysql` · `mariadb` 가 든 것, 3306 포트 사용 여부(`Get-NetTCPConnection`).
  있으면 '이 PC 의 MySQL 사용' 을, 없으면 '동봉 MySQL' 을 기본으로 고르고, 3306 이 차 있으면 동봉 MySQL 의 포트로
  3307 부터 빈 포트를 제안합니다.
- **동봉** — `{app}\mysql\my.ini` 를 쓰고(경로는 `/`, 옵션 파일에서 `\` 는 이스케이프), 데이터 폴더를
  `--initialize-insecure` 로 만든 뒤 `OrcusMySQL` 서비스(LocalSystem, 자동 시작, 실패 시 재시작)로 등록하고
  root 비밀번호를 정합니다. `bind-address=127.0.0.1` · `mysqlx=OFF` (이 PC 의 앱만 접속), `utf8mb4_0900_ai_ci`,
  `lower_case_table_names=1`, `max_allowed_packet=1G`(개발 PC 와 같게), `skip-log-bin`, `secure-file-priv=NULL`.
  `.env` 의 `DB_HOST` 는 `127.0.0.1` — `localhost` 는 `::1` 을 먼저 시도해 접속마다 지연됩니다.
- **기존** — 설치를 시작하기 전에 동봉 `mysql.exe` 로 실제로 접속해 봅니다(비밀번호는 임시 옵션 파일로 넘기고 바로
  지움). 8.0.16 미만과 MariaDB 는 받지 않습니다(CHECK 제약 · `utf8mb4_0900_ai_ci`). 같은 이름의 DB 가 있으면
  지우지 않고 이어서 쓴다고 확인을 받습니다. `DB_HOST=localhost` (개발 PC 와 같은 계정 규칙).
- **비밀번호 문자 제한** — 영문 · 숫자와 `! # $ ^ & * ( ) - _ = + [ ] { } ; : , . ? ~`. `.env` 와 SQLAlchemy 접속
  문자열(`mysql+pymysql://root:<비밀번호>@...`)에 그대로 들어가므로 `@`(주소 구분자) · `%`(퍼센트 인코딩)는
  연결을 깨고, 따옴표 · 역슬래시 · 공백은 `.env` · MySQL 옵션 파일에서 따로 풀어야 합니다. Setup 과
  `setup_target.PASSWORD_CHARS` 가 같은 규칙을 씁니다.

### 15.5 업그레이드 · 제거

- **업그레이드 판정** — 설치 폴더에 `installer\install_state.ini` 와 `.env` 가 있으면 업그레이드입니다. 설정 화면을
  건너뛰고, 파일을 바꾸기 전에(`PrepareToInstall`) `OrcusMySQL` 을 멈추며, 실행 중인 컨트롤러 · 파이썬 모듈은
  Restart Manager 가 닫기를 묻습니다. `runtime\Lib\site-packages` 는 비우고 새로 넣고(새 판에서 빠진 패키지가 남지 않게),
  `.env` 와 `backend\pipeline_config.json`(파서 On/Off)은 그대로 둡니다.
- **제거** — 실행 중인 프로그램을 닫을지 묻고, 서비스를 멈춰 지운 뒤, 데이터베이스 파일을 지울지 묻습니다
  (기본 '아니요', 무인 제거는 `/DELETEDBDATA=1` 일 때만). 방화벽 규칙도 지웁니다. **보관소는 어떤 경우에도 남기고**,
  `.env` · `logs\` · `data\` 도 남습니다. '이 PC 의 MySQL' 로 설치했다면 그 MySQL 과 데이터베이스는 건드리지 않습니다.

### 15.6 새 PC 에서만 드러나던 문제 (설치 파일을 만들며 고친 것)

| 문제 | 개발 PC 에서 안 보였던 이유 | 고친 곳 |
| :--- | :--- | :--- |
| 대시보드가 켜지자마자 꺼짐 (Streamlit 첫 실행 이메일 질문) | 오래전에 콘솔에서 답해 둔 `credentials.toml` 이 있음 | 컨트롤러 `EnsureStreamlitCredentials` (10.3) |
| 재부팅 직후 DB 접속 실패 (`cryptography` 없음) | 다른 클라이언트가 먼저 접속해 서버 인증 캐시가 차 있음 | `tools/requirements.txt` · 런타임 (2.3) |
| 동봉 판 대신 사용자 폴더 패키지를 불러옴 | 개발 PC 는 원래 그 패키지를 씀 | 런타임 `sitecustomize.py` (15.2) |
| 쿠키 로그인 유지가 꺼짐 | 개발 PC 에만 `streamlit-cookies-controller` 가 깔려 있었음 (requirements 에 없었음) | `tools/requirements.txt` · 런타임 |

### 15.7 시험

- **단위** — `backend/tests/test_setup_target.py` (pytest, MySQL 없음).
- **시험용 판** (`build_installer.py --test`) — 관리자 권한 없이 설치 · 제거되고(`PrivilegesRequired=lowest`), 서비스 대신
  mysqld 를 백그라운드로 띄우며(`no_service`, 제거 · 업그레이드 때 `setup_target.py stop-test-mysql` 로 끔), VC++ 런타임 ·
  방화벽 · 바로 가기는 건너뛰고, AppId 가 달라 정식 판과 섞이지 않습니다. 나머지 설치 흐름은 정식 판과 같습니다.
  이 판으로 무인 설치 → 동봉 런타임만으로 앱 · 대시보드 · 컨트롤러 · 관리 창 실행 → 보관소 원본으로 수집기가 DB 와
  parquet 을 채우는 흐름 → 업그레이드 → 제거(데이터 남김) → 같은 데이터로 재설치 → 제거(데이터 삭제) → 기존 MySQL
  모드 → 설치 마법사 화면(감지 · 입력 검사 · 진행 문구)을 확인했습니다.
- **정식 판에서만 도는 부분** — 서비스 등록 · 방화벽 규칙 · VC++ 런타임 설치는 관리자 권한이 있어야 해서 시험용 판으로는
  돌지 않습니다. 실제 설치할 PC(또는 빈 Windows 가상 머신)에서 한 번 설치해 보고 `logs\install.log` 를 확인하세요.

---

*이 문서는 V2.3.5 기준입니다. 코드를 고치면 해당 절도 함께 갱신해 주세요.*
