"""원본 백업 보관소(archive_vault)를 프로젝트 밖으로 옮긴다.

왜 옮기는가
-----------
보관소가 프로젝트 안(data/archive_vault)에 있으면 프로젝트 폴더를 지우는 순간 원본 백업도
같이 사라진다. 백업의 존재 이유가 "원본이 없어져도 복원할 수 있다"는 것이라, 보관소는
프로젝트와 수명이 다른 곳(예: DB 가 설치된 폴더)에 있어야 한다.

새 위치는 .env 의 ORCUS_DB_DATA_DIR / ORCUS_VAULT_ROOT 로 정하며,
실제 해석은 vault_manager.resolve_vault_root() 한 곳에서만 한다.

DB 는 손댈 필요가 없다
----------------------
DB 에 저장된 Vault 경로는 전부 보관소 루트 기준의 '상대경로'다
(예: tdms_files/Job_1/xxx.tdms). 루트가 어디를 가리키든 그대로 유효하므로
파일만 옮기면 복원 기능이 그대로 동작한다.

안전장치
--------
- 기본은 미리보기(dry-run). 실제로 옮기려면 --apply 를 준다.
- '복사 -> 검증 -> 원본 삭제' 순서로 진행한다. 중간에 실패해도 원본은 남는다.
- 같은 이름이 이미 있으면 크기가 같을 때만 건너뛰고, 다르면 건드리지 않고 보고한다.
- 읽기 전용 속성(WinError 5) 때문에 삭제가 막히면 속성을 풀고 다시 시도한다.

사용법
------
    python backend/migrate_vault_location.py            # 무엇이 옮겨질지 미리보기
    python backend/migrate_vault_location.py --apply    # 실제 이동
"""
import os
import shutil
import stat
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from vault_manager import (DEFAULT_VAULT_ROOT, VAULT_ROOT, VAULT_IS_EXTERNAL,
                           ensure_vault_root)


def _force_remove(func, path, _exc):
    """읽기 전용 속성으로 삭제가 막히면 속성을 풀고 한 번 더 시도한다."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"    [경고] 정리 실패: {path} ({e})")


def _walk_files(root):
    """root 아래 모든 파일을 (상대경로, 절대경로, 크기) 로 돌려준다."""
    out = []
    for cur, _dirs, names in os.walk(root):
        for name in names:
            abs_path = os.path.join(cur, name)
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = -1
            out.append((os.path.relpath(abs_path, root), abs_path, size))
    return out


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,} B"
        n /= 1024.0


def migrate(apply=False):
    src = DEFAULT_VAULT_ROOT
    dst = VAULT_ROOT

    print("=" * 66)
    print(" 원본 백업 보관소 이전")
    print("=" * 66)
    print(f"  이전 위치: {src}")
    print(f"  새 위치  : {dst}")

    if not VAULT_IS_EXTERNAL:
        print("\n[중단] 새 위치가 예전 위치와 같습니다.")
        print("       .env 에 ORCUS_DB_DATA_DIR 또는 ORCUS_VAULT_ROOT 를 설정하세요.")
        return 1

    if not os.path.isdir(src):
        print("\n[알림] 옮길 보관소가 없습니다. 새 위치만 준비합니다.")
        ok, msg = ensure_vault_root()
        print(f"       {msg}")
        return 0 if ok else 1

    ok, msg = ensure_vault_root()
    print(f"\n  {msg}")
    if not ok:
        print("[중단] 새 위치를 쓸 수 없어 이전하지 않습니다.")
        return 1

    items = _walk_files(src)
    if not items:
        print("\n[알림] 이전 보관소가 비어 있습니다. 옮길 파일이 없습니다.")
        return 0

    total_bytes = sum(s for _r, _a, s in items if s > 0)
    print(f"\n  대상: 파일 {len(items):,}개 / {_human(total_bytes)}")

    to_copy, already, conflict = [], [], []
    for rel, abs_src, size in items:
        abs_dst = os.path.join(dst, rel)
        if os.path.exists(abs_dst):
            try:
                same = os.path.getsize(abs_dst) == size
            except OSError:
                same = False
            (already if same else conflict).append((rel, abs_src, abs_dst, size))
        else:
            to_copy.append((rel, abs_src, abs_dst, size))

    print(f"    - 새로 복사    : {len(to_copy):,}개")
    print(f"    - 이미 동일    : {len(already):,}개 (건너뜀)")
    print(f"    - 이름 충돌    : {len(conflict):,}개 (크기가 달라 건드리지 않음)")
    for rel, _s, _d, _z in conflict[:10]:
        print(f"        ! {rel}")

    if not apply:
        print("\n[미리보기] 실제로 옮기려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    print("\n  복사 중...")
    copied = 0
    for rel, abs_src, abs_dst, _size in to_copy:
        os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
        shutil.copy2(abs_src, abs_dst)
        copied += 1
        if copied % 20 == 0 or copied == len(to_copy):
            print(f"    {copied:,}/{len(to_copy):,}")

    # 검증: 옮기기로 한 파일이 모두 같은 크기로 존재하는지 확인한 뒤에만 원본을 지운다.
    print("  검증 중...")
    bad = []
    for rel, abs_src, abs_dst, size in to_copy + already:
        if not os.path.exists(abs_dst):
            bad.append(f"없음: {rel}")
        elif size >= 0 and os.path.getsize(abs_dst) != size:
            bad.append(f"크기 불일치: {rel}")
    if bad:
        print(f"\n[중단] 검증 실패 {len(bad)}건. 원본은 그대로 둡니다.")
        for b in bad[:10]:
            print(f"    {b}")
        return 1
    print(f"    검증 통과: {len(to_copy) + len(already):,}개")

    if conflict:
        print(f"\n[보류] 이름 충돌 {len(conflict)}건이 남아 이전 폴더를 지우지 않습니다.")
        print("       위 목록을 직접 확인한 뒤 이전 폴더를 정리하세요.")
        return 0

    print("  이전 폴더 정리 중...")
    shutil.rmtree(src, onerror=_force_remove)
    if os.path.isdir(src):
        print(f"    [경고] 일부가 남았습니다: {src}")
    else:
        print(f"    정리 완료: {src}")

    print("\n[완료] 보관소 이전이 끝났습니다.")
    print(f"       DB 에는 상대경로만 저장되어 있어 레코드는 손대지 않았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(migrate(apply="--apply" in sys.argv))
