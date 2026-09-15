import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from DB.database import SessionLocal
from DB.models import Part, Workplan, Workingstep, Job, WorkplanFileArchive

# 한 Workplan 안에서 같은 순서를 두 행이 나눠 가질 수는 없다. 아래 컬럼들은 만든 경로에 따라
# 한쪽에만 값이 있을 수 있어(XML 경로는 tool_id/operation_type, NC 경로는 이송·회전수),
# 중복을 합칠 때 살아남는 행의 빈칸을 나머지 행에서 채워 넣는다.
_MERGEABLE_STEP_FIELDS = ("tool_id", "operation_type", "xml_tool_code",
                          "feed_rate", "spindle_speed")


def _dedupe_workingsteps(db):
    """같은 (workplan_id, step_order) 로 중복 생성된 Workingstep 을 한 행으로 합친다.

    xml_parser 가 Workingstep 을 db.add() 만 해 둔 상태에서 nc_parser 의 동기화 함수를
    부르면, autoflush=False 인 세션에서는 그 행이 조회에 잡히지 않아 같은 스텝이 한 번 더
    만들어졌다. 원인은 고쳤지만(nc_parser 의 db.flush()) 그 전에 들어간 행이 남아 있으면
    계층형 마스터 화면에 같은 스텝이 두 줄로 보이므로 여기서 정리한다.
    """
    merged = 0
    groups = {}
    for step in db.query(Workingstep).order_by(Workingstep.step_id).all():
        groups.setdefault((step.workplan_id, step.step_order), []).append(step)

    for (workplan_id, step_order), steps in groups.items():
        if len(steps) < 2:
            continue
        keeper, extras = steps[0], steps[1:]
        for field in _MERGEABLE_STEP_FIELDS:
            if getattr(keeper, field) is None:
                for extra in extras:
                    value = getattr(extra, field)
                    if value is not None:
                        setattr(keeper, field, value)
                        break
        for extra in extras:
            db.delete(extra)
            merged += 1
        print(f"[정리] Workplan {workplan_id} 의 {step_order}번 스텝이 "
              f"{len(steps)}개로 중복되어 있어 1개로 합쳤습니다.")
    return merged

def _superseded_nohash_workplans(db, used_ids):
    """해시를 모르던 시절 만들어졌다가 실제 해시를 가진 행에 밀려난 Workplan 을 고른다.

    NC 파일이 XML 보다 늦게 도착하면 nc_hash 가 'NOHASH' 인 Workplan 이 먼저 생긴다.
    나중에 NC 가 들어와 해시를 알게 되면 예전에는 새 Workplan 을 따로 만들었고, 먼저 만든
    행과 거기 달린 Workingstep 이 아무 Job 도 참조하지 않는 고아로 남았다.
    (지금은 xml_parser 가 기존 행의 해시를 채워 재사용하므로 새로 생기지는 않는다.)

    NC 만 올려두고 아직 돌리지 않은 정상 계획을 같이 지우지 않도록, '같은 부품·프로그램에
    실제 해시를 가진 형제 행이 있다'는 조건을 만족하는 것만 대상으로 삼는다.
    """
    targets = []
    for wp in db.query(Workplan).filter(Workplan.nc_hash == "NOHASH").all():
        if wp.workplan_id in used_ids:
            continue
        successor = db.query(Workplan).filter(
            Workplan.part_code == wp.part_code,
            Workplan.program_code == wp.program_code,
            Workplan.nc_hash != "NOHASH",
        ).first()
        if successor:
            targets.append((wp, successor.workplan_id))
    return targets


def _contentless_workplans(db, used_ids):
    """아무 내용도 없이 신원만 남은 Workplan 을 고른다.

    파서가 임시 Workplan 에 진짜 program_code / nc_hash 를 채우려다 uq_workplan_identity 에
    걸려 실패하면, 절반만 갱신된 껍데기 행이 남을 수 있었다. (예: program_code 는 실제
    이름인데 nc_hash 는 'NOHASH' 이고 Job·Workingstep·NC 원본이 전부 없는 행)
    program_code 가 'Unknown' 이 아니라 위의 더미 조건에도, NOHASH 승계 조건에도 걸리지 않는다.

    'NC 만 올려두고 아직 돌리지 않은 정상 계획'을 지우지 않도록, Job·Workingstep·NC 경로·
    NC 아카이브가 '모두' 비어 있는 행만 대상으로 삼는다. 그런 행은 담고 있는 정보가 없다.
    """
    targets = []
    for wp in db.query(Workplan).filter(Workplan.nc_hash == "NOHASH").all():
        if wp.workplan_id in used_ids or wp.nc_file_path:
            continue
        if db.query(Workingstep).filter(Workingstep.workplan_id == wp.workplan_id).count() > 0:
            continue
        if db.query(WorkplanFileArchive).filter(
                WorkplanFileArchive.workplan_id == wp.workplan_id).first() is not None:
            continue
        targets.append(wp)
    return targets


def run_cleanup():
    db = SessionLocal()
    try:
        # Find all dummy workplans
        dummy_wps = db.query(Workplan).filter(
            (Workplan.program_code.like("UNKNOWN_WORKPLAN_%")) | (Workplan.program_code == "Unknown")
        ).all()

        deleted_parts = 0

        # 세 가지 규칙이 같은 행을 동시에 집을 수 있어(예: program_code='Unknown' 이면서
        # 내용도 비어 있는 행) 삭제 대상은 id 집합으로 모아 한 번씩만 센다.
        doomed = {}

        for wp in dummy_wps:
            # Check if any job uses it
            job_count = db.query(Job).filter(Job.workplan_id == wp.workplan_id).count()
            if job_count == 0:
                doomed[wp.workplan_id] = wp

        # 해시가 뒤늦게 확보되면서 밀려난 NOHASH Workplan 도 함께 정리한다.
        # (딸린 Workingstep 은 cascade 로 같이 지워진다)
        used_ids = {wp_id for (wp_id,) in db.query(Job.workplan_id).distinct()}
        for wp, successor_id in _superseded_nohash_workplans(db, used_ids):
            if wp.workplan_id not in doomed:
                print(f"[정리] 고아 Workplan {wp.workplan_id} ({wp.program_code}) 삭제 "
                      f"- 해시를 확보한 Workplan {successor_id}로 대체됨")
            doomed[wp.workplan_id] = wp

        # 신원만 남고 내용이 전혀 없는 껍데기 Workplan 도 함께 정리한다.
        for wp in _contentless_workplans(db, used_ids):
            if wp.workplan_id not in doomed:
                print(f"[정리] 빈 Workplan {wp.workplan_id} ({wp.program_code}) 삭제 "
                      f"- Job·Workingstep·NC 원본이 모두 없음")
            doomed[wp.workplan_id] = wp

        for wp in doomed.values():
            db.delete(wp)
        deleted_wps = len(doomed)

        # 고아 Workplan 을 먼저 지운 뒤에 남은 스텝만 보도록 순서를 지킨다.
        db.flush()
        merged_steps = _dedupe_workingsteps(db)

        # Flush so workplans are deleted before parts (due to FK)
        db.flush()

        # Find all dummy parts
        dummy_parts = db.query(Part).filter(Part.part_name.like("UNKNOWN_PART%")).all()
        
        for part in dummy_parts:
            # Check if any workplan uses it
            wp_count = db.query(Workplan).filter(Workplan.part_code == part.part_code).count()
            if wp_count == 0:
                db.delete(part)
                deleted_parts += 1
                
        if deleted_wps > 0 or deleted_parts > 0 or merged_steps > 0:
            db.commit()
            print(f"[알림] 더미 데이터 청소 완료: Workplan {deleted_wps}건, Part {deleted_parts}건 삭제됨, "
                  f"중복 Workingstep {merged_steps}건 병합됨.")
        else:
            print("[알림] 정리할 더미 데이터가 없습니다.")
            
    except Exception as e:
        db.rollback()
        print(f"오류 발생 (Dummy Cleanup): {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_cleanup()
