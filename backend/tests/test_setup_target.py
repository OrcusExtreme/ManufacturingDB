"""설치 마무리(installer/setup_target.py) — MySQL·서비스 없이 도는 부분.

실제로 MySQL 을 초기화하고 서비스·스키마까지 만드는 검증은 시험용 Setup 으로 한다
(installer/build_installer.py --test, README 의 '배포용 설치 파일' 참고).
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "installer"))

import setup_target as st  # noqa: E402


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,written", [
    ("610610610", "610610610"),
    (3306, "3306"),
    (r"D:\Orcus\archive_vault", r"D:\Orcus\archive_vault"),     # 기존 .env 와 같은 모양 (따옴표 없이)
    (r"D:\Orcus Data\archive_vault", r"'D:\Orcus Data\archive_vault'"),
    ("pa#ss$x", "'pa#ss$x'"),                                    # '#' 주석, '${}' 치환을 막는다
])
def test_env_quote(value, written):
    assert st.env_quote(value) == written


def test_env_values_read_back_exactly(tmp_path):
    from dotenv import dotenv_values
    values = {"DB_PASSWORD": "a#b$c{d}e;f", "ORCUS_VAULT_ROOT": r"D:\Orcus Data\archive_vault",
              "DB_HOST": "127.0.0.1", "DB_PORT": 3307}
    path = tmp_path / ".env"
    path.write_text(st.merge_env("", values), encoding="utf-8")
    read = dotenv_values(path)
    assert {k: read[k] for k in values} == {k: str(v) for k, v in values.items()}


def test_merge_env_keeps_unknown_lines_and_replaces_known_keys():
    old = "# 내 메모\nDB_HOST=localhost\nEXTRA=1\nDB_PORT=3306\nDB_PORT=9999\n"
    new = st.merge_env(old, {"DB_HOST": "127.0.0.1", "DB_PORT": 3307, "ORCUS_VAULT_ROOT": r"D:\Orcus\archive_vault"})
    lines = new.splitlines()
    assert lines[:4] == ["# 내 메모", "DB_HOST=127.0.0.1", "EXTRA=1", "DB_PORT=3307"]
    assert "DB_PORT=9999" not in new                              # 중복 키는 하나만 남긴다
    assert lines[-1] == r"ORCUS_VAULT_ROOT=D:\Orcus\archive_vault"


def test_write_env_is_utf8_and_readable_by_controller_rules(tmp_path):
    st.write_env(str(tmp_path), {"DB_NAME": "orcus", "ORCUS_VAULT_ROOT": r"C:\Orcus\archive_vault"})
    raw = (tmp_path / ".env").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")                    # BOM 이 있으면 첫 키를 못 읽는 도구가 있다
    assert b"\r\n" not in raw
    assert "ORCUS_VAULT_ROOT=C:\\Orcus\\archive_vault" in raw.decode("utf-8")


def test_setup_steps_follow_written_env_not_the_launching_shell(tmp_path, monkeypatch):
    """Setup 을 띄운 셸에 다른 DB 접속 정보가 있어도 설치 단계는 방금 쓴 .env 의 DB 를 본다."""
    st.write_env(str(tmp_path), {"DB_HOST": "127.0.0.1", "DB_PORT": 3317, "DB_NAME": "orcus",
                                 "ORCUS_VAULT_ROOT": r"D:\OrcusTest\archive_vault"})
    monkeypatch.setenv("DB_PORT", "3306")                 # 개발 PC 터미널에 남아 있던 실제 DB
    monkeypatch.setenv("DB_PASSWORD", "live-password")
    monkeypatch.setenv("ORCUS_DB_DATA_DIR", r"D:\Orcus")
    monkeypatch.setattr(sys, "path", list(sys.path))
    st.use_app_env(str(tmp_path))
    assert os.environ["DB_PORT"] == "3317"
    assert os.environ["ORCUS_VAULT_ROOT"] == r"D:\OrcusTest\archive_vault"
    assert "DB_PASSWORD" not in os.environ and "ORCUS_DB_DATA_DIR" not in os.environ   # .env 에 없는 키는 지운다


# ---------------------------------------------------------------------------
# 입력 검사
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("password", ["610610610", "Abc!#$^&*()-_=+[]{};:,.?~9"])
def test_password_allowed(password):
    st.check_password(password)


@pytest.mark.parametrize("password", ["", "p@ss", "100%", "a b", "it's", 'say"hi', "back\\slash", "한글암호"])
def test_password_rejected(password):
    with pytest.raises(st.SetupError):
        st.check_password(password)


def test_ascii_path_rule():
    st.check_ascii_path("데이터 폴더", r"D:\Orcus")
    st.check_ascii_path("데이터 폴더", r"C:\Program Data\Orcus")       # 공백은 된다
    for bad in (r"D:\연구실\Orcus", "relative\\path", ""):
        with pytest.raises(st.SetupError):
            st.check_ascii_path("데이터 폴더", bad)


@pytest.mark.parametrize("port,ok", [(3306, True), ("3307", True), (80, False), (70000, False), ("x", False)])
def test_port_rule(port, ok):
    if ok:
        assert st.check_port(port) == int(port)
    else:
        with pytest.raises(st.SetupError):
            st.check_port(port)


def _options(tmp_path, **over):
    opts = {"mode": "bundled", "port": 3306, "password": "610610610", "data_root": r"D:\Orcus"}
    opts.update(over)
    path = tmp_path / "opts.json"
    # Setup([Code] SaveStringsToUTF8File)은 BOM 을 붙여 쓴다
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(opts).encode("utf-8"))
    return str(path)


def test_load_options_defaults(tmp_path):
    o = st.load_options(_options(tmp_path))
    assert (o["db_name"], o["service"], o["no_service"], o["port"]) == ("orcus", "OrcusMySQL", False, 3306)


@pytest.mark.parametrize("bad", [{"mode": "docker"}, {"db_name": "orcus; DROP"}, {"service": "My SQL"},
                                 {"data_root": r"D:\데이터"}, {"password": "p@ss"}])
def test_load_options_rejects(tmp_path, bad):
    with pytest.raises(st.SetupError):
        st.load_options(_options(tmp_path, **bad))


# ---------------------------------------------------------------------------
# my.ini · 상태 파일
# ---------------------------------------------------------------------------
def test_my_ini_uses_forward_slashes_and_safe_defaults():
    ini = st.render_my_ini(r"D:\ManufacturingDB\mysql", r"D:\Orcus\mysql_data", 3307)
    assert 'basedir="D:/ManufacturingDB/mysql"' in ini
    assert 'datadir="D:/Orcus/mysql_data"' in ini
    assert "\\" not in ini                       # 옵션 파일에서 역슬래시는 이스케이프로 읽힌다
    for line in ("port=3307", "bind-address=127.0.0.1", "lower_case_table_names=1", "max_allowed_packet=1G",
                 "character-set-server=utf8mb4", "collation-server=utf8mb4_0900_ai_ci"):
        assert line in ini
    ini.encode("ascii")


def test_state_roundtrip_is_plain_ini(tmp_path):
    state = {"mysql": {"mode": "bundled", "service": "OrcusMySQL", "port": 3306, "datadir": r"D:\Orcus\mysql_data"},
             "paths": {"vault_root": r"D:\Orcus\archive_vault"}}
    path = st.write_state(str(tmp_path), state)
    text = open(path, encoding="ascii").read()
    assert "mode=bundled" in text and "datadir=D:\\Orcus\\mysql_data" in text   # GetIniString 이 읽는 모양
    state = st.read_state(str(tmp_path))
    assert state is not None and state["mysql"]["port"] == "3306"


def test_initialized_datadir_detection(tmp_path):
    assert not st.is_initialized_datadir(str(tmp_path))
    (tmp_path / "mysql").mkdir()
    (tmp_path / "auto.cnf").write_text("[auto]\n")
    assert st.is_initialized_datadir(str(tmp_path))


# ---------------------------------------------------------------------------
# sc query 출력 해석 (언어에 따라 문구가 다르다)
# ---------------------------------------------------------------------------
SC_EN = """
SERVICE_NAME: OrcusMySQL
        TYPE               : 10  WIN32_OWN_PROCESS
        STATE              : 4  RUNNING
                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)
        WIN32_EXIT_CODE    : 0  (0x0)
"""
SC_KO = """
SERVICE_NAME: OrcusMySQL
        종류               : 10  WIN32_OWN_PROCESS
        상태               : 1  STOPPED
        WIN32_EXIT_CODE    : 1077  (0x435)
"""


@pytest.mark.parametrize("out,state", [(SC_EN, 4), (SC_KO, 1)])
def test_service_state_parsing(monkeypatch, out, state):
    monkeypatch.setattr(st, "run", lambda args, timeout=0: (0, out))
    assert st.service_state("OrcusMySQL") == state


def test_service_missing(monkeypatch):
    monkeypatch.setattr(st, "run", lambda args, timeout=0: (1060, "[SC] OpenService FAILED 1060"))
    assert st.service_state("OrcusMySQL") is None


class FakeServices:
    """sc · net · mysqld --install 을 흉내 낸다 (정식 판의 서비스 경로는 관리자 권한이 있어야 실제로 돈다)."""

    def __init__(self, state=None):
        self.state = state          # None 없음 · 1 정지 · 4 실행 중
        self.calls = []

    def run(self, args, timeout=0, check=False):
        args = [str(a) for a in args]
        self.calls.append(args)
        exe = os.path.basename(args[0]).lower()
        if exe == "sc.exe" and args[1] == "query":
            if self.state is None:
                return 1060, "[SC] EnumQueryServicesStatus:OpenService FAILED 1060"
            name = {1: "STOPPED", 4: "RUNNING"}[self.state]
            return 0, f"SERVICE_NAME: {args[2]}\n  STATE : {self.state}  {name}\n"
        if exe == "sc.exe" and args[1] == "delete":
            self.state = None
        elif exe == "net.exe" and args[1] == "stop":
            self.state = 1
        elif exe == "net.exe" and args[1] == "start":
            self.state = 4
        elif exe == "mysqld.exe" and args[1] == "--install":
            self.state = 1
            return 0, "Service successfully installed."
        return 0, ""


def test_register_service_replaces_an_old_registration(monkeypatch):
    fake = FakeServices(state=4)                      # 예전 설치 폴더를 가리키는 서비스가 돌고 있다
    monkeypatch.setattr(st, "run", fake.run)
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    st.register_service("OrcusMySQL", r"D:\ManufacturingDB\mysql\bin\mysqld.exe", r"D:\ManufacturingDB\mysql\my.ini")
    st.start_service("OrcusMySQL")
    verbs = [c[1] if os.path.basename(c[0]).lower() != "mysqld.exe" else "install" for c in fake.calls
             if c[1] != "query"]
    assert verbs == ["stop", "delete", "install", "description", "failure", "start"]
    install = next(c for c in fake.calls if c[1] == "--install")
    assert install[2:] == ["OrcusMySQL", r"--defaults-file=D:\ManufacturingDB\mysql\my.ini"]
    failure = next(c for c in fake.calls if c[1] == "failure")
    assert failure[2:] == ["OrcusMySQL", "reset=", "86400", "actions=", "restart/10000/restart/10000/restart/60000"]
    assert fake.state == 4


def test_register_service_without_admin_rights_is_explained(monkeypatch):
    fake = FakeServices()
    def denied(args, timeout=0, check=False):
        if os.path.basename(str(args[0])).lower() == "mysqld.exe":
            return 1, "Install/Remove of the Service Denied!"
        return fake.run(args, timeout)
    monkeypatch.setattr(st, "run", denied)
    with pytest.raises(st.SetupError) as e:
        st.register_service("OrcusMySQL", r"C:\x\mysqld.exe", r"C:\x\my.ini")
    assert "관리자 권한" in str(e.value)


# ---------------------------------------------------------------------------
# Setup 과의 약속: 실패하면 설명 파일 + 종료 코드
# ---------------------------------------------------------------------------
def test_failure_writes_error_file_and_code(tmp_path):
    code = st.main(["mysql", "--app-dir", str(tmp_path), "--options", _options(tmp_path, password="p@ss")])
    assert code == st.EXIT_FAIL
    err = (tmp_path / "logs" / "install_error.txt").read_text(encoding="utf-8")
    assert "비밀번호" in err
    log = (tmp_path / "logs" / "install.log").read_text(encoding="utf-8")
    assert "[실패]" in log


def test_upgrade_without_previous_state_is_explained(tmp_path):
    assert st.main(["mysql", "--app-dir", str(tmp_path), "--upgrade"]) == st.EXIT_FAIL
    assert "install_state.ini" in (tmp_path / "logs" / "install_error.txt").read_text(encoding="utf-8")


def test_step_markers_are_ascii(capsys):
    st.step("mysql_init", "MySQL 데이터 폴더를 초기화합니다.")
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "##STEP mysql_init"
    first.encode("ascii")
