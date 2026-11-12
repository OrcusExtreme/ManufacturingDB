"""가공 시각의 외기 온습도·날씨를 env_memo 에 자동으로 채운다 (Open-Meteo, 경남 창원시 마산합포구).

무엇을 채우나
  env_memo.temperature(℃) · humidity(%) · weather(맑음/흐림/비 …) 를 가공 시작시각(job.start_time)에
  가장 가까운 정시 값으로 채운다. 온도·습도는 사람이 업로드 화면에서 적는 칸과 같은 칸이다.

규칙
  1. 빈 칸만 채운다. 사람이 적은 값은 덮어쓰지 않고, 나중에 업로드 화면에서 적으면 그 값이 이긴다.
  2. 자동으로 채운 칸을 weather_source 에 남긴다 ('open-meteo:temperature,humidity,weather').
     같은 칸에 사람이 적은 값과 외기 값이 섞이므로, 분석할 때 이 표시로 둘을 가른다.
     업로드 화면에서 온도·습도를 적으면 그 칸은 목록에서 빠진다 (mark_manual).
  3. Job 마다 한 번만 채운다. weather_time(기준 정시)이 그 표시다. 다시 받으려면 그 칸들과
     weather_time 을 비운다.
  4. 기준 정시가 SETTLE_HOURS 지난 뒤에 채운다. 그날 자료에는 아직 오지 않은 시각의 예보값이 들어
     있고, 지난 몇 시간 값도 새 예보가 들어오면 바뀐다. 한 번 채우면 다시 받지 않으므로 값이 자리
     잡은 뒤에 받는다.

왜 파서가 아니라 백그라운드 스레드인가
  수집기는 워커 스레드 하나로 파일을 차례로 처리한다. 파서 안에서 인터넷을 부르면 네트워크가 느릴 때
  수집 전체가 멈춘다. start_time 을 채우는 곳도 여러 군데(xml/tdms/log 파서, 복구 backfill)라,
  '시작시각은 있는데 아직 날씨를 채우지 않은 Job' 을 주기적으로 찾으면 어느 경로로 들어온 Job 이든
  함께 처리된다. 네트워크가 끊겨 있으면 다음 주기에 다시 시도하고, 처음 켤 때 기존 Job 도 채운다.

출처
  Open-Meteo Historical Weather API (archive-api.open-meteo.com). 키가 필요 없고 비상업 용도로 하루
  1만 회 미만 무료다. 자료가 CC BY 4.0 이라 화면에 출처를 적는다. 관측소 실측이 아니라 기상 모델의
  격자값이어서 기상청 관측과 1~2 ℃ 다를 수 있다. 날짜마다 한 번만 부른다 (그날 24시간을 한꺼번에).

설정 (.env, 모두 선택)
  WEATHER_AUTOFILL=0                    자동 기록 끄기
  WEATHER_LATITUDE / WEATHER_LONGITUDE  기준 지점 (기본: 기상청 창원 관측소, 마산합포구 가포동)
  WEATHER_PLACE_NAME                    화면에 보일 지명 (기본: 창원시 마산합포구)

job.start_time 은 한국 시간(KST)으로 본다. 실험실 MySQL 세션 시간대가 SYSTEM(KST)이라 XML 에
'+09:00' 이 붙어 들어온 값도 한국 시간으로 저장돼 있다.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

SOURCE = "open-meteo"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ATTRIBUTION = "Open-Meteo.com (CC BY 4.0)"
TIMEZONE_NAME = "Asia/Seoul"
KST = timezone(timedelta(hours=9))

# 기상청 창원 관측소(ASOS 155) 자리 — 경남 창원시 마산합포구 가포동.
DEFAULT_LATITUDE = 35.1702
DEFAULT_LONGITUDE = 128.5728
DEFAULT_PLACE_NAME = "창원시 마산합포구"

SETTLE_HOURS = 12
SWEEP_INTERVAL_SECONDS = 60 * 60
REQUEST_TIMEOUT_SECONDS = 20

# 자동으로 채우는 env_memo 칸. weather_source 에 이 순서로 적는다.
AUTO_FIELDS = ("temperature", "humidity", "weather")
FIELD_LABELS = {"temperature": "온도", "humidity": "습도", "weather": "날씨"}

# WMO 날씨 코드 -> 한국어. 0~3 은 전운량 구간이라 기상청 하늘상태(맑음 0~5 / 구름많음 6~8 /
# 흐림 9~10, 10분위)와 경계가 거의 같다: 0·1 맑은 편, 2 구름많음, 3 흐림.
WMO_TEXT = {
    0: "맑음", 1: "대체로 맑음", 2: "구름많음", 3: "흐림",
    45: "안개", 48: "안개(상고대)",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 85: "약한 소낙눈", 86: "소낙눈",
    95: "뇌우", 96: "뇌우(우박)", 99: "뇌우(우박)",
}


class WeatherFetchError(Exception):
    """날씨 자료를 받지 못했다 (네트워크 · 응답 오류). 다음 주기에 다시 시도한다."""


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
def _env_float(name, default):
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[날씨 자동 기록] .env 의 {name}='{raw}' 가 숫자가 아니라 기본값 {default} 를 씁니다.")
        return default


def location():
    """(위도, 경도). 기본은 기상청 창원 관측소 자리(마산합포구 가포동)."""
    return (_env_float("WEATHER_LATITUDE", DEFAULT_LATITUDE),
            _env_float("WEATHER_LONGITUDE", DEFAULT_LONGITUDE))


def place_name():
    return (os.getenv("WEATHER_PLACE_NAME") or "").strip() or DEFAULT_PLACE_NAME


def is_enabled():
    return (os.getenv("WEATHER_AUTOFILL") or "1").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# 값 변환
# ---------------------------------------------------------------------------
def weather_text(code):
    """WMO 날씨 코드를 한국어로. 모르면 None."""
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    return WMO_TEXT.get(code, f"WMO {code}")


def to_kst_naive(value):
    """DB · XML · 판다스에서 온 시각을 한국 시간 기준 '시간대 없는 datetime' 으로. 모르면 None.

    시간대가 붙은 값(XML 의 '+09:00')은 한국 시간으로 바꾸고, 시간대가 없는 값은 이미 한국 시간으로 본다.
    """
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):          # pandas.Timestamp
        value = value.to_pydatetime()
    if value != value:                           # pandas.NaT (자기 자신과 같지 않다)
        return None
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        try:
            value = datetime.fromisoformat(text_value.replace(" ", "T", 1))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(KST).replace(tzinfo=None)
    return value


def nearest_hour(value):
    """가공 시작시각에 가장 가까운 정시 (한국 시간). 30분은 다음 정시로 올린다."""
    moment = to_kst_naive(value)
    if moment is None:
        return None
    base = moment.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(hours=1) if moment - base >= timedelta(minutes=30) else base


def parse_source(value):
    """'open-meteo:temperature,humidity' -> ('open-meteo', {'temperature', 'humidity'})."""
    if not value:
        return None, set()
    name, _, fields = str(value).partition(":")
    return (name.strip() or None), {f.strip() for f in fields.split(",") if f.strip()}


def format_source(name, fields):
    """parse_source 의 반대. 자동으로 채운 칸이 없으면 None."""
    ordered = [f for f in AUTO_FIELDS if f in fields]
    return f"{name}:{','.join(ordered)}" if name and ordered else None


def describe(weather_source, weather_time=None):
    """화면에 붙일 한 줄. 자동으로 채운 칸이 없으면 None.

    예: '온도·습도·날씨는 가공 시각 외기 자동 기록 (창원시 마산합포구, 2026-11-11 19:00 기준 · Open-Meteo.com (CC BY 4.0))'
    """
    _, fields = parse_source(weather_source)
    labels = [FIELD_LABELS[f] for f in AUTO_FIELDS if f in fields]
    if not labels:
        return None
    when = to_kst_naive(weather_time)
    basis = f", {when:%Y-%m-%d %H:%M} 기준" if when else ""
    return f"{'·'.join(labels)}는 가공 시각 외기 자동 기록 ({place_name()}{basis} · {ATTRIBUTION})"


# ---------------------------------------------------------------------------
# 받기
# ---------------------------------------------------------------------------
def _error_reason(http_error):
    try:
        return json.load(http_error).get("reason") or http_error.reason
    except Exception:
        return http_error.reason


def fetch_day(day, latitude=None, longitude=None, timeout=REQUEST_TIMEOUT_SECONDS):
    """그날(한국 시간) 0~23시 정시 값. {datetime: {"temperature": ℃, "humidity": %, "weather_code": WMO}}

    값이 없는 시각은 None 이다. 네트워크 · 응답 오류는 WeatherFetchError 로 올린다.
    """
    if latitude is None or longitude is None:
        latitude, longitude = location()
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "hourly": "temperature_2m,relative_humidity_2m,weather_code",
        "timezone": TIMEZONE_NAME,
    })
    try:
        with urllib.request.urlopen(f"{ARCHIVE_URL}?{query}", timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise WeatherFetchError(f"HTTP {e.code}: {_error_reason(e)}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:      # 연결 실패 · 시간 초과 · 깨진 JSON
        raise WeatherFetchError(str(e)) from e
    if not isinstance(body, dict) or body.get("error"):
        reason = body.get("reason") if isinstance(body, dict) else body
        raise WeatherFetchError(str(reason or "알 수 없는 응답"))

    hourly = body.get("hourly") or {}
    stamps = hourly.get("time") or []

    def column(name):
        values = hourly.get(name) or []
        return list(values) + [None] * (len(stamps) - len(values))

    temps = column("temperature_2m")
    humidities = column("relative_humidity_2m")
    codes = column("weather_code")
    observed = {}
    for i, stamp in enumerate(stamps):
        try:
            hour = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            continue
        observed[hour] = {"temperature": temps[i], "humidity": humidities[i], "weather_code": codes[i]}
    return observed


# ---------------------------------------------------------------------------
# 채우기
# ---------------------------------------------------------------------------
def _apply(memo, hour, values):
    """env_memo 한 행의 빈 칸만 채우고 표시를 남긴다. 채운 칸 이름 목록을 돌려준다."""
    temperature, humidity = values.get("temperature"), values.get("humidity")
    incoming = {
        "temperature": float(temperature) if temperature is not None else None,
        "humidity": float(humidity) if humidity is not None else None,
        "weather": weather_text(values.get("weather_code")),
    }
    filled = [f for f in AUTO_FIELDS if getattr(memo, f) is None and incoming[f] is not None]
    for field in filled:
        setattr(memo, field, incoming[field])
    _, already = parse_source(memo.weather_source)
    memo.weather_source = format_source(SOURCE, already | set(filled))
    # 그 시각 자료를 받았으면 값이 비어 있었더라도 끝난 것으로 본다 (매 주기 같은 요청을 되풀이하지 않게).
    memo.weather_time = hour
    return filled


def fill_missing_weather(session_factory=None, fetch=None, now=None, verbose=True):
    """시작시각은 있는데 날씨를 아직 채우지 않은 Job 을 찾아 빈 칸을 채운다. 채운 Job 수를 돌려준다."""
    from sqlalchemy import or_
    from DB.models import EnvMemo, Job

    if session_factory is None:
        from DB.database import SessionLocal as session_factory
    fetch = fetch or fetch_day
    # 시험에서 시각을 넘겨받는다. 없거나 읽지 못하면 지금(한국 시간).
    current = to_kst_naive(now) or datetime.now(KST).replace(tzinfo=None)
    settled_before = current - timedelta(hours=SETTLE_HOURS)

    db = session_factory()
    try:
        candidates = (db.query(EnvMemo.env_id, Job.start_time)
                      .join(Job, Job.job_id == EnvMemo.job_id)
                      .filter(Job.start_time.isnot(None), EnvMemo.weather_time.is_(None))
                      .filter(or_(EnvMemo.temperature.is_(None), EnvMemo.humidity.is_(None),
                                  EnvMemo.weather.is_(None)))
                      .all())
        db.rollback()       # 읽기만 했다. 응답을 기다리는 동안 트랜잭션을 열어 두지 않는다.
        by_day = {}
        for env_id, start_time in candidates:
            hour = nearest_hour(start_time)
            if hour is None or hour > settled_before:
                continue        # 아직 값이 자리 잡지 않았다. 다음 주기에 다시 본다.
            by_day.setdefault(hour.date(), []).append((env_id, hour))

        filled_jobs = 0
        for day in sorted(by_day):
            try:
                observed = fetch(day)
            except Exception as e:      # 한 날짜가 실패해도 다른 날짜는 채운다
                print(f"[날씨 자동 기록] {day} 자료를 받지 못해 다음 주기에 다시 시도합니다: {e}")
                continue
            for env_id, hour in by_day[day]:
                values = observed.get(hour)
                if values is None:
                    continue    # 응답에 그 시각이 없다. 표시하지 않고 다음 주기에 다시 본다.
                # 응답을 기다리는 사이 사람이 업로드 화면에서 온도·습도를 적었을 수 있다. 행을 잠그고
                # 다시 읽어 그때도 비어 있는 칸만 채운다 (사람이 적은 값을 덮어쓰지 않는다).
                memo = (db.query(EnvMemo).filter(EnvMemo.env_id == env_id)
                        .with_for_update().one_or_none())
                if memo is None or memo.weather_time is not None:
                    db.rollback()
                    continue
                if _apply(memo, hour, values):
                    filled_jobs += 1
                db.commit()

        if filled_jobs and verbose:
            print(f"[날씨 자동 기록] Job {filled_jobs}건에 가공 시각 온습도·날씨를 채웠습니다 "
                  f"({place_name()} · {ATTRIBUTION})")
        return filled_jobs
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_manual(conn, job_id, fields):
    """사람이 적은 칸을 '자동으로 채운 칸' 목록에서 뺀다. 업로드 화면이 온도·습도를 적을 때 부른다."""
    row = conn.execute(text("SELECT weather_source FROM env_memo WHERE job_id = :jid"),
                       {"jid": job_id}).first()
    if row is None or not row[0]:
        return
    name, auto = parse_source(row[0])
    remaining = auto - set(fields)
    if remaining != auto:
        conn.execute(text("UPDATE env_memo SET weather_source = :src WHERE job_id = :jid"),
                     {"src": format_source(name, remaining), "jid": job_id})


def start_weather_autofill(interval_seconds=SWEEP_INTERVAL_SECONDS):
    """파이프라인과 함께 도는 자동 기록 스레드를 띄운다 (시작 직후 1회 + 주기 반복). 꺼져 있으면 None."""
    if not is_enabled():
        print("[날씨 자동 기록] 꺼져 있습니다 (.env 의 WEATHER_AUTOFILL=0).")
        return None

    def _loop():
        while True:
            try:
                fill_missing_weather()
            except Exception as e:
                print(f"[날씨 자동 기록 오류] {e}")
            time.sleep(interval_seconds)

    latitude, longitude = location()
    print(f"[날씨 자동 기록] {place_name()} ({latitude:.4f}, {longitude:.4f}) 기준, "
          f"{interval_seconds // 60}분마다 빈 온습도·날씨를 채웁니다 (출처 {ATTRIBUTION})")
    thread = threading.Thread(target=_loop, daemon=True, name="weather-autofill")
    thread.start()
    return thread


if __name__ == "__main__":
    # 수동 실행: 지금 채울 수 있는 Job 을 한 번 채운다 (python backend/weather_service.py)
    count = fill_missing_weather()
    print(f"채운 Job: {count}건")
