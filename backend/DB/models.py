import datetime

from sqlalchemy import (CheckConstraint, Column, Integer, String, Float, Double, Boolean, DateTime,
                        JSON, ForeignKey, LargeBinary, Numeric, Text, UniqueConstraint, event, text)
from sqlalchemy.orm import Session, relationship
from .database import Base


# 영어 요일 이름. weekday() 가 돌려주는 0~6 을 그대로 색인한다.
#
# strftime('%A') 나 calendar.day_name 을 쓰면 안 된다. 둘 다 **로케일에 따라 달라져서**
# 한국어 Windows 에서는 '금요일' 이 나온다(실측 확인). 장비 PC 의 지역 설정에 따라 DB 에
# 들어가는 값이 바뀌면 같은 컬럼에 영어와 한국어가 섞인다. weekday() 는 로케일과 무관하므로
# 고정 표를 직접 색인해 언제 어디서 돌려도 같은 영어 이름이 나오게 한다.
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")


def english_day_of_week(value):
    """가공 시작시각에서 영어 요일을 뽑는다. 알 수 없으면 None.

    value 는 datetime 일 수도, 'YYYY-MM-DD HH:MM:SS' 문자열일 수도 있다.
    (xml_parser 는 XML 의 StartTime 문자열을 그대로 컬럼에 넣고 DB 가 변환한다)
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, datetime.date):
        return DAY_NAMES[value.weekday()]
    else:
        text_value = str(value).strip().replace("T", " ")
        if not text_value:
            return None
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(text_value[:26], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    return DAY_NAMES[dt.weekday()]

class User(Base):
    """대시보드 로그인 계정.

    가공 데이터를 누가 넣었는지 남기기 위한 계정이다. 로그인한 사람의 `worker_name` 이
    업로드 시 그 Job 의 env_memo.worker_name 으로 들어간다.

    비밀번호는 절대 평문으로 두지 않는다. `backend/auth.py` 가 PBKDF2-HMAC-SHA256 으로
    1회용 소금(salt)과 함께 해싱한 문자열만 여기 담는다.
    """
    __tablename__ = "app_user"
    __table_args__ = {'comment': '대시보드 로그인 계정 (작업자 이름 포함)'}

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, unique=True, comment='로그인 아이디')
    password_hash = Column(String(255), nullable=False, comment='PBKDF2 해시 (평문 저장 안 함)')
    worker_name = Column(String(50), nullable=False, comment='작업자 이름 (env_memo 에 기록)')
    created_at = Column(DateTime, comment='가입 시각')
    last_login_at = Column(DateTime, comment='마지막 로그인 시각')


class PendingSignup(Base):
    """관리자 승인을 기다리는 가입 신청.

    승인하기 전까지는 `app_user` 에 계정을 만들지 않는다. 미승인 계정을 먼저 만들어 두면
    '계정은 있는데 로그인은 막힌' 상태가 생기고, 그 상태를 코드 곳곳에서 계속 구분해 다뤄야
    한다. 대기 줄을 따로 두면 승인을 통과한 것만 계정이 된다.

    승인·거절은 대시보드가 아니라 제조 DB Controller 의 '유저 관리' 창
    (`backend/user_manager.py`)에서 한다. 승인하면 이 행이 `app_user` 로 옮겨지고,
    거절하면 지워진다.
    """
    __tablename__ = "app_pending_signup"
    __table_args__ = {'comment': '관리자 승인 대기 중인 가입 신청'}

    pending_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, unique=True, comment='가입할 아이디')
    worker_name = Column(String(50), nullable=False, comment='작업자 이름')
    password_hash = Column(String(255), nullable=False, comment='PBKDF2 해시 (평문 저장 안 함)')
    created_at = Column(DateTime, comment='신청 시각')


class AppSession(Base):
    """로그인 세션. 브라우저를 새로고침해도 로그인이 풀리지 않게 하는 근거다.

    Streamlit 의 session_state 는 웹소켓 세션에 묶여 있어 새로고침하면 사라진다. 그래서
    로그인할 때 난수 토큰을 만들어 URL 질의문자열로 넘기고, 그 토큰의 **해시**를 여기 둔다.

    토큰 자체가 아니라 해시를 저장하는 이유는 비밀번호와 같다. DB 를 들여다본 사람이 그대로
    로그인할 수 있으면 안 된다. 토큰은 256비트 난수라 사전 공격이 통하지 않으므로 느린
    해시(PBKDF2)까지는 필요 없고 SHA-256 으로 충분하다.
    """
    __tablename__ = "app_session"
    __table_args__ = {'comment': '로그인 세션 (새로고침 유지용)'}

    session_id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), nullable=False, unique=True, comment='토큰 SHA-256')
    user_id = Column(Integer, ForeignKey("app_user.user_id", ondelete="CASCADE"),
                     nullable=False, comment='계정')
    created_at = Column(DateTime, comment='발급 시각')
    expires_at = Column(DateTime, nullable=False, comment='만료 시각')


class Part(Base):
    __tablename__ = "part"
    __table_args__ = {'comment': '가공 대상 부품 마스터 정보'}

    # 이름을 PK로 쓰면 한글/공백/이름 변경이 그대로 키에 박히고 자식 테이블까지 전파되므로,
    # 키는 의미 없는 일련번호(1부터 증가)로 두고 사람이 읽는 이름은 part_name 속성으로 분리한다.
    part_code = Column(Integer, primary_key=True, autoincrement=True, comment='부품 고유 번호 (1부터 자동 증가)')
    part_name = Column(String(100), nullable=False, unique=True, comment='부품 이름 (XML: PartCode / 폴더명)')
    project_code = Column(String(50), comment='XML: ProjectCode')
    material_code = Column(String(50), comment='XML: MaterialCode')

    workplans = relationship("Workplan", back_populates="part", cascade="all, delete-orphan")


class Workplan(Base):
    __tablename__ = "workplan"
    __table_args__ = (
        # 기존에 문자열 PK로 표현하던 "부품 + 프로그램 + NC 해시" 조합은 유일 제약으로 옮겨,
        # 중복 차단 기능은 그대로 두고 PK는 숫자 대리키로 유지한다.
        UniqueConstraint('part_code', 'program_code', 'nc_hash', name='uq_workplan_identity'),
        {'comment': 'ISO14649 Workplan: 단일 NC 프로그램(코드) 단위의 정적 계획'},
    )

    workplan_id = Column(Integer, primary_key=True, autoincrement=True, comment='Workplan 고유 번호 (1부터 자동 증가)')
    part_code = Column(Integer, ForeignKey("part.part_code", ondelete="CASCADE"), nullable=False)
    program_code = Column(String(50), comment='XML: ProgramCode / ProgramName')
    nc_hash = Column(String(32), comment='NC 원본 내용 MD5 앞 8자리 (같은 이름·다른 내용 구분용)')
    # NC 원본 경로는 workplan_file_archive.nc_raw_path 로 옮겼다.
    # 보관소 사본은 원본과 같은 자리에 있다 (backend/vault_layout.py).
    # nc_hash 는 경로가 아니라 uq_workplan_identity 를 이루는 신원 값이라 여기 남는다.

    part = relationship("Part", back_populates="workplans")
    workingsteps = relationship("Workingstep", back_populates="workplan", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="workplan", cascade="all, delete-orphan")
    file_archive = relationship("WorkplanFileArchive", back_populates="workplan",
                                cascade="all, delete-orphan", uselist=False)

class Tool(Base):
    """공구 마스터 (ISO 14649 기반) — 매거진에 장착된 공구와 미장착 재고를 함께 담는다.

    `tool_code` 는 매거진 포트 번호라 장착된 공구에만 있고, 미장착 재고는 NULL 이다.
    (UNIQUE 는 NULL 을 여러 개 허용한다.) 장착된 공구는 NC/XML 이 T번호로 부르므로 분류·직경·
    날 수가 반드시 있어야 하고, 이를 CHECK 로 강제한다. 미장착 재고에는 시트에 '?' 로만 적힌
    행이나 인서트처럼 그 셋이 없는 것이 섞여 있어 강제하지 않는다.

    DDL 은 docs/tool_iso14649_schema.sql 과 같고(verify_tool_csv.py 가 대조한다), 데이터 원본은
    docs/tool_iso14649.csv 다. 공구 마스터는 tool_inserter.py 만 교체한다.
    """
    __tablename__ = "tool"
    __table_args__ = (
        CheckConstraint(
            "tool_code IS NULL OR (iso14649_class IS NOT NULL AND cutter_diameter IS NOT NULL "
            "AND number_of_teeth IS NOT NULL)",
            name="chk_tool_mounted_complete"),
        {'comment': '공구 마스터 (ISO 14649 기반, 매거진 장착 공구 + 미장착 재고)'},
    )

    # [1] 식별 · 제조사
    tool_id = Column(Integer, primary_key=True, autoincrement=True)
    # 표준 표기는 두 자리 0 채움 (T01 · T06 · T29). 규칙은 backend/tool_codes.py 한 곳.
    # 매칭은 이 문자열이 아니라 **번호**로 한다 (NC 는 T6 으로 적기 때문).
    tool_code = Column(String(20), unique=True, comment='매거진 포트 T번호 (T01·T06). 미장착 재고는 NULL')
    manufacturer = Column(String(50), comment='제조사명')
    model_code = Column(String(100), comment='제조사 카탈로그 형번')
    # [2] ISO 14649 분류
    iso14649_class = Column(String(50), comment='ISO 14649-111 공구 엔티티 (ENDMILL, TWIST_DRILL ...)')
    hand_of_cut = Column(String(10), server_default=text("'RIGHT'"), comment='절삭 방향 (RIGHT/LEFT/NEUTRAL)')
    # [3] 공통 치수 (mm)
    cutter_diameter = Column(Numeric(6, 3), comment='직경 Dc')
    shank_diameter = Column(Numeric(6, 3), comment='생크 직경 Ds / Dcon')
    cutting_edge_length = Column(Numeric(6, 2), comment='절삭날 길이 APMX / Lc')
    overall_length = Column(Numeric(6, 2), comment='전체 길이 OAL')
    usable_length = Column(Numeric(6, 2), comment='유효장 LU')
    # [4] 공구 종류별 치수
    corner_radius = Column(Numeric(5, 3), server_default=text("'0.000'"), comment='코너 R / 볼 반경 RE')
    point_angle = Column(Numeric(5, 2), comment='드릴 선단각 (°)')
    approach_angle = Column(Numeric(5, 2), comment='진입각 KAPR (°)')
    helix_angle = Column(Numeric(4, 2), comment='비틀림각 (°)')
    # [5] 절삭날 · 재질
    number_of_teeth = Column(Integer, server_default=text("'1'"), comment='날 수 Z')
    tool_material = Column(String(30), comment='모재 재질 (CARBIDE, HSS ...)')
    coating_type = Column(String(50), comment='코팅 (TiAlN, DLC, UNCOATED ...)')
    coolant_through = Column(Boolean, server_default=text("'0'"), comment='내부 급유 지원')
    # [6] 재고 · 세팅
    stock_count = Column(Integer, server_default=text("'0'"), comment='재고 수량')
    gauge_length = Column(Numeric(6, 3), comment='홀더 장착 후 게이지 라인 길이 (mm)')
    memo = Column(Text, comment='비고')
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"), comment='등록 시각')
    updated_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
                        comment='수정 시각')

    workingsteps = relationship("Workingstep", back_populates="tool")


class Workingstep(Base):
    __tablename__ = "workingstep"
    __table_args__ = {'comment': 'ISO14649 Workingstep: Workplan 내의 개별 가공 단위(공구 호출 순서)'}

    step_id = Column(Integer, primary_key=True, autoincrement=True)
    workplan_id = Column(Integer, ForeignKey("workplan.workplan_id", ondelete="CASCADE"), nullable=False)
    tool_id = Column(Integer, ForeignKey("tool.tool_id", ondelete="SET NULL"), comment='연결된 공구 ID')
    operation_type = Column(String(50), comment='가공방식 (MachiningOperation 병합)')
    
    step_order = Column(Integer, nullable=False, comment='공구 호출 순서 (Index)')
    tool_number = Column(Integer, nullable=False, comment='호출된 공구 번호 (예: 11, 15, 16, 17)')
    # 표준 표기로 적는다 (NC/XML 이 T6 으로 적어도 T06 으로 기록).
    # 공구 마스터와의 연결은 아래 tool_number(정수)로 한다.
    xml_tool_code = Column(String(50), comment='호출 공구 표준 표기 (T06 형태)')
    feed_rate = Column(Float, comment='가공 이송속도 (mm/min, NC코드 파싱)')
    spindle_speed = Column(Float, comment='스핀들 주축 회전수 (RPM, NC코드 파싱)')

    workplan = relationship("Workplan", back_populates="workingsteps")
    tool = relationship("Tool", back_populates="workingsteps")


class Job(Base):
    __tablename__ = "job"
    __table_args__ = {'comment': 'Workplan을 바탕으로 수행된 실제 1회성 가공 이력'}

    job_id = Column(Integer, primary_key=True, autoincrement=True, comment='가공 이력 고유 ID')
    source_folder = Column(String(255), unique=True, comment='기존 폴더명 기반의 원본 식별자')
    workplan_id = Column(Integer, ForeignKey("workplan.workplan_id", ondelete="CASCADE"), nullable=False)
    work_id = Column(String(50), nullable=False, comment='XML: WorkID')
    
    machine_code = Column(String(50), comment='XML: MachineCode')
    machine_ip = Column(String(20), comment='XML: MachineIpAddress')
    
    start_time = Column(DateTime, comment='XML: StartTime')
    end_time = Column(DateTime, comment='XML: FinishTime')
    
    cutting_seconds = Column(Double, comment='XML: CuttingSeconds')
    moving_distance = Column(Double, comment='XML: MovingDistance')
    cutting_moving_distance = Column(Double, comment='XML: CuttingMovingDistance')
    
    is_finish = Column(Boolean, comment='XML: IsFinish')
    is_error = Column(Boolean, comment='XML: IsError')
    
    machining_type = Column(String(100), nullable=True)   # 가공 종류 (수기 입력)

    # 프로젝트명·부품명은 part.project_code / part.part_name 하나만 쓴다.
    # 예전에는 Job 마다 덮어쓸 수 있는 사본(research_project / custom_part_name)을 따로 들고
    # 조회할 때마다 COALESCE 로 둘을 합쳤는데, 같은 사실이 두 곳에 적히니 어느 쪽이 맞는지
    # 알 수 없었다. 이제 Workplan -> Part 를 조인해 한 곳에서만 읽는다.

    # 파일 경로는 전부 job_file_archive 로 옮겼다.
    # (tdms_raw_path / tdms_parquet_raw_path / tdms_fft_parquet_raw_path 와 각 *_vault_path)
    # machining_window 는 경로가 아니라 판별 결과(JSON)라 여기 남는다.
    machining_window = Column(JSON, comment='TDMS 실가공 구간 판별 결과 (구간/근거/DAQ 시계 보정값)')
    
    tool_conditions = Column(JSON, comment='런타임 공구 상태 (사용횟수, 오프셋, 마모도 등)')

    workplan = relationship("Workplan", back_populates="jobs")
    machine_log = relationship("MachineLog", back_populates="job", cascade="all, delete-orphan", uselist=False)
    inspection = relationship("Inspection", back_populates="job", cascade="all, delete-orphan", uselist=False)
    env_memo = relationship("EnvMemo", back_populates="job", cascade="all, delete-orphan", uselist=False)
    surface_roughnesses = relationship("SurfaceRoughness", back_populates="job", cascade="all, delete-orphan")
    # 파일 경로는 전부 이쪽에 있다. job.file_archive.tdms_raw_path 처럼 읽는다.
    file_archive = relationship("JobFileArchive", back_populates="job",
                                cascade="all, delete-orphan", uselist=False)


class MachineLog(Base):
    __tablename__ = "machine_log"
    __table_args__ = {'comment': '가공 단위 장비 알람 및 로그 요약 테이블 (Job과 1:1)'}

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False, unique=True)
    max_spindle_load = Column(Float, comment='최대 스핀들 부하')
    max_spindle_rpm = Column(Float, comment='최대 스핀들 RPM')
    max_feed_rate = Column(Float, comment='최대 이송 속도(Feed Rate)')
    avg_spindle_rpm = Column(Float, comment='평균 스핀들 RPM')
    alarm_count = Column(Integer, comment='알람 발생 횟수')
    critical_alarm_msg = Column(Text, comment='주요 알람 메시지 (존재 시)')

    job = relationship("Job", back_populates="machine_log")
    file_archive = relationship("LogFileArchive", back_populates="machine_log",
                                cascade="all, delete-orphan", uselist=False)


class SurfaceRoughness(Base):
    __tablename__ = "surface_roughness"
    __table_args__ = {'comment': '표면조도 측정 결과 (1:N 측정 가능)'}
    
    roughness_id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False)
    measure_name = Column(String(100), comment='측정 항목 또는 부위 이름 (예: Roughness_1)')
    
    ra = Column(Float, comment='산술평균거칠기 (Ra, μm)')
    rq = Column(Float, comment='제곱평균제곱근거칠기 (Rq, μm)')
    rz = Column(Float, comment='십점평균거칠기 (Rz, μm)')
    
    # 평가곡선 Parquet 경로는 surface_roughness_archive 로 옮겼다
    # (profile_parquet_raw_path). 보관소 자리는 vault_layout 로 계산한다.

    job = relationship("Job", back_populates="surface_roughnesses")
    file_archive = relationship("SurfaceRoughnessArchive", back_populates="surface_roughness",
                                cascade="all, delete-orphan", uselist=False)


class Inspection(Base):
    __tablename__ = "inspection"
    __table_args__ = {'comment': '가공 완료 후 품질 측정 테이블'}

    inspection_id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False, unique=True)
    dimension_tolerance = Column(String(100), comment='치수 및 공차')
    surface_roughness_ra = Column(Float, comment='표면조도 Ra')
    surface_roughness_rz = Column(Float, comment='표면조도 Rz')
    pass_fail = Column(String(10), comment='합불 판정 (PASS/FAIL)')

    job = relationship("Job", back_populates="inspection")


class EnvMemo(Base):
    __tablename__ = "env_memo"
    __table_args__ = {'comment': '작업자, 환경, 수기 메모 테이블'}

    env_id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False, unique=True)
    worker_name = Column(String(50), comment='작업자')
    temperature = Column(Float, comment='온도')
    humidity = Column(Float, comment='습도')
    # 가공 시작시각(job.start_time)에서 자동으로 채운다 (영어: Monday~Sunday).
    # 파생값이라 사람이 입력하지 않는다 — 아래 _sync_day_of_week 참고.
    day_of_week = Column(String(10), comment='요일 (job.start_time 에서 자동 계산, 영어)')
    chip_shape = Column(String(100), comment='칩 형태')
    abnormal_noise = Column(String(100), comment='이상 소음')
    free_memo = Column(Text, comment='자유 메모')

    job = relationship("Job", back_populates="env_memo")


class WorkplanFileArchive(Base):
    __tablename__ = "workplan_file_archive"
    __table_args__ = {'comment': 'Workplan 관련 대용량 파일 경로 및 원본 아카이브'}
    
    workplan_id = Column(Integer, ForeignKey("workplan.workplan_id", ondelete="CASCADE"), primary_key=True)
    # 예전에는 nc_file_path 한 칸에 xml_parser 가 Vault 경로를, nc_parser 가 원본 폴더 경로를
    # 섞어 넣어서 읽는 쪽이 어느 기준인지 알 수 없었다. 기준별로 칸을 나눈다.
    nc_raw_path = Column(String(1000), comment='NC 원본 파일 경로 (RAW_DATA_ROOT 기준 상대경로)')

    workplan = relationship("Workplan", back_populates="file_archive")
    nc_file_content = Column(LargeBinary(length=(2**32)-1), nullable=True, comment='NC 원본 바이너리 (최대 4GB LONGBLOB)')
    nc_file_sha256 = Column(String(64), nullable=True, comment='nc_file_content 저장 시점의 SHA-256 (복원 검증 기준값)')


class JobFileArchive(Base):
    __tablename__ = "job_file_archive"
    __table_args__ = {'comment': 'Job 관련 대용량 파일 경로 및 원본 아카이브 (XML, TDMS Parquet 등)'}
    
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), primary_key=True)

    # *_raw_path 만 둔다. 장비가 어디에 떨궜는지는 '사실'이라 저장해야 하지만,
    # 보관소 안의 자리는 레코드로부터 계산되는 '규칙'이라 저장하지 않는다.
    # 규칙은 backend/vault_layout.py 한 곳에만 있다.
    xml_file_content = Column(LargeBinary(length=(2**32)-1), nullable=True, comment='XML 원본 바이너리 (최대 4GB LONGBLOB)')
    xml_file_sha256 = Column(String(64), nullable=True, comment='xml_file_content 저장 시점의 SHA-256 (복원 검증 기준값)')

    tdms_raw_path = Column(String(1000), comment='TDMS 원본 파일 경로')
    tdms_parquet_raw_path = Column(String(1000), comment='TDMS Time-domain Parquet 원본 경로')
    tdms_fft_parquet_raw_path = Column(String(1000), comment='TDMS FFT Parquet 원본 경로')

    job = relationship("Job", back_populates="file_archive")

    
class CadFileArchive(Base):
    __tablename__ = "cad_file_archive"
    __table_args__ = {'comment': '가공 대상 도면 원본 파일 아카이브 (STEP, STP, STL)'}
    
    cad_id = Column(Integer, primary_key=True, autoincrement=True)
    part_code = Column(Integer, ForeignKey("part.part_code", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), comment='파일 원본명')
    file_type = Column(String(20), comment='파일 확장자 (step, stp, stl)')
    file_content = Column(LargeBinary(length=(2**32)-1), nullable=True, comment='CAD 원본 바이너리 (15MB 제한)')
    file_sha256 = Column(String(64), nullable=True, comment='file_content 저장 시점의 SHA-256 (복원 검증 기준값)')

    part = relationship("Part")


class SurfaceRoughnessArchive(Base):
    __tablename__ = "surface_roughness_archive"
    __table_args__ = {'comment': '표면조도 측정 결과 파일 경로 아카이브'}
    
    roughness_id = Column(Integer, ForeignKey("surface_roughness.roughness_id", ondelete="CASCADE"), primary_key=True)
    profile_parquet_raw_path = Column(String(1000), nullable=True, comment='평가곡선 Parquet 원본 경로')

    surface_roughness = relationship("SurfaceRoughness", back_populates="file_archive")


class LogFileArchive(Base):
    __tablename__ = "log_file_archive"
    __table_args__ = {'comment': 'MachineLog 관련 파일 경로 아카이브 (로그/CSV)'}
    
    log_id = Column(Integer, ForeignKey("machine_log.log_id", ondelete="CASCADE"), primary_key=True)
    log_raw_path = Column(String(1000), comment='원시 로그/CSV 원본 경로')

    machine_log = relationship("MachineLog", back_populates="file_archive")


# ---------------------------------------------------------------------------
# day_of_week 자동 채움
# ---------------------------------------------------------------------------
# job.start_time 을 채우는 곳이 8군데다 (xml/log/tdms 파서, 복구 backfill,
# tdms_visualizer). 그 자리마다 요일 계산을 끼워 넣으면 언젠가 한 곳이 빠지고, 그러면
# 같은 컬럼에 값이 있는 Job 과 없는 Job 이 섞인다. 그래서 '어디서 채우든' 한 번은 지나가는
# flush 직전에 딱 한 곳에서 맞춘다.
#
# 요일은 날짜로부터 계산되는 파생값이므로 사람이 고칠 값이 아니다. start_time 이 바뀌면
# 요일도 따라 바뀌어야 하니, 비어 있을 때만 채우는 게 아니라 **항상 동기화**한다.
@event.listens_for(Session, "before_flush")
def _sync_day_of_week(session, flush_context, instances):
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Job):
            continue
        wanted = english_day_of_week(obj.start_time)
        if wanted is None:
            continue      # 시작시각을 아직 모르면 나중 flush 에서 다시 본다

        memo = obj.env_memo
        if memo is None:
            # 아직 DB 에 없는 Job 이면 job_id 가 없다. 관계로 이어 두면 SQLAlchemy 가
            # INSERT 순서를 맞춰 주므로 PK 를 몰라도 된다.
            memo = EnvMemo()
            obj.env_memo = memo
            session.add(memo)
        if memo.day_of_week != wanted:
            memo.day_of_week = wanted
