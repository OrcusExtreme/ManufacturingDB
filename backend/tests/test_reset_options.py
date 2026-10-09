"""초기화 항목 선택(reset_database_and_storage) — DB 없이 도는 부분.

실제 MySQL 로 DROP · create_all 까지 돌려 보는 검증은 verify_reset.py 가 한다.
"""
import os
import subprocess
import sys
import time

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

import reset_database_and_storage as core
import reset_manager


@pytest.fixture
def calls(monkeypatch):
    """실제로 지우는 함수들을 기록만 하는 가짜로 바꾼다."""
    log = []
    monkeypatch.setattr(core, "running_modules", lambda: [])
    monkeypatch.setattr(core, "count_files", lambda root: 0)
    monkeypatch.setattr(core, "reset_database", lambda keep_tools=True, engine=None: (
        log.append(("db", keep_tools)) or {"dropped": {"job": 3, "tool": 50}, "preserved": {}}))
    monkeypatch.setattr(core, "reset_tools", lambda engine=None, raw_root=None: (
        log.append(("tools",)) or {"deleted": 50, "removed_files": []}))
    monkeypatch.setattr(core, "remove_watch_tool_files", lambda raw_root=None: (
        log.append(("watch_files",)) or []))
    monkeypatch.setattr(core, "reset_storage", lambda roots=None: log.append(("storage",)) or {})
    return log


def test_nothing_selected_refuses(calls):
    with pytest.raises(core.ResetError):
        core.run_reset()
    assert calls == []


def test_running_module_refuses_before_touching_anything(calls, monkeypatch):
    monkeypatch.setattr(core, "running_modules", lambda: [("Watchdog 수집기", 4242)])
    with pytest.raises(core.ResetError) as e:
        core.run_reset(db=True, storage=True, tools=True)
    assert "Watchdog 수집기(PID 4242)" in str(e.value) and "전체 중지" in str(e.value)
    assert calls == []


@pytest.mark.parametrize("choice,expected", [
    (dict(db=True), [("db", True)]),                                   # 공구 목록은 남긴다
    (dict(tools=True), [("tools",)]),
    (dict(storage=True), [("storage",)]),
    (dict(db=True, tools=True), [("db", False), ("watch_files",)]),    # tool 은 DB 와 함께 다시 만듦
    (dict(db=True, storage=True), [("db", True), ("storage",)]),
    (dict(db=True, storage=True, tools=True), [("db", False), ("watch_files",), ("storage",)]),
])
def test_only_chosen_items_run_in_order(calls, choice, expected):
    summary = core.run_reset(**choice)
    assert calls == expected
    if choice.get("tools") and choice.get("db"):
        assert summary["tools"]["deleted"] == 50     # DB 와 함께 지운 tool 행 수로 보고


def test_tables_to_reset_keeps_accounts_and_optionally_tools():
    tables = ["part", "job", "tool", "app_user", "app_session", "app_pending_signup", "legacy_x"]
    assert core.tables_to_reset(tables, keep_tools=True) == ["part", "job", "legacy_x"]
    assert core.tables_to_reset(tables, keep_tools=False) == ["part", "job", "tool", "legacy_x"]


def _tree(root):
    os.makedirs(os.path.join(root, "Proj", "Part", "1", "NC"))
    for rel in ("Proj/Part/1/NC/a.nc", "loose.txt", "공구 마스터.csv", "실험실 공구 정리.xlsx",
                "surface_roughness_01.csv", "Proj/공구 마스터.csv"):
        with open(os.path.join(root, *rel.split("/")), "w") as f:
            f.write("x")


def test_clean_directory_empties_but_keeps_root(tmp_path):
    _tree(tmp_path)
    folders, files = core.clean_directory(str(tmp_path))
    assert (folders, files) == (1, 4) and os.path.isdir(tmp_path) and not os.listdir(tmp_path)


def test_clean_directory_refuses_vault(tmp_path, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr(core, "is_inside_vault", lambda p: True)
    assert core.clean_directory(str(tmp_path)) == (0, 0)
    assert core.count_files(str(tmp_path)) == 6       # 하나도 지우지 않음


def test_watch_tool_files_only_at_root(tmp_path):
    _tree(tmp_path)
    removed = core.remove_watch_tool_files(str(tmp_path))
    assert removed == ["공구 마스터.csv", "실험실 공구 정리.xlsx"]
    assert os.path.exists(tmp_path / "surface_roughness_01.csv")
    assert os.path.exists(tmp_path / "Proj" / "공구 마스터.csv")     # 루트가 아닌 곳은 공구 마스터가 아니다


def test_running_modules_detects_a_real_process():
    fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                             os.path.join("frontend", "dashboard.py")])
    try:
        time.sleep(0.8)
        assert ("웹 대시보드", fake.pid) in core.running_modules()
    finally:
        fake.kill()
        fake.wait()


@pytest.mark.parametrize("argv,expected", [
    (["--db"], dict(db=True, storage=False, tools=False)),
    (["--storage", "--tools"], dict(db=False, storage=True, tools=True)),
    (["--all"], dict(db=True, storage=True, tools=True)),
])
def test_cli_flags(monkeypatch, argv, expected):
    seen = {}
    monkeypatch.setattr(core, "run_reset", lambda **kw: seen.update(kw) or {})
    assert core.main(argv) == 0 and seen == expected


def test_cli_without_flags_does_nothing(monkeypatch):
    monkeypatch.setattr(core, "run_reset", lambda **kw: pytest.fail("지우면 안 됨"))
    assert core.main([]) == 2


def test_cli_reports_refusal(monkeypatch):
    def refuse(**kw):
        raise core.ResetError("실행 중인 모듈이 있어 초기화하지 않았습니다")
    monkeypatch.setattr(core, "run_reset", refuse)
    assert core.main(["--db"]) == 1


def test_window_summary_text():
    text = reset_manager.ResetWindow._describe({
        "db": {"dropped": {"job": 1, "part": 1, "tool": 50}, "preserved": {}},
        "tools": {"deleted": 50, "removed_files": []}, "storage": {}, "vault_files": 35})
    assert text == "DB 2개 테이블 다시 만듦 · 공구 50개 삭제 · 스토리지 비움 · 보관소 파일 35개 그대로"
