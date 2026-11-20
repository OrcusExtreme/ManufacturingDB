"""배포 판 번호 일치 (installer/build_installer.py 의 VERSION_MARKS).

README 릴리즈 노트 맨 위(### [Vx.y.z])가 설치 파일의 판 번호다. README 배지 · 대시보드 사이드바 ·
컨트롤러 버전 정보(.rc)가 다르면 정식 설치 파일을 만들지 않으므로, 빌드 전에 테스트에서 먼저 잡는다
(V3.2.0 을 내고도 대시보드·컨트롤러가 v3.1.0 으로 남아 있던 일).
"""
import importlib.util
import os
import shutil

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_installer():
    spec = importlib.util.spec_from_file_location(
        "build_installer", os.path.join(PROJECT, "installer", "build_installer.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_version_mark_matches_the_release_notes():
    bi = _build_installer()
    version = bi.app_version()
    assert bi.version_mismatches(version) == []


def test_a_forgotten_version_mark_is_reported(tmp_path):
    bi = _build_installer()
    for rel, _, _ in bi.VERSION_MARKS:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.path.join(PROJECT, rel), dest)
    version = bi.app_version()
    dashboard = tmp_path / "frontend" / "dashboard.py"
    dashboard.write_text(dashboard.read_text(encoding="utf-8").replace(f">v{version}<", ">v3.1.0<"),
                         encoding="utf-8")
    assert bi.version_mismatches(version, project=tmp_path) == ["frontend/dashboard.py (대시보드 사이드바): 3.1.0"]
