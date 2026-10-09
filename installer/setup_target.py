"""설치 마무리: Setup.exe 가 파일을 복사한 뒤, 동봉 런타임으로 단계마다 한 번씩 부른다.

    runtime\\python.exe -E -s -X utf8 installer\\setup_target.py <단계> --app-dir <설치 폴더>
                                                     [--options <json>] [--upgrade]

단계
  mysql    MySQL 을 준비하고 데이터베이스를 만든 뒤 .env 와 installer/install_state.ini 를 쓴다.
             동봉(bundled)  my.ini 작성 → (처음이면) 데이터 폴더 초기화 → 'OrcusMySQL' 서비스 등록·시작
                            → root 비밀번호 지정
             기존(existing) 이 PC 에서 이미 돌고 있는 MySQL 에 접속해 버전(8.0.16 이상)을 확인
  schema   models.py 기준으로 빠진 테이블을 만들고(create_all + schema_patch), 보관소·data 폴더를 만든 뒤
           공구 목록이 비어 있으면 installer/seed/공구 마스터.csv 로 채운다.
  verify   앱과 같은 길(.env → DB.database)로 다시 접속해 테이블·컬럼·공구·보관소·패키지를 확인한다.
  stop-test-mysql  시험용 설치(서비스 없이 띄운 mysqld)를 끈다. 시험용 Setup 의 제거·업그레이드가 부른다.

어느 단계든 몇 번을 다시 실행해도 결과가 같다. 이미 초기화된 데이터 폴더, 이미 있는 데이터베이스·테이블·
공구는 그대로 두고 빠진 것만 채운다. 그래서 설치 프로그램을 다시 실행하는 것(복구·업그레이드)이 곧
이 단계들을 다시 부르는 것이다. 지우는 일은 하지 않는다.

Setup.exe 와 주고받는 것
  - 표준 출력의 '##STEP <키>' 줄  Setup 이 진행 문구를 바꾸는 데 쓴다. 키만 ASCII 로 보내므로
                                  출력 인코딩이 어떻게 해석되든 깨지지 않는다.
  - logs/install.log              모든 진행 기록 (UTF-8, 이어 쓰기)
  - logs/install_error.txt        실패했을 때 사람에게 보여 줄 설명 (UTF-8). Setup 이 읽어 메시지 창에 띄운다.
  - 종료 코드                      0 성공 · 1 실패 · 2 MySQL root 비밀번호가 맞지 않음
"""
import argparse
import configparser
import datetime
import importlib
import json
import os
import re
import socket
import string
import subprocess
import sys
import time
import traceback

EXIT_OK, EXIT_FAIL, EXIT_AUTH = 0, 1, 2

APP_VERSION_FILE = os.path.join("installer", "VERSION")
STATE_REL = os.path.join("installer", "install_state.ini")
SEED_TOOL_MASTER_REL = os.path.join("installer", "seed", "공구 마스터.csv")
LOG_REL = os.path.join("logs", "install.log")
ERROR_REL = os.path.join("logs", "install_error.txt")

DEFAULT_DB_NAME = "orcus"
DEFAULT_SERVICE = "OrcusMySQL"
VAULT_DIR_NAME = "archive_vault"      # vault_manager.VAULT_DIR_NAME 과 같은 이름
MYSQL_DATA_DIR_NAME = "mysql_data"
# CHECK 제약(tool.chk_tool_mounted_complete)을 MySQL 이 실제로 검사하기 시작한 버전.
# 그 전 버전은 CHECK 를 문법만 받고 무시해서, 공구 마스터 검사가 DB 에서 빠진다.
MIN_MYSQL_VERSION = (8, 0, 16)

# .env 와 SQLAlchemy 접속 문자열(mysql+pymysql://root:<비밀번호>@...)에 그대로 들어가는 값이다.
# '@' 는 주소 구분자로, '%' 는 퍼센트 인코딩으로 읽혀 연결이 깨지고, 따옴표·역슬래시·공백은
# .env 와 MySQL 옵션 파일에서 따로 풀어야 해서 받지 않는다. Setup 의 입력 검사도 같은 규칙을 쓴다.
PASSWORD_CHARS = set(string.ascii_letters + string.digits + "!#$^&*()-_=+[]{};:,.?~")

# 대시보드·수집기·변환기·REST API·관리 창이 실제로 import 하는 패키지 (tools/requirements.txt 와 같은 목록)
REQUIRED_MODULES = (
    "streamlit", "streamlit_option_menu", "pandas", "numpy", "scipy", "pyarrow", "nptdms",
    "plotly", "openpyxl", "watchdog", "psutil", "PIL", "sqlalchemy", "pymysql", "dotenv",
    "cryptography", "tkinter", "fastapi", "uvicorn",
)

MY_INI_TEMPLATE = """\
# Written by the Manufacturing DB installer (installer/setup_target.py).
# Re-running the installer rewrites this file. Put local changes in a separate
# option file instead, or they will be lost on the next upgrade.
[mysqld]
basedir="{basedir}"
datadir="{datadir}"
port={port}
# Only the dashboard and collector on this PC talk to the database.
bind-address=127.0.0.1
mysqlx=OFF
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
default-storage-engine=INNODB
sql-mode="ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
# Must stay the same for the life of the data directory (fixed at initialization).
lower_case_table_names=1
max_allowed_packet=1G
max_connections=151
innodb_buffer_pool_size=256M
innodb_redo_log_capacity=100M
skip-log-bin
log-error="{datadir}/orcus-mysql.err"
secure-file-priv=NULL

[client]
port={port}
default-character-set=utf8mb4
"""


class SetupError(Exception):
    """사람에게 그대로 보여 줄 설명을 담은 실패. code 는 프로세스 종료 코드가 된다."""

    def __init__(self, message, code=EXIT_FAIL):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 기록
# ---------------------------------------------------------------------------
class _Tee:
    """print 를 화면(Setup 이 받는 파이프)과 install.log 양쪽에 쓴다."""

    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
                self.stream.flush()
            except Exception:
                pass
        self.log_file.write(text)
        self.log_file.flush()
        return len(text)

    def flush(self):
        pass


def open_log(app_dir, command):
    os.makedirs(os.path.join(app_dir, "logs"), exist_ok=True)
    log_file = open(os.path.join(app_dir, LOG_REL), "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = sys.stdout
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n===== {stamp} · {command} · 파이썬 {sys.version.split()[0]} ({sys.executable}) =====")
    return log_file


def step(key, message):
    """Setup 에 진행 단계를 알리고(ASCII 키) 기록에는 사람이 읽을 문장을 남긴다."""
    print(f"##STEP {key}")
    print(f"[단계] {message}")


def write_error(app_dir, message):
    try:
        os.makedirs(os.path.join(app_dir, "logs"), exist_ok=True)
        with open(os.path.join(app_dir, ERROR_REL), "w", encoding="utf-8") as f:
            f.write(message.strip() + "\n")
    except Exception:
        pass


def clear_error(app_dir):
    try:
        os.remove(os.path.join(app_dir, ERROR_REL))
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# 입력 검사 (Setup 의 [Code] 와 같은 규칙)
# ---------------------------------------------------------------------------
def check_password(password):
    if not password:
        raise SetupError("MySQL root 비밀번호가 비어 있습니다.")
    bad = sorted({ch for ch in password if ch not in PASSWORD_CHARS})
    if bad:
        raise SetupError("MySQL root 비밀번호에 쓸 수 없는 문자가 있습니다: " + " ".join(repr(c) for c in bad)
                         + "\n영문·숫자와 ! # $ ^ & * ( ) - _ = + [ ] { } ; : , . ? ~ 만 쓸 수 있습니다.")


def check_ascii_path(label, path):
    """MySQL 은 basedir/datadir 경로를 시스템 코드페이지로 다루는 곳이 있어 영문 경로만 받는다."""
    if not path or not os.path.isabs(path):
        raise SetupError(f"{label} 경로가 올바르지 않습니다: {path!r}")
    try:
        path.encode("ascii")
    except UnicodeEncodeError:
        raise SetupError(f"{label} 경로에는 영문·숫자·기호만 쓸 수 있습니다 (MySQL 제약): {path}")


def check_db_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name or ""):
        raise SetupError(f"데이터베이스 이름이 올바르지 않습니다: {name!r}")


def check_port(port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SetupError(f"포트 번호가 올바르지 않습니다: {port!r}")
    if not 1024 <= port <= 65535:
        raise SetupError(f"포트 번호는 1024~65535 사이여야 합니다: {port}")
    return port


# ---------------------------------------------------------------------------
# .env · install_state.ini
# ---------------------------------------------------------------------------
_PLAIN_ENV_VALUE = re.compile(r"[A-Za-z0-9_.:/\\\-]*")


def env_quote(value):
    """python-dotenv 가 글자 그대로 읽도록 값을 적는다. 평범한 값은 따옴표 없이 둔다(기존 .env 와 같은 모양)."""
    value = str(value)
    if _PLAIN_ENV_VALUE.fullmatch(value):
        return value
    if "'" not in value:
        return f"'{value}'"       # 작은따옴표 안은 ${...} 치환도 이스케이프도 없다
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


ENV_HEADER = """\
# 제조 DB 설치 프로그램이 만든 설정입니다 (installer/setup_target.py).
# 업그레이드 설치는 이 파일을 다시 쓰지 않습니다. 값을 바꾸면 컨트롤러를 다시 시작하세요.
"""

ENV_VAULT_COMMENT = """\
# 원본 백업 보관소(archive_vault). 프로젝트(설치) 폴더와 MySQL 데이터 폴더 바깥에 둔다.
"""


def merge_env(existing_text, updates):
    """기존 .env 의 줄과 주석은 그대로 두고, 아는 키의 값만 바꾸거나 빠진 키를 끝에 붙인다."""
    lines = existing_text.splitlines() if existing_text else []
    seen = set()
    out = []
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            if key in seen:
                continue                     # 같은 키가 두 번 있으면 뒤의 것은 버린다(앞의 값이 이긴다)
            seen.add(key)
            out.append(f"{key}={env_quote(updates[key])}")
        else:
            out.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        if not lines:
            out.append(ENV_HEADER.rstrip("\n"))
        for key in missing:
            if key == "ORCUS_VAULT_ROOT":
                out.append("")
                out.append(ENV_VAULT_COMMENT.rstrip("\n"))
            out.append(f"{key}={env_quote(updates[key])}")
    return "\n".join(out) + "\n"


def write_env(app_dir, updates):
    path = os.path.join(app_dir, ".env")
    old = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            old = f.read()
    text = merge_env(old, updates)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def read_env(app_dir):
    from dotenv import dotenv_values
    path = os.path.join(app_dir, ".env")
    if not os.path.exists(path):
        raise SetupError(f"설정 파일이 없습니다: {path}\n설치 프로그램을 다시 실행해 처음부터 설정하세요.")
    return dotenv_values(path)


def write_state(app_dir, state):
    """Setup/Uninstall 의 [Code] 가 GetIniString 으로 읽는다. 그래서 'key=value' 꼴의 ASCII 로 쓴다."""
    cp = configparser.ConfigParser(interpolation=None)
    for section, values in state.items():
        cp[section] = {k: "" if v is None else str(v) for k, v in values.items()}
    path = os.path.join(app_dir, STATE_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii", errors="replace", newline="\r\n") as f:
        cp.write(f, space_around_delimiters=False)
    os.replace(tmp, path)
    return path


def read_state(app_dir):
    path = os.path.join(app_dir, STATE_REL)
    if not os.path.exists(path):
        return None
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path, encoding="ascii")
    return {s: dict(cp[s]) for s in cp.sections()}


def app_version(app_dir):
    try:
        with open(os.path.join(app_dir, APP_VERSION_FILE), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# 명령 실행 · 서비스
# ---------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def run(args, timeout=180, check=False):
    """명령을 숨긴 창으로 실행해 (종료 코드, 출력) 을 돌려준다. 출력은 기록에도 남긴다."""
    printable = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args)
    print(f"  $ {printable}")
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise SetupError(f"명령이 {timeout}초 안에 끝나지 않았습니다: {printable}")
    except OSError as e:
        raise SetupError(f"명령을 실행하지 못했습니다: {printable} ({e})")
    out = _decode(proc.stdout)
    for line in out.splitlines():
        if line.strip():
            print(f"    {line}")
    if check and proc.returncode != 0:
        raise SetupError(f"명령이 실패했습니다 (코드 {proc.returncode}): {printable}\n{tail(out)}")
    return proc.returncode, out


def _decode(raw):
    for enc in ("utf-8", "cp949", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def tail(text, lines=12):
    return "\n".join([ln for ln in text.splitlines() if ln.strip()][-lines:])


_SC_STATE = re.compile(r":\s*(\d)\s+(?:STOPPED|START_PENDING|STOP_PENDING|RUNNING|CONTINUE_PENDING|"
                       r"PAUSE_PENDING|PAUSED)\b")


def service_state(name):
    """서비스 상태 번호 (1 정지 · 2 시작 중 · 3 정지 중 · 4 실행 중). 없으면 None.

    sc 의 출력 문구는 Windows 언어에 따라 바뀌지만 '상태 : 4 RUNNING' 의 숫자와 영문 상태명은 그대로다.
    """
    code, out = run(["sc.exe", "query", name], timeout=30)
    if code == 1060:          # ERROR_SERVICE_DOES_NOT_EXIST
        return None
    m = _SC_STATE.search(out)
    return int(m.group(1)) if m else 0


def wait_service_state(name, wanted, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        state = service_state(name)
        if state == wanted or (wanted is None and state is None):
            return True
        time.sleep(1)
    return False


def stop_service(name):
    state = service_state(name)
    if state in (None, 1):
        return
    print(f"  · '{name}' 서비스를 멈춥니다.")
    run(["net.exe", "stop", name], timeout=180)
    if not wait_service_state(name, 1, timeout=120):
        raise SetupError(f"'{name}' 서비스가 멈추지 않습니다. 서비스 관리자(services.msc)에서 멈춘 뒤 다시 설치하세요.")


def register_service(name, mysqld, ini):
    """서비스를 (다시) 등록한다. 설치 폴더가 바뀌었어도 지금 경로를 가리키도록 매번 지우고 새로 만든다."""
    if service_state(name) is not None:
        stop_service(name)
        run(["sc.exe", "delete", name], timeout=30)
        if not wait_service_state(name, None, timeout=30):
            raise SetupError(f"이전 '{name}' 서비스를 지우지 못했습니다. 서비스 관리자(services.msc) 창이 열려 있으면 "
                             f"닫고 다시 설치하세요.")
    code, out = run([mysqld, "--install", name, f"--defaults-file={ini}"], timeout=60)
    if code != 0 or service_state(name) is None:
        raise SetupError(f"MySQL 서비스({name})를 등록하지 못했습니다 (관리자 권한이 필요합니다).\n{tail(out)}")
    run(["sc.exe", "description", name, "공작기계지능화실험실 제조 DB 의 데이터베이스 (MySQL 8.0, 설치 프로그램이 등록)"],
        timeout=30)
    # 예기치 않게 죽으면 10초 뒤 다시 띄운다 (하루 동안 실패가 쌓이지 않으면 횟수를 다시 센다)
    run(["sc.exe", "failure", name, "reset=", "86400", "actions=", "restart/10000/restart/10000/restart/60000"],
        timeout=30)


def start_service(name):
    if service_state(name) == 4:
        return
    code, out = run(["net.exe", "start", name], timeout=180)
    if not wait_service_state(name, 4, timeout=60):
        raise SetupError(f"MySQL 서비스({name})가 시작되지 않습니다.\n{tail(out)}")


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------
def port_open(port, host="127.0.0.1", timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(port, timeout=90, proc=None):
    end = time.time() + timeout
    while time.time() < end:
        if port_open(port):
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(1)
    return False


def connect(port, password, database=None, host="127.0.0.1"):
    import pymysql
    return pymysql.connect(host=host, port=port, user="root", password=password or "", database=database,
                           charset="utf8mb4", connect_timeout=10, autocommit=True)


def mysql_error_code(exc):
    try:
        return int(exc.args[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def query_scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def server_version(conn):
    raw = str(query_scalar(conn, "SELECT VERSION()"))
    nums = tuple(int(x) for x in re.findall(r"\d+", raw)[:3])
    return raw, nums


def is_initialized_datadir(datadir):
    # --initialize 가 끝나면 시스템 스키마 폴더 'mysql' 과 서버 식별 파일 auto.cnf 가 생긴다
    return os.path.isdir(os.path.join(datadir, "mysql")) and os.path.exists(os.path.join(datadir, "auto.cnf"))


def render_my_ini(basedir, datadir, port):
    def fwd(p):
        return os.path.abspath(p).replace("\\", "/")
    return MY_INI_TEMPLATE.format(basedir=fwd(basedir), datadir=fwd(datadir), port=port)


def bundled_paths(app_dir, datadir):
    basedir = os.path.join(app_dir, "mysql")
    return {
        "basedir": basedir,
        "mysqld": os.path.join(basedir, "bin", "mysqld.exe"),
        "mysqladmin": os.path.join(basedir, "bin", "mysqladmin.exe"),
        "ini": os.path.join(basedir, "my.ini"),
        "datadir": datadir,
    }


def initialize_datadir(paths):
    datadir = paths["datadir"]
    if os.path.isdir(datadir) and os.listdir(datadir):
        raise SetupError(f"MySQL 데이터 폴더가 비어 있지 않은데 MySQL 데이터가 아닙니다: {datadir}\n"
                         f"다른 폴더를 고르거나 그 폴더를 비운 뒤 다시 설치하세요.")
    os.makedirs(os.path.dirname(datadir), exist_ok=True)
    print("  · 데이터 폴더를 초기화합니다 (1~2분 걸릴 수 있습니다).")
    code, out = run([paths["mysqld"], f"--defaults-file={paths['ini']}", "--initialize-insecure", "--console"],
                    timeout=900)
    if code != 0 or not is_initialized_datadir(datadir):
        hint = ""
        if code in (-1073741515, 3221225781):      # 0xC0000135 STATUS_DLL_NOT_FOUND
            hint = "\nVisual C++ 런타임(MSVCP140.dll 등)이 없습니다. 설치 프로그램을 다시 실행하세요."
        raise SetupError(f"MySQL 데이터 폴더를 초기화하지 못했습니다 (코드 {code}).{hint}\n{tail(out)}")


def start_detached_mysqld(paths):
    """시험용 설치: 서비스 대신 mysqld 를 백그라운드 프로세스로 띄운다 (관리자 권한 없이 끝까지 시험하려고)."""
    args = [paths["mysqld"], f"--defaults-file={paths['ini']}"]
    print("  $ " + " ".join(args) + "   (시험용: 서비스 없이 실행)")
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        # 부모(Setup·셸)가 작업 개체에 묶여 있어도 Setup 이 끝난 뒤 함께 죽지 않게 떼어 낸다
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=base | CREATE_BREAKAWAY_FROM_JOB,
                                close_fds=True)
    except OSError:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=base, close_fds=True)
    return proc


def read_mysql_error_log(datadir, lines=15):
    path = os.path.join(datadir, "orcus-mysql.err")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return tail(f.read(), lines)
    except OSError:
        return ""


def ensure_root_password(port, password, fresh):
    """root 로 접속해 비밀번호를 맞춘다. 막 초기화한 데이터 폴더는 root 비밀번호가 비어 있다."""
    import pymysql
    if not fresh:
        try:
            connect(port, password).close()
            print("  · root 비밀번호로 접속했습니다 (기존 데이터 폴더).")
            return
        except pymysql.err.OperationalError as e:
            if mysql_error_code(e) != 1045:
                raise SetupError(f"MySQL 에 접속하지 못했습니다: {e}")
        # 지난 설치가 비밀번호를 정하기 전에 멈췄다면 아직 빈 비밀번호다. 그때만 이어서 정한다.
        try:
            connect(port, "").close()
        except pymysql.err.OperationalError:
            raise SetupError("이 데이터 폴더의 MySQL root 비밀번호가 입력한 값과 다릅니다.\n"
                             "예전에 쓰던 비밀번호를 입력하거나, 다른 데이터 폴더를 고르세요.", EXIT_AUTH)
    conn = connect(port, "")
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
    finally:
        conn.close()
    connect(port, password).close()
    print("  · root 비밀번호를 지정했습니다.")


def create_database(port, password, db_name, host="127.0.0.1"):
    conn = connect(port, password, host=host)
    try:
        exists = query_scalar(conn, "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s",
                              (db_name,))
        if exists:
            n = query_scalar(conn, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s",
                             (db_name,))
            print(f"  · 데이터베이스({db_name})가 이미 있습니다 (테이블 {n}개). 그대로 이어서 씁니다.")
        else:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            print(f"  · 데이터베이스({db_name})를 만들었습니다 (utf8mb4).")
    finally:
        conn.close()


def check_existing_server(port, password, host):
    import pymysql
    try:
        conn = connect(port, password, host=host)
    except pymysql.err.OperationalError as e:
        code = mysql_error_code(e)
        if code == 1045:
            raise SetupError("MySQL root 비밀번호가 맞지 않습니다.", EXIT_AUTH)
        if code == 2003:
            raise SetupError(f"이 PC 의 MySQL({host}:{port})에 연결할 수 없습니다. MySQL 서비스가 실행 중인지 확인하세요.")
        raise SetupError(f"MySQL 에 접속하지 못했습니다: {e}")
    try:
        raw, nums = server_version(conn)
    finally:
        conn.close()
    print(f"  · 기존 MySQL {raw} 에 접속했습니다 ({host}:{port}).")
    if "mariadb" in raw.lower() or nums < MIN_MYSQL_VERSION:
        need = ".".join(map(str, MIN_MYSQL_VERSION))
        raise SetupError(f"이 PC 의 MySQL({raw})은 쓸 수 없습니다. MySQL {need} 이상이 필요합니다.\n"
                         f"설치 프로그램을 다시 실행해 '동봉된 MySQL 설치'를 고르세요.")


# ---------------------------------------------------------------------------
# 단계: mysql
# ---------------------------------------------------------------------------
def load_options(path):
    with open(path, encoding="utf-8-sig") as f:
        opts = json.load(f)
    mode = opts.get("mode")
    if mode not in ("bundled", "existing"):
        raise SetupError(f"MySQL 설치 방식이 올바르지 않습니다: {mode!r}")
    opts["port"] = check_port(opts.get("port"))
    check_password(opts.get("password", ""))
    opts["db_name"] = opts.get("db_name") or DEFAULT_DB_NAME
    check_db_name(opts["db_name"])
    opts["service"] = opts.get("service") or DEFAULT_SERVICE
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", opts["service"]):
        raise SetupError(f"서비스 이름이 올바르지 않습니다: {opts['service']!r}")
    check_ascii_path("데이터 폴더", opts.get("data_root", ""))
    opts["data_root"] = os.path.normpath(opts["data_root"])
    opts["no_service"] = bool(opts.get("no_service"))
    return opts


def settings_from_previous(app_dir):
    """업그레이드: 지난번 설치가 남긴 install_state.ini 와 .env 에서 설정을 되살린다."""
    state = read_state(app_dir)
    if not state or "mysql" not in state:
        raise SetupError("지난 설치 기록(installer/install_state.ini)이 없어 업그레이드할 수 없습니다.\n"
                         "설치 프로그램을 다시 실행하면 처음 설치처럼 설정을 묻습니다.")
    env = read_env(app_dir)
    m, p = state["mysql"], state.get("paths", {})
    password = env.get("DB_PASSWORD") or ""
    return {
        "mode": m.get("mode"),
        "port": check_port(env.get("DB_PORT") or m.get("port")),
        "password": password,
        "db_name": env.get("DB_NAME") or m.get("db_name") or DEFAULT_DB_NAME,
        "service": m.get("service") or DEFAULT_SERVICE,
        "datadir": m.get("datadir"),
        "data_root": p.get("data_root"),
        "vault_root": env.get("ORCUS_VAULT_ROOT") or p.get("vault_root"),
        "host": env.get("DB_HOST") or "127.0.0.1",
        "no_service": m.get("no_service") == "1",
    }


def cmd_mysql(app_dir, options_path, upgrade):
    if upgrade:
        s = settings_from_previous(app_dir)
        print(f"[업그레이드] 지난 설정을 그대로 씁니다: MySQL {s['mode']} · 포트 {s['port']} · DB {s['db_name']}")
    else:
        if not options_path:
            raise SetupError("설치 설정(--options)이 없습니다.")
        o = load_options(options_path)
        s = dict(o, datadir=os.path.join(o["data_root"], MYSQL_DATA_DIR_NAME),
                 vault_root=os.path.join(o["data_root"], VAULT_DIR_NAME),
                 host="127.0.0.1" if o["mode"] == "bundled" else "localhost")

    state = {"mysql": {"mode": s["mode"], "port": s["port"], "db_name": s["db_name"]},
             "paths": {"data_root": s.get("data_root") or "", "vault_root": s["vault_root"]},
             "meta": {"version": app_version(app_dir),
                      "configured_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}

    if s["mode"] == "bundled":
        check_ascii_path("설치 폴더", app_dir)
        check_ascii_path("MySQL 데이터 폴더", s["datadir"])
        paths = bundled_paths(app_dir, s["datadir"])
        step("mysql_prepare", f"동봉 MySQL 을 준비합니다 (데이터 폴더 {s['datadir']}, 포트 {s['port']}).")
        if not os.path.exists(paths["mysqld"]):
            raise SetupError(f"동봉 MySQL 이 없습니다: {paths['mysqld']}")
        with open(paths["ini"], "w", encoding="ascii", newline="\r\n") as f:
            f.write(render_my_ini(paths["basedir"], paths["datadir"], s["port"]))
        print(f"  · 설정 파일: {paths['ini']}")

        # 시험용 설치가 남긴 mysqld 가 아직 떠 있으면(업그레이드 직전 멈추지 못한 경우) 포트가 겹친다
        if s["no_service"]:
            stop_test_mysql(app_dir, quiet=True)
        elif service_state(s["service"]) is not None:
            stop_service(s["service"])
        if port_open(s["port"]):
            raise SetupError(f"다른 프로그램이 이미 포트({s['port']})를 쓰고 있습니다. "
                             f"설치 프로그램을 다시 실행해 다른 포트를 고르세요.")

        fresh = False
        if is_initialized_datadir(paths["datadir"]):
            print("  · 이미 초기화된 데이터 폴더입니다. 기존 데이터를 그대로 씁니다.")
        else:
            step("mysql_init", "MySQL 데이터 폴더를 초기화합니다.")
            initialize_datadir(paths)
            fresh = True

        proc = None
        if s["no_service"]:
            step("mysql_start", "MySQL 을 시작합니다 (시험용: 서비스 없이).")
            proc = start_detached_mysqld(paths)
            state["mysql"].update(no_service=1, pid=proc.pid)
        else:
            step("mysql_service", f"Windows 서비스 '{s['service']}' 로 등록합니다 (부팅 때 자동 시작).")
            register_service(s["service"], paths["mysqld"], paths["ini"])
            step("mysql_start", "MySQL 서비스를 시작합니다.")
            start_service(s["service"])
            state["mysql"]["service"] = s["service"]
        if not wait_port(s["port"], timeout=120, proc=proc):
            raise SetupError(f"MySQL 이 포트 {s['port']} 에서 응답하지 않습니다.\n"
                             f"{read_mysql_error_log(paths['datadir'])}")
        state["mysql"]["datadir"] = paths["datadir"]

        step("mysql_password", "root 계정을 설정합니다.")
        ensure_root_password(s["port"], s["password"], fresh)
    else:
        step("mysql_existing", f"이 PC 의 MySQL 에 접속합니다 ({s['host']}:{s['port']}).")
        check_existing_server(s["port"], s["password"], s["host"])

    step("mysql_database", f"데이터베이스({s['db_name']})를 준비합니다.")
    create_database(s["port"], s["password"], s["db_name"], host=s["host"])

    if not upgrade:
        step("env_write", "앱 설정(.env)을 씁니다.")
        env_path = write_env(app_dir, {
            "DB_HOST": s["host"],
            "DB_PORT": s["port"],
            "DB_USER": "root",
            "DB_PASSWORD": s["password"],
            "DB_NAME": s["db_name"],
            "PYTHONPATH": "backend",
            "ORCUS_VAULT_ROOT": s["vault_root"],
        })
        print(f"  · {env_path}")
    write_state(app_dir, state)
    print("MySQL 준비 완료.")


# ---------------------------------------------------------------------------
# 단계: schema · verify (앱 코드를 그대로 쓴다)
# ---------------------------------------------------------------------------
ENV_KEYS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "ORCUS_VAULT_ROOT", "ORCUS_DB_DATA_DIR")
_ENV_SCOPES = (
    ("사용자", "HKEY_CURRENT_USER", r"Environment"),
    ("시스템", "HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
)


def persistent_env_overrides():
    """Windows 에 '영구' 환경 변수로 잡힌 DB_* · ORCUS_* — (이름, 범위, 값).

    컨트롤러가 띄우는 앱은 이 값을 물려받고, dotenv 는 이미 있는 환경 변수를 덮어쓰지 않는다(override=False).
    그래서 여기 값이 있으면 앱은 .env 대신 이 값으로 DB·보관소를 찾는다.
    """
    import winreg
    found = []
    for scope, hive, path in _ENV_SCOPES:
        try:
            with winreg.OpenKey(getattr(winreg, hive), path) as key:
                for name in ENV_KEYS:
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    found.append((name, scope, str(value)))
        except OSError:
            continue
    return found


def use_app_env(app_dir):
    """방금 쓴 .env 를 그대로 기준으로 삼고 backend 를 import 경로에 둔다.

    Setup 을 띄운 셸에 DB_* 가 남아 있어도(개발 PC 의 터미널 등) 그 값에 끌려가지 않게, .env 의 값으로
    덮어쓰고 .env 에 없는 키는 지운다. 설치를 마친 뒤 컨트롤러가 띄우는 앱이 보게 될 값과 같다.
    """
    from dotenv import dotenv_values
    path = os.path.join(app_dir, ".env")
    if not os.path.exists(path):
        raise SetupError(f"앱 설정 파일이 없습니다: {path}\n설치 프로그램을 다시 실행하세요.")
    values = dotenv_values(path)
    for key in ENV_KEYS:
        value = values.get(key)
        if value is not None:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    backend = os.path.join(app_dir, "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    return values


def cmd_schema(app_dir):
    use_app_env(app_dir)
    from sqlalchemy import inspect, text

    import DB.database as dbmod
    from DB import models  # noqa: F401  (Base.metadata 에 모델을 등록한다)
    from DB.database import Base
    from DB.schema_patch import ensure_schema

    step("schema_tables", "테이블을 만듭니다 (models.py 기준, 이미 있는 테이블은 그대로).")
    before = set(inspect(dbmod.engine).get_table_names())
    Base.metadata.create_all(dbmod.engine)
    after = set(inspect(dbmod.engine).get_table_names())
    created = sorted(after - before)
    print(f"  · 새로 만든 테이블 {len(created)}개" + (f": {', '.join(created)}" if created else ""))
    added = ensure_schema(verbose=True)
    if added:
        print(f"  · 보정한 컬럼 {len(added)}개")

    step("schema_folders", "원본 백업 보관소와 data 폴더를 만듭니다.")
    import vault_manager
    ok, message = vault_manager.ensure_vault_root()
    print(f"  · {message}")
    if not ok:
        raise SetupError(message)
    for folder in (vault_manager.RAW_DATA_ROOT, vault_manager.PROCESSED_ROOT, vault_manager.FAILED_ROOT):
        os.makedirs(folder, exist_ok=True)
        print(f"  · {folder}")

    step("schema_tools", "공구 목록을 확인합니다.")
    with dbmod.engine.connect() as conn:
        tools = conn.execute(text("SELECT COUNT(*) FROM tool")).scalar() or 0
    seed = os.path.join(app_dir, SEED_TOOL_MASTER_REL)
    if tools:
        print(f"  · 공구 {tools}개가 이미 있습니다. 그대로 둡니다.")
    elif not os.path.exists(seed):
        print(f"  · [주의] 기본 공구 목록 파일이 없어 공구 목록을 비워 둡니다: {seed}")
    else:
        import tool_inserter
        result = tool_inserter.parse_and_insert_tools(seed)
        if not result.ok:
            detail = "\n".join(f"  · {p}" for p in (result.problems or [])[:10])
            raise SetupError(f"기본 공구 목록을 넣지 못했습니다: {result.message}\n{detail}".rstrip())
        print(f"  · 기본 공구 목록 {result.inserted}개를 넣었습니다 (보관소 사본: {result.archived or '없음'}).")
    print("테이블 · 폴더 · 공구 목록 준비 완료.")


def cmd_verify(app_dir):
    env_values = use_app_env(app_dir)
    step("verify", "설치 결과를 확인합니다.")
    problems, notes = [], []

    for name, scope, value in persistent_env_overrides():
        if (env_values.get(name) or "") != value:
            problems.append(f"Windows {scope} 환경 변수({name})가 설정돼 있어 앱이 .env 대신 그 값({value if name != 'DB_PASSWORD' else '***'})을 씁니다. "
                            f"제어판 › 시스템 › 환경 변수에서 지우거나 .env 와 같게 맞추세요.")

    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:      # noqa: BLE001 — 무엇이 깨졌든 이름과 함께 보고한다
            problems.append(f"파이썬 패키지({name})를 불러오지 못했습니다: {e}")

    from sqlalchemy import inspect, text

    import DB.database as dbmod
    from DB import models  # noqa: F401
    from DB.database import Base
    try:
        with dbmod.engine.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar()
            tools = conn.execute(text("SELECT COUNT(*) FROM tool")).scalar() or 0
            users = conn.execute(text("SELECT COUNT(*) FROM app_user")).scalar() or 0
        insp = inspect(dbmod.engine)
        actual = set(insp.get_table_names())
        for name, table in Base.metadata.tables.items():
            if name not in actual:
                problems.append(f"테이블이 없습니다: {name}")
                continue
            have = {c["name"] for c in insp.get_columns(name)}
            missing = [c.name for c in table.columns if c.name not in have]
            if missing:
                problems.append(f"{name} 테이블에 컬럼이 없습니다: {', '.join(missing)} "
                                f"(예전 버전의 DB 라면 backend 의 migrate_*.py 로 옮겨야 합니다)")
        notes.append(f"MySQL {version} · {dbmod.DB_HOST}:{dbmod.DB_PORT} · 데이터베이스 '{dbmod.DB_NAME}' · "
                     f"테이블 {len(actual)}개")
        notes.append(f"공구 {tools}개 · 가입 계정 {users}개")
        if not tools:
            notes.append("[주의] 공구 목록이 비어 있습니다. 대시보드 '공구 마스터' 화면에서 올리세요.")
    except Exception as e:          # noqa: BLE001
        problems.append(f"앱 설정(.env)으로 데이터베이스에 접속하지 못했습니다: {e}")

    import vault_manager
    ok, message = vault_manager.ensure_vault_root()
    (notes if ok else problems).append(message)

    if not os.path.exists(os.path.join(app_dir, ".streamlit", "config.toml")):
        problems.append("대시보드 설정 파일(.streamlit/config.toml)이 없습니다.")

    for line in notes:
        print(f"  · {line}")
    if problems:
        raise SetupError("설치 확인에서 문제가 발견됐습니다:\n" + "\n".join(f"  · {p}" for p in problems))
    print("설치 확인 완료: 문제 없음.")


# ---------------------------------------------------------------------------
# 시험용 설치 정리
# ---------------------------------------------------------------------------
def stop_test_mysql(app_dir, quiet=False):
    """서비스 없이 띄운 시험용 mysqld 를 끈다. 정상 종료(mysqladmin shutdown)를 먼저 시도한다."""
    state = read_state(app_dir) or {}
    m = state.get("mysql", {})
    pid = int(m.get("pid") or 0)
    if m.get("no_service") != "1" or not pid:
        if not quiet:
            print("  · 시험용 MySQL 기록이 없습니다.")
        return
    import psutil
    try:
        proc = psutil.Process(pid)
        if proc.name().lower() != "mysqld.exe":
            raise psutil.NoSuchProcess(pid)
    except psutil.NoSuchProcess:
        if not quiet:
            print(f"  · 시험용 MySQL(PID {pid})은 이미 꺼져 있습니다.")
        return
    port = m.get("port")
    password = ""
    try:
        password = read_env(app_dir).get("DB_PASSWORD") or ""
    except SetupError:
        pass
    admin = os.path.join(app_dir, "mysql", "bin", "mysqladmin.exe")
    if os.path.exists(admin) and port:
        env = dict(os.environ, MYSQL_PWD=password)
        subprocess.run([admin, "--protocol=TCP", "-h", "127.0.0.1", "-P", port, "-u", "root", "shutdown"],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                       creationflags=CREATE_NO_WINDOW)
    try:
        proc.wait(timeout=60)
        print(f"  · 시험용 MySQL(PID {pid})을 정상 종료했습니다.")
    except psutil.TimeoutExpired:
        proc.kill()
        print(f"  · 시험용 MySQL(PID {pid})이 멈추지 않아 강제로 끝냈습니다.")


# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="제조 DB 설치 마무리 (Setup.exe 가 부른다)")
    parser.add_argument("command", choices=("mysql", "schema", "verify", "stop-test-mysql"))
    parser.add_argument("--app-dir", required=True, help="설치 폴더")
    parser.add_argument("--options", help="설치 설정 JSON (처음 설치할 때 Setup 이 만든다)")
    parser.add_argument("--upgrade", action="store_true", help="지난 설치의 설정을 그대로 쓴다")
    args = parser.parse_args(argv)
    app_dir = os.path.abspath(args.app_dir)

    saved = sys.stdout, sys.stderr
    log_file = open_log(app_dir, args.command + (" (업그레이드)" if args.upgrade else ""))
    try:
        clear_error(app_dir)
        if args.command == "mysql":
            cmd_mysql(app_dir, args.options, args.upgrade)
        elif args.command == "schema":
            cmd_schema(app_dir)
        elif args.command == "verify":
            cmd_verify(app_dir)
        else:
            stop_test_mysql(app_dir)
        print("##STEP done")
        return EXIT_OK
    except SetupError as e:
        print(f"[실패] {e}")
        write_error(app_dir, str(e))
        return e.code
    except Exception as e:          # noqa: BLE001 — 예상 못 한 실패도 원인을 남기고 끝낸다
        traceback.print_exc()
        write_error(app_dir, f"예상하지 못한 오류로 설치 마무리를 끝내지 못했습니다:\n{type(e).__name__}: {e}\n"
                             f"자세한 내용은 {os.path.join(app_dir, LOG_REL)} 에 있습니다.")
        return EXIT_FAIL
    finally:
        sys.stdout, sys.stderr = saved
        log_file.close()


if __name__ == "__main__":
    sys.exit(main())
