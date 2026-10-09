"""배포용 설치 파일(Setup.exe)을 만든다. 개발 PC 에서 실행한다.

    python installer\\build_installer.py           # dist\\Setup_ManufacturingDB_v<버전>.exe
    python installer\\build_installer.py --test    # 시험용: 관리자 권한 없이 설치·제거까지 돌려 볼 수 있는 판

결과물은 build\\installer\\ 아래에 모으고(git 이 추적하지 않는 폴더) 완성된 설치 파일만 dist\\ 에 둔다.

  1. 파이썬 런타임   python.org 공식 배포본(zip)을 받아 풀고 installer\\requirements-runtime.txt 를 설치한다
                     → stage\\runtime. 파이썬 버전과 패키지 목록이 그대로면 다시 만들지 않는다.
  2. MySQL          이 PC 에 설치된 MySQL Server 8.0 에서 서버·클라이언트 실행에 필요한 파일만 골라 담고
                     → stage\\mysql, 담은 mysqld 로 임시 데이터 폴더를 실제로 초기화·기동해 본다.
  3. VC++ 런타임     MySQL 이 쓰는 Microsoft vc_redist.x64.exe → stage\\redist (서명 확인)
  4. 프로젝트 파일    git 이 관리하는 파일(커밋 전 새 파일 포함, .gitignore 대상 제외) → stage\\app.
                     컨트롤러는 소스에서 새로 빌드해 넣는다(프로젝트 루트의 exe 가 실행 중이어도 된다).
  5. 기본 공구 목록   지금 DB 의 공구 마스터를 내보내 installer\\seed\\공구 마스터.csv 로 넣는다.
  6. Inno Setup     installer\\ManufacturingDB.iss 를 컴파일한다 → dist\\

필요한 것: Inno Setup 6 (ISCC.exe), MySQL Server 8.0 설치본, g++(w64devkit), 인터넷(처음 한 번).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT / "installer"
BUILD = PROJECT / "build" / "installer"
CACHE = BUILD / "cache"
STAGE = BUILD / "stage"
DIST = PROJECT / "dist"

# 컨트롤러가 지금 쓰는 파이썬과 같은 3.14. 판을 올릴 때는 python.org/ftp/python/index-windows.json 의
# pythoncore-3.14-64 항목에서 url 과 sha256 을 함께 옮겨 적는다.
PYTHON_VERSION = "3.14.8"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.zip"
PYTHON_SHA256 = "4873947a8afc037846b180312b83c744a4146a851cfd316a75c3125a4d8299da"
RUNTIME_LAYOUT_VERSION = "1"        # 런타임을 다듬는 규칙(아래 prune)을 바꾸면 올린다 → 다시 만든다

# Microsoft 가 안내하는 '지원되는 최신 판' 고정 링크 (2015~2026 공용 v14 런타임).
# 받은 판보다 새 런타임이 이미 있는 PC 에서는 설치를 건너뛴다 (ManufacturingDB.iss 의 VCRedistReady).
VC_REDIST_URL = "https://aka.ms/vc14/vc_redist.x64.exe"

DEFAULT_MYSQL_HOME = Path(r"C:\Program Files\MySQL\MySQL Server 8.0")
# 담을 프로그램. 이들이 쓰는 bin\ 의 DLL 은 실행 파일의 import 표를 따라가며 빌드 때 모은다
# (8.0.46 기준 libcrypto · libssl · libprotobuf-lite → abseil_dll). 목록을 손으로 적어 두면 판이 바뀔 때 빠진다.
MYSQL_PROGRAMS = ("bin/mysqld.exe", "bin/mysql.exe", "bin/mysqladmin.exe", "bin/mysqldump.exe")
MYSQL_DOCS = ("LICENSE", "README")
# share: 오류 메시지·문자셋 (초기화에 필수) · lib/private: REGEXP 함수가 쓰는 ICU 데이터 · lib/plugin: 플러그인
MYSQL_DIRS = ("share", "lib/plugin", "lib/private")

# 패키지의 자체 시험 코드. 실행에 쓰이지 않고 크기만 차지한다(런타임에서 지워도 import 와 무관).
PRUNE_TEST_DIRS_UNDER = ("numpy", "pandas", "scipy", "pyarrow")

# 동봉 런타임의 site-packages 에 넣는 sitecustomize. 파이썬은 사용자 폴더의 패키지
# (%APPDATA%\Python\Python314\site-packages, pip install --user 가 넣는 곳)를 런타임의 site-packages 보다
# '앞에' 둔다. 같은 PC 에 파이썬 3.14 를 따로 쓰는 사람이 있으면 그 판이 동봉한 판을 가린다.
SITECUSTOMIZE = '''\
"""제조 DB 동봉 런타임 전용 (installer/build_installer.py 가 넣는다).

이 PC 사용자 폴더의 파이썬 패키지(%APPDATA%\\\\Python\\\\Python3xx\\\\site-packages)를 쓰지 않는다.
파이썬은 그 경로를 이 런타임의 site-packages 보다 앞에 두기 때문에, 같은 PC 에서 같은 판의 파이썬을 따로 쓰며
pip install --user 로 넣은 패키지가 있으면 동봉한 판이 가려진다. 설치본이 개발 PC 와 똑같이 돌도록 뺀다.
"""
import os
import site
import sys

_user = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _user]
site.ENABLE_USER_SITE = False
'''

# 런타임을 다 만든 뒤 실제로 불러 보고 조금씩 써 보는 검사 (설치된 PC 에서 처음 터지는 일을 여기서 막는다)
RUNTIME_SMOKE = r"""
import importlib, io, os, site, sys, tempfile
assert site.ENABLE_USER_SITE is False, "user site-packages 가 켜져 있습니다"
assert all(p.lower().startswith(sys.prefix.lower()) for p in sys.path if p), sys.path
import PIL, pandas
for m in (PIL, pandas):
    assert m.__file__.lower().startswith(sys.prefix.lower()), m.__file__
mods = ["streamlit", "streamlit_option_menu", "streamlit_cookies_controller", "pandas", "numpy", "scipy",
        "scipy.signal", "pyarrow", "pyarrow.parquet", "nptdms", "plotly", "plotly.graph_objects", "openpyxl",
        "watchdog.observers", "psutil", "PIL.Image", "sqlalchemy", "pymysql", "dotenv", "cryptography", "tkinter",
        "fastapi", "uvicorn"]
for m in mods:
    importlib.import_module(m)
import numpy as np, pandas as pd, tkinter
df = pd.DataFrame({"t": np.arange(5.0), "v": np.sin(np.arange(5.0))})
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "x.parquet")
    df.to_parquet(p, engine="pyarrow")
    assert pd.read_parquet(p).shape == (5, 2)
tkinter.Tcl().eval("info patchlevel")
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
key.public_key().encrypt(b"x", padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
print("runtime ok", sys.version.split()[0])
"""


def say(msg):
    print(msg, flush=True)


def run(args, cwd=None, env=None, timeout=None, quiet=False):
    if not quiet:
        say("  $ " + " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args))
    proc = subprocess.run([str(a) for a in args], cwd=cwd, env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        print(out[-4000:])
        raise SystemExit(f"[빌드 실패] 명령이 코드 {proc.returncode} 로 끝났습니다: {args[0]}")
    return out


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, sha256=None):
    dest = Path(dest)
    if dest.exists() and (sha256 is None or sha256_of(dest) == sha256):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"  ↓ {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if sha256 and sha256_of(tmp) != sha256:
        tmp.unlink()
        raise SystemExit(f"[빌드 실패] 받은 파일의 SHA-256 이 맞지 않습니다: {url}")
    os.replace(tmp, dest)
    return dest


def rmtree(path):
    path = Path(path)
    if path.exists():
        shutil.rmtree(path)


def isolated_env(**extra):
    """개발 PC 의 파이썬 설정(PYTHONPATH, pip.ini, 사용자 site-packages)이 런타임에 섞이지 않게 한다."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "PIP_", "VIRTUAL_ENV"))}
    env.update(PYTHONNOUSERSITE="1", PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_NO_INPUT="1", PYTHONUTF8="1")
    env.update(extra)
    return env


def dir_size_mb(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) / 1e6


# ---------------------------------------------------------------------------
# 1. 파이썬 런타임
# ---------------------------------------------------------------------------
def build_runtime(force):
    target = STAGE / "runtime"
    stamp = target / ".build-stamp"
    req = INSTALLER / "requirements-runtime.txt"
    want = f"{PYTHON_VERSION}\n{sha256_of(req)}\n{RUNTIME_LAYOUT_VERSION}\n"
    if not force and stamp.exists() and stamp.read_text(encoding="utf-8") == want \
            and (target / "python.exe").exists():
        say(f"[1/6] 파이썬 런타임: 이전에 만든 것을 그대로 씁니다 ({PYTHON_VERSION}, {dir_size_mb(target):.0f} MB)")
        write_sitecustomize(target)
        return target
    say(f"[1/6] 파이썬 런타임 {PYTHON_VERSION} 을 만듭니다")
    archive = download(PYTHON_URL, CACHE / f"python-{PYTHON_VERSION}-amd64.zip", PYTHON_SHA256)
    rmtree(target)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)
    # 문서·C 헤더·링크 라이브러리·설치 관리자 메타데이터는 실행에 쓰이지 않는다
    for name in ("Doc", "include", "libs", "__install__.json"):
        p = target / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    py = target / "python.exe"
    env = isolated_env()
    run([py, "-m", "pip", "install", "--no-warn-script-location", "--prefer-binary", "-r", req], env=env)
    run([py, "-m", "pip", "check"], env=env)
    site = target / "Lib" / "site-packages"
    removed = 0
    for top in PRUNE_TEST_DIRS_UNDER:
        for d in sorted((site / top).rglob("tests"), key=lambda p: len(p.parts), reverse=True):
            if d.is_dir():
                shutil.rmtree(d)
                removed += 1
    say(f"  · 패키지 시험 코드 폴더 {removed}개를 뺐습니다")
    write_sitecustomize(target)
    freeze = run([py, "-m", "pip", "freeze", "--all"], env=env, quiet=True)
    (BUILD / "runtime-freeze.txt").write_text(freeze, encoding="utf-8")
    stamp.write_text(want, encoding="utf-8")
    say(f"  · 런타임 크기 {dir_size_mb(target):.0f} MB")
    return target


def write_sitecustomize(runtime):
    """sitecustomize 를 넣고, 컨트롤러가 띄우는 것과 같은 방식(-s 없이)으로 런타임을 불러 검사한다."""
    (runtime / "Lib" / "site-packages" / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    env = {k: v for k, v in isolated_env().items() if k != "PYTHONNOUSERSITE"}
    with tempfile.TemporaryDirectory() as tmp:
        out = run([runtime / "python.exe", "-c", RUNTIME_SMOKE], cwd=tmp, env=env, quiet=True)
    say("  · " + out.strip().splitlines()[-1] + " (사용자 site-packages 차단 확인)")


# ---------------------------------------------------------------------------
# 2. MySQL
# ---------------------------------------------------------------------------
def pe_imports(path):
    """PE 파일(exe/dll)이 불러오는 DLL 이름 — 일반 import 와 지연 로드(delay-load) 둘 다."""
    import struct
    data = Path(path).read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        return []
    n_sections, = struct.unpack_from("<H", data, pe + 6)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    magic, = struct.unpack_from("<H", data, opt)
    dirs = opt + (112 if magic == 0x20B else 96)          # PE32+ / PE32 의 데이터 디렉터리 위치
    sections = []
    for i in range(n_sections):
        sec = opt + opt_size + 40 * i
        vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, sec + 8)
        sections.append((va, max(vsize, raw_size), raw_ptr))

    def offset(rva):
        for va, size, ptr in sections:
            if va <= rva < va + size:
                return rva - va + ptr
        raise ValueError(f"RVA {rva:#x} 가 어느 섹션에도 없습니다: {path}")

    def cstr(rva):
        o = offset(rva)
        return data[o:data.index(b"\0", o)].decode("ascii")

    names = []
    for entry, size, name_at in ((1, 20, 12), (13, 32, 4)):   # (import 표, 지연 로드 표)
        rva, = struct.unpack_from("<I", data, dirs + 8 * entry)
        if not rva:
            continue
        p = offset(rva)
        while True:
            name_rva, = struct.unpack_from("<I", data, p + name_at)
            if not name_rva:
                break
            names.append(cstr(name_rva))
            p += size
    return names


def mysql_bin_dependencies(home):
    """MYSQL_PROGRAMS 가 (간접으로라도) 쓰는 DLL 중 MySQL bin\\ 에 있는 것. 시스템 DLL·VC++ 런타임은 뺀다."""
    local = {p.name.lower(): p for p in (home / "bin").glob("*.dll")}
    found = {}
    todo = [home / rel for rel in MYSQL_PROGRAMS]
    while todo:
        for dll in pe_imports(todo.pop()):
            key = dll.lower()
            if key in local and key not in found:
                found[key] = local[key]
                todo.append(local[key])
    return sorted(found.values())


def mysql_version(mysqld):
    out = run([mysqld, "--version"], quiet=True)
    m = re.search(r"Ver (\d+)\.(\d+)\.(\d+)", out)
    if not m:
        raise SystemExit(f"[빌드 실패] MySQL 버전을 읽지 못했습니다: {out.strip()}")
    return tuple(int(x) for x in m.groups())


def build_mysql(home, skip_selftest):
    home = Path(home)
    mysqld = home / "bin" / "mysqld.exe"
    if not mysqld.exists():
        raise SystemExit(f"[빌드 실패] MySQL Server 8.0 을 찾지 못했습니다: {home}\n"
                         f"  --mysql-home 으로 설치 폴더를 알려 주세요 (MySQL Community Server 8.0 ZIP 을 풀어 둔 폴더도 됩니다).")
    version = mysql_version(mysqld)
    if version[:2] != (8, 0):
        raise SystemExit(f"[빌드 실패] MySQL 8.0 이 필요합니다 (지금 {'.'.join(map(str, version))}).")
    vtext = ".".join(map(str, version))
    say(f"[2/6] MySQL {vtext} 을 담습니다 ({home})")
    target = STAGE / "mysql"
    rmtree(target)
    deps = mysql_bin_dependencies(home)
    for rel in MYSQL_PROGRAMS + MYSQL_DOCS + tuple(f"bin/{p.name}" for p in deps):
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(home / rel, dest)
    say(f"  · DLL: {', '.join(p.name for p in deps)}")
    for rel in MYSQL_DIRS:
        shutil.copytree(home / rel, target / rel, ignore=shutil.ignore_patterns("*.pdb", "*.lib"))
    (target / "VERSION.txt").write_text(f"MySQL Community Server {vtext} (GPL)\n", encoding="ascii")
    say(f"  · {dir_size_mb(target):.0f} MB")
    if not skip_selftest:
        selftest_mysql(target)
    return vtext


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def selftest_mysql(staged):
    """담은 파일만으로 서버가 실제로 뜨는지 본다. 빠진 DLL·share 파일이 있으면 여기서 걸린다.

    MySQL 은 경로를 시스템 코드페이지로 다루는 곳이 있어, 한글이 들어간 프로젝트 경로 대신 영문 임시 폴더에
    복사해서 돌린다 (설치 프로그램도 같은 이유로 영문 경로만 받는다).
    """
    say("  · 담은 MySQL 로 임시 서버를 초기화·기동해 봅니다")
    root = Path(tempfile.mkdtemp(prefix="orcus_mysql_selftest_"))
    try:
        str(root).encode("ascii")
    except UnicodeEncodeError:
        say(f"  · [건너뜀] 임시 폴더 경로에 영문이 아닌 글자가 있습니다: {root}")
        shutil.rmtree(root, ignore_errors=True)
        return
    proc = None
    try:
        base = root / "mysql"
        shutil.copytree(staged, base)
        data = root / "data"
        port = free_port()
        ini = root / "my.ini"
        sys.path.insert(0, str(INSTALLER))
        import setup_target
        ini.write_text(setup_target.render_my_ini(str(base), str(data), port), encoding="ascii")
        run([base / "bin" / "mysqld.exe", f"--defaults-file={ini}", "--initialize-insecure", "--console"],
            timeout=600, quiet=True)
        proc = subprocess.Popen([str(base / "bin" / "mysqld.exe"), f"--defaults-file={ini}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 90
        while time.time() < end and not setup_target.port_open(port):
            if proc.poll() is not None:
                raise SystemExit("[빌드 실패] 담은 MySQL 이 시작하자마자 꺼졌습니다:\n"
                                 + setup_target.read_mysql_error_log(str(data)))
            time.sleep(1)
        client = [base / "bin" / "mysql.exe", "--protocol=TCP", "-h", "127.0.0.1", "-P", str(port), "-u", "root",
                  "--skip-password", "--batch", "--skip-column-names"]
        out = run(client + ["-e", "SELECT VERSION(), @@character_set_server, @@lower_case_table_names, "
                                  "REGEXP_LIKE('T01', '^T[0-9]+$')"], quiet=True)
        answer = out.strip().splitlines()[-1].split("\t")       # 앞줄의 클라이언트 경고는 버린다
        if len(answer) != 4 or answer[1:] != ["utf8mb4", "1", "1"]:
            raise SystemExit(f"[빌드 실패] 담은 MySQL 의 응답이 예상과 다릅니다: {out.strip()}")
        say(f"  · 임시 서버 응답: MySQL {answer[0]} · utf8mb4 · lower_case_table_names=1 · REGEXP(ICU) 동작")
        run([base / "bin" / "mysqladmin.exe", "--protocol=TCP", "-h", "127.0.0.1", "-P", str(port), "-u", "root",
             "--skip-password", "shutdown"], quiet=True)
        proc.wait(timeout=60)
        proc = None
    finally:
        if proc is not None:
            proc.kill()
            proc.wait()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. VC++ 런타임
# ---------------------------------------------------------------------------
def powershell(command):
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], quiet=True).strip()


def fetch_vc_redist():
    say("[3/6] Visual C++ 재배포 패키지")
    path = CACHE / "vc14_redist.x64.exe"
    if path.exists() and time.time() - path.stat().st_mtime > 30 * 86400:
        path.unlink()                       # 한 달이 지나면 새 판으로 받는다
    download(VC_REDIST_URL, path)
    sig = powershell(f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
                     f"$s.Status.ToString() + '|' + $s.SignerCertificate.Subject")
    status, _, subject = sig.partition("|")
    if status != "Valid" or "O=Microsoft Corporation" not in subject:
        raise SystemExit(f"[빌드 실패] vc_redist.x64.exe 의 서명을 확인하지 못했습니다: {sig}")
    version = powershell(f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion")
    nums = [int(x) for x in re.findall(r"\d+", version)[:3]]
    if len(nums) < 3:
        raise SystemExit(f"[빌드 실패] vc_redist.x64.exe 의 버전을 읽지 못했습니다: {version!r}")
    target = STAGE / "redist"
    rmtree(target)
    target.mkdir(parents=True)
    shutil.copy2(path, target / "vc_redist.x64.exe")
    say(f"  · {'.'.join(map(str, nums))} (Microsoft 서명 확인)")
    return nums


# ---------------------------------------------------------------------------
# 4·5. 프로젝트 파일 · 컨트롤러 · 기본 공구 목록
# ---------------------------------------------------------------------------
STAGE_EXCLUDE_PREFIXES = ("backend/tests/", "installer/", "dist/")
STAGE_EXCLUDE_FILES = (".gitignore", "pyrefly.toml")


def project_files():
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=PROJECT, stdout=subprocess.PIPE, check=True).stdout
    files = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8")
        if rel in STAGE_EXCLUDE_FILES or rel.startswith(STAGE_EXCLUDE_PREFIXES):
            continue
        if (PROJECT / rel).is_file():          # 지웠지만 아직 커밋하지 않은 파일은 건너뛴다
            files.append(rel)
    return sorted(set(files))


def app_version():
    """README 릴리즈 노트의 맨 위 판 번호 (### [V3.0.6] ...)."""
    text = (PROJECT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^### \[V(\d+\.\d+\.\d+)\]", text, re.M)
    if not m:
        raise SystemExit("[빌드 실패] README.md 릴리즈 노트에서 판 번호(### [Vx.y.z])를 찾지 못했습니다. --version 으로 주세요.")
    return m.group(1)


def stage_app(version):
    say("[4/6] 프로젝트 파일을 모읍니다")
    target = STAGE / "app"
    rmtree(target)
    files = project_files()
    untracked = set(subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"], cwd=PROJECT,
                                   stdout=subprocess.PIPE, check=True).stdout.decode("utf-8").split("\0"))
    for rel in files:
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / rel, dest)
    new = sorted(f for f in files if f in untracked)
    say(f"  · {len(files)}개 파일 (커밋 전 새 파일 {len(new)}개 포함)")
    for rel in new:
        say(f"      + {rel}")
    if (target / ".env").exists():
        raise SystemExit("[빌드 실패] .env 가 담겼습니다. .gitignore 를 확인하세요 (DB 비밀번호가 배포됩니다).")

    inst = target / "installer"
    inst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INSTALLER / "setup_target.py", inst / "setup_target.py")
    (inst / "VERSION").write_text(version + "\n", encoding="ascii")
    freeze = BUILD / "runtime-freeze.txt"
    if freeze.exists():
        shutil.copy2(freeze, inst / "runtime-freeze.txt")

    say("  · 컨트롤러를 소스에서 빌드합니다")
    # MinGW 의 g++/ld 는 경로를 시스템 코드페이지로 다뤄, 한글이 든 절대 경로(이 프로젝트 폴더)에는 쓰지 못한다.
    # 배치 파일이 프로젝트 루트로 이동한 뒤 쓰는 '상대 경로'로 빌드하고, 결과는 파이썬으로 옮긴다.
    built_rel = Path("build") / "installer" / "system_controller.exe"
    built = PROJECT / built_rel
    built.unlink(missing_ok=True)
    # cmd /c 에 따옴표 친 인자 두 개를 그대로 주면 cmd 가 앞뒤 따옴표를 떼어 경로가 깨지므로,
    # 명령 전체를 한 번 더 감싸는 shell=True 형태(cmd /c ""배치" "인자"")로 넘긴다.
    bat = PROJECT / "tools" / "compile_controller.bat"
    proc = subprocess.run(f'"{bat}" "{built_rel}"', shell=True, cwd=PROJECT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not built.exists():
        print(proc.stdout.decode("utf-8", "replace")[-3000:])
        raise SystemExit("[빌드 실패] 컨트롤러를 빌드하지 못했습니다 (g++ · w64devkit 이 필요합니다).")
    shutil.copy2(built, target / "system_controller.exe")
    built.unlink()
    return target


def export_seed(app_stage, tool_master):
    say("[5/6] 기본 공구 목록")
    seed_dir = app_stage / "installer" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    dest = seed_dir / "공구 마스터.csv"
    backend = PROJECT / "backend"
    if tool_master:
        shutil.copy2(tool_master, dest)
        source = str(tool_master)
    else:
        # 앱 코드로 지금 DB 의 공구 마스터를 그대로 내보낸다 (대시보드 '공구 마스터' 내려받기와 같은 내용).
        code = ("import sys, tool_inserter; data = tool_inserter.export_tool_master_csv(); "
                "open(sys.argv[1], 'wb').write(data)")
        proc = subprocess.run([sys.executable, "-c", code, str(dest)], cwd=backend,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            source = "지금 DB 의 공구 마스터"
        else:
            lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
            reason = lines[-1] if lines else f"코드 {proc.returncode}"
            say(f"  · [주의] DB 에서 공구 마스터를 내보내지 못해 docs/tool_iso14649.csv 를 씁니다 ({reason})")
            shutil.copy2(PROJECT / "docs" / "tool_iso14649.csv", dest)
            source = "docs/tool_iso14649.csv"
    # 설치 PC 의 수집기가 쓰는 것과 같은 검사기로 읽어 본다 — 여기서 통과해야 설치 때도 들어간다
    check = ("import sys, tool_inserter; rows = tool_inserter.parse_tool_master(sys.argv[1]); "
             "print(len(rows), sum(1 for r in rows if r['tool_code']))")
    out = run([sys.executable, "-c", check, str(dest)], cwd=backend, quiet=True).strip().splitlines()[-1]
    total, mounted = (int(x) for x in out.split())
    if not total:
        raise SystemExit("[빌드 실패] 기본 공구 목록이 비어 있습니다.")
    say(f"  · {source} → 공구 {total}개 (장착 {mounted} · 미장착 {total - mounted})")


# ---------------------------------------------------------------------------
# 6. Inno Setup
# ---------------------------------------------------------------------------
def find_iscc(explicit):
    candidates = [explicit, os.environ.get("ISCC"),
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
                  os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
                  os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe"),
                  shutil.which("iscc")]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise SystemExit("[빌드 실패] Inno Setup 6 의 ISCC.exe 를 찾지 못했습니다.\n"
                     "  https://jrsoftware.org/isdl.php 에서 설치하거나(winget install JRSoftware.InnoSetup) "
                     "--iscc 로 경로를 주세요.")


def compile_setup(iscc, version, mysql_ver, vc, test):
    say("[6/6] Inno Setup 으로 설치 파일을 만듭니다 (압축에 몇 분 걸립니다)")
    DIST.mkdir(exist_ok=True)
    args = [iscc, "/Qp", f"/DAppVersion={version}", f"/DStageDir={STAGE}", f"/DMySQLVersion={mysql_ver}",
            f"/DVCRedistMajor={vc[0]}", f"/DVCRedistMinor={vc[1]}", f"/DVCRedistBld={vc[2]}", f"/O{DIST}"]
    if test:
        args.append("/DTestMode")
    args.append(INSTALLER / "ManufacturingDB.iss")
    started = time.time()
    out = run(args, cwd=INSTALLER, timeout=3600)
    for line in out.splitlines():
        if "warning" in line.lower():
            say("  " + line.strip())
    name = f"Setup_ManufacturingDB_v{version}{'_TEST' if test else ''}.exe"
    exe = DIST / name
    if not exe.exists():
        raise SystemExit(f"[빌드 실패] 설치 파일이 만들어지지 않았습니다: {exe}")
    digest = sha256_of(exe)
    (DIST / (name + ".sha256")).write_text(f"{digest} *{name}\n", encoding="ascii")
    say(f"  · {exe}  ({exe.stat().st_size / 1e6:.0f} MB, {time.time() - started:.0f}초)")
    say(f"  · SHA-256 {digest}")
    return exe


def main(argv=None):
    parser = argparse.ArgumentParser(description="제조 DB 배포용 설치 파일을 만든다")
    parser.add_argument("--test", action="store_true",
                        help="시험용 판: 관리자 권한 없이, 서비스·방화벽·바로 가기 없이 설치·제거한다")
    parser.add_argument("--version", help="설치 파일 판 번호 (기본: README 릴리즈 노트 맨 위)")
    parser.add_argument("--mysql-home", default=str(DEFAULT_MYSQL_HOME), help="MySQL Server 8.0 설치 폴더")
    parser.add_argument("--tool-master", help="기본 공구 목록 파일 (기본: 지금 DB 에서 내보냄)")
    parser.add_argument("--iscc", help="ISCC.exe 경로")
    parser.add_argument("--rebuild-runtime", action="store_true", help="파이썬 런타임을 처음부터 다시 만든다")
    parser.add_argument("--skip-mysql-selftest", action="store_true", help="담은 MySQL 의 기동 시험을 건너뛴다")
    args = parser.parse_args(argv)

    if os.name != "nt":
        raise SystemExit("Windows 에서만 빌드할 수 있습니다.")
    iscc = find_iscc(args.iscc)
    version = args.version or app_version()
    say(f"제조 DB 설치 파일 빌드 · 판 {version}{' (시험용)' if args.test else ''} · ISCC {iscc}")
    STAGE.mkdir(parents=True, exist_ok=True)

    build_runtime(args.rebuild_runtime)
    mysql_ver = build_mysql(args.mysql_home, args.skip_mysql_selftest)
    vc = fetch_vc_redist()
    app = stage_app(version)
    export_seed(app, args.tool_master)
    exe = compile_setup(iscc, version, mysql_ver, vc, args.test)
    (BUILD / "last_build.json").write_text(json.dumps({
        "setup": str(exe), "version": version, "test": args.test, "python": PYTHON_VERSION,
        "mysql": mysql_ver, "vc_redist": ".".join(map(str, vc)),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    say("완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
