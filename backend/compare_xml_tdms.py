"""장비 XML 값 대 TDMS+NC 계산값 비교표 (읽기 전용 — DB 값은 바꾸지 않는다).

가공 요약(시작·종료·절삭시간·이동거리·완료 여부)은 예전에 장비 XML 이 줬고, 지금은 TDMS(CNC 채널)+NC 로
계산한다 (tdms_visualizer, job.summary_source='TDMS'). XML 이 보관된 Job 마다 두 값을 나란히 놓아
계산 규칙이 장비와 얼마나 맞는지 본다. 장비가 수집 폴더에 XML 을 계속 넣는 동안 실측 Job 이 쌓이면
완료 판정 기준(tdms_alignment.END_GAP_TOLERANCE) 같은 규칙을 이 표로 다듬는다.

    python backend/compare_xml_tdms.py              # XML 이 보관된 모든 Job
    python backend/compare_xml_tdms.py 1 2          # 지정한 Job 만
    python backend/compare_xml_tdms.py --csv 표.csv # 표를 CSV(엑셀용 UTF-8)로도 저장

판정 기준 (목표치: 시작 ±1초, 절삭시간 ±0.5초, 거리 ±1%)
    시작·종료 시각   ±1초       XML 은 초 단위로 적힌다
    절삭시간         ±0.5초
    절삭 이동거리    ±1%        TDMS 기준은 NC 절삭 경로 길이 (NC 블록 대조가 맞을 때)
    총 이동거리      ±1%        DB 값은 공구 길이 보정(G43/G49)을 켜고 끌 때 위치 표시만 공구 길이만큼 바뀌는
                                점프를 뺀 실제 이동이고, 장비 XML 은 점프도 이동으로 센다. 그래서 가공 구간에
                                적힌 뺀 양(position_jump_mm)을 DB 값에 더해 같은 정의로 비교한다 (표의 DB 칸은
                                저장된 값 그대로). 점프 기록이 없는 예전 계산값은 그대로 비교한다
    완료 여부        같아야 함  TDMS 쪽 판단 근거는 가공 구간의 program_end_basis
    작업 ID·장비·프로그램  같아야 함 (프로그램은 확장자·대소문자 차이를 무시)
"""
import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET

import pandas as pd

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

TIME_TOLERANCE_S = 1.0
CUTTING_TOLERANCE_S = 0.5
DISTANCE_TOLERANCE_PCT = 1.0

# (항목, XML 태그, DB 키, 비교 방식)
FIELDS = (
    ("가공 시작", "StartTime", "start_time", "time"),
    ("가공 종료", "FinishTime", "end_time", "time"),
    ("절삭시간(초)", "CuttingSeconds", "cutting_seconds", "seconds"),
    ("절삭 이동거리(mm)", "CuttingMovingDistance", "cutting_moving_distance", "distance"),
    ("총 이동거리(mm)", "MovingDistance", "moving_distance", "distance"),
    ("완료 여부", "IsFinish", "is_finish", "bool"),
    ("작업 ID", "WorkID", "work_id", "text"),
    ("장비", "MachineCode", "machine_code", "text"),
    ("프로그램", "ProgramCode", "program_code", "program"),
)
CSV_COLUMNS = ("job_id", "folder", "summary_source", "item", "xml", "db", "diff", "tolerance", "verdict", "note")


def _number(text):
    try:
        return float(text) if text not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _time(text):
    """XML 시각(한국 시간, ISO) -> 시간대 없는 datetime. 못 읽으면 None."""
    if not text:
        return None
    try:
        t = pd.Timestamp(text)
    except (TypeError, ValueError):
        return None
    if pd.isna(t):
        return None
    if t.tzinfo is not None:
        t = t.tz_convert("Asia/Seoul").tz_localize(None)
    return t.floor("us").to_pydatetime()        # XML 은 1/10^7 초까지 적는다


def xml_reference(xml_bytes):
    """장비 XML 의 가공 요약 값. WorkModel 이 아니면 None."""
    root = ET.fromstring(xml_bytes)
    if root.tag != "WorkModel":
        return None
    raw = {tag: root.findtext(tag) for _, tag, _, _ in FIELDS}
    out: dict = {}
    for _, tag, key, kind in FIELDS:
        text = raw[tag]
        if kind == "time":
            out[key] = _time(text)
        elif kind in ("seconds", "distance"):
            out[key] = _number(text)
        elif kind == "bool":
            out[key] = None if text is None else text.strip().lower() == "true"
        else:
            out[key] = text.strip() if text else None
    return out


def _fmt(value):
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-4]       # 1/100초까지
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _xml_definition_distance(value, db):
    """총 이동거리를 장비 XML 과 같은 정의(위치 점프 포함)로 맞춘 값과 비고.

    DB 값은 위치 표시 점프를 뺀 실제 이동이다. 가공 구간에 뺀 양(position_jump_mm)이 적혀 있으면 더한다.
    없으면(점프를 빼기 전에 계산한 값) 이미 같은 정의라 그대로 둔다.
    """
    jump_mm = db.get("position_jump_mm")
    if jump_mm is None:
        return float(value), ""
    aligned = float(value) + float(jump_mm)
    return aligned, (f"DB 값은 위치 점프 {db.get('position_jumps') or 0}번({float(jump_mm):.1f}mm)을 뺀 실제 이동 — "
                     f"XML 처럼 점프를 더한 {aligned:.1f} 로 비교")


def compare_values(xml, db):
    """항목별 비교 행 목록. xml·db 는 같은 키(start_time …)를 가진 dict."""
    from tdms_alignment import same_program

    rows = []
    for label, _, key, kind in FIELDS:
        x, d = xml.get(key), db.get(key)
        diff = tolerance = verdict = note = ""
        if x is None or d is None:
            verdict = "비교 불가"
            note = "XML 에 없음" if x is None else "DB 에 없음"
        elif kind == "time":
            sec = (pd.Timestamp(d) - pd.Timestamp(x)).total_seconds()
            diff, tolerance = f"{sec:+.2f}초", f"±{TIME_TOLERANCE_S:g}초"
            verdict = "일치" if abs(sec) <= TIME_TOLERANCE_S else "차이"
        elif kind == "seconds":
            delta = float(d) - float(x)
            diff, tolerance = f"{delta:+.2f}초", f"±{CUTTING_TOLERANCE_S:g}초"
            verdict = "일치" if abs(delta) <= CUTTING_TOLERANCE_S else "차이"
        elif kind == "distance":
            measured = float(d)
            if key == "moving_distance":
                measured, note = _xml_definition_distance(d, db)
            delta = measured - float(x)
            pct = (delta / float(x) * 100.0) if float(x) else 0.0
            diff, tolerance = f"{delta:+.1f} ({pct:+.2f}%)", f"±{DISTANCE_TOLERANCE_PCT:g}%"
            verdict = "일치" if abs(pct) <= DISTANCE_TOLERANCE_PCT else "차이"
        elif kind == "program":
            verdict = "일치" if same_program(x, d) else "차이"
        else:           # 완료 여부(bool) · 작업 ID · 장비 (글자)
            verdict = "일치" if x == d else "차이"
            if key == "is_finish" and db.get("program_end_basis"):
                note = f"TDMS 판단 근거: {db['program_end_basis']}"
        rows.append({"item": label, "xml": _fmt(x), "db": _fmt(d), "diff": diff,
                     "tolerance": tolerance, "verdict": verdict, "note": note})
    return rows


def _window(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def collect(db, job_ids=None):
    """XML 이 보관된 Job 들의 비교 행. 읽기만 한다."""
    from sqlalchemy import text

    sql = """
        SELECT j.job_id, j.source_folder, j.summary_source, j.start_time, j.end_time, j.cutting_seconds,
               j.cutting_moving_distance, j.moving_distance, j.is_finish, j.work_id, j.machine_code,
               j.machining_window, w.program_code, a.xml_file_content
        FROM job j
        JOIN job_file_archive a ON a.job_id = j.job_id
        LEFT JOIN workplan w ON w.workplan_id = j.workplan_id
        WHERE a.xml_file_content IS NOT NULL
        ORDER BY j.job_id
    """
    wanted = {int(j) for j in job_ids} if job_ids else None
    rows = []
    for rec in db.execute(text(sql)).mappings():
        if wanted is not None and rec["job_id"] not in wanted:
            continue
        try:
            xml = xml_reference(bytes(rec["xml_file_content"]))
        except ET.ParseError as e:
            rows.append({"job_id": rec["job_id"], "folder": rec["source_folder"],
                         "summary_source": rec["summary_source"], "item": "XML", "verdict": "비교 불가",
                         "note": f"XML 을 읽지 못함: {e}"})
            continue
        if xml is None:
            continue
        values = dict(rec)
        values["is_finish"] = None if rec["is_finish"] is None else bool(rec["is_finish"])
        window = _window(rec["machining_window"])
        for key in ("program_end_basis", "position_jumps", "position_jump_mm"):
            values[key] = window.get(key)
        for row in compare_values(xml, values):
            rows.append({"job_id": rec["job_id"], "folder": rec["source_folder"],
                         "summary_source": rec["summary_source"] or "임시", **row})
    return rows


def print_table(rows):
    if not rows:
        print("XML 이 보관된 Job 이 없습니다.")
        return
    width = {"item": 18, "xml": 24, "db": 24, "diff": 20, "verdict": 8}
    current = None
    for row in rows:
        if row["job_id"] != current:
            current = row["job_id"]
            print(f"\n[Job {current}] {row.get('folder') or ''}  (가공 요약 출처: {row.get('summary_source')})")
            print("  " + "".join(f"{h:<{width[k]}}" for k, h in (("item", "항목"), ("xml", "장비 XML"),
                                                                 ("db", "DB(현재)"), ("diff", "차이"),
                                                                 ("verdict", "판정"))) + "비고")
        print("  " + "".join(f"{str(row.get(k, '')):<{width[k]}}" for k in width) + (row.get("note") or ""))
    judged = [r for r in rows if r.get("verdict") in ("일치", "차이")]
    differ = [r for r in judged if r["verdict"] == "차이"]
    print(f"\n비교 {len(judged)}칸 중 허용치 밖 {len(differ)}칸"
          + (": " + ", ".join(f"Job {r['job_id']} {r['item']}" for r in differ) if differ else ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description="장비 XML 값 대 TDMS+NC 계산값 비교표 (읽기 전용)")
    parser.add_argument("job_ids", nargs="*", type=int, help="비교할 Job 번호 (없으면 XML 이 있는 모든 Job)")
    parser.add_argument("--csv", help="표를 CSV 로도 저장할 경로")
    args = parser.parse_args(argv)

    from DB.database import SessionLocal

    db = SessionLocal()
    try:
        rows = collect(db, args.job_ids)
    finally:
        db.rollback()       # 읽기만 했다
        db.close()
    print_table(rows)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV 저장: {args.csv}")
    return rows


if __name__ == "__main__":
    main()
