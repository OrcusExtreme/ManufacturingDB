"""옛 보관소 배치(job_id 기반)를 원본 트리 미러 배치로 옮긴다.

옛 배치 -> 새 배치
------------------
    jobs/{source_folder}/metadata.xml        ->  {source_folder}/XML/{원래 파일명}
    workplan_nc/WP{workplan_id}.nc           ->  {source_folder}/NC/{원래 파일명}
    tdms_files/Job_{job_id}/{name}           ->  {source_folder}/TDMS/{name}
    machine_logs/Job_{job_id}/{name}         ->  {source_folder}/Log/{name}
    surface_roughness/Job_{job_id}/{name}    ->  {source_folder}/Surface_Roughness/{name}
                                                 (.parquet 은 processed_parquet/ 로)
    processed_parquet/job_{job_id}_viz.parquet -> {source_folder}/processed_parquet/viz.parquet
    etc_files/Job_{job_id}/{name}            ->  {source_folder}/etc/{name}
    cad_models/Part_{부품명}/{name}           ->  {Project}/{Part}/CAD_Files/{name}
    tool_master/tool_info.xlsx               ->  실험실 공구 정리.xlsx (보관소 루트)

왜 옮기나
---------
옛 배치는 경로에 job_id·workplan_id 를 썼다. 둘 다 대리키(surrogate key)라 재구축·수집순서·
실패에 따라 값이 달라진다. 실제로 auto-increment 가 한 번 밀리자 보관소의 Job_1 이 job2 로
복원됐고, 원래 이름을 잃은 `WP2.nc` 가 다시 program_code 로 읽히며 신원이 또 어긋났다.
원본 경로를 그대로 쓰면 경로에 id 가 없어 그런 드리프트가 생길 수 없고, 복원이 폴더 복사
한 번으로 끝난다.

원래 파일명 복구
---------------
옛 보관소는 XML 을 'metadata.xml', NC 를 'WP{id}.nc' 로 이름을 바꿔 담았다. 원래 이름은
아카이브 테이블의 *_raw_path 에 남아 있으므로 그것으로 되돌린다. DB 가 비어 있거나 경로가
없으면 보관소 이름을 그대로 두고 그 사실을 보고한다.

안전장치
--------
- 기본은 미리보기(dry-run). 실제 적용은 --apply.
- 복사 -> 검증(크기) -> 옛 트리 삭제 순서. 검증에 실패하면 **아무것도 지우지 않는다.**
- 옮길 곳에 파일이 이미 있으면 덮어쓰지 않고 건너뛴다.
- 실행 전에 컨트롤러에서 '전체 중지'를 하세요. 수집기가 켜져 있으면 파일 이동을 데이터
  손실로 보고 자동 복원을 겁니다.

사용법
------
    python backend/migrate_vault_to_mirror_layout.py
    python backend/migrate_vault_to_mirror_layout.py --apply
"""
import os
import shutil
import stat
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import job_layout
from vault_manager import VAULT_ROOT, ensure_vault_root

# 옛 배치의 최상위 폴더들 (이 이름이 보이면 아직 옮기지 않은 것이다)
LEGACY_DIRS = ["jobs", "workplan_nc", "tdms_files", "machine_logs",
               "surface_roughness", "processed_parquet", "etc_files",
               "cad_models", "tool_master"]

TOOL_MASTER_NAME = "실험실 공구 정리.xlsx"


def _force_remove(func, path, _exc):
    """Windows 읽기전용 속성 때문에 지워지지 않는 경우를 위한 재시도."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _basename(p):
    return os.path.basename(str(p).replace("\\", "/")) if p else None


def load_mappings():
    """DB 에서 job_id / workplan_id -> source_folder 와 원래 파일명을 모은다.

    DB 가 비어 있으면 빈 매핑을 돌려준다. 그래도 jobs/ · cad_models/ 처럼 경로에 이름이
    들어 있는 것들은 옮길 수 있다.
    """
    jobs, workplans, parts = {}, {}, {}
    try:
        from DB.database import SessionLocal
        from DB.models import (Job, Part, Workplan, JobFileArchive,
                               WorkplanFileArchive, LogFileArchive)
    except Exception as e:
        print(f"  [알림] DB 모듈을 불러오지 못해 DB 매핑 없이 진행합니다: {e}")
        return jobs, workplans, parts

    db = SessionLocal()
    try:
        for job in db.query(Job).all():
            if not job.source_folder:
                continue
            j_arc = db.query(JobFileArchive).filter_by(job_id=job.job_id).first()
            log_name = None
            if job.machine_log:
                l_arc = db.query(LogFileArchive).filter_by(
                    log_id=job.machine_log.log_id).first()
                log_name = _basename(l_arc.log_raw_path) if l_arc else None
            jobs[job.job_id] = {
                "source_folder": job.source_folder,
                "workplan_id": job.workplan_id,
                "tdms_name": _basename(j_arc.tdms_raw_path) if j_arc else None,
                "log_name": log_name,
            }
            if job.workplan_id:
                w_arc = db.query(WorkplanFileArchive).filter_by(
                    workplan_id=job.workplan_id).first()
                workplans[job.workplan_id] = {
                    "source_folder": job.source_folder,
                    "nc_name": _basename(w_arc.nc_raw_path) if w_arc else None,
                }
        for part in db.query(Part).all():
            parts[str(part.part_name).lower()] = (part.project_code, part.part_name)
    except Exception as e:
        print(f"  [알림] DB 조회 중 오류가 나 DB 매핑 없이 진행합니다: {e}")
    finally:
        db.close()
    return jobs, workplans, parts


def _job_id_from(entry, prefix="Job_"):
    if not entry.startswith(prefix):
        return None
    try:
        return int(entry[len(prefix):])
    except ValueError:
        return None


def plan_moves(jobs, workplans, parts):
    """(옛 절대경로, 새 보관소 상대경로) 목록과, 자리를 정하지 못한 파일 목록."""
    moves, unresolved = [], []

    def vault(*seg):
        return os.path.join(VAULT_ROOT, *seg)

    # 1) jobs/{source_folder}/metadata.xml  ->  {source_folder}/XML/{원래 이름}
    #    같은 폴더의 job_manifest.json 은 새 배치에서 쓸모가 없어 옮기지 않는다(삭제 대상).
    jobs_root = vault("jobs")
    if os.path.isdir(jobs_root):
        xml_name_by_sf = {}
        for jid, info in jobs.items():
            xml_name_by_sf[info["source_folder"].lower()] = None
        for root, _d, files in os.walk(jobs_root):
            for name in files:
                src = os.path.join(root, name)
                sf = os.path.relpath(root, jobs_root).replace("\\", "/")
                if name == "job_manifest.json":
                    continue      # 새 배치에서는 필요 없다
                if name.lower().endswith(".xml"):
                    moves.append((src, f"{sf}/{job_layout.XML_DIR}/{name}"))
                elif os.path.basename(root) == job_layout.ETC_DIR:
                    parent_sf = os.path.relpath(os.path.dirname(root),
                                                jobs_root).replace("\\", "/")
                    moves.append((src, f"{parent_sf}/{job_layout.ETC_DIR}/{name}"))
                else:
                    unresolved.append(src)

    # 2) workplan_nc/WP{id}.nc -> {source_folder}/NC/{원래 이름}
    nc_root = vault("workplan_nc")
    if os.path.isdir(nc_root):
        for name in sorted(os.listdir(nc_root)):
            src = os.path.join(nc_root, name)
            if not os.path.isfile(src):
                continue
            wid = None
            stem = os.path.splitext(name)[0]
            if stem.upper().startswith("WP") and stem[2:].isdigit():
                wid = int(stem[2:])
            info = workplans.get(wid) if wid is not None else None
            if not info:
                unresolved.append(src)
                continue
            out_name = info["nc_name"] or name
            moves.append((src, f"{info['source_folder']}/{job_layout.NC_DIR}/{out_name}"))

    # 3) Job_{id} 로 묶인 분류 폴더들
    keyed = [
        ("tdms_files", job_layout.TDMS_DIR),
        ("machine_logs", job_layout.LOG_DIR),
        ("surface_roughness", job_layout.ROUGHNESS_DIR),
        ("etc_files", job_layout.ETC_DIR),
    ]
    for legacy_dir, kind in keyed:
        base = vault(legacy_dir)
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            folder = os.path.join(base, entry)
            if not os.path.isdir(folder):
                continue
            jid = _job_id_from(entry)
            info = jobs.get(jid) if jid is not None else None
            for name in sorted(os.listdir(folder)):
                src = os.path.join(folder, name)
                if not os.path.isfile(src):
                    continue
                if not info:
                    unresolved.append(src)
                    continue
                # 조도 폴더에는 측정 CSV 와 파생 Parquet 이 섞여 있다. 원본에서는
                # 서로 다른 자리에 살았으므로 확장자를 보고 갈라 보낸다.
                out_kind = (job_layout.PARQUET_DIR
                            if name.lower().endswith(".parquet") and kind == job_layout.ROUGHNESS_DIR
                            else kind)
                out_name = name
                if out_kind == job_layout.PARQUET_DIR and name.startswith("roughness_job_"):
                    # roughness_job_{id}_{측정명}.parquet -> roughness_{측정명}.parquet
                    tail = name[len("roughness_job_"):]
                    if "_" in tail:
                        out_name = "roughness_" + tail.split("_", 1)[1]
                moves.append((src, f"{info['source_folder']}/{out_kind}/{out_name}"))

    # 4) processed_parquet/job_{id}_viz.parquet -> {sf}/processed_parquet/viz.parquet
    pq_root = vault("processed_parquet")
    if os.path.isdir(pq_root):
        for name in sorted(os.listdir(pq_root)):
            src = os.path.join(pq_root, name)
            if not os.path.isfile(src):
                continue
            jid, out_name = None, name
            if name.startswith("job_"):
                parts_ = name[len("job_"):].split("_", 1)
                if parts_[0].isdigit():
                    jid = int(parts_[0])
                    out_name = parts_[1] if len(parts_) > 1 else name
            info = jobs.get(jid) if jid is not None else None
            if not info:
                unresolved.append(src)
                continue
            moves.append((src, f"{info['source_folder']}/{job_layout.PARQUET_DIR}/{out_name}"))

    # 5) cad_models/Part_{부품명} -> {Project}/{Part}/CAD_Files
    cad_root = vault("cad_models")
    if os.path.isdir(cad_root):
        for entry in sorted(os.listdir(cad_root)):
            folder = os.path.join(cad_root, entry)
            if not os.path.isdir(folder) or not entry.startswith("Part_"):
                continue
            part_name = entry[len("Part_"):]
            proj, canonical = parts.get(part_name.lower(), (None, part_name))
            for name in sorted(os.listdir(folder)):
                src = os.path.join(folder, name)
                if not os.path.isfile(src):
                    continue
                if not proj:
                    unresolved.append(src)
                    continue
                moves.append((src, f"{proj}/{canonical}/{job_layout.CAD_DIR}/{name}"))

    # 6) tool_master/tool_info.xlsx -> 보관소 루트 (원본도 루트에 둔다)
    tm_root = vault("tool_master")
    if os.path.isdir(tm_root):
        for name in sorted(os.listdir(tm_root)):
            src = os.path.join(tm_root, name)
            if os.path.isfile(src) and name.lower().endswith(".xlsx"):
                moves.append((src, TOOL_MASTER_NAME))
            elif os.path.isfile(src):
                unresolved.append(src)

    return moves, unresolved


def main(argv):
    apply = "--apply" in argv
    print("=" * 70)
    print(" 보관소 배치 이전: job_id 기반 → 원본 트리 미러")
    print("=" * 70)

    ok, msg = ensure_vault_root()
    print(f"  보관소: {VAULT_ROOT}")
    if not ok:
        print(f"  [중단] {msg}")
        return 1

    present = [d for d in LEGACY_DIRS if os.path.isdir(os.path.join(VAULT_ROOT, d))]
    if not present:
        print("  옛 배치 폴더가 없습니다. 이미 새 구조이거나 보관소가 비어 있습니다.")
        return 0
    print(f"  옛 배치 폴더: {', '.join(present)}")
    print()

    print("[1/4] DB 에서 매핑 확보")
    jobs, workplans, parts = load_mappings()
    print(f"  Job {len(jobs)}건 · Workplan {len(workplans)}건 · Part {len(parts)}건")
    print()

    print("[2/4] 옮길 자리 계산")
    moves, unresolved = plan_moves(jobs, workplans, parts)
    todo, exists = [], 0
    for src, rel in moves:
        dest = os.path.join(VAULT_ROOT, *rel.split("/"))
        if os.path.exists(dest):
            exists += 1
            continue
        todo.append((src, dest, rel))
    for src, _dest, rel in todo:
        print(f"  {'→' if apply else '~'} {os.path.relpath(src, VAULT_ROOT)}")
        print(f"      → {rel}")
    if exists:
        print(f"  (이미 새 자리에 있어 건너뜀 {exists}개)")
    if unresolved:
        print()
        print(f"  [주의] 자리를 정하지 못한 파일 {len(unresolved)}개 (DB 에 해당 Job 이 없음)")
        for p in unresolved[:10]:
            print(f"      {os.path.relpath(p, VAULT_ROOT)}")
        if len(unresolved) > 10:
            print(f"      ... 외 {len(unresolved) - 10}개")
        print("      이 파일들은 **옮기지도 지우지도 않습니다.** 옛 폴더에 그대로 남습니다.")

    if not apply:
        print()
        print("[3/4] 검증 · [4/4] 옛 폴더 삭제는 --apply 에서 수행합니다.")
        print()
        print("실제 적용: python backend/migrate_vault_to_mirror_layout.py --apply")
        return 0

    print()
    print("[3/4] 복사 + 검증")
    copied, failed = 0, []
    for src, dest, rel in todo:
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            if os.path.getsize(dest) != os.path.getsize(src):
                failed.append(rel)
            else:
                copied += 1
        except OSError as e:
            failed.append(f"{rel} ({e})")
    print(f"  복사 {copied}개 · 실패 {len(failed)}개")
    for f in failed:
        print(f"    [실패] {f}")

    if failed:
        print()
        print("[중단] 검증에 실패한 파일이 있어 옛 폴더를 지우지 않았습니다.")
        print("  원인을 고친 뒤 다시 실행하세요. 이미 옮긴 파일은 건너뜁니다.")
        return 1

    print()
    print("[4/4] 옛 폴더 삭제")
    if unresolved:
        print("  자리를 정하지 못한 파일이 있어 옛 폴더를 지우지 않습니다.")
        print("  해당 Job 을 DB 에 복구한 뒤 다시 실행하거나, 파일을 직접 옮기세요.")
        return 1
    for d in present:
        path = os.path.join(VAULT_ROOT, d)
        if not os.path.isdir(path):
            continue
        shutil.rmtree(path, onerror=_force_remove)
        print(f"  - {d}/ 삭제")

    print()
    print("=" * 70)
    print(" 완료: 보관소가 원본 트리와 같은 구조가 되었습니다.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
