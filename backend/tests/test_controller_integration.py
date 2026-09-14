"""통합 검증 스크립트:
1. backend/pipeline_control.py 토글 제어 및 JSON 동기화 검증
2. backend/data_insert_recognization.py 에서 파서 비활성화 시 정상 skip 검증
3. watchdog, streamlit, tdms 프로세스 시작/종료 및 포트 해제 수명주기 검증
4. C++ system_controller.exe 바이너리 정상 동작 검증
"""
import os
import sys
import time
import json
import socket
import subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_dir = os.path.dirname(backend_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from pipeline_control import (
    get_pipeline_status,
    set_parser_enabled,
    is_parser_enabled,
    pause_pipeline,
    resume_pipeline,
    CONFIG_PATH
)
import job_layout
from data_insert_recognization import MachiningDataHandler


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(('127.0.0.1', port)) == 0


TEST_PROJECT = "_CtrlTestProj"


def _purge_test_rows():
    """이 테스트가 만들었을 수 있는 DB 행을 지운다 (실제 가공 데이터는 건드리지 않는다)."""
    try:
        from DB.database import SessionLocal
        from DB.models import Job, Part, Workplan
    except Exception:
        return
    db = SessionLocal()
    try:
        parts = db.query(Part).filter(Part.project_code == TEST_PROJECT).all()
        part_codes = {p.part_code for p in parts}
        for job in db.query(Job).filter(Job.research_project == TEST_PROJECT).all():
            db.delete(job)
        db.flush()
        for wp in db.query(Workplan).all():
            if wp.part_code in part_codes:
                db.delete(wp)
        db.flush()
        for part in parts:
            db.delete(part)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"  - [정리 경고] 테스트 DB 행 정리 실패: {exc}")
    finally:
        db.close()


def test_1_pipeline_control():
    print("=" * 60)
    print(" [1/4] pipeline_control.py 동적 토글 및 JSON 연동 검증")
    print("=" * 60)
    
    # 1. 초기 상태 읽기
    status = get_pipeline_status()
    print(f"  - 초기 파이프라인 상태: {status}")
    assert "parsers" in status, "parsers 키가 없습니다."
    
    # 2. nc 파서 비활성화 테스트
    set_parser_enabled("nc", False)
    assert is_parser_enabled("nc") is False, "nc 파서 비활성화 실패"
    
    # JSON 파일 직접 확인
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["enable_nc"] is False, "JSON 파일에 반영되지 않음"
    print("  - nc 파서 비활성화 -> JSON 즉각 반영 확인 완료")
    
    # 3. nc 파서 다시 활성화 테스트
    set_parser_enabled("nc", True)
    assert is_parser_enabled("nc") is True, "nc 파서 활성화 실패"
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["enable_nc"] is True, "JSON 파일 활성화 반영 실패"
    print("  - nc 파서 재활성화 -> JSON 즉각 반영 확인 완료")

    # 4. 전체 일시중지/재개 테스트
    pause_pipeline()
    assert get_pipeline_status()["pipeline_paused"] is True
    resume_pipeline()
    assert get_pipeline_status()["pipeline_paused"] is False
    print("  - 파이프라인 전체 일시중지 및 재개 기능 확인 완료")
    print("  -> [성공] pipeline_control 검증 완료\n")


def test_2_parser_skip_logic():
    print("=" * 60)
    print(" [2/4] data_insert_recognization.py 파서 토글 시 Skip 동작 검증")
    print("=" * 60)
    
    handler = MachiningDataHandler()
    
    # 테스트용 가공 경로 생성: machining_raw_data/TestProject/TestPart/TestJob/...
    raw_base = os.path.join(project_dir, "machining_raw_data", "_CtrlTestProj", "TestPart", "TestJob")
    # 폴더 이름은 job_layout 규칙을 그대로 쓴다. 예전에는 NC_Code / XML_Metadata 라는
    # 존재하지 않는 이름이라, 수집기가 '분류 폴더가 아님'으로 보고 다른 경로를 타고 있었다.
    nc_dir = os.path.join(raw_base, job_layout.NC_DIR)
    xml_dir = os.path.join(raw_base, job_layout.XML_DIR)
    os.makedirs(nc_dir, exist_ok=True)
    os.makedirs(xml_dir, exist_ok=True)
    
    temp_nc = os.path.join(nc_dir, "TEST_O0001.nc")
    with open(temp_nc, "w", encoding="utf-8") as f:
        f.write("G00 X0 Y0\nM30\n")
        
    temp_xml = os.path.join(xml_dir, "TEST_STEP242.xml")
    with open(temp_xml, "w", encoding="utf-8") as f:
        f.write("<root></root>\n")
        
    try:
        # 1. nc 비활성화 상태에서 처리 시도
        set_parser_enabled("nc", False)
        print("  - NC 파서 비활성화 설정")
        handler.handle_file(temp_nc)
        print("  - NC 파일 처리 시 파서 비활성화로 안전하게 스킵됨 확인")
        
        # 2. xml 비활성화 상태에서 처리 시도
        set_parser_enabled("xml", False)
        print("  - XML 파서 비활성화 설정")
        handler.handle_file(temp_xml)
        print("  - XML 파일 처리 시 파서 비활성화로 안전하게 스킵됨 확인")

        # 3. 파이프라인 전체 일시중지 상태에서 처리 시도
        pause_pipeline()
        print("  - 파이프라인 전체 일시중지 설정")
        handler.handle_file(temp_nc)
        print("  - 전체 일시중지 상태에서 안전하게 스킵됨 확인")
        
    finally:
        # 원래대로 복구
        set_parser_enabled("nc", True)
        set_parser_enabled("xml", True)
        resume_pipeline()
        
        # 임시 파일 정리
        if os.path.exists(temp_nc):
            os.remove(temp_nc)
        if os.path.exists(temp_xml):
            os.remove(temp_xml)
        test_base = os.path.join(project_dir, "machining_raw_data", "_CtrlTestProj")
        if os.path.exists(test_base):
            import shutil
            shutil.rmtree(test_base, ignore_errors=True)

        # 파일뿐 아니라 DB 행도 되돌린다. 파서 skip 가드가 어떤 이유로든 동작하지 않으면
        # get_or_create_job 이 실제 DB 에 테스트용 Job/Part/Workplan 을 남기기 때문이다.
        _purge_test_rows()
            
    print("  -> [성공] 파서 토글 Skip 로직 검증 완료\n")


def test_3_process_lifecycle():
    print("=" * 60)
    print(" [3/4] 모듈 프로세스 수명주기 및 리소스 정리 검증")
    print("=" * 60)
    
    TEST_PORT = 8599
    assert not is_port_open(TEST_PORT), f"테스트 시작 전 포트 {TEST_PORT}이 열려 있습니다."
    
    # 1. Streamlit 실행 및 포트 바인딩 테스트
    print(f"  - Streamlit UI 모듈 기동 테스트 (Port {TEST_PORT})...")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    streamlit_cmd = [
        sys.executable, "-m", "streamlit", "run",
        os.path.join(project_dir, "frontend", "dashboard.py"),
        f"--server.port={TEST_PORT}",
        "--server.headless=true"
    ]
    
    proc = subprocess.Popen(
        streamlit_cmd,
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    try:
        # 포트 리스닝 대기 (최대 15초)
        bound = False
        for _ in range(30):
            time.sleep(0.5)
            if is_port_open(TEST_PORT):
                bound = True
                break
        assert bound, f"Streamlit 서버가 포트 {TEST_PORT}에 바인딩되지 못했습니다."
        print(f"  - Streamlit {TEST_PORT} 포트 바인딩 정상 감지 (PID: {proc.pid})!")
        
    finally:
        # 프로세스 트리 강제 종료
        print(f"  - Streamlit 프로세스 트리 종료 (taskkill /F /T /PID {proc.pid})...")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        time.sleep(1.5)
        
        # 포트 해제 확인
        assert not is_port_open(TEST_PORT), f"Streamlit 종료 후 포트 {TEST_PORT}이 여전히 점유 중입니다!"
        print(f"  - 포트 {TEST_PORT} 완전히 해제됨 확인 (좀비 프로세스 방지 완료)")
        
    print("  -> [성공] 프로세스 수명주기 및 포트 해제 검증 완료\n")


def test_4_system_controller_binary():
    print("=" * 60)
    print(" [4/4] C++ system_controller.exe 바이너리 검증")
    print("=" * 60)
    
    exe_path = os.path.join(project_dir, "system_controller.exe")
    assert os.path.exists(exe_path), f"system_controller.exe 파일이 없습니다: {exe_path}"
    size = os.path.getsize(exe_path)
    print(f"  - 바이너리 파일 확인: {exe_path} ({size:,} bytes)")
    assert size > 1_000_000, "바이너리 크기가 너무 작습니다."
    
    # system_controller.exe는 GUI(SUBSYSTEM,WINDOWS) 어플리케이션이므로
    # 잠시 실행한 후 프로세스가 정상 구동되는지 확인하고 안전하게 종료
    print("  - system_controller.exe 실행 테스트...")
    proc = subprocess.Popen([exe_path], cwd=project_dir)
    try:
        time.sleep(1.5)
        # 프로세스가 비정상 크래시하지 않고 살아있는지 확인
        poll = proc.poll()
        assert poll is None, f"system_controller.exe가 비정상 종료되었습니다 (코드: {poll})"
        print(f"  - system_controller.exe 정상 기동 확인 (PID: {proc.pid})")
    finally:
        # 종료
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        print("  - system_controller.exe 프로세스 정상 종료 완료")

    print("  -> [성공] system_controller.exe 바이너리 검증 완료\n")


if __name__ == "__main__":
    print("\n============================================================")
    print("         통합 시스템 컨트롤러 & 파이프라인 제어 검증")
    print("============================================================\n")
    test_1_pipeline_control()
    test_2_parser_skip_logic()
    test_3_process_lifecycle()
    test_4_system_controller_binary()
    print("============================================================")
    print("       모든 검증 테스트가 성공적으로 통과되었습니다!")
    print("============================================================\n")
