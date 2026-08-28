import subprocess
import sys
import time
import os

# Windows cmd/powershell cp949 인코딩 오류 방지용 (이모지 출력 지원)
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 실행할 서브프로세스 목록을 저장할 리스트
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
    
    print("🧹 [1/5] 더미 데이터 청소를 실행합니다...")
    cleaner_path = os.path.join(base_dir, "backend", "clean_dummy_data.py")
    subprocess.call([sys.executable, cleaner_path])
    
    print("🚀 [2/5] 데이터 수집 파이프라인(Backend)을 시작합니다...")
    parser_path = os.path.join(base_dir, "backend", "data_insert_recognization.py")
    p1 = subprocess.Popen([sys.executable, parser_path])
    processes.append(p1)
    
    # 파서가 초기화될 시간을 잠깐 줍니다.
    time.sleep(2)
    
    print("🚀 [3/5] 관리자용 대시보드를 시작합니다 (Port 8501)...")
    admin_path = os.path.join(base_dir, "frontend", "admin_dashboard.py")
    p2 = subprocess.Popen([sys.executable, "-m", "streamlit", "run", admin_path, "--server.port", "8501"])
    processes.append(p2)
    
    print("🚀 [4/5] 사용자용 대시보드를 시작합니다 (Port 8502)...")
    user_path = os.path.join(base_dir, "frontend", "user_dashboard.py")
    p3 = subprocess.Popen([sys.executable, "-m", "streamlit", "run", user_path, "--server.port", "8502"])
    processes.append(p3)

    print("🚀 [5/5] TDMS 파켓 변환 백그라운드 서비스를 시작합니다...")
    tdms_viz_path = os.path.join(base_dir, "backend", "tdms_visualizer.py")
    p4 = subprocess.Popen([sys.executable, tdms_viz_path])
    processes.append(p4)

def stop_services():
    print("\n[알림] 모든 시스템 구성 요소 및 백그라운드 프로세스 트리를 종료합니다...")
    try:
        import psutil
    except ImportError:
        print("[경고] psutil 모듈이 설치되어 있지 않아 서브프로세스 기본 종료 방식을 사용합니다.")
        for p in processes:
            p.terminate()
        return

    for p in processes:
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
        
        # 메인 스크립트가 종료되지 않도록 무한 대기
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        # 사용자가 Ctrl+C를 누르면 실행
        stop_services()
    except Exception as e:
        print(f"\n❌ 예기치 않은 오류 발생: {e}")
        stop_services()
