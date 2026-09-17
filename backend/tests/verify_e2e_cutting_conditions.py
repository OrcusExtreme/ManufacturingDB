"""실제 DB 연동 검증 스크립트:
1. O0911.nc 파일에서 F710, S3800 파싱 검증
2. DB의 Workingstep에 feed_rate=710.0, spindle_speed=3800.0 이 정상 반영되는지 검증
3. master_tree의 계층형 마스터 데이터 조회 쿼리가 올바르게 결과를 반환하는지 검증
"""
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text
from DB.database import SessionLocal
from DB.models import Part, Workplan, Workingstep, Tool
from parsers.nc_parser import parse_nc_cutting_conditions, sync_workplan_nc_cutting_conditions


def run_e2e_verification():
    print("=" * 60)
    print(" [1/3] O0911.nc 파일 경로 탐색 및 직접 파싱 검증")
    print("=" * 60)
    
    # 1. O0911.nc 파일 찾기
    nc_path = None
    candidates = [
        "D:/Project_Lab/Lab Database/실제 가공/NC Code/O0911.nc",
        "D:/Project_Lab/Lab Database/실제 가공/MMAS_Data/O0911.nc",
    ]
    # Check with os.walk for safety if encoding differs
    base = 'D:/Project_Lab/Lab Database'
    for item in os.listdir(base):
        if ord(item[0]) > 128: # 실제 가공
            candidate_p = os.path.join(base, item, 'NC Code', 'O0911.nc')
            if os.path.exists(candidate_p):
                nc_path = candidate_p
                break

    if not nc_path:
        for c in candidates:
            if os.path.exists(c):
                nc_path = c
                break

    assert nc_path and os.path.exists(nc_path), f"O0911.nc 파일을 찾을 수 없습니다: {nc_path}"
    print(f"  - 파일 발견: {nc_path}")

    with open(nc_path, 'rb') as f:
        content = f.read().decode('utf-8', errors='ignore')

    steps = parse_nc_cutting_conditions(content)
    print(f"  - 파싱 결과: {steps}")
    assert len(steps) >= 1, "파싱된 스텝이 없습니다."
    target_step = steps[0]

    print(f"  - 파싱된 Tool Number: {target_step['tool_number']} (기대: 6)")
    print(f"  - 파싱된 Spindle Speed: {target_step['spindle_speed']} RPM (기대: 3800.0)")
    print(f"  - 파싱된 Feed Rate: {target_step['feed_rate']} mm/min (기대: 710.0)")

    assert target_step['tool_number'] == 6, f"Tool 번호 불일치: {target_step['tool_number']}"
    assert target_step['spindle_speed'] == 3800.0, f"Spindle Speed 불일치: {target_step['spindle_speed']}"
    assert target_step['feed_rate'] == 710.0, f"Feed Rate 불일치: {target_step['feed_rate']}"
    print("  >>> [1/3 성공] O0911.nc 파싱 검증 완료 (F: 710, S: 3800)")

    print("\n" + "=" * 60)
    print(" [2/3] DB Workingstep 테이블 저장 및 연동 검증")
    print("=" * 60)
    db = SessionLocal()
    test_wp_id = None
    test_part_code = None
    try:
        from DB.models import Part, Workplan
        # 기존 동일 이름 테스트 데이터가 있으면 정리
        existing_part = db.query(Part).filter(Part.part_name == "TEST_PART_0911_VERIFY").first()
        if existing_part:
            db.delete(existing_part)
            db.commit()

        # Part 생성
        part = Part(part_name="TEST_PART_0911_VERIFY", project_code="TEST_PROJ")
        db.add(part)
        db.flush()
        test_part_code = part.part_code

        # Workplan 생성
        wp = Workplan(part_code=test_part_code, program_code="O0911.nc")
        db.add(wp)
        db.flush()
        test_wp_id = wp.workplan_id
        # NC 경로는 workplan_file_archive 에 기록한다.
        from DB.models import WorkplanFileArchive
        db.add(WorkplanFileArchive(workplan_id=test_wp_id, nc_raw_path=nc_path))
        db.flush()
        db.commit()

        # NC 가공 조건 동기화 함수 실행
        success = sync_workplan_nc_cutting_conditions(db, test_wp_id, nc_file_path=nc_path)
        assert success, "sync_workplan_nc_cutting_conditions 실행 실패"

        # DB에서 저장된 Workingstep 조회
        saved_steps = db.execute(text(
            "SELECT step_id, workplan_id, step_order, tool_number, xml_tool_code, feed_rate, spindle_speed "
            "FROM workingstep WHERE workplan_id = :w"
        ), {"w": test_wp_id}).fetchall()

        print(f"  - DB 저장된 Workingstep 개수: {len(saved_steps)}")
        assert len(saved_steps) == 1, f"저장된 스텝 개수 오류: {len(saved_steps)}"
        ws = saved_steps[0]
        print(f"  - DB 조회 결과: step_order={ws[2]}, tool_number={ws[3]}, xml_tool_code={ws[4]}, feed_rate={ws[5]}, spindle_speed={ws[6]}")

        assert ws[3] == 6, f"Tool 번호 불일치: {ws[3]}"
        assert ws[5] == 710.0, f"DB Feed Rate 불일치: {ws[5]}"
        assert ws[6] == 3800.0, f"DB Spindle Speed 불일치: {ws[6]}"
        print("  >>> [2/3 성공] DB 실제 저장 및 조회 검증 완료 (feed_rate=710.0, spindle_speed=3800.0)")

        print("\n" + "=" * 60)
        print(" [3/3] frontend/components/master_tree.py 쿼리 호환성 검증")
        print("=" * 60)
        ws_query = text(f"""
            SELECT ws.step_order AS '순서', ws.operation_type AS '작업(Op)',
                   ws.xml_tool_code AS '사용 공구',
                   ws.spindle_speed AS '주축회전수 (RPM)',
                   ws.feed_rate AS '이송속도 (mm/min)',
                   t.company_name AS '제조사',
                   t.tool_type AS '공구종류',
                   t.cutter_diameter AS '직경',
                   t.tool_teeth AS '날수'
            FROM workingstep ws
            LEFT JOIN tool t ON ws.tool_id = t.tool_id
            WHERE ws.workplan_id = :w
            ORDER BY ws.step_order
        """)
        ui_rows = db.execute(ws_query, {"w": test_wp_id}).fetchall()
        assert len(ui_rows) == 1
        ui_row = ui_rows[0]
        print(f"  - UI 쿼리 결과: 순서={ui_row[0]}, 사용공구={ui_row[2]}, 주축회전수={ui_row[3]} RPM, 이송속도={ui_row[4]} mm/min")
        assert ui_row[3] == 3800.0
        assert ui_row[4] == 710.0
        print("  >>> [3/3 성공] Frontend Master Tree 쿼리 및 데이터 정합성 검증 완료!")

    finally:
        # 테스트 데이터 정리
        if test_wp_id:
            db.execute(text("DELETE FROM workingstep WHERE workplan_id = :w"), {"w": test_wp_id})
            db.execute(text("DELETE FROM workplan WHERE workplan_id = :w"), {"w": test_wp_id})
        if test_part_code:
            db.execute(text("DELETE FROM part WHERE part_code = :p"), {"p": test_part_code})
        db.commit()
        db.close()

    print("\n" + "=" * 60)
    print(" ✅ 모든 엔드투엔드 검증 통과 (F: 710, S: 3800)")
    print("=" * 60)


if __name__ == '__main__':
    run_e2e_verification()
