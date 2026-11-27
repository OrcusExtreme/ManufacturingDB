"""기존 Workplan 의 워킹스텝을 지금 규칙으로 NC 에서 다시 나눈다 (미리보기 / --apply).

    python backend/resync_workingsteps.py                  # 미리보기: Workplan 마다 지금 행 -> 새 행 (DB 는 그대로)
    python backend/resync_workingsteps.py --apply          # 반영. 바꾸기 전 행을 backups/ 에 JSON 으로 남긴다
    python backend/resync_workingsteps.py 2 3 [--apply]    # 고른 Workplan 만

규칙은 parsers/nc_parser.py 의 '워킹스텝 분리 규칙' — ISO 14649 처럼 가공 형상 · 공정(황삭/정삭)과 전략 ·
절삭조건 · 공구가 같은 이어진 패스가 한 스텝이다 (V3.3.2). 형상은 실제 공구 지름(Job 공구 지정 -> 공구 마스터)
으로 가르고, 형상 분류 · 크기 · 제거 체적 · 반경 절입 · 밀링 공정 이름은 부품의 소재(블록) 크기(part.stock_*)가
있을 때만 적는다 (없으면 제대로 판단할 수 없어 비움). 예전 판에서 만든 워킹스텝은
  - 같은 S·F 로 깎은 패스가 형상과 상관없이 한 행으로 합쳐져 있고 (O0911: 슬롯 두 개 -> 1행),
  - 형상 · 공정 · 전략 · 반경 절입 · 패스 정보 칸이 비어 있다.

반영은 수집 때와 같은 함수(nc_parser._apply_nc_steps)로 한다.
  - 공구 순서가 같으면 값만 고친다. step_id 가 그대로 남는다.
  - 다르면 지우고 다시 만든다. step_id 를 참조하는 표는 없고, Job 별 공구 지정(job_tool_assignment)은
    T번호 기준이라 영향이 없다.
  - operation_type 은 이제 NC 가 정하는 ISO 14649 공정이라 사람이 적은 값을 덮어쓴다 (NC 가 정하지 못한
    행만 남긴다). 바뀌는 값은 미리보기에 나오고 바꾸기 전 행은 백업에 남는다.
NC 원본(DB 아카이브 -> 원본 경로)을 찾지 못한 Workplan(XML ToolNumbers 로 만든 스텝 등)은 건드리지 않는다.
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from DB.database import SessionLocal  # noqa: E402
from DB.models import Job, Workingstep, Workplan  # noqa: E402
from parsers import nc_parser  # noqa: E402
from parsers.nc_geometry import feature_size_text  # noqa: E402
from parsers.nc_parser import STEP_VALUE_FIELDS, step_lines_text  # noqa: E402
from vault_manager import PROJECT_ROOT  # noqa: E402

BACKUP_DIR = os.path.join(PROJECT_ROOT, "backups")
ROW_FIELDS = ("step_id", "step_order", "tool_number", "xml_tool_code", "tool_id", *STEP_VALUE_FIELDS)

FIELD_LABELS = {"step_order": "순서", "operation_type": "공정", "spindle_speed": "S", "feed_rate": "F",
                "depth_of_cut": "축방향 절입", "radial_cutting_depth": "반경 절입", "pass_count": "패스 수",
                "nc_line_start": "NC 시작 줄", "nc_line_end": "NC 끝 줄", "start_position": "시작 위치",
                "end_position": "끝 위치", "cutting_distance": "절삭 길이", "removed_volume": "제거 체적",
                "feature_no": "형상 번호", "feature_type": "형상", "feature_geometry": "형상 크기",
                "strategy": "전략", "toolpaths": "패스별 경로"}

# 판정
REBUILD = "rebuild"     # 행 모양(공구 순서)이 달라 지우고 다시 만든다
UPDATE = "update"       # 행은 두고 값만 고친다
SAME = "same"           # 이미 지금 규칙과 같다
NO_NC = "no_nc"         # NC 원본을 찾지 못했다 (그대로 둔다)
NO_CUT = "no_cut"       # NC 에 절삭 블록이 없다 (그대로 둔다)


def _same_value(old, new):
    """DB 에 적힌 값과 새 값이 같은지. FLOAT 칸은 단정도라 읽어 온 값의 끝자리가 다를 수 있다."""
    if isinstance(old, str) and isinstance(new, (dict, list)):
        old = json.loads(old)           # JSON 칸을 문자열로 돌려주는 드라이버
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return math.isclose(old, new, rel_tol=1e-6, abs_tol=1e-9)
    return old == new


def changed_fields(existing, parsed):
    """값만 고칠 때 바뀌는 칸 {칸: 행 수}. 공구 순서가 같은 경우에만 부른다."""
    counts = {}
    for ws, p in zip(existing, parsed):
        for field in ("step_order", *STEP_VALUE_FIELDS):
            new = p[field]
            if field == "operation_type" and new is None:
                new = nc_parser._kept_operation(ws.operation_type)   # 사람이 적은 값만 남고 예전 NC 값은 지워진다
            if not _same_value(getattr(ws, field), new):
                counts[field] = counts.get(field, 0) + 1
    return counts


def plan(db, workplan_id):
    """Workplan 하나의 (지금 행, 새 스텝, 판정, 소재). 소재는 부품의 소재 크기 (없으면 None)."""
    existing = (db.query(Workingstep).filter(Workingstep.workplan_id == workplan_id)
                .order_by(Workingstep.step_order, Workingstep.step_id).all())
    stock = nc_parser.stock_for_workplan(db, workplan_id)
    source = nc_parser._resolve_nc_source(db, workplan_id, None, None)
    if not source:
        return existing, [], NO_NC, stock
    parsed = nc_parser.parse_nc_cutting_conditions(source, tools=nc_parser.tool_specs_for_workplan(db, workplan_id),
                                                   stock=stock)
    if not parsed:
        return existing, [], NO_CUT, stock
    if not nc_parser.same_step_layout(existing, parsed):
        return existing, parsed, REBUILD, stock
    return existing, parsed, (UPDATE if changed_fields(existing, parsed) else SAME), stock


def row_dict(ws):
    return {field: getattr(ws, field) for field in ROW_FIELDS}


def _number(value, unit=""):
    return "-" if value is None else f"{value:g}{unit}"


def _stage(values):
    """황삭/정삭: 공정 이름에서, 없으면(소재 크기를 몰라 공정 이름을 비움) 패스별 경로의 stage 에서."""
    operation = values.get("operation_type") or ""
    if operation.endswith("_rough_milling") or operation.endswith("_finish_milling"):
        return None                                         # 공정 이름에 이미 들어 있다
    toolpaths = values.get("toolpaths")
    if isinstance(toolpaths, str):
        toolpaths = json.loads(toolpaths)
    stages = {tp.get("stage") for tp in toolpaths or [] if tp.get("stage")}
    return stages.pop() if len(stages) == 1 else None


def step_line(order, tool_code, values):
    """미리보기 한 줄 (DB 원문 그대로):
    '1  T06  F1 slot W20 × L101 × D2  bottom_and_side_rough_milling · bidirectional  S3800  F710  ap 2  ae 12  패스 2  11~14줄  267mm'."""
    parts = [f"{order:>3}", f"{tool_code or '-':<4}"]
    if values.get("feature_no") is not None:
        feature = f"F{values['feature_no']}"
        if values.get("feature_type"):
            feature += f" {values['feature_type']} {feature_size_text(values.get('feature_geometry'))}"
        parts.append(feature)
    operation = values.get("operation_type") or _stage(values)
    if operation:
        parts.append(operation + (f" · {values['strategy']}" if values.get("strategy") else ""))
    parts += [f"S{_number(values.get('spindle_speed'))}", f"F{_number(values.get('feed_rate'))}",
              f"ap {_number(values.get('depth_of_cut'))}"]
    if values.get("radial_cutting_depth") is not None:
        parts.append(f"ae {_number(values['radial_cutting_depth'])}")
    if values.get("pass_count") is not None:
        lines = step_lines_text(values.get("nc_line_start"), values.get("nc_line_end"))
        parts += [f"패스 {values['pass_count']}", f"{lines}줄" if lines else "",
                  _number(values.get("cutting_distance"), "mm") if values.get("cutting_distance") else ""]
    return "  ".join(p for p in parts if p)


def print_plan(workplan, jobs, existing, parsed, verdict, stock=None):
    job_text = ", ".join(str(j) for j in jobs) or "없음"
    print(f"\n=== Workplan {workplan.workplan_id} · {workplan.program_code} · Job {job_text} ===")
    if stock:
        print(f"  소재 {stock['x']:g} × {stock['y']:g} × {stock['z']:g} mm (원점 {stock['origin']})")
    elif verdict not in (NO_NC, NO_CUT):
        print("  소재 크기 없음 — 형상 분류 · 크기 · 제거 체적 · 반경 절입 · 밀링 공정 이름은 기록하지 않습니다 "
              "(업로드 화면에서 부품의 소재 크기를 넣으면 판단합니다)")
    if verdict == NO_NC:
        print(f"  그대로 둠: NC 원본을 찾지 못했습니다 (지금 {len(existing)}행)")
        return
    if verdict == NO_CUT:
        print(f"  그대로 둠: NC 에 절삭 블록이 없습니다 (지금 {len(existing)}행)")
        return
    if verdict == SAME:
        print(f"  그대로: 이미 지금 규칙과 같습니다 ({len(existing)}행)")
        return
    if verdict == UPDATE:
        fields = " · ".join(f"{FIELD_LABELS[field]} {count}행"
                            for field, count in changed_fields(existing, parsed).items())
        print(f"  * 값만 고침: {len(existing)}행 (행 · step_id 유지) — {fields}")
    else:
        print(f"  * 다시 나눔: 지금 {len(existing)}행 -> 새 {len(parsed)}행")
        for i, ws in enumerate(existing):
            print(f"    {'지금' if i == 0 else '    '} {step_line(ws.step_order, ws.xml_tool_code, row_dict(ws))}")
    for i, p in enumerate(parsed):
        print(f"    {'새  ' if i == 0 else '    '} {step_line(p['step_order'], p['xml_tool_code'], p)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="기존 Workplan 의 워킹스텝을 지금 규칙으로 NC 에서 다시 나눈다")
    ap.add_argument("workplan_ids", nargs="*", type=int, help="고를 Workplan ID (없으면 모두)")
    ap.add_argument("--apply", action="store_true", help="DB 에 반영 (없으면 미리보기)")
    args = ap.parse_args(argv)

    from DB.schema_patch import ensure_schema
    ensure_schema(verbose=True)       # 패스 수 · NC 줄 · 위치 · 절삭 길이 칸이 있어야 한다

    db = SessionLocal()
    backup, verdicts = [], {}
    try:
        query = db.query(Workplan)
        if args.workplan_ids:
            query = query.filter(Workplan.workplan_id.in_(args.workplan_ids))
        workplans = query.order_by(Workplan.workplan_id).all()
        missing = sorted(set(args.workplan_ids) - {w.workplan_id for w in workplans})
        if missing:
            print(f"[알림] 없는 Workplan 은 건너뜁니다: {missing}")

        for wp in workplans:
            jobs = [j for (j,) in db.query(Job.job_id).filter(Job.workplan_id == wp.workplan_id)
                    .order_by(Job.job_id)]
            try:
                existing, parsed, verdict, stock = plan(db, wp.workplan_id)
            except Exception as e:      # noqa: BLE001 — 한 Workplan 이 깨져도 나머지는 본다
                db.rollback()
                print(f"\n=== Workplan {wp.workplan_id}: NC 를 읽지 못했습니다 — {e}")
                verdicts["error"] = verdicts.get("error", 0) + 1
                continue
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            print_plan(wp, jobs, existing, parsed, verdict, stock)
            if not args.apply or verdict not in (REBUILD, UPDATE):
                continue
            before = [row_dict(ws) for ws in existing]
            try:
                nc_parser._apply_nc_steps(db, wp.workplan_id, parsed)
                db.commit()
            except Exception as e:      # noqa: BLE001
                db.rollback()
                print(f"  [오류] 반영하지 못했습니다 — {e}")
                continue
            after = [row_dict(ws) for ws in db.query(Workingstep)
                     .filter(Workingstep.workplan_id == wp.workplan_id).order_by(Workingstep.step_order)]
            backup.append({"workplan_id": wp.workplan_id, "program_code": wp.program_code, "jobs": jobs,
                           "verdict": verdict, "before": before, "after": after})
        db.rollback()
    finally:
        db.close()

    labels = {REBUILD: "다시 나눔", UPDATE: "값만 고침", SAME: "그대로", NO_NC: "NC 없음",
              NO_CUT: "절삭 없음", "error": "오류"}
    print("\n[요약] " + " · ".join(f"{labels[k]} {v}" for k, v in verdicts.items()) if verdicts
          else "\n[요약] Workplan 이 없습니다.")
    if args.apply and backup:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, f"resync_workingsteps_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2, default=str)
        print(f"[반영 완료] Workplan {len(backup)}개. 바꾸기 전 행: {path}")
    elif not args.apply:
        print("[미리보기] DB 는 바꾸지 않았습니다. 반영하려면 --apply 를 붙이세요. (* 표시가 바뀌는 Workplan)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
