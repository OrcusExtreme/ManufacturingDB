import time
import os
import threading
import queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from collections import OrderedDict

import job_layout
from vault_manager import RAW_DATA_ROOT, FAILED_ROOT
from DB.schema_patch import ensure_schema
from parsers.xml_parser import parse_xml
from parsers.tdms_parser import parse_tdms
from parsers.log_parser import parse_log
from parsers.roughness_parser import parse_roughness
from parsers.cad_parser import parse_cad
from parsers.nc_parser import parse_nc
from tool_inserter import parse_and_insert_tools
from pipeline_control import is_parser_enabled, get_config

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
        
    def on_deleted(self, event):
        path_norm = os.path.normpath(event.src_path)
        if path_norm in self.processed_files:
            del self.processed_files[path_norm]
            
        # machining_raw_data 하위 경로 분석
        parts = path_norm.split(os.sep)
        try:
            raw_idx = parts.index("machining_raw_data")
            rel_parts = [p for p in parts[raw_idx + 1:] if p]
        except ValueError:
            return
            
        # 백그라운드 스레드에서 프로젝트/부품/Job/파일 단위 유실 검사 및 자동 복구 수행
        threading.Thread(target=self._auto_restore_by_rel_parts, args=(rel_parts,), daemon=True).start()

    def _auto_restore_by_rel_parts(self, rel_parts):
        time.sleep(1.5)  # 디바운스 대기
        from DB.database import SessionLocal
        from DB.models import Job
        from recovery_engine import restore_single_job_to_raw_data
        
        db = SessionLocal()
        try:
            target_jobs = []
            if len(rel_parts) == 0:
                # machining_raw_data 폴더 자체가 통째로 삭제된 경우 -> 전체 복원
                target_jobs = db.query(Job).all()
            elif len(rel_parts) == 1:
                # 프로젝트 단위 폴더 삭제 (예: TestProject, Alchemist)
                proj = rel_parts[0]
                target_jobs = db.query(Job).filter(
                    (Job.research_project == proj) | (Job.source_folder.like(f"{proj}/%"))
                ).all()
            elif len(rel_parts) == 2:
                # Part 단위 폴더 삭제 (예: Alchemist/Bracket)
                prefix = f"{rel_parts[0]}/{rel_parts[1]}/"
                target_jobs = db.query(Job).filter(Job.source_folder.like(f"{prefix}%")).all()
            else:
                # Job 단위 또는 개별 파일 삭제 (예: Alchemist/Bracket/1 또는 .../metadata.xml)
                if rel_parts[2] == job_layout.CAD_DIR:
                    # Part 레벨 CAD 파일 삭제 감지 시 Part에 속한 Job의 CAD 복원
                    prefix = f"{rel_parts[0]}/{rel_parts[1]}/"
                    target_jobs = db.query(Job).filter(Job.source_folder.like(f"{prefix}%")).all()
                else:
                    sf = f"{rel_parts[0]}/{rel_parts[1]}/{rel_parts[2]}"
                    is_num = rel_parts[2].isdigit()
                    target_jobs = db.query(Job).filter(
                        (Job.source_folder == sf) | 
                        ((Job.job_id == int(rel_parts[2])) if is_num else False)
                    ).all()
                    
            if not target_jobs:
                if len(rel_parts) > 0:
                    deleted_target = '/'.join(rel_parts)
                    print(f"\n[자동 복원 알림] 삭제 감지된 '{deleted_target}'는 DB에 등록된 가공 이력이 없어 복원 대상에서 제외됩니다.")
                return
                
            base_dir = os.path.dirname(os.path.abspath(__file__))
            watch_dir = RAW_DATA_ROOT
            
            for job in target_jobs:
                sf = job.source_folder or f"Job_{job.job_id}"
                dest_dir = os.path.join(watch_dir, *sf.split('/'))
                
                # 폴더가 없거나 파일이 유실된 경우 복원
                if not os.path.exists(dest_dir) or len(os.listdir(dest_dir)) == 0:
                    print(f"\n[자동 복원 트리거] {sf} (Job ID: {job.job_id}) 유실 감지 -> Vault/DB에서 자동 복원을 진행합니다.")
                    dest, count = restore_single_job_to_raw_data(job.job_id)
                    if count > 0:
                        print(f"[자동 복원 완료] {sf}에 총 {count}개의 파일이 복구되었습니다.\n")
                        if os.path.exists(dest_dir):
                            for root_d, _, files in os.walk(dest_dir):
                                for f in files:
                                    self._mark_processed(os.path.normpath(os.path.join(root_d, f)))
        except Exception as e:
            print(f"[자동 복원 에러]: {e}")
        finally:
            db.close()
        
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

        # 이미 사라진 경로에 대한 뒤늦은 이벤트(하위 폴더로 옮겼거나 삭제된 경우)는 바로 넘어간다.
        # 그냥 두면 wait_for_file_ready 가 10초를 기다린 뒤에야 포기해서, 단일 워커 스레드가
        # 그 시간 동안 뒤에 쌓인 진짜 파일들을 처리하지 못한다.
        if not os.path.exists(file_path_norm):
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

        # 계층형 폴더 구조 분석:
        #   machining_raw_data / Project / Part / CAD_Files / 도면
        #   machining_raw_data / Project / Part / JobID / {XML|Log|TDMS|NC|Surface_Roughness|etc} / 파일
        parts = file_path_norm.split(os.sep)
        try:
            raw_idx = parts.index("machining_raw_data")
            rel_parts = parts[raw_idx + 1:] # Project, Part, JobID, ...
        except ValueError:
            return

        if len(rel_parts) < 3:
            # Project/Part/ 파일은 처리 불가
            return

        # Job 폴더 바로 아래 떨어진 파일은 확장자에 맞는 하위 폴더로 옮긴 뒤 처리한다.
        # (장비가 예전 방식으로 한 폴더에 쏟아내도 구조가 저절로 정리된다)
        if len(rel_parts) == 4 and rel_parts[2] != job_layout.CAD_DIR:
            moved = job_layout.relocate_into_subdir(file_path_norm)
            if os.path.normpath(moved) != file_path_norm:
                print(f"[정리] {file_name} -> {os.path.basename(os.path.dirname(moved))}/ 로 이동")
                file_path_norm = os.path.normpath(moved)
                # 이동으로 생기는 watchdog 생성 이벤트가 같은 파일을 두 번 처리하지 않도록 미리 표시
                self._mark_processed(file_path_norm)
                parts = file_path_norm.split(os.sep)
                rel_parts = parts[parts.index("machining_raw_data") + 1:]

        # 하위 폴더 이름은 사람이 손으로 만들면 대소문자가 섞이므로 표준 표기로 맞춰 본다.
        sub_kind = job_layout.canonical_subdir(rel_parts[3]) if len(rel_parts) > 4 else None

        # 폴더가 한 겹 더 감싸인 채로 들어오는 경우가 있다
        # (예: 이미 있는 Job 폴더 위로 같은 이름의 폴더를 통째로 복사해 {Job}/{Job}/TDMS/... 가 됨).
        # 그때도 자료 종류를 알아볼 수 있도록 Job 폴더와 파일 사이의 모든 단계에서 아는 이름을 찾는다.
        # 확장자만으로 가르면 조도 CSV 가 CNC 로그로 넘어가 헤더 검사에 걸려 조용히 버려진다.
        if sub_kind is None and len(rel_parts) > 4:
            for segment in reversed(rel_parts[3:-1]):
                guess = job_layout.canonical_subdir(segment)
                if guess:
                    sub_kind = guess
                    break

        if sub_kind == job_layout.PARQUET_DIR:
            return      # 시스템이 만든 산출물이라 다시 수집할 필요가 없다

        if sub_kind == job_layout.ETC_DIR:
            project_name = rel_parts[0]
            part_name = rel_parts[1]
            folder_level_3 = rel_parts[2]
            current_job_id = f"{project_name}/{part_name}/{folder_level_3}"
            
            print(f"[Vault 백업] etc 참고용 파일을 안전 보관소(Vault)에 백업합니다: {file_path_norm}")
            from job_manager import get_or_create_job
            from DB.database import SessionLocal
            from vault_manager import save_to_vault
            
            db_s = SessionLocal()
            try:
                job = get_or_create_job(db_s, current_job_id)
                save_to_vault(file_path_norm, "etc_files", f"Job_{job.job_id}", file_name)
            except Exception as e_etc:
                print(f"[etc Vault 백업 에러]: {e_etc}")
            finally:
                db_s.close()
            return
            
        project_name = rel_parts[0]
        part_name = rel_parts[1]
        folder_level_3 = rel_parts[2] # JobID 또는 CAD_Files

        is_cad = folder_level_3 == job_layout.CAD_DIR
        is_roughness = sub_kind == job_layout.ROUGHNESS_DIR
        current_job_id = None
        if not is_cad:
            current_job_id = f"{project_name}/{part_name}/{folder_level_3}"

        print(f"[처리] 파일 감지: {file_path_norm}")

        # 어느 하위 폴더에 있느냐로 자료 종류를 정하고, 확장자는 그 안에서 다시 확인한다.
        # (예전처럼 Job 루트에 남아 있는 파일은 sub_kind 가 없으므로 확장자만으로 판단한다)
        if get_config().get("paused", False):
            print(f"  -> [파이프라인 일시중지] 수집이 중지된 상태이므로 처리를 건너뜁니다: {file_name}")
            return

        try:
            if is_cad:
                if file_extension in ['.step', '.stp', '.stl']:
                    if not is_parser_enabled('cad'):
                        print(f"  -> [파서 비활성화] CAD 파서가 꺼져 있어 처리를 건너뜁니다: {file_name}")
                        return
                    self.process_cad(file_path_norm, project_name, part_name)
            elif is_roughness:
                if file_extension in ['.txt', '.csv', '.fpk']:
                    if not is_parser_enabled('roughness'):
                        print(f"  -> [파서 비활성화] 표면 조도 파서가 꺼져 있어 처리를 건너뜁니다: {file_name}")
                        return
                    self.process_roughness(file_path_norm, current_job_id)
            elif sub_kind == job_layout.XML_DIR or (sub_kind is None and file_extension == '.xml'):
                if file_extension == '.xml':
                    if not is_parser_enabled('xml'):
                        print(f"  -> [파서 비활성화] XML 파서가 꺼져 있어 처리를 건너뜁니다: {file_name}")
                        return
                    self.process_xml(file_path_norm, current_job_id)
            elif sub_kind == job_layout.TDMS_DIR or (sub_kind is None and file_extension == '.tdms'):
                if file_extension == '.tdms':
                    if not is_parser_enabled('tdms'):
                        print(f"  -> [파서 비활성화] TDMS 파서가 꺼져 있어 처리를 건너뜁니다: {file_name}")
                        return
                    self.process_tdms(file_path_norm, current_job_id)
            elif sub_kind == job_layout.NC_DIR or (sub_kind is None and file_extension == '.nc'):
                if file_extension == '.nc':
                    if not is_parser_enabled('nc'):
                        print(f"  -> [파서 비활성화] NC 파서가 꺼져 있어 처리를 건너뜁니다: {file_name}")
                        return
                    self.process_nc(file_path_norm, current_job_id)
            elif sub_kind == job_layout.LOG_DIR or (sub_kind is None and file_extension in ['.csv', '.log']):
                if file_extension in ['.csv', '.log']:
                    if not is_parser_enabled('log'):
                        print(f"  -> [파서 비활성화] CNC 로그 파서가 꺼져 있어 처리를 건너뜁니다: {file_name}")
                        return
                    self.process_log(file_path_norm, current_job_id)
        except Exception as e:
            print(f"[오류] 파일 처리 중 에러 발생 ({file_path}): {e}")
            import shutil
            from datetime import datetime
            
            try:
                # DLQ: 파싱에 실패한 파일은 격리 보관소로 옮겨 재처리 루프를 끊는다
                failed_dir = FAILED_ROOT
                os.makedirs(failed_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_filename = os.path.basename(file_path_norm)
                failed_file_name = f"{timestamp}_{original_filename}"
                failed_path = os.path.join(failed_dir, failed_file_name)
                
                shutil.move(file_path_norm, failed_path)
                print(f"[DLQ] 실패한 파일을 격리 폴더로 이동했습니다: {failed_path}")
            except Exception as move_error:
                print(f"[오류] 실패한 파일 이동 중 에러 발생: {move_error}")

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
    # 새 기능으로 늘어난 컬럼이 빠져 있으면 채운 뒤 시작한다 (이미 있으면 아무 것도 하지 않음)
    ensure_schema()

    # 원본 백업 보관소가 어디인지, 실제로 쓸 수 있는지 시작할 때 못박아 둔다.
    # 보관소를 프로젝트 밖(DB 설치 폴더 등)으로 뺄 수 있게 되면서 "백업이 어디에 쌓이는지"가
    # 눈에 안 보이면 안 되는 정보가 됐다. 쓸 수 없는 상태면 파일이 들어오기 전에 알아야 한다.
    from vault_manager import ensure_vault_root
    vault_ok, vault_msg = ensure_vault_root()
    print(f"[알림] {vault_msg}")
    if not vault_ok:
        print("[경고] 원본 백업이 저장되지 않습니다. .env 의 ORCUS_DB_DATA_DIR /"
              " ORCUS_VAULT_ROOT 설정과 폴더 권한을 확인하세요.")

    try:
        from recovery_engine import backfill_missing_job_metadata
        backfill_missing_job_metadata()
    except Exception as b_err:
        print(f"[경고] Job 메타데이터 Backfill 실행 중 오류: {b_err}")

    # 원본 복원 검증은 화면에서 빠지고 여기(백그라운드)에서만 주기적으로 수행된다.
    try:
        from integrity_monitor import start_background_verification
        start_background_verification()
    except Exception as v_err:
        print(f"[경고] 원본 복원 검증 백그라운드 시작 실패: {v_err}")

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
    WATCH_DIRECTORY = RAW_DATA_ROOT
    
    # 테스트를 위해 폴더가 없으면 생성
    if not os.path.exists(WATCH_DIRECTORY):
        os.makedirs(WATCH_DIRECTORY)
        
    start_pipeline(WATCH_DIRECTORY)