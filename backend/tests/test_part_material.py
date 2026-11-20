"""부품 소재 입력 경로 (job_manager.material_is_known / canonical_material / set_part_material,
xml_parser 의 '비어 있을 때만 채움' 규칙).

소재는 예전에 장비 XML 의 MaterialCode 에서만 들어왔다. TDMS·NC 에는 소재 정보가 없으므로 업로드 화면에서
사람이 정하고, XML 은 부품 소재가 아직 비어 있을('Unknown') 때만 채운다.

실제 MySQL 은 건드리지 않는다. 인메모리 SQLite 에 테이블을 만들고 XML 파서가 쓰는 세션·보관소 함수를
바꿔 끼운다 (test_nc_workingstep_sync 와 같은 방식). SQLite 의 '=' 는 대소문자를 구분하므로, 부품 이름의
대소문자 통일(MySQL 콜레이션에 기대는 부분)은 여기서 다루지 않는다.
"""
import os
import sys

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from DB.database import Base
from DB.models import Job, Part
import parsers.xml_parser as xml_parser
from job_manager import (UNKNOWN_MATERIAL, canonical_material, get_or_create_job,
                         material_is_known, set_part_material)

FOLDER_ID = "Proj/Part/1"


def _xml(material_tag="<MaterialCode>Inconel</MaterialCode>"):
    # StartTime 은 넣지 않는다 (SQLite 의 DateTime 은 문자열을 받지 않는다 — 실제 MySQL 은 받는다).
    return f"""<?xml version="1.0" encoding="utf-8"?>
<WorkModel>
  <WorkID>261111185522</WorkID>
  <ProjectCode>Proj</ProjectCode>
  <MachineCode>DNM200</MachineCode>
  {material_tag}
  <PartCode>Part</PartCode>
  <ProgramCode>O1110.NC</ProgramCode>
  <CuttingSeconds>598.2</CuttingSeconds>
  <IsFinish>true</IsFinish>
  <IsError>false</IsError>
</WorkModel>
"""


@pytest.fixture
def Session(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":       # MySQL 전용 기본값 때문에 모델로 만들 수 없다
            table.create(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE tool (tool_id INTEGER PRIMARY KEY, tool_code VARCHAR(20))"))
    factory = sessionmaker(bind=eng, autocommit=False, autoflush=False)   # 운영과 같은 설정
    monkeypatch.setattr(xml_parser, "SessionLocal", factory)
    monkeypatch.setattr(xml_parser, "archive_file", lambda *a, **k: None)   # 실제 보관소에 사본을 남기지 않는다
    return factory


def _part(db, name="Part", material=UNKNOWN_MATERIAL, project="Proj"):
    part = Part(part_name=name, project_code=project, material_code=material)
    db.add(part)
    db.flush()
    return part


def _parse_xml(tmp_path, xml_text):
    job_dir = tmp_path / "machining_raw_data" / "Proj" / "Part" / "1"
    (job_dir / "XML").mkdir(parents=True)
    xml_path = job_dir / "XML" / "DNM200__O1110.NC__Part__261111185522_0.xml"
    xml_path.write_text(xml_text, encoding="utf-8")
    xml_parser.parse_xml(str(xml_path), FOLDER_ID)


def _material(Session, name="Part"):
    db = Session()
    try:
        return db.query(Part).filter(Part.part_name == name).one().material_code
    finally:
        db.close()


# ---------------------------------------------------------------------------
# material_is_known
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   ", "Unknown", "unknown", " UNKNOWN ", float("nan")])
def test_placeholder_and_blank_are_not_a_material(value):
    assert material_is_known(value) is False


@pytest.mark.parametrize("value", ["AL6061", " SUS304 ", "Inconel 718"])
def test_real_material_is_known(value):
    assert material_is_known(value) is True


# ---------------------------------------------------------------------------
# canonical_material
# ---------------------------------------------------------------------------

def test_canonical_material_follows_existing_spelling(Session):
    db = Session()
    _part(db, "A", "AL6061")
    assert canonical_material(db, "al6061") == "AL6061"
    assert canonical_material(db, "  Inconel    718 ") == "Inconel 718"     # 공백 정리, 새 소재는 그대로
    assert canonical_material(db, "Unknown") is None
    assert canonical_material(db, "  ") is None
    assert canonical_material(db, "X" * 80) == "X" * 50                     # VARCHAR(50)


# ---------------------------------------------------------------------------
# set_part_material
# ---------------------------------------------------------------------------

def test_set_part_material_replaces_placeholder_and_reports_change(Session):
    db = Session()
    _part(db)
    part, changed = set_part_material(db, "Proj", "Part", "SUS304")
    assert changed and part.material_code == "SUS304"

    part, changed = set_part_material(db, "Proj", "Part", "SUS304")
    assert part is not None and not changed

    # 사람이 고친 소재도 다시 고칠 수 있다
    part, changed = set_part_material(db, "Proj", "Part", "AL7075")
    assert changed and part.material_code == "AL7075"


def test_set_part_material_ignores_unknown_input(Session):
    db = Session()
    _part(db, material="AL6061")
    assert set_part_material(db, "Proj", "Part", "Unknown") == (None, False)
    assert set_part_material(db, "Proj", "Part", "") == (None, False)
    assert set_part_material(db, "Proj", "", "AL6061") == (None, False)
    db.commit()
    assert _material(Session) == "AL6061"


def test_set_part_material_creates_part_for_cad_only_upload(Session):
    """CAD 만 올리는 새 부품: 소재를 먼저 적으면 부품이 생기고, 뒤이어 만드는 Job 이 그 부품을 쓴다."""
    db = Session()
    part, changed = set_part_material(db, "Proj", "Part", "al6061")
    assert changed and (part.project_code, part.material_code) == ("Proj", "al6061")
    db.commit()

    job = get_or_create_job(db, "Proj/Part/NEW_20261112120000")
    db.commit()
    assert db.query(Part).count() == 1
    assert job.workplan.part.material_code == "al6061"       # 'Unknown' 으로 되돌리지 않는다


def test_set_part_material_uses_existing_spelling(Session):
    db = Session()
    _part(db, "Other", "AL6061")
    _part(db)
    part, changed = set_part_material(db, "Proj", "Part", "al6061")
    assert changed and part.material_code == "AL6061"


# ---------------------------------------------------------------------------
# xml_parser: 사람이 정한 소재를 XML 이 덮어쓰지 않는다
# ---------------------------------------------------------------------------

def test_xml_does_not_overwrite_material_set_on_upload(Session, tmp_path):
    db = Session()
    _part(db, material="AL6061")
    db.commit()
    db.close()

    _parse_xml(tmp_path, _xml())          # XML 은 Inconel 이라고 적혀 있다
    assert _material(Session) == "AL6061"

    db = Session()
    assert db.query(Job).count() == 1     # 나머지 수집은 그대로 진행됐다
    db.close()


def test_xml_fills_material_when_part_has_none(Session, tmp_path):
    db = Session()
    _part(db)                              # 'Unknown'
    db.commit()
    db.close()

    _parse_xml(tmp_path, _xml())
    assert _material(Session) == "Inconel"


def test_xml_new_part_takes_material_in_existing_spelling(Session, tmp_path):
    db = Session()
    _part(db, "Other", "INCONEL")
    db.commit()
    db.close()

    _parse_xml(tmp_path, _xml())
    assert _material(Session) == "INCONEL"


def test_xml_without_material_keeps_placeholder(Session, tmp_path):
    _parse_xml(tmp_path, _xml(material_tag=""))
    assert _material(Session) == UNKNOWN_MATERIAL
