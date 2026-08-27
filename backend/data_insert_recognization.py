import time
import os
import threading
import queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from collections import OrderedDict

from parsers.xml_parser import parse_xml
from parsers.tdms_parser import parse_tdms
from parsers.log_parser import parse_log
from parsers.roughness_parser import parse_roughness
from parsers.cad_parser import parse_cad
from parsers.nc_parser import parse_nc
from tool_inserter import parse_and_insert_tools

class MachiningDataHandler(FileSystemEventHandler):
    def __init__(self, max_cache_size=10000):
        super().__init__()
        self.processed_files = OrderedDict()
        self.max_cache_size = max_cache_size
        self.file_queue = queue.Queue()
        
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()

    def _mark_processed(self, file_path):
        self.processed_files[file_path] = True
        if len(self.processed_files) > self.max_cache_size:
            self.processed_files.popitem(last=False)

    def on_created(self, event):
        if event.is_directory:
            return
        self.file_queue.put(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        file_path_norm = os.path.normpath(event.src_path)
        if file_path_norm in self.processed_files:
            del self.processed_files[file_path_norm]
        self.file_queue.put(event.src_path)
        
    def _process_queue(self):
        while True:
            file_path = self.file_queue.get()
            if file_path is None:
                break
            try:
                self.handle_file(file_path)
            except Exception as e:
                print(f"[오류] Worker 스레드 파일 처리 중 오류 ({file_path}): {e}")
            finally:
                self.file_queue.task_done()

    def wait_for_file_ready(self, file_path, max_retries=20, delay=0.5):
        """
        파일 접근 권한(Lock)이 해제되고, 파일 크기가 일정하게 유지될 때까지 대기합니다.
        네트워크 환경(NAS/SMB)에서 파일 복사가 지연될 때 파싱 오류를 방어합니다.
        """
        previous_size = -1
        for _ in range(max_retries):
            try:
                with open(file_path, 'rb'):
                    pass
                current_size = os.path.getsize(file_path)
                if current_size == previous_size:
                    return True
                previous_size = current_size
            except (PermissionError, IOError, OSError):
                pass
            time.sleep(delay)
        return False

    def handle_file(self, file_path):
        file_path_norm = os.path.normpath(file_path)
        
        if file_path_norm in self.processed_files:
            return
            
        file_extension = os.path.splitext(file_path_norm)[1].lower()
        file_name = os.path.basename(file_path_norm)

        # 공구 정리 엑셀 파일인 경우 별도로 처리하고 종료
        if file_extension == '.xlsx' and '실험실 공구 정리' in file_name:
            if not self.wait_for_file_ready(file_path_norm):
                print(f"[오류] 엑셀 파일이 준비되지 않았습니다: {file_path_norm}")
                return
            
            parse_and_insert_tools(file_path_norm)
            self._mark_processed(file_path_norm)
            return

        # 지원하는 파일 확장자인지 먼저 확인 (desktop.ini 등 불필요한 시스템 파일 무시)
        valid_extensions = {'.step', '.stp', '.stl', '.txt', '.csv', '.fpk', '.xml', '.tdms', '.log', '.nc'}
        if file_extension not in valid_extensions:
            return

        # 일반 가공 파일 네트워크 복사 등으로 인해 지연될 경우를 대비해 완전한 전송 대기
        if not self.wait_for_file_ready(file_path_norm):
            print(f"[오류] 파일 전송이 완료되지 않았거나 접근 권한이 해제되지 않았습니다: {file_path_norm}")
            return

        # 성공적으로 열리고 전송이 끝나면 처리 대상
        self._mark_processed(file_path_norm)

        # 계층형 폴더 구조 분석: machining_raw_data / Project / Part / JobID(또는 CAD_Files) / [Surface_Roughness]
        parts = file_path_norm.split(os.sep)
        try:
            raw_idx = parts.index("machining_raw_data")
            rel_parts = parts[raw_idx + 1:] # Project, Part, JobID, ...
        except ValueError:
            return
            
        if len(rel_parts) < 3:
            # Project/Part/ 파일은 처리 불가
            return
            
        if len(rel_parts) > 3 and rel_parts[3].lower() == "etc":
            print(f"[처리 보류] etc 폴더 내의 참고용 파일은 파싱하지 않습니다: {file_path_norm}")
            return
            
        project_name = rel_parts[0]
        part_name = rel_parts[1]
        folder_level_3 = rel_parts[2] # JobID 또는 CAD_Files
        
        is_roughness = False
        is_cad = False
        
        if folder_level_3 == "CAD_Files":
            is_cad = True
        else:
            if len(rel_parts) > 4 and rel_parts[3] == "Surface_Roughness":
                is_roughness = True
            
            job_id_num = folder_level_3
            current_job_id = f"{project_name}/{part_name}/{job_id_num}"

        print(f"[처리] 파일 감지: {file_path_norm}")

        # 파일 확장자에 따른 분기 처리
        try:
            if is_cad:
                if file_extension in ['.step', '.stp', '.stl']:
                    self.process_cad(file_path_norm, project_name, part_name)
            elif is_roughness:
                if file_extension in ['.txt', '.csv', '.fpk']:
                    self.process_roughness(file_path_norm, current_job_id)
            else:
                if file_extension == '.xml':
                    self.process_xml(file_path_norm, current_job_id)
                elif file_extension == '.tdms':
                    self.process_tdms(file_path_norm, current_job_id)
                elif file_extension == '.nc':
                    self.process_nc(file_path_norm, current_job_id)
                elif file_extension in ['.csv', '.log']:
                    self.process_log(file_path_norm, current_job_id)
        except Exception as e:
            print(f"[오류] 파일 처리 중 에러 발생 ({file_path}): {e}")

    def process_xml(self, file_path, job_id=None):
        parse_xml(file_path, job_id)
        
    def process_tdms(self, file_path, job_id=None):
        parse_tdms(file_path, job_id)

    def process_log(self, file_path, job_id=None):
        parse_log(file_path, job_id)
            
    def process_roughness(self, file_path, job_id=None):
        job_folder_path = os.path.dirname(os.path.dirname(file_path))
        parse_roughness(job_folder_path, job_id=job_id)
            
    def process_cad(self, file_path, project_name, part_name):
        parse_cad(file_path, project_name, part_name)

    def process_nc(self, file_path, job_id=None):
        parse_nc(file_path, job_id)

def start_pipeline(watch_dir):
    event_handler = MachiningDataHandler()
    
    print(f"[알림] 기존 파일 스캔 중... ({watch_dir})")
    
    # 1-pass 스캔: 기존 파일들을 큐에 적재
    for root_dir, dirs, files in os.walk(watch_dir):
        for file in files:
            file_path = os.path.join(root_dir, file)
            event_handler.file_queue.put(file_path)
            
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=True)
    observer.start()
    
    print(f"[시작] [{watch_dir}] 디렉토리 모니터링 시작 (종료: Ctrl+C)")
    
    try:
        while True:
            time.sleep(1) # CPU 점유율을 낮추기 위한 대기
    except KeyboardInterrupt:
        observer.stop()
        print("\n파이프라인을 종료합니다.")
    observer.join()

if __name__ == "__main__":
    # 실제 CNC/DAQ 장비 데이터가 저장되는 공유 폴더 경로 지정 (Project 폴더 내)
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    WATCH_DIRECTORY = os.path.join(os.path.dirname(BASE_DIR), "machining_raw_data")
    
    # 테스트를 위해 폴더가 없으면 생성
    if not os.path.exists(WATCH_DIRECTORY):
        os.makedirs(WATCH_DIRECTORY)
        
    start_pipeline(WATCH_DIRECTORY)