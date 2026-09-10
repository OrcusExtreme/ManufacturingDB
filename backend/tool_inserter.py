import os
import re
import shutil
import pandas as pd
from DB.database import SessionLocal, engine
from DB.models import Tool
from sqlalchemy import text
import time
from vault_manager import get_vault_path

# 공구 마스터 원본 엑셀 보관 위치: archive_vault/tool_master/tool_info.xlsx
# (업로드/투입된 파일명과 무관하게 항상 이 이름으로 최신본 1개만 덮어써서 보관한다)
TOOL_MASTER_VAULT_DIR = "tool_master"
TOOL_MASTER_VAULT_FILENAME = "tool_info.xlsx"
TOOL_MASTER_VAULT_REL_PATH = f"{TOOL_MASTER_VAULT_DIR}/{TOOL_MASTER_VAULT_FILENAME}"

# DB 적재를 허용하는 공구 코드 형식: T + 1~2자리 숫자 (예: T1, T29)
# XML 런타임의 공구 호출(T번호)과 매핑 가능한 코드만 받아들인다.
TOOL_CODE_PATTERN = re.compile(r'^T\d{1,2}$')

# 공구 종류 영문 변환 매핑
TOOL_TYPE_MAPPING = {
    '볼 엔드밀': 'Ball End Mill',
    '엔드밀': 'End Mill',
    '페이스 커터': 'Face Cutter',
    '리머': 'Reamer',
    '드릴': 'Drill',
    '플랫 엔드밀': 'Flat End Mill',
    'PROBE(프루브)': 'Probe',
    '수동 프루브': 'Manual Probe',
    '인서트 팁': 'Insert Tip',
    '드릴 바디': 'Drill Body',
    '드릴 팁': 'Drill Tip',
    'MASTER TOOL': 'Master Tool'
}

SPEC_MAPPING = {
    '보정길이': 'Offset Length',
    '보정 길이': 'Offset Length',
    '날장': 'Flute Length',
    '전장': 'Overall Length',
    '반경': 'Radius',
    '직경': 'Diameter',
    '각도': 'Angle',
    '섕크': 'Shank',
    '샹크': 'Shank',
    '파이': 'Phi'
}

def translate_spec(val):
    if not val:
        return val
    for ko, en in SPEC_MAPPING.items():
        val = val.replace(ko, en)
    return val

def translate_tool_type(val):
    if val in TOOL_TYPE_MAPPING:
        return TOOL_TYPE_MAPPING[val]
    return val

def clean_number(val):
    if pd.isna(val) or val == '?' or str(val).strip() == '':
        return None
    val_str = str(val).replace('Ø', '').replace('ø', '').strip()
    try:
        return float(val_str)
    except ValueError:
        return None

def clean_int(val):
    if pd.isna(val) or val == '?' or str(val).strip() == '':
        return None
    try:
        return int(float(str(val).strip()))
    except ValueError:
        return None

def clean_string(val):
    if pd.isna(val) or val == '?':
        return None
    return str(val).strip()


def normalize_tool_code(val):
    """
    엑셀의 공구 코드를 'T숫자(1~2자리)' 형식으로 정규화한다.
    T4 / t4 / 'T 4' 는 'T4'로 통일하고, T19-TIP·설명 문구 등 형식에 맞지 않는 값은 None을 반환한다.
    """
    code = clean_string(val)
    if not code:
        return None
    code = code.upper().replace(' ', '').replace('　', '')
    return code if TOOL_CODE_PATTERN.match(code) else None


def archive_tool_excel(file_path):
    """
    파싱에 사용한 원본 엑셀을 archive_vault/tool_master/tool_info.xlsx 로 복사 보관한다.
    보관 폴더에 남아 있던 이전 원본은 모두 지우고 새 파일만 남긴다(최신본 1개 유지).
    (파일이 삭제되어도 자동 복구하지 않는 단순 보관용이며, 실패해도 DB 동기화는 계속 진행한다)
    """
    try:
        if not file_path or not os.path.exists(file_path):
            return None
        dest = get_vault_path(TOOL_MASTER_VAULT_DIR, TOOL_MASTER_VAULT_FILENAME)
        if os.path.abspath(file_path) == os.path.abspath(dest):
            return dest

        # 이전 원본 제거: 보관 폴더는 공구 마스터 원본 전용이므로 안에 있는 파일을 모두 정리한다.
        archive_dir = os.path.dirname(dest)
        for name in os.listdir(archive_dir):
            old_path = os.path.join(archive_dir, name)
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError as e:
                    print(f"    - [경고] 이전 원본 삭제 실패({name}): {e}")

        shutil.copy2(file_path, dest)
        return dest
    except Exception as e:
        print(f"    - [경고] 공구 엑셀 원본 보관 실패: {e}")
        return None

def resequence_tool_ids():
    """
    tool_id를 1번부터 빈 번호 없이 다시 부여한다.
    기존 정렬 순서는 그대로 두고, 삭제 등으로 생긴 중간 빈 번호만 앞으로 당긴다.
    workingstep.tool_id 참조도 같은 트랜잭션에서 함께 옮기므로 가공 이력 연결은 유지된다.
    반환값: 번호가 실제로 바뀐 공구 수
    """
    with engine.begin() as conn:
        old_ids = [row[0] for row in conn.execute(text("SELECT tool_id FROM tool ORDER BY tool_id"))]
        if not old_ids:
            conn.execute(text("ALTER TABLE tool AUTO_INCREMENT = 1"))
            return 0

        next_auto_increment = len(old_ids) + 1
        changed = sum(1 for new_id, old_id in enumerate(old_ids, start=1) if new_id != old_id)
        if changed == 0:
            conn.execute(text(f"ALTER TABLE tool AUTO_INCREMENT = {next_auto_increment}"))
            return 0

        # PK 충돌을 피하기 위해 전체를 임시 번호 구간으로 밀어둔 뒤 1번부터 다시 부여한다.
        offset = max(old_ids) + 1000
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            conn.execute(text("UPDATE tool SET tool_id = tool_id + :off"), {"off": offset})
            conn.execute(text("UPDATE workingstep SET tool_id = tool_id + :off WHERE tool_id IS NOT NULL"),
                         {"off": offset})
            for new_id, old_id in enumerate(old_ids, start=1):
                conn.execute(text("UPDATE tool SET tool_id = :new_id WHERE tool_id = :old_id"),
                             {"new_id": new_id, "old_id": old_id + offset})
                conn.execute(text("UPDATE workingstep SET tool_id = :new_id WHERE tool_id = :old_id"),
                             {"new_id": new_id, "old_id": old_id + offset})
        finally:
            # 커넥션이 풀에 반납되기 전에 반드시 원복한다 (FK 검사 해제가 다른 작업으로 새는 것 방지)
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

        # 다음 신규 공구가 N+1번을 받도록 AUTO_INCREMENT도 맞춰준다.
        conn.execute(text(f"ALTER TABLE tool AUTO_INCREMENT = {next_auto_increment}"))
        return changed


def relink_workingsteps(db):
    """
    공구 마스터를 교체한 뒤 workingstep의 공구 연결을 tool_code 기준으로 다시 맺는다.
    전체 교체 과정에서 tool_id가 새로 부여되므로, XML이 남긴 xml_tool_code(없으면 tool_number)로 재매칭한다.
    이번 엑셀에 없는 공구 번호를 호출한 Workingstep은 tool_id가 NULL로 남지만
    tool_number / xml_tool_code는 그대로 보존되므로, 나중에 엑셀에 그 공구를 추가하면 다시 연결된다.
    반환값: (연결된 Workingstep 수, 연결되지 않은 Workingstep 수)
    """
    db.execute(text("UPDATE workingstep SET tool_id = NULL"))
    db.execute(text(
        "UPDATE workingstep w "
        "JOIN tool t ON t.tool_code = COALESCE(w.xml_tool_code, CONCAT('T', w.tool_number)) "
        "SET w.tool_id = t.tool_id"
    ))
    linked = db.execute(text("SELECT COUNT(*) FROM workingstep WHERE tool_id IS NOT NULL")).scalar()
    unlinked = db.execute(text("SELECT COUNT(*) FROM workingstep WHERE tool_id IS NULL")).scalar()
    return linked, unlinked


def parse_and_insert_tools(file_path):
    print(f"\n  -> [Tool Inserter] 엑셀 파일 로드 중: {os.path.basename(file_path)}")

    db = SessionLocal()
    try:
        # Excel 파일 읽기
        df = pd.read_excel(file_path)

        # 1) 엑셀 전체를 먼저 정규화해 모은다 (같은 공구 코드가 여러 번 나오면 마지막 행 기준)
        parsed_tools = {}
        skipped_count = 0
        duplicate_count = 0

        for index, row in df.iterrows():
            company = clean_string(row.get('회사명 '))
            raw_tool_type = clean_string(row.get('공구 종류'))
            tool_type = translate_tool_type(raw_tool_type) if raw_tool_type else None
            diameter = clean_number(row.get('직경'))
            spec = translate_spec(clean_string(row.get('규격')))
            teeth = clean_int(row.get('날 수'))
            stock = clean_int(row.get('총 개수'))
            tool_code = normalize_tool_code(row.get('5축 가공기', row.get('Tool Code', row.get('공구 번호', row.get('공구코드')))))
            memo = clean_string(row.get('Unnamed: 9', row.get('비고', row.get('Memo'))))
            
            # 유효성 검사: T* / T** 형식의 공구 코드가 아니면 XML 런타임 공구 호출과 매핑이 불가능하므로 무조건 스킵한다.
            # (미입력이거나 T19-TIP·설명 문구 등 규격이 아닌 값은 DB에 적재하지 않는다)
            if not tool_code:
                skipped_count += 1
                continue

            if tool_code in parsed_tools:
                duplicate_count += 1

            parsed_tools[tool_code] = {
                "company_name": company,
                "tool_type": tool_type,
                "cutter_diameter": diameter,
                "specification": spec,
                "tool_teeth": teeth,
                "stock_count": stock,
                "memo": memo,
            }

        # 안전장치: 열 이름이 다르거나 파일이 깨져 유효한 공구가 한 건도 없으면 기존 마스터를 지우지 않는다.
        if not parsed_tools:
            db.rollback()
            print(f"    - [오류] 유효한 공구(T*/T** 코드)를 찾지 못해 교체를 취소했습니다. "
                  f"기존 공구 마스터는 그대로 유지됩니다. (검사한 행: {len(df)}건)")
            return

        # 2) 전체 교체: 기존 공구 마스터를 모두 지우고 이번 엑셀 내용만 남긴다.
        #    workingstep.tool_id는 FK(ON DELETE SET NULL)로 비워지고, 아래에서 tool_code 기준으로 다시 연결한다.
        deleted_count = db.query(Tool).delete(synchronize_session=False)
        db.flush()

        for tool_code, values in parsed_tools.items():
            db.add(Tool(tool_code=tool_code, **values))
        db.flush()

        # 3) 가공 이력(Workingstep)의 공구 연결 복구
        linked, unlinked = relink_workingsteps(db)

        db.commit()
        print(f"    - 공구 마스터 교체 완료! (기존 삭제: {deleted_count}건, 신규 등록: {len(parsed_tools)}건, "
              f"코드 형식 불일치 제외: {skipped_count}건, 중복 코드 병합: {duplicate_count}건)")
        print(f"    - Workingstep 공구 재연결: 연결 {linked}건 / 미연결 {unlinked}건(엑셀에 없는 공구 번호)")

        # 삽입/삭제 이력 때문에 tool_id가 4번부터 시작하는 등 번호가 비지 않도록 1번부터 다시 정렬한다
        try:
            resequenced = resequence_tool_ids()
            if resequenced:
                print(f"    - tool_id 정리 완료: {resequenced}건의 번호를 1번부터 빈 번호 없이 다시 부여했습니다")
        except Exception as e:
            print(f"    - [경고] tool_id 정리 실패: {e}")

        # 파싱이 성공한 경우에만 원본 엑셀을 고정 파일명으로 보관한다
        archived_path = archive_tool_excel(file_path)
        if archived_path:
            print(f"    - 원본 엑셀 보관: {archived_path}")
        print("")
        
    except Exception as e:
        db.rollback()
        print(f"    - [오류] 공구 DB 동기화 실패: {e}")
    finally:
        db.close()
