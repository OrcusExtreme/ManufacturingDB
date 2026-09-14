"""Job 원본 폴더의 자료 종류별 하위 폴더 규칙 (단일 출처).

수집·복원·업로드·다운로드가 각자 문자열로 폴더 이름을 들고 있으면 한 곳만 바뀌어도 어긋나므로,
폴더 이름과 "어떤 파일이 어디에 속하는가"를 여기 한 곳에서만 정한다.

    machining_raw_data/{Project}/{Part}/
      ├─ CAD_Files/                     ← Part 레벨 (도면은 가공차수와 무관)
      └─ {JobID}/
           ├─ XML/                      ← 장비 메타데이터
           ├─ Log/                      ← CNC 1Hz 상태 로그 (.log/.csv)
           ├─ TDMS/                     ← 고주파 센서 원본
           ├─ NC/                       ← NC 프로그램
           ├─ Surface_Roughness/        ← 조도 측정 결과
           ├─ etc/                      ← 참고용 기타 파일
           └─ processed_parquet/        ← 변환 산출물 (시스템이 생성)

예전에는 XML/Log/TDMS/NC 가 Job 폴더 바로 아래 섞여 있었다. 장비에서 그 방식으로 떨어지는
파일도 계속 들어오므로, 수집기는 Job 루트에서 발견한 파일을 확장자에 맞는 하위 폴더로 옮긴 뒤
처리한다. 읽는 쪽(NC 탐색 등)은 하위 폴더를 먼저 보고 없으면 Job 루트도 보도록 한다.
"""
import os

XML_DIR = "XML"
LOG_DIR = "Log"
TDMS_DIR = "TDMS"
NC_DIR = "NC"
ROUGHNESS_DIR = "Surface_Roughness"
ETC_DIR = "etc"
PARQUET_DIR = "processed_parquet"

# Part 레벨 폴더 (Job 폴더와 같은 깊이에 놓인다)
CAD_DIR = "CAD_Files"

# 확장자 -> 들어가야 할 하위 폴더.
# .csv 는 조도 폴더 안에 있으면 조도 측정값이지만, Job 루트에 떨어진 것은 CNC 로그로 다룬다.
EXTENSION_DIRS = {
    ".xml": XML_DIR,
    ".log": LOG_DIR,
    ".csv": LOG_DIR,
    ".tdms": TDMS_DIR,
    ".tdms_index": TDMS_DIR,
    ".nc": NC_DIR,
}

# Job 폴더 아래에서 '가공차수'가 아니라 자료 분류로 쓰이는 이름들.
# 경로를 해석할 때 이 이름이 나오면 그 아래를 파일이 아니라 분류로 읽어야 한다.
KNOWN_SUBDIRS = {XML_DIR, LOG_DIR, TDMS_DIR, NC_DIR, ROUGHNESS_DIR, ETC_DIR, PARQUET_DIR}

# 폴더 이름 비교는 대소문자를 구분하지 않는다 (사람이 손으로 만들면 nc/Nc 가 섞인다).
_SUBDIR_BY_LOWER = {name.lower(): name for name in KNOWN_SUBDIRS}


def canonical_subdir(name):
    """폴더 이름을 표준 표기로 바꿔 돌려준다. 분류 폴더가 아니면 None."""
    return _SUBDIR_BY_LOWER.get((name or "").lower())


def subdir_for_file(file_name):
    """파일 이름의 확장자에 맞는 하위 폴더 이름. 규칙에 없으면 None."""
    return EXTENSION_DIRS.get(os.path.splitext(file_name)[1].lower())


def job_dir_of(path, kinds=KNOWN_SUBDIRS):
    """분류 폴더 안의 파일 경로에서 Job 폴더 경로를 거슬러 올라간다.

    .../{JobID}/XML/meta.xml -> .../{JobID}
    .../{JobID}/meta.xml     -> .../{JobID}   (예전 구조)
    """
    parent = os.path.dirname(path)
    if os.path.basename(parent) in kinds or canonical_subdir(os.path.basename(parent)) in kinds:
        return os.path.dirname(parent)
    return parent


def subdir_path(job_dir, kind, create=False):
    """Job 폴더 아래 분류 폴더의 절대 경로."""
    target = os.path.join(job_dir, kind)
    if create:
        os.makedirs(target, exist_ok=True)
    return target


def find_files(job_dir, kind, extensions):
    """Job 폴더에서 해당 종류의 파일을 찾는다 (하위 폴더 우선, 없으면 Job 루트).

    예전 구조로 남아 있는 Job 도 읽을 수 있도록 두 곳을 모두 본다.
    돌려주는 목록은 이름순으로 정렬해, 같은 입력이면 항상 같은 파일이 먼저 오게 한다.
    """
    suffixes = tuple(e.lower() for e in extensions)
    found = []
    for base in (os.path.join(job_dir, kind), job_dir):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if os.path.isfile(full) and name.lower().endswith(suffixes):
                found.append(full)
        if found:
            break   # 하위 폴더에서 찾았으면 Job 루트는 보지 않는다
    return found


def relocate_into_subdir(file_path):
    """Job 루트에 떨어진 파일을 확장자에 맞는 하위 폴더로 옮기고 새 경로를 돌려준다.

    옮길 규칙이 없거나 이미 하위 폴더 안이면 원래 경로를 그대로 돌려준다.
    같은 이름이 이미 있으면 그 파일을 최신본으로 교체한다(같은 파일이 다시 떨어진 경우).
    """
    file_name = os.path.basename(file_path)
    kind = subdir_for_file(file_name)
    if not kind:
        return file_path

    job_dir = os.path.dirname(file_path)
    if canonical_subdir(os.path.basename(job_dir)):
        return file_path        # 이미 분류 폴더 안

    dest_dir = subdir_path(job_dir, kind, create=True)
    dest = os.path.join(dest_dir, file_name)
    if os.path.abspath(dest) == os.path.abspath(file_path):
        return file_path

    if os.path.exists(dest):
        os.remove(dest)
    os.replace(file_path, dest)

    # TDMS 는 nptdms 가 만드는 *.tdms_index 가 옆에 붙어 다닌다. 같이 옮겨야 짝이 맞는다.
    if kind == TDMS_DIR:
        sidecar = file_path + "_index"
        if os.path.exists(sidecar):
            sidecar_dest = os.path.join(dest_dir, os.path.basename(sidecar))
            if os.path.exists(sidecar_dest):
                os.remove(sidecar_dest)
            os.replace(sidecar, sidecar_dest)

    return dest
