"""보관소(Vault) 폴더 이름의 대소문자 표기를 DB 기준으로 통일한다.

왜 어긋났나
-----------
Windows 파일시스템과 MySQL 기본 콜레이션은 둘 다 대소문자를 구분하지 않는다. 그래서 같은
부품을 'DB_Test' 로도 'DB_TEST' 로도 넣을 수 있고, DB 조회는 어느 쪽으로도 같은 행을 찾는다
(중복 Part 는 생기지 않는다). 그런데 Vault 폴더는 'DB 에 저장된 이름'이 아니라 '이번에
들어온 이름'으로 만들어졌고, os.makedirs 는 대소문자만 다른 폴더가 이미 있으면 이름을 바꾸지
않고 그대로 재사용한다. 결과적으로 DB 는 새 표기, 디스크는 옛 표기로 갈렸다.

Windows 에서는 대소문자가 달라도 파일이 열려 당장 문제가 없지만, 보관소를 리눅스/NAS 로
옮기거나 다른 PC 에서 복원하면 대소문자를 구분하는 파일시스템에서 경로가 통째로 어긋난다.

무엇을 하나
-----------
- DB 의 part.part_name / part.project_code 를 '정답'으로 삼는다.
- Vault 의 jobs/{프로젝트}/{부품} 과 cad_models/Part_{부품} 폴더 이름을 그 표기로 맞춘다.
- DB 에 없는 이름의 폴더는 '고아'로 보고하기만 한다. 보관소 파일은 지우지 않는다.

DB 는 손대지 않는다. 저장된 경로는 전부 보관소 루트 기준 상대경로이고, 대소문자만 맞추는
것이라 레코드를 바꿀 필요가 없다.

사용법
------
    python backend/normalize_name_casing.py            # 무엇이 바뀔지 미리보기
    python backend/normalize_name_casing.py --apply    # 실제 이름 변경
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from DB.database import SessionLocal
from DB.models import Part
from vault_manager import VAULT_ROOT


def _rename_to(path, wanted_name, apply, log):
    """path 의 폴더 이름만 wanted_name 으로 바꾼다. 이미 같으면 아무것도 안 한다."""
    parent = os.path.dirname(path)
    current = os.path.basename(path)
    if current == wanted_name:
        return False

    target = os.path.join(parent, wanted_name)
    log.append(f"  {os.path.relpath(path, VAULT_ROOT)}  ->  {wanted_name}")
    if not apply:
        return True

    # 대소문자만 바뀌는 이름 변경은 Windows 에서 곧바로 os.rename 이 먹지 않을 수 있어
    # 임시 이름을 거쳐 두 번에 나눠 바꾼다.
    if current.lower() == wanted_name.lower():
        tmp = os.path.join(parent, f"__casing_tmp_{os.getpid()}")
        os.rename(path, tmp)
        os.rename(tmp, target)
    else:
        os.rename(path, target)
    return True


def normalize(apply=False):
    db = SessionLocal()
    try:
        parts = db.query(Part).all()
        by_lower = {}
        for p in parts:
            if p.part_name:
                by_lower[p.part_name.lower()] = p
        projects = {}
        for p in parts:
            if p.project_code:
                projects[p.project_code.lower()] = p.project_code
    finally:
        db.close()

    print("=" * 66)
    print(" 보관소 폴더 표기 통일")
    print("=" * 66)
    print(f"  보관소: {VAULT_ROOT}")
    print(f"  DB 기준 부품 표기: {', '.join(sorted(p.part_name for p in by_lower.values())) or '(없음)'}")

    changes, orphans = [], []

    # 1) jobs/{프로젝트}/{부품}
    jobs_root = os.path.join(VAULT_ROOT, "jobs")
    if os.path.isdir(jobs_root):
        for proj_name in list(os.listdir(jobs_root)):
            proj_path = os.path.join(jobs_root, proj_name)
            if not os.path.isdir(proj_path):
                continue
            wanted_proj = projects.get(proj_name.lower())
            if wanted_proj:
                if _rename_to(proj_path, wanted_proj, apply, changes):
                    proj_path = os.path.join(jobs_root, wanted_proj)
            else:
                orphans.append(f"jobs/{proj_name}  (DB 에 없는 프로젝트)")

            for part_name in list(os.listdir(proj_path)):
                part_path = os.path.join(proj_path, part_name)
                if not os.path.isdir(part_path):
                    continue
                row = by_lower.get(part_name.lower())
                if row:
                    _rename_to(part_path, row.part_name, apply, changes)
                else:
                    orphans.append(f"jobs/{os.path.basename(proj_path)}/{part_name}  (DB 에 없는 부품)")

    # 2) cad_models/Part_{부품}
    cad_root = os.path.join(VAULT_ROOT, "cad_models")
    if os.path.isdir(cad_root):
        for name in list(os.listdir(cad_root)):
            full = os.path.join(cad_root, name)
            if not os.path.isdir(full) or not name.startswith("Part_"):
                continue
            bare = name[len("Part_"):]
            row = by_lower.get(bare.lower())
            if row:
                _rename_to(full, f"Part_{row.part_name}", apply, changes)
            else:
                orphans.append(f"cad_models/{name}  (DB 에 없는 부품)")

    print(f"\n[표기 변경] {len(changes)}건")
    for line in changes:
        print(line)
    if not changes:
        print("  (이미 DB 표기와 같습니다)")

    if orphans:
        print(f"\n[고아 폴더] {len(orphans)}건 - DB 가 참조하지 않습니다. "
              f"보관소는 지우지 않으므로 직접 확인하세요.")
        for line in sorted(set(orphans)):
            print(f"  {line}")

    if not apply and changes:
        print("\n[미리보기] 실제로 바꾸려면 --apply 를 붙여 다시 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(normalize(apply="--apply" in sys.argv))
