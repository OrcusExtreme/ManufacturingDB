import os
import pandas as pd
from DB.database import SessionLocal
from DB.models import Tool
from sqlalchemy import text
import time

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

def parse_and_insert_tools(file_path):
    print(f"\n  -> [Tool Inserter] 엑셀 파일 로드 중: {os.path.basename(file_path)}")
    
    db = SessionLocal()
    try:
        # Excel 파일 읽기
        df = pd.read_excel(file_path)
        
        # 기존 삭제 로직 제거 (가공 이력 고아 방지)
        inserted_count = 0
        updated_count = 0
        
        for index, row in df.iterrows():
            company = clean_string(row.get('회사명 '))
            raw_tool_type = clean_string(row.get('공구 종류'))
            tool_type = translate_tool_type(raw_tool_type) if raw_tool_type else None
            diameter = clean_number(row.get('직경'))
            spec = translate_spec(clean_string(row.get('규격')))
            teeth = clean_int(row.get('날 수'))
            stock = clean_int(row.get('총 개수'))
            tool_code = clean_string(row.get('5축 가공기', row.get('Tool Code', row.get('공구 번호', row.get('공구코드')))))
            memo = clean_string(row.get('Unnamed: 9', row.get('비고', row.get('Memo'))))
            
            # 유효성 검사: tool_code가 없으면 XML 런타임 데이터와 매핑이 불가능하므로 무조건 스킵 (None 증식 방지)
            if not tool_code:
                continue
                
            # Upsert 로직
            existing_tool = db.query(Tool).filter(Tool.tool_code == tool_code).first()

            if existing_tool:
                # Update
                existing_tool.company_name = company
                existing_tool.tool_type = tool_type
                existing_tool.cutter_diameter = diameter
                existing_tool.specification = spec
                existing_tool.tool_teeth = teeth
                existing_tool.stock_count = stock
                existing_tool.memo = memo
                updated_count += 1
            else:
                # Insert 새 객체 생성
                new_tool = Tool(
                    tool_code=tool_code,
                    company_name=company,
                    tool_type=tool_type,
                    cutter_diameter=diameter,
                    specification=spec,
                    tool_teeth=teeth,
                    stock_count=stock,
                    memo=memo
                )
                db.add(new_tool)
                inserted_count += 1
                
        db.commit()
        print(f"    - 공구 DB 동기화 완료! (신규 삽입: {inserted_count}건, 업데이트: {updated_count}건)\n")
        
    except Exception as e:
        db.rollback()
        print(f"    - [오류] 공구 DB 동기화 실패: {e}")
    finally:
        db.close()
