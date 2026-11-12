"""실제 DB 연동 검증 스크립트 (단독 실행):
1. 2026-11-11 가공 NC(O1110, tests/fixtures)를 조건별 워킹스텝 32개로 나누는지 파싱 검증
2. 실제 DB 의 workingstep 에 32행 · 절입 깊이가 반영되고, 다시 동기화해도 행이 바뀌지 않는지 검증
3. master_tree 의 계층형 마스터 데이터 조회 쿼리가 올바르게 결과를 반환하는지 검증

    python backend/tests/verify_e2e_cutting_conditions.py

테스트 행(부품 TEST_PART_STEP_VERIFY)을 만들었다가 끝나면 지운다. 시작할 때
schema_patch.ensure_schema() 를 부르므로, 실제 DB 에 workingstep.depth_of_cut 이 아직 없으면
이때 붙는다 (수집기를 켤 때와 같은 동작이다).
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
from DB.models import Part, Workplan
from DB.schema_patch import ensure_schema
from parsers.nc_parser import parse_nc_cutting_conditions, sync_workplan_nc_cutting_conditions

NC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "O1110_20261111_mmas.nc")
TEST_PART = "TEST_PART_STEP_VERIFY"

# 공구별 첫 조건 · 마지막 조건 (S, F). 예전 규칙은 공구당 한 행에 마지막 S 와 첫 F 를 섞어 적었다.
EXPECTED_ENDS = {1: ((5000.0, 150.0), (9500.0, 570.0)),
                 2: ((4500.0, 300.0), (7500.0, 900.0)),
                 3: ((3500.0, 400.0), (6000.0, 1150.0))}


def _check_steps(rows, label):
    """rows: (tool_number, spindle_speed, feed_rate, depth_of_cut) 목록"""
    assert len(rows) == 32, f"{label}: 스텝 수 {len(rows)} (기대 32)"
    per_tool = {}
    for tool, s, f, depth in rows:
        per_tool.setdefault(tool, []).append((s, f))
        assert depth == 0.5, f"{label}: 절입 깊이 {depth} (기대 0.5)"
    assert {t: len(v) for t, v in per_tool.items()} == {1: 10, 2: 11, 3: 11}, \
        f"{label}: 공구별 스텝 수 {[(t, len(v)) for t, v in per_tool.items()]}"
    for tool, (first, last) in EXPECTED_ENDS.items():
        assert per_tool[tool][0] == first and per_tool[tool][-1] == last, \
            f"{label}: T{tool} 조건 {per_tool[tool][0]} ~ {per_tool[tool][-1]} (기대 {first} ~ {last})"
    assert (1, 9500.0, 150.0) not in [(t, s, f) for t, s, f, _ in rows], \
        f"{label}: 실제로 깎은 적 없는 조합(T1 S9500/F150)이 있다"


def run_e2e_verification():
    print("=" * 60)
    print(" [1/3] O1110 (2026-11-11) 직접 파싱 검증")
    print("=" * 60)
    assert os.path.exists(NC_PATH), f"NC 파일을 찾을 수 없습니다: {NC_PATH}"
    print(f"  - 파일: {NC_PATH}")

    steps = parse_nc_cutting_conditions(NC_PATH)
    for s in steps[:3]:
        print(f"  - {s}")
    print(f"  - ... 총 {len(steps)}개")
    _check_steps([(s['tool_number'], s['spindle_speed'], s['feed_rate'], s['depth_of_cut']) for s in steps],
                 "파싱")
    print("  >>> [1/3 성공] 공구 3개 · 조건 32개로 분리, 절입 깊이 0.5 mm")

    print("\n" + "=" * 60)
    print(" [2/3] DB Workingstep 테이블 저장 및 연동 검증")
    print("=" * 60)
    added = ensure_schema(verbose=True)
    if added:
        print(f"  - 스키마 보정으로 컬럼 추가: {added}")

    db = SessionLocal()
    test_wp_id = None
    test_part_code = None
    try:
        # 기존 동일 이름 테스트 데이터가 있으면 정리 (Workplan · Workingstep 은 cascade)
        existing_part = db.query(Part).filter(Part.part_name == TEST_PART).first()
        if existing_part:
            db.delete(existing_part)
            db.commit()

        part = Part(part_name=TEST_PART, project_code="TEST_PROJ")
        db.add(part)
        db.flush()
        test_part_code = part.part_code

        wp = Workplan(part_code=test_part_code, program_code="O1110.NC", nc_hash="TESTHASH")
        db.add(wp)
        db.flush()
        test_wp_id = wp.workplan_id
        db.commit()

        assert sync_workplan_nc_cutting_conditions(db, test_wp_id, nc_file_path=NC_PATH), \
            "sync_workplan_nc_cutting_conditions 실행 실패"

        query = text("SELECT step_id, tool_number, spindle_speed, feed_rate, depth_of_cut "
                     "FROM workingstep WHERE workplan_id = :w ORDER BY step_order")
        saved = db.execute(query, {"w": test_wp_id}).fetchall()
        print(f"  - DB 저장된 Workingstep 개수: {len(saved)}")
        _check_steps([tuple(r[1:]) for r in saved], "DB")

        # 같은 NC 로 다시 동기화해도 행이 그대로여야 한다 (같은 모양이면 값만 맞춘다)
        assert sync_workplan_nc_cutting_conditions(db, test_wp_id, nc_file_path=NC_PATH)
        again = db.execute(query, {"w": test_wp_id}).fetchall()
        assert [r[0] for r in again] == [r[0] for r in saved], "재동기화 때 step_id 가 바뀌었다"
        print("  >>> [2/3 성공] 32행 저장 · 절입 깊이 반영 · 재동기화 시 행 유지")

        print("\n" + "=" * 60)
        print(" [3/3] frontend/components/master_tree.py 쿼리 호환성 검증")
        print("=" * 60)
        ws_query = text("""
            SELECT ws.step_order AS '순서', ws.operation_type AS '작업(Op)',
                   ws.xml_tool_code AS '사용 공구',
                   ws.spindle_speed AS '주축회전수 (RPM)',
                   ws.feed_rate AS '이송속도 (mm/min)',
                   ws.depth_of_cut AS '절입 깊이 (mm)',
                   t.manufacturer AS '제조사',
                   t.iso14649_class AS 'ISO 14649 분류',
                   t.cutter_diameter AS '직경',
                   t.number_of_teeth AS '날수'
            FROM workingstep ws
            LEFT JOIN tool t ON ws.tool_id = t.tool_id
            WHERE ws.workplan_id = :w
            ORDER BY ws.step_order
        """)
        ui_rows = db.execute(ws_query, {"w": test_wp_id}).fetchall()
        assert len(ui_rows) == 32
        first = ui_rows[0]
        print(f"  - 첫 행: 순서={first[0]}, 사용공구={first[2]}, 주축회전수={first[3]} RPM, "
              f"이송속도={first[4]} mm/min, 절입 깊이={first[5]} mm")
        assert (first[2], first[3], first[4], first[5]) == ("T01", 5000.0, 150.0, 0.5)
        print("  >>> [3/3 성공] Frontend Master Tree 쿼리 및 데이터 정합성 검증 완료!")

    finally:
        # 테스트 데이터 정리
        db.rollback()
        if test_wp_id:
            db.execute(text("DELETE FROM workingstep WHERE workplan_id = :w"), {"w": test_wp_id})
            db.execute(text("DELETE FROM workplan_file_archive WHERE workplan_id = :w"), {"w": test_wp_id})
            db.execute(text("DELETE FROM workplan WHERE workplan_id = :w"), {"w": test_wp_id})
        if test_part_code:
            db.execute(text("DELETE FROM part WHERE part_code = :p"), {"p": test_part_code})
        db.commit()
        db.close()

    print("\n" + "=" * 60)
    print(" ✅ 모든 엔드투엔드 검증 통과 (O1110: 공구 3개 · 워킹스텝 32개 · 절입 깊이 0.5 mm)")
    print("=" * 60)


if __name__ == '__main__':
    run_e2e_verification()
