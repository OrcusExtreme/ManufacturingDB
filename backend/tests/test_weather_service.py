"""가공 시각 외기 온습도·날씨 자동 기록 (backend/weather_service.py).

네트워크와 실제 MySQL 은 쓰지 않는다. Open-Meteo 응답은 가짜 fetch 로 바꿔 끼우고, DB 는
인메모리 SQLite 에 모델 테이블을 만든다 (tool 은 MySQL 전용 기본값 때문에 만들지 않는다 — 여기서 안 쓴다).
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from DB.database import Base
from DB.models import EnvMemo, Job, Part, Workplan
import weather_service as ws

NOW = datetime(2026, 11, 12, 15, 0)      # 한국 시간


def _hours(day, temp=10.0, humidity=50, code=0):
    """fetch_day 와 같은 모양: 그날 0~23시."""
    return {datetime(day.year, day.month, day.day, h): {"temperature": temp + h, "humidity": humidity,
                                                        "weather_code": code}
            for h in range(24)}


@pytest.fixture
def Session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in Base.metadata.sorted_tables:
        if table.name != "tool":
            table.create(eng)
    return sessionmaker(bind=eng, autocommit=False, autoflush=False)


def _job(Session, start_time, **env):
    db = Session()
    part = db.query(Part).first()
    if part is None:
        part = Part(part_name="Part", project_code="Proj", material_code="AL6061")
        db.add(part)
        db.flush()
        db.add(Workplan(part_code=part.part_code, program_code="O1110.NC", nc_hash="NOHASH"))
        db.flush()
    wp = db.query(Workplan).first()
    job = Job(workplan_id=wp.workplan_id, work_id="W", start_time=start_time)
    db.add(job)
    db.flush()
    memo = db.query(EnvMemo).filter_by(job_id=job.job_id).first()
    if memo is None:                       # start_time 이 없으면 요일 동기화가 만들지 않는다
        memo = EnvMemo(job_id=job.job_id)
        db.add(memo)
    for key, value in env.items():
        setattr(memo, key, value)
    db.commit()
    job_id = job.job_id
    db.close()
    return job_id


def _memo(Session, job_id):
    db = Session()
    try:
        return db.query(EnvMemo).filter_by(job_id=job_id).one()
    finally:
        db.close()


class FakeFetch:
    def __init__(self, fail_days=()):
        self.calls = []
        self.fail_days = set(fail_days)

    def __call__(self, day):
        self.calls.append(day)
        if day in self.fail_days:
            raise ws.WeatherFetchError("연결 실패")
        return _hours(day, temp=10.0, humidity=60, code=2)


# ---------------------------------------------------------------------------
# 변환
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, hour", [
    (datetime(2026, 11, 11, 18, 55, 22), datetime(2026, 11, 11, 19)),
    (datetime(2026, 11, 11, 18, 29, 59), datetime(2026, 11, 11, 18)),
    (datetime(2026, 11, 11, 18, 30), datetime(2026, 11, 11, 19)),
    (datetime(2026, 11, 11, 23, 40), datetime(2026, 11, 12, 0)),               # 날짜가 넘어간다
    ("2026-09-11T16:46:05.575026+09:00", datetime(2026, 9, 11, 17)),           # XML 그대로
    ("2026-11-11T18:55:22.05", datetime(2026, 11, 11, 19)),                    # 시간대 없는 XML
    (datetime(2026, 9, 11, 7, 46, tzinfo=timezone.utc), datetime(2026, 9, 11, 17)),
    (None, None), ("", None), ("모름", None),
])
def test_nearest_hour_in_korean_time(value, hour):
    assert ws.nearest_hour(value) == hour


@pytest.mark.parametrize("code, word", [
    (0, "맑음"), (1, "대체로 맑음"), (2, "구름많음"), (3, "흐림"), (61, "약한 비"), (63, "비"),
    (73, "눈"), (95, "뇌우"), (5, "WMO 5"), (None, None), ("x", None),
])
def test_weather_text(code, word):
    assert ws.weather_text(code) == word


def test_source_round_trip():
    assert ws.format_source("open-meteo", {"weather", "temperature"}) == "open-meteo:temperature,weather"
    assert ws.parse_source("open-meteo:temperature,weather") == ("open-meteo", {"temperature", "weather"})
    assert ws.format_source("open-meteo", set()) is None
    assert ws.parse_source(None) == (None, set())


def test_describe_names_the_auto_fields_and_the_source():
    line = ws.describe("open-meteo:humidity,weather", datetime(2026, 11, 11, 19))
    assert line is not None
    assert line.startswith("습도·날씨는 가공 시각 외기 자동 기록")
    assert "2026-11-11 19:00 기준" in line and "Open-Meteo.com (CC BY 4.0)" in line
    assert ws.describe(None) is None


# ---------------------------------------------------------------------------
# 채우기
# ---------------------------------------------------------------------------

def test_fills_blank_fields_at_the_nearest_hour(Session):
    job = _job(Session, datetime(2026, 11, 11, 18, 55, 22))
    fetch = FakeFetch()
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 1
    memo = _memo(Session, job)
    assert (memo.temperature, memo.humidity, memo.weather) == (29.0, 60.0, "구름많음")   # 19시 값
    assert memo.weather_source == "open-meteo:temperature,humidity,weather"
    assert memo.weather_time == datetime(2026, 11, 11, 19)
    assert fetch.calls == [date(2026, 11, 11)]


def test_manual_values_are_never_overwritten(Session):
    job = _job(Session, datetime(2026, 9, 11, 16, 46, 6), temperature=21.5)
    ws.fill_missing_weather(Session, fetch=FakeFetch(), now=NOW, verbose=False)
    memo = _memo(Session, job)
    assert memo.temperature == 21.5                     # 사람이 적은 실내 온도
    assert (memo.humidity, memo.weather) == (60.0, "구름많음")
    assert memo.weather_source == "open-meteo:humidity,weather"


def test_value_typed_while_waiting_for_the_response_is_kept(Session):
    """응답을 기다리는 사이 업로드 화면에서 온도를 적어도 그 값을 덮어쓰지 않는다."""
    job = _job(Session, datetime(2026, 11, 11, 18, 55))
    eng = Session.kw["bind"]

    def fetch_while_someone_types(day):
        with eng.begin() as conn:
            conn.execute(text("UPDATE env_memo SET temperature = 22.0 WHERE job_id = :j"), {"j": job})
        return _hours(day, temp=10.0, humidity=60, code=2)

    assert ws.fill_missing_weather(Session, fetch=fetch_while_someone_types, now=NOW, verbose=False) == 1
    memo = _memo(Session, job)
    assert memo.temperature == 22.0
    assert (memo.humidity, memo.weather) == (60.0, "구름많음")
    assert memo.weather_source == "open-meteo:humidity,weather"


def test_fully_manual_job_is_left_alone(Session):
    job = _job(Session, datetime(2026, 9, 11, 16, 46), temperature=21.5, humidity=40.0, weather="맑음")
    fetch = FakeFetch()
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 0
    assert fetch.calls == []
    assert _memo(Session, job).weather_source is None


def test_one_request_per_day_and_none_on_the_next_run(Session):
    a = _job(Session, datetime(2026, 11, 11, 9, 10))
    b = _job(Session, datetime(2026, 11, 11, 18, 55))
    c = _job(Session, datetime(2026, 9, 11, 16, 46))
    fetch = FakeFetch()
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 3
    assert sorted(fetch.calls) == [date(2026, 9, 11), date(2026, 11, 11)]
    assert _memo(Session, a).temperature == 19.0 and _memo(Session, b).temperature == 29.0
    assert _memo(Session, c).weather_time == datetime(2026, 9, 11, 17)

    fetch.calls.clear()
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 0
    assert fetch.calls == []                            # 한 번 채운 Job 은 다시 묻지 않는다


def test_waits_until_the_hour_has_settled(Session):
    recent = _job(Session, NOW - timedelta(hours=ws.SETTLE_HOURS - 1))
    fetch = FakeFetch()
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 0
    assert fetch.calls == [] and _memo(Session, recent).weather_time is None
    later = NOW + timedelta(hours=2)
    assert ws.fill_missing_weather(Session, fetch=fetch, now=later, verbose=False) == 1


def test_job_without_start_time_is_skipped(Session):
    job = _job(Session, None)
    assert ws.fill_missing_weather(Session, fetch=FakeFetch(), now=NOW, verbose=False) == 0
    assert _memo(Session, job).weather_time is None


def test_a_failed_day_is_retried_and_others_still_fill(Session, capsys):
    ok = _job(Session, datetime(2026, 9, 11, 16, 46))
    failed = _job(Session, datetime(2026, 11, 11, 18, 55))
    fetch = FakeFetch(fail_days={date(2026, 11, 11)})
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 1
    assert _memo(Session, ok).weather_time is not None
    assert _memo(Session, failed).weather_time is None
    assert "다음 주기에 다시 시도" in capsys.readouterr().out

    fetch.fail_days.clear()
    assert ws.fill_missing_weather(Session, fetch=fetch, now=NOW, verbose=False) == 1
    assert _memo(Session, failed).weather == "구름많음"


def test_an_hour_with_no_values_is_marked_done(Session):
    job = _job(Session, datetime(2026, 11, 11, 18, 55))

    def empty(day):
        return {datetime(day.year, day.month, day.day, h): {"temperature": None, "humidity": None,
                                                            "weather_code": None} for h in range(24)}
    assert ws.fill_missing_weather(Session, fetch=empty, now=NOW, verbose=False) == 0
    memo = _memo(Session, job)
    assert memo.weather_time == datetime(2026, 11, 11, 19) and memo.weather_source is None


def test_mark_manual_drops_fields_the_person_typed(Session):
    job = _job(Session, datetime(2026, 11, 11, 18, 55))
    ws.fill_missing_weather(Session, fetch=FakeFetch(), now=NOW, verbose=False)
    eng = Session.kw["bind"]
    with eng.begin() as conn:
        conn.execute(text("UPDATE env_memo SET temperature = 22.0 WHERE job_id = :j"), {"j": job})
        ws.mark_manual(conn, job, ["temperature"])
    assert _memo(Session, job).weather_source == "open-meteo:humidity,weather"
    with eng.begin() as conn:
        ws.mark_manual(conn, job, ["humidity", "weather"])
    assert _memo(Session, job).weather_source is None


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("WEATHER_AUTOFILL", "0")
    assert ws.is_enabled() is False
    assert ws.start_weather_autofill() is None
    monkeypatch.setenv("WEATHER_AUTOFILL", "1")
    assert ws.is_enabled() is True


def test_location_from_env(monkeypatch):
    monkeypatch.delenv("WEATHER_LATITUDE", raising=False)
    monkeypatch.delenv("WEATHER_LONGITUDE", raising=False)
    assert ws.location() == (ws.DEFAULT_LATITUDE, ws.DEFAULT_LONGITUDE)
    monkeypatch.setenv("WEATHER_LATITUDE", "35.18")
    monkeypatch.setenv("WEATHER_LONGITUDE", "잘못된 값")
    assert ws.location() == (35.18, ws.DEFAULT_LONGITUDE)
