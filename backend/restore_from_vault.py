"""백업 보관소(archive_vault)의 원본을 프로젝트 data/machining_raw_data 로 되돌린다.

보관소는 원본 트리를 **그대로 비추므로**(backend/vault_layout.py) 복원은 폴더 복사 한 번이다.
파일 이름도 자리도 원본과 같아서, 되돌린 결과가 지우기 직전과 파일명까지 동일하다.

예전에는 이 일이 훨씬 복잡했다. 보관소가 파일을 합성한 이름과 id 기반 폴더에 담았기 때문에
(`jobs/{sf}/metadata.xml`, `workplan_nc/WP{id}.nc`, `tdms_files/Job_{id}/`) 복원하려면
job_id 가 어느 폴더였는지 되짚고 원래 파일명을 복구해야 했다. 그 되짚기가 한 번 어긋나자
Job_1 이 job2 로 복원됐고, 이름을 잃은 `WP2.nc` 가 다시 program_code 로 읽히며 신원이
또 틀어졌다. 배치를 원본과 같게 만들어 그 계층을 통째로 없앴다.

DB 는 보지 않는다. 파일만 제자리에 놓으면 수집기가 다시 읽어 DB 까지 채운다. 그래서 DB 가
통째로 비어 있어도 복원된다.

덮어쓰지 않는다
--------------
이미 있는 파일은 건드리지 않고 없는 것만 채운다. 여러 번 돌려도 결과가 같고, 살아남은
원본을 보관소 사본으로 덮어써 버리는 사고가 나지 않는다.

사용법
------
    python backend/restore_from_vault.py            # 미리보기
    python backend/restore_from_vault.py --apply    # 실제 복원
"""
import os
import shutil
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import job_layout
from vault_manager import RAW_DATA_ROOT, VAULT_ROOT, ensure_vault_root


def plan_restore():
    """보관소를 훑어 (보관소 절대경로, 원본 절대경로, 상태) 목록을 만든다.

    상태: "copy" 되돌릴 대상 · "skip" 이미 있음
    """
    plan = []
    for root, _dirs, files in os.walk(VAULT_ROOT):
        for name in files:
            src = os.path.join(root, name)
            if job_layout.is_ignored_file(src):
                continue
            rel = os.path.relpath(src, VAULT_ROOT)
            dest = os.path.join(RAW_DATA_ROOT, rel)
            plan.append((src, dest, "skip" if os.path.exists(dest) else "copy"))
    plan.sort(key=lambda t: t[1])
    return plan


def main(argv):
    apply = "--apply" in argv
    print("=" * 70)
    print(" 백업 보관소 → data/machining_raw_data 원본 복원")
    print("=" * 70)

    ok, msg = ensure_vault_root()
    print(f"  보관소   : {VAULT_ROOT}")
    print(f"  복원 대상: {RAW_DATA_ROOT}")
    if not ok:
        print(f"  [중단] {msg}")
        return 1

    plan = plan_restore()
    if not plan:
        print("  [중단] 보관소가 비어 있어 되돌릴 원본이 없습니다.")
        return 1

    todo = [p for p in plan if p[2] == "copy"]
    skipped = len(plan) - len(todo)
    print(f"  보관소 파일 {len(plan):,}개 · 되돌릴 파일 {len(todo):,}개 "
          f"· 이미 있어 건너뜀 {skipped:,}개")
    print()

    copied = 0
    for src, dest, _state in todo:
        rel = os.path.relpath(dest, RAW_DATA_ROOT)
        print(f"  {'→' if apply else '~'} {rel}")
        if not apply:
            continue
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        except OSError as e:
            print(f"      [실패] {e}")

    print()
    print("=" * 70)
    if apply:
        print(f" 복원 완료: 파일 {copied:,}개를 raw_data 에 되돌렸습니다.")
        if copied != len(todo):
            print(f" [주의] {len(todo) - copied:,}개는 복사하지 못했습니다. 위 실패 줄을 확인하세요.")
        print(" 수집기가 켜져 있으면 곧 자동으로 다시 읽어 DB 를 채웁니다.")
    else:
        print(f" 미리보기: 파일 {len(todo):,}개가 복원 대상입니다.")
        print(" 실제 복원: python backend/restore_from_vault.py --apply")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
