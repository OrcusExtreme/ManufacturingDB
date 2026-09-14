import subprocess
import sys
import time
import os

# Windows cmd/powershell cp949 인코딩 오류 방지용 (이모지 출력 지원)
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 실행할 서브프로세스 목록. 종료 시점뿐 아니라 감시 로그에도 쓰이므로
# (Popen, 화면에 찍을 이름) 쌍으로 담는다.
processes = []

def install_requirements():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_path = os.path.join(base_dir, "requirements.txt")
    if os.path.exists(req_path):
        print("📦 [0/3] 필수 파이썬 패키지를 확인하고 설치합니다 (requirements.txt)...")
        try:
            # 의존성 설치 과정 출력 (조용히 실행 원하면 stdout=subprocess.DEVNULL 추가)
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_path])
            print("✅ 패키지 확인 및 설치 완료!\n")
        except Exception as e:
            print(f"❌ 패키지 설치 중 오류 발생: {e}")
            print("수동으로 'pip install -r requirements.txt'를 실행해 주세요.\n")

def start_services():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("🧹 [1/4] 더미 데이터 청소를 실행합니다...")
    cleaner_path = os.path.join(base_dir, "backend", "clean_dummy_data.py")
    subprocess.call([sys.executable, cleaner_path])

    print("🚀 [2/4] 데이터 수집 파이프라인(Backend)을 시작합니다...")
    parser_path = os.path.join(base_dir, "backend", "data_insert_recognization.py")
    # -u: 파이프라인 로그(자동 복원 알림, 원본 복원 검증 결과 등)가 버퍼에 갇히지 않고 즉시 출력되도록.
    p1 = subprocess.Popen([sys.executable, "-u", parser_path])
    processes.append((p1, "Backend 파이프라인"))
    
    # 파서가 초기화될 시간을 잠깐 줍니다.
    time.sleep(2)
    
    print("🚀 [3/4] 통합 대시보드를 시작합니다 (Port 8501)...")
    dashboard_path = os.path.join(base_dir, "frontend", "dashboard.py")
    p2 = subprocess.Popen([sys.executable, "-m", "streamlit", "run", dashboard_path, "--server.port", "8501"])
    processes.append((p2, "Streamlit 대시보드"))

    print("🚀 [4/4] TDMS 파켓 변환 백그라운드 서비스를 시작합니다...")
    tdms_viz_path = os.path.join(base_dir, "backend", "tdms_visualizer.py")
    p4 = subprocess.Popen([sys.executable, "-u", tdms_viz_path])
    processes.append((p4, "TDMS 변환기"))

def stop_services():
    print("\n[알림] 모든 시스템 구성 요소 및 백그라운드 프로세스 트리를 종료합니다...")
    try:
        import psutil
    except ImportError:
        print("[경고] psutil 모듈이 설치되어 있지 않아 서브프로세스 기본 종료 방식을 사용합니다.")
        for p, _name in processes:
            p.terminate()
        return

    for p, _name in processes:
        try:
            parent = psutil.Process(p.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            parent.terminate()
            
            gone, still_alive = psutil.wait_procs(children + [parent], timeout=3)
            for p_alive in still_alive:
                p_alive.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            print(f"[경고] 프로세스 트리 종료 중 오류 (PID {p.pid}): {e}")
            
    print("[완료] 모든 서비스가 안전하게 종료되었습니다.")

if __name__ == "__main__":
    print("="*60)
    print("   공작기계지능화실험실 통합 파이프라인 관리자   ")
    print("="*60)
    
    try:
        install_requirements()
        start_services()
        print("\n🟢 모든 시스템이 정상적으로 백그라운드에서 구동 중입니다.")
        print("🟢 시스템을 완전히 종료하려면 창을 닫거나 [Ctrl + C]를 누르세요.\n")
        
        # 메인 스크립트가 종료되지 않도록 자식 프로세스 상태를 주기적으로 감시.
        # 한 번 죽은 프로세스는 계속 죽은 상태이므로, 2초마다 같은 줄을 반복해서
        # 찍지 않도록 이미 알린 것은 기억해 두고 처음 한 번만 알린다.
        reported = set()
        while True:
            time.sleep(2)
            for p, name in processes:
                ret = p.poll()
                if ret is not None and p.pid not in reported:
                    reported.add(p.pid)
                    print(f"⚠️ [프로세스 감시] {name} (PID {p.pid})가 예기치 않게 종료되었습니다 (종료 코드: {ret}).")

    except KeyboardInterrupt:
        # 사용자가 Ctrl+C를 누르면 실행
        print("\n[알림] 사용자에 의한 종료 신호(KeyboardInterrupt) 수신.")
        stop_services()
    except Exception as e:
        import traceback
        print(f"\n❌ 예기치 않은 오류 발생: {e}\n{traceback.format_exc()}")
        stop_services()
