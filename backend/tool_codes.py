"""공구 코드 표기 규칙 — 단일 출처.

DB 에 저장하는 표준 표기는 **두 자리 0 채움**이다.

    T1  -> T01      T6  -> T06      T29 -> T29

왜 0 을 채우나
--------------
문자열로 정렬하면 T1 · T11 · T2 순이 되어 사람이 보는 목록이 뒤섞인다. 자릿수를 맞추면
정렬이 번호 순과 일치하고, 화면·엑셀·NC 어디에 나와도 폭이 같아 눈으로 비교하기 쉽다.

왜 매칭은 숫자로 하나
--------------------
표기를 바꾸면 문자열 비교가 전부 깨진다. NC 코드는 `T6` 으로 적히고 엑셀은 `T06` 으로
적힐 수 있는데, 예전에는 세 곳에서 `f"T{번호}"` 로 문자열을 만들어 `tool.tool_code` 와
그대로 비교했다(`tool_inserter` 의 relink SQL, `nc_parser`, `xml_parser`). 그 방식은
양쪽 표기가 한 글자라도 다르면 조용히 연결이 끊긴다 — tool_id 가 NULL 로 남고 화면에서는
공구가 '미연결' 로 보인다.

공구를 가리키는 진짜 값은 **번호**다. 그래서 표기는 사람에게 보여줄 때만 쓰고,
매칭은 언제나 숫자로 한다. 이 모듈이 그 둘을 잇는 유일한 자리다.

    canonical_tool_code('T6')   -> 'T06'      저장·표시용
    tool_code_number('T06')     -> 6          매칭용
    tool_code_number('T6')      -> 6          같은 공구로 인식
"""
import re

# 유효한 공구 코드: T + (앞의 0 은 무시) 1~2자리 숫자.
# 'T19-TIP' 이나 설명 문구처럼 형식에 맞지 않는 엑셀 칸을 걸러내는 역할도 한다.
_TOOL_CODE_RE = re.compile(r"^T0*(\d{1,2})$")

# 저장 표기의 자릿수. 두 자리면 T01~T99 가 모두 같은 폭이 된다.
TOOL_CODE_DIGITS = 2


def tool_code_number(value):
    """어떤 표기로 들어와도 공구 **번호**(int)를 돌려준다. 형식에 맞지 않으면 None.

    받아들이는 형태: 'T6' · 'T06' · 't6' · 'T 6' · 6 · '6' · 6.0
    (엑셀은 숫자 칸을 float 으로 읽어 오는 일이 잦다)
    """
    if value is None:
        return None

    # 숫자로 들어온 경우 (엑셀 셀, DB 의 tool_number 등)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 99 else None
    if isinstance(value, float):
        if value != int(value):
            return None
        n = int(value)
        return n if 0 <= n <= 99 else None

    text = str(value).strip().upper()
    if not text:
        return None
    # 전각 공백까지 걷어낸다 (엑셀에 손으로 적은 값에 섞여 있다)
    text = text.replace(" ", "").replace("　", "")
    if not text:
        return None

    m = _TOOL_CODE_RE.match(text)
    if m:
        return int(m.group(1))

    # 'T' 없이 숫자만 적힌 칸
    if text.isdigit() and len(text) <= 2:
        return int(text)
    return None


# 장비가 붙이는 꼬리표까지 허용하는 느슨한 형태.
# 실측 XML 의 <Tools> 는 toolCode 를 'T6_10' 으로 적는다(공구 6 의 변형 표기).
_TOOL_LABEL_RE = re.compile(r"^T0*(\d{1,2})(?![0-9])")


def tool_number_from_label(value):
    """공구를 **가리키는 이름표**에서 번호를 뽑는다. 'T6_10' -> 6, 'T19-TIP' -> 19.

    tool_code_number 와 왜 나뉘어 있나
    ---------------------------------
    두 함수는 묻는 것이 다르다.

      tool_code_number       "이 칸이 공구 마스터의 정식 코드인가?"  -> 엄격
      tool_number_from_label "이 이름표가 어느 공구를 가리키는가?"   -> 느슨

    공구 마스터(엑셀)에서는 엄격해야 한다. 'T19-TIP' 같은 변형 행을 받아들이면 공구 19 와
    별개인 가짜 공구가 마스터에 생긴다. 반대로 '어느 공구를 가리키는가' 를 물을 때는
    느슨해야 한다 — 실제 XML 이 'T6_10' 으로 적는데 엄격하게 보면 아무 공구도 못 찾는다.

    예전에는 이 구분이 없어서 `tool_properties['T6_10']` 에 담아 두고 `'T6'` 로 찾았고,
    그래서 XML 의 공구 제원(사용 횟수·오프셋)이 **한 번도 붙지 않았다**.
    """
    if value is None:
        return None
    n = tool_code_number(value)      # 정식 표기면 그대로
    if n is not None:
        return n
    text = str(value).strip().upper().replace(" ", "").replace("　", "")
    m = _TOOL_LABEL_RE.match(text)
    return int(m.group(1)) if m else None


def format_tool_code(number):
    """공구 번호를 표준 표기로. 6 -> 'T06'. 번호를 모르면 None."""
    if number is None:
        return None
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return f"T{n:0{TOOL_CODE_DIGITS}d}"


def canonical_tool_code(value):
    """어떤 표기로 들어와도 표준 표기로 바꾼다. 형식에 맞지 않으면 None.

    'T6' -> 'T06' · 't 6' -> 'T06' · 'T29' -> 'T29' · 'T19-TIP' -> None
    """
    return format_tool_code(tool_code_number(value))


def same_tool(a, b):
    """두 표기가 같은 공구를 가리키는지 (번호로 비교). 둘 중 하나라도 모르면 False."""
    na, nb = tool_code_number(a), tool_code_number(b)
    return na is not None and na == nb


def index_by_number(pairs):
    """(코드, 값) 쌍들을 **번호** 기준 딕셔너리로 만든다.

    공구 마스터를 조회해 매칭 표를 만들 때 쓴다. 표기가 T6 이든 T06 이든 같은 칸에 들어가므로,
    옛 표기가 남아 있는 DB 에서도 연결이 끊기지 않는다.
    """
    out = {}
    for code, value in pairs:
        n = tool_code_number(code)
        if n is not None and n not in out:
            out[n] = value
    return out
