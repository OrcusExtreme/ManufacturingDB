"""
원본 복원 검증 공용 로직 (단일 소스).

DB에 저장된 원본 바이너리(LONGBLOB)가 삽입 당시와 바이트 단위로 동일한지
SHA-256으로 재계산하여 증명한다. 검증 결과는 항상 3가지 상태를 갖는다:

  True  -> 일치 (복원 성공, 신뢰 가능)
  False -> 불일치 (변조/손상 감지)
  None  -> 검증 불가 (기준 해시가 기록되지 않음 - "실패"가 아니라 "모름")

`if ok:` 로만 분기하면 None(검증 불가)이 실패로 취급되고,
`if ok is not False:` 로 분기하면 None이 성공으로 취급되어 둘 다 오해를 부른다.
반드시 ok 값 자체(True/False/None)를 화면에 그대로 노출해야 한다.

이 파일 하나만 UI/백엔드가 공유해서 쓴다 - 해시 재계산 로직을 화면 쪽에서
다시 구현하면 검증의 신뢰 근거(ETL과 동일한 계산)가 깨진다.
"""
import hashlib
from dataclasses import dataclass
from typing import Optional


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class VerifyResult:
    ok: Optional[bool]        # True=일치, False=불일치, None=검증 불가(기준 해시 없음)
    hash_stored: Optional[str]
    hash_actual: str
    size_actual: int
    hash_recorded: bool       # 기준 해시가 애초에 DB에 기록되어 있었는지

    @property
    def status_label(self) -> str:
        if self.ok is True:
            return "✅ 일치 (복원 검증 성공)"
        if self.ok is False:
            return "❌ 불일치 (변조/손상 감지)"
        return "⚪ 검증 불가 (기준 해시 미기록)"


def verify_content(content: Optional[bytes], stored_hash: Optional[str]) -> Optional[VerifyResult]:
    """content(LONGBLOB에서 읽어온 원본 바이트)를 stored_hash와 비교해 재검증한다.
    content 자체가 없으면(원본이 아예 보존돼 있지 않음) None을 반환한다 - 이건
    VerifyResult의 '검증 불가'와는 다른, '복원할 원본 자체가 없음' 케이스다.
    """
    if content is None:
        return None

    actual_hash = sha256_bytes(content)
    hash_recorded = bool(stored_hash)

    if not hash_recorded:
        ok = None
    else:
        ok = (actual_hash == stored_hash)

    return VerifyResult(
        ok=ok,
        hash_stored=stored_hash,
        hash_actual=actual_hash,
        size_actual=len(content),
        hash_recorded=hash_recorded,
    )
