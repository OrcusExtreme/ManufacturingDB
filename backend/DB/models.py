from sqlalchemy import (Column, Integer, String, Float, Double, Boolean, DateTime, JSON, ForeignKey,
                        LargeBinary, Text, UniqueConstraint)
from sqlalchemy.orm import relationship
from .database import Base

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
    nc_file_path = Column(String(255), comment='NC 코드 파일 경로')

    part = relationship("Part", back_populates="workplans")
    workingsteps = relationship("Workingstep", back_populates="workplan", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="workplan", cascade="all, delete-orphan")

class Tool(Base):
    __tablename__ = "tool"
    __table_args__ = {'comment': '공구 마스터 정보 테이블 (Excel에서 불러오기)'}

    tool_id = Column(Integer, primary_key=True, autoincrement=True)
    tool_code = Column(String(50), nullable=False, comment='공구 세트 번호 (예: T4, T29)')
    company_name = Column(String(50), comment='제조사명')
    tool_type = Column(String(50), comment='공구 종류')
    cutter_diameter = Column(Double, comment='직경 (숫자값)')
    specification = Column(String(100), comment='규격')
    tool_teeth = Column(Integer, comment='날수')
    stock_count = Column(Integer, comment='재고 개수')
    memo = Column(String(255), comment='비고')

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
    xml_tool_code = Column(String(50), comment='XML: toolCode / toolName')
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
    
    research_project = Column(String(100), nullable=True) # 연구 프로젝트 명 (수기 입력)
    machining_type = Column(String(100), nullable=True)   # 가공 종류 (수기 입력)
    custom_part_name = Column(String(100), nullable=True) # 사용자 지정 Part 명 (수기 입력)
    
    tdms_file_path = Column(String(500), nullable=True) # 매핑된 TDMS 파일 절대 경로
    log_file_path = Column(String(500), nullable=True)  # 매핑된 Log 파일 절대 경로
    tdms_parquet_path = Column(String(500), nullable=True) # TDMS Time-domain Parquet 절대 경로
    tdms_fft_parquet_path = Column(String(500), nullable=True) # TDMS Frequency-domain(FFT) Parquet 절대 경로
    machining_window = Column(JSON, comment='TDMS 실가공 구간 판별 결과 (구간/근거/DAQ 시계 보정값)')
    
    tool_conditions = Column(JSON, comment='런타임 공구 상태 (사용횟수, 오프셋, 마모도 등)')

    workplan = relationship("Workplan", back_populates="jobs")
    machine_log = relationship("MachineLog", back_populates="job", cascade="all, delete-orphan", uselist=False)
    inspection = relationship("Inspection", back_populates="job", cascade="all, delete-orphan", uselist=False)
    env_memo = relationship("EnvMemo", back_populates="job", cascade="all, delete-orphan", uselist=False)
    surface_roughnesses = relationship("SurfaceRoughness", back_populates="job", cascade="all, delete-orphan")


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


class SurfaceRoughness(Base):
    __tablename__ = "surface_roughness"
    __table_args__ = {'comment': '표면조도 측정 결과 (1:N 측정 가능)'}
    
    roughness_id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False)
    measure_name = Column(String(100), comment='측정 항목 또는 부위 이름 (예: Roughness_1)')
    
    ra = Column(Float, comment='산술평균거칠기 (Ra, μm)')
    rq = Column(Float, comment='제곱평균제곱근거칠기 (Rq, μm)')
    rz = Column(Float, comment='십점평균거칠기 (Rz, μm)')
    
    profile_parquet_path = Column(String(500), nullable=True, comment='평가곡선 Parquet 파일 경로')
    
    job = relationship("Job", back_populates="surface_roughnesses")


class Inspection(Base):
    __tablename__ = "inspection"
    __table_args__ = {'comment': '가공 완료 후 품질 측정 테이블'}

    inspection_id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False, unique=True)
    dimension_tolerance = Column(String(100), comment='치수 및 공차')
    surface_roughness_ra = Column(Float, comment='표면조도 Ra')
    surface_roughness_rz = Column(Float, comment='표면조도 Rz')
    shape_accuracy = Column(String(100), comment='형상정밀도')
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
    day_of_week = Column(String(10), comment='요일')
    chip_shape = Column(String(100), comment='칩 형태')
    abnormal_noise = Column(String(100), comment='이상 소음')
    free_memo = Column(Text, comment='자유 메모')

    job = relationship("Job", back_populates="env_memo")


class WorkplanFileArchive(Base):
    __tablename__ = "workplan_file_archive"
    __table_args__ = {'comment': 'Workplan 관련 대용량 파일 경로 및 원본 아카이브'}
    
    workplan_id = Column(Integer, ForeignKey("workplan.workplan_id", ondelete="CASCADE"), primary_key=True)
    nc_file_path = Column(String(1000), comment='NC 원본 파일 Vault 경로')
    nc_file_content = Column(LargeBinary(length=(2**32)-1), nullable=True, comment='NC 원본 바이너리 (최대 4GB LONGBLOB)')
    nc_file_sha256 = Column(String(64), nullable=True, comment='nc_file_content 저장 시점의 SHA-256 (복원 검증 기준값)')


class JobFileArchive(Base):
    __tablename__ = "job_file_archive"
    __table_args__ = {'comment': 'Job 관련 대용량 파일 경로 및 원본 아카이브 (XML, TDMS Parquet 등)'}
    
    job_id = Column(Integer, ForeignKey("job.job_id", ondelete="CASCADE"), primary_key=True)
    xml_file_path = Column(String(1000), comment='XML 원본 파일 Vault 경로')
    xml_file_content = Column(LargeBinary(length=(2**32)-1), nullable=True, comment='XML 원본 바이너리 (최대 4GB LONGBLOB)')
    xml_file_sha256 = Column(String(64), nullable=True, comment='xml_file_content 저장 시점의 SHA-256 (복원 검증 기준값)')
    tdms_parquet_file_path = Column(String(1000), comment='TDMS Time-domain Parquet 원본 파일 Vault 경로')
    tdms_fft_parquet_file_path = Column(String(1000), comment='TDMS Frequency-domain(FFT) Parquet 원본 파일 Vault 경로')
    
class CadFileArchive(Base):
    __tablename__ = "cad_file_archive"
    __table_args__ = {'comment': '가공 대상 도면 원본 파일 아카이브 (STEP, STP, STL)'}
    
    cad_id = Column(Integer, primary_key=True, autoincrement=True)
    part_code = Column(Integer, ForeignKey("part.part_code", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), comment='파일 원본명')
    file_type = Column(String(20), comment='파일 확장자 (step, stp, stl)')
    file_path = Column(String(1000), comment='Vault 경로')
    file_content = Column(LargeBinary(length=(2**32)-1), nullable=True, comment='CAD 원본 바이너리 (15MB 제한)')
    file_sha256 = Column(String(64), nullable=True, comment='file_content 저장 시점의 SHA-256 (복원 검증 기준값)')

    part = relationship("Part")


class SurfaceRoughnessArchive(Base):
    __tablename__ = "surface_roughness_archive"
    __table_args__ = {'comment': '표면조도 측정 결과 파일 경로 아카이브'}
    
    roughness_id = Column(Integer, ForeignKey("surface_roughness.roughness_id", ondelete="CASCADE"), primary_key=True)
    profile_parquet_file_path = Column(String(1000), nullable=True, comment='평가곡선 원본 파일 Vault 경로')
    stat_csv_file_path = Column(String(1000), nullable=True, comment='통계 CSV 파일 Vault 경로')
    curve_csv_file_path = Column(String(1000), nullable=True, comment='평가곡선 CSV 파일 Vault 경로')


class LogFileArchive(Base):
    __tablename__ = "log_file_archive"
    __table_args__ = {'comment': 'MachineLog 관련 파일 경로 아카이브 (로그/CSV)'}
    
    log_id = Column(Integer, ForeignKey("machine_log.log_id", ondelete="CASCADE"), primary_key=True)
    log_file_path = Column(String(1000), comment='원시 로그/CSV 파일 Vault 경로')
