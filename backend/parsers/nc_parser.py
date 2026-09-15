import os
import sys
import re
from collections import Counter

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy.orm import Session
from sqlalchemy import text
from DB.database import get_db
from DB.models import Workingstep, WorkplanFileArchive
from job_manager import get_or_create_job, adopt_workplan_identity


def parse_nc_cutting_conditions(nc_input):
    """
    NC 프로그램 코드(G-code)에서 공구 호출 단위 공정(Workingstep)별로
    호출된 공구 번호(T), 스핀들 주축 회전수(S, RPM), 가공 이송속도(F, mm/min)를 파싱합니다.
    
    compact 포맷(예: S3800M3, G1Z-2F710, M6T6)과 공백 구분 포맷을 모두 지원하며,
    절삭 이송속도(G1, G2, G3 등)를 우선 선별합니다.
    """
    if not nc_input:
        return []

    content = ""
    if os.path.exists(str(nc_input)) and os.path.isfile(str(nc_input)):
        for enc in ['utf-8', 'cp949', 'euc-kr', 'latin1']:
            try:
                with open(nc_input, 'r', encoding=enc) as f:
                    content = f.read()
                    break
            except Exception:
                continue
    elif isinstance(nc_input, bytes):
        for enc in ['utf-8', 'cp949', 'euc-kr', 'latin1']:
            try:
                content = nc_input.decode(enc)
                break
            except Exception:
                continue
    else:
        content = str(nc_input)

    if not content:
        return []

    lines = content.splitlines()
    steps = []
    
    current_tool = None
    current_s = None
    cutting_feeds = []
    all_feeds = []
    
    def save_step():
        nonlocal current_tool, current_s, cutting_feeds, all_feeds
        if current_tool is not None or current_s is not None or cutting_feeds or all_feeds:
            chosen_feed = None
            if cutting_feeds:
                chosen_feed = Counter(cutting_feeds).most_common(1)[0][0]
            elif all_feeds:
                chosen_feed = all_feeds[0]
                
            steps.append({
                'tool_number': current_tool,
                'spindle_speed': current_s,
                'feed_rate': chosen_feed,
            })
            current_tool = None
            current_s = None
            cutting_feeds = []
            all_feeds = []

    for line in lines:
        clean = re.sub(r'\(.*?\)|<.*?>|;.*$', '', line).strip()
        if not clean or clean.startswith('%'):
            continue
            
        has_m6 = bool(re.search(r'(?i)(?<![#A-Z])M0?6(?![0-9])', clean))
        t_match = re.search(r'(?i)(?<![#A-Z])T(\d+)', clean)
        
        if has_m6 and t_match:
            save_step()
            current_tool = int(t_match.group(1))
        elif has_m6 and not t_match:
            pass
        elif t_match and not has_m6:
            if current_tool is None:
                current_tool = int(t_match.group(1))
            elif current_tool != int(t_match.group(1)):
                if current_s is not None or cutting_feeds or all_feeds:
                    save_step()
                    current_tool = int(t_match.group(1))

        # 주축 회전수 S (예: S3800, S3800M3, S900 M3)
        s_match = re.search(r'(?i)(?<![#A-Z])S(\d+(?:\.\d*)?)', clean)
        if s_match:
            current_s = float(s_match.group(1))
            
        # 이송속도 F (예: F710, G1Z-2F710, F710.0)
        f_match = re.search(r'(?i)(?<![#A-Z])F(\d+(?:\.\d*)?)', clean)
        if f_match:
            f_val = float(f_match.group(1))
            all_feeds.append(f_val)
            is_cutting = bool(re.search(r'(?i)(?<![#A-Z])G0?[123](?![0-9])', clean))
            if is_cutting or any(axis in clean.upper() for axis in ['X', 'Y', 'Z']):
                cutting_feeds.append(f_val)

    save_step()
    
    for idx, s in enumerate(steps, start=1):
        s['step_order'] = idx
        s['xml_tool_code'] = f"T{s['tool_number']}" if s['tool_number'] is not None else None
        
    return steps


def sync_workplan_nc_cutting_conditions(db: Session, workplan_id: str, nc_file_path: str = None, nc_content: str = None):
    """
    Workplan에 연동된 NC 코드에서 가공 조건(Feed rate, Spindle speed)을 파싱하여
    해당 workplan의 workingstep 레코드에 반영합니다.
    """
    try:
        content_to_parse = nc_content
        if not content_to_parse:
            if nc_file_path and os.path.exists(nc_file_path):
                content_to_parse = nc_file_path
            else:
                archive = db.query(WorkplanFileArchive).filter(WorkplanFileArchive.workplan_id == workplan_id).first()
                if archive and archive.nc_file_content:
                    content_to_parse = archive.nc_file_content
                elif archive and archive.nc_file_path:
                    # 저장된 경로는 RAW_DATA_ROOT 기준 상대경로라 그대로 열 수 없다.
                    # (get_abs_raw_data_path 는 절대경로가 들어와도 그대로 돌려주므로 옛 데이터도 안전하다)
                    from vault_manager import get_abs_raw_data_path
                    abs_nc = get_abs_raw_data_path(archive.nc_file_path)
                    if abs_nc and os.path.exists(abs_nc):
                        content_to_parse = abs_nc
                    
        if not content_to_parse:
            return False

        parsed_steps = parse_nc_cutting_conditions(content_to_parse)
        if not parsed_steps:
            return False

        # xml_parser 는 Workingstep 을 db.add() 로 세션에 담아두기만 한 채 이 함수를 부른다.
        # 세션이 autoflush=False 라(DB/database.py) flush 없이 조회하면 그 행들이 보이지 않아
        # "스텝이 아직 없다"고 판단하고 아래에서 같은 스텝을 한 번 더 만들어 버린다.
        # 조회 전에 반드시 flush 해서 같은 트랜잭션의 미반영 행까지 보이게 한다.
        db.flush()
        existing_steps = db.query(Workingstep).filter(Workingstep.workplan_id == workplan_id).order_by(Workingstep.step_order).all()

        tool_mapping = {
            code: t_id
            for code, t_id in db.execute(
                text("SELECT tool_code, tool_id FROM tool WHERE tool_code IS NOT NULL")
            )
        }

        if existing_steps:
            # 1. tool_number 기준 매핑 시도
            step_by_tool = {}
            for p in parsed_steps:
                if p['tool_number'] is not None and p['tool_number'] not in step_by_tool:
                    step_by_tool[p['tool_number']] = p

            for ws in existing_steps:
                p = step_by_tool.get(ws.tool_number)
                # 공구 번호 매핑 안 되면 동일 step_order로 fallback
                if not p and ws.step_order <= len(parsed_steps):
                    p = parsed_steps[ws.step_order - 1]
                if p:
                    if p['feed_rate'] is not None:
                        ws.feed_rate = p['feed_rate']
                    if p['spindle_speed'] is not None:
                        ws.spindle_speed = p['spindle_speed']
        else:
            # Workingstep이 아직 없는 경우 NC 기반으로 신규 생성
            for s in parsed_steps:
                t_code = s['xml_tool_code']
                target_tool_id = tool_mapping.get(t_code) if t_code else None
                new_ws = Workingstep(
                    workplan_id=workplan_id,
                    tool_id=target_tool_id,
                    operation_type=None,
                    step_order=s['step_order'],
                    tool_number=s['tool_number'] if s['tool_number'] is not None else 0,
                    xml_tool_code=t_code,
                    feed_rate=s['feed_rate'],
                    spindle_speed=s['spindle_speed']
                )
                db.add(new_ws)

        db.commit()
        return True
    except Exception as e:
        print(f"[NC Parser] 가공 조건 동기화 중 오류 ({workplan_id}): {e}")
        db.rollback()
        return False


def parse_nc(file_path, job_id_str=None):
    """
    단독으로 업로드된 NC 파일을 파싱하여 DB에 반영합니다.
    XML 없이 NC 파일만 업로드된 경우에도 Job과 Workplan을 생성/조회하고 nc_file_path를 업데이트하며,
    Workingstep에 feed_rate 및 spindle_speed를 파싱하여 연동합니다.
    """
    if not job_id_str:
        return False
        
    # 경로가 machining_raw_data/{프로젝트}/{부품}/... 형태인지만 확인한다.
    # (프로젝트·부품 이름 자체는 job_id_str 로 이미 넘어오므로 여기서 쓰지 않는다.
    #  예전에는 parts[raw_idx+2] 를 꺼내 쓰면서 경로가 짧으면 IndexError 로 터졌다.)
    parts = file_path.split(os.sep)
    try:
        raw_idx = parts.index("machining_raw_data")
    except ValueError:
        print(f"[NC Parser] machining_raw_data 아래의 파일이 아닙니다: {file_path}")
        return False
    if len(parts) - raw_idx < 4:
        print(f"[NC Parser] 파일 경로에서 프로젝트/부품 정보를 찾을 수 없습니다: {file_path}")
        return False

    db = next(get_db())
    try:
        # get_or_create_job은 필요시 Part, Workplan, Job을 모두 생성합니다.
        job = get_or_create_job(db, job_id_str)
        if not job:
            print(f"[NC Parser] Job 생성 실패: {job_id_str}")
            return False
            
        # 해당 Job의 Workplan에 nc_file_path 업데이트
        from vault_manager import get_rel_raw_data_path
        if job.workplan:
            nc_rel_for_workplan = get_rel_raw_data_path(file_path)

            # program_code가 없는 경우 NC 파일명을 프로그램 코드로 임시 사용
            fallback_program = None
            if not job.workplan.program_code or job.workplan.program_code == "UNKNOWN":
                fallback_program = os.path.splitext(os.path.basename(file_path))[0]

            prev_workplan_id = job.workplan_id
            adopt_workplan_identity(db, job, program_code=fallback_program,
                                    nc_file_path=nc_rel_for_workplan)
            if job.workplan_id == prev_workplan_id:
                # 합쳐지지 않은 경우에만 경로를 최신 파일로 갱신한다.
                # 기존 Workplan 에 흡수됐다면 먼저 등록된 NC 경로를 그대로 둔다.
                job.workplan.nc_file_path = nc_rel_for_workplan

            # NC 파일 바이너리 데이터 읽기 및 저장
            nc_binary_data = None
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    nc_data = f.read()
                    if len(nc_data) <= 15 * 1024 * 1024:
                        nc_binary_data = nc_data
                    else:
                        print(f"    - [알림] NC 파일이 15MB를 초과하여 DB 내부 저장을 생략합니다.")
            
            # XML 이 NC 보다 먼저 처리되면 Workplan 의 nc_hash 가 'NOHASH' 로 남는다.
            # 이제 NC 내용을 알게 됐으므로 여기서 채워 넣는다. 그러지 않으면 나중에 같은 부품·
            # 프로그램의 XML 이 다시 들어올 때 해시가 다르다고 판단해 Workplan 이 하나 더 생기고,
            # 먼저 만든 쪽이 아무 Job 도 참조하지 않는 고아로 남는다.
            if nc_binary_data and job.workplan.nc_hash in (None, "NOHASH"):
                import hashlib
                real_hash = hashlib.md5(nc_binary_data).hexdigest()[:8]
                # 그냥 대입하면 같은 부품·프로그램의 Workplan 이 이미 있을 때
                # uq_workplan_identity 에 걸려 트랜잭션 전체가 rollback 된다.
                # adopt_workplan_identity 가 충돌 시 기존 Workplan 으로 Job 을 옮겨 준다.
                adopt_workplan_identity(db, job, nc_hash=real_hash,
                                        nc_file_path=nc_rel_for_workplan)
                print(f"    - [NC Parser] Workplan {job.workplan_id} 의 NC 해시를 채웠습니다: {real_hash}")

            from integrity import sha256_bytes
            nc_sha256 = sha256_bytes(nc_binary_data) if nc_binary_data else None

            # 경로는 반드시 RAW_DATA_ROOT 기준 상대경로로 저장한다.
            # 예전에는 이 컬럼만 절대경로였는데, 그러면 프로젝트 폴더를 옮기거나 다른 PC 에서
            # 같은 DB 를 열었을 때 이 한 칸만 깨진다 (다른 경로 컬럼은 전부 상대경로다).
            nc_rel_path = get_rel_raw_data_path(file_path)

            wp_archive = db.query(WorkplanFileArchive).filter(WorkplanFileArchive.workplan_id == job.workplan_id).first()
            if wp_archive:
                wp_archive.nc_file_path = nc_rel_path
                wp_archive.nc_file_content = nc_binary_data
                wp_archive.nc_file_sha256 = nc_sha256
            else:
                wp_archive = WorkplanFileArchive(
                    workplan_id=job.workplan_id,
                    nc_file_path=nc_rel_path,
                    nc_file_content=nc_binary_data,
                    nc_file_sha256=nc_sha256
                )
                db.add(wp_archive)
                
            db.commit()

            # NC 가공 조건 (feed_rate, spindle_speed) 파싱 및 Workingstep 동기화
            sync_workplan_nc_cutting_conditions(db, job.workplan_id, nc_file_path=file_path)

            print(f"[NC Parser] NC 파일 파싱 및 가공조건 반영 완료 (Job PK: {job.job_id}): {file_path}")
            return True
        else:
            print(f"[NC Parser] Workplan이 존재하지 않습니다. (Job PK: {job.job_id})")
            return False
            
    except Exception as e:
        print(f"[NC Parser] 데이터베이스 처리 중 에러 발생: {e}")
        db.rollback()
        return False
    finally:
        db.close()
