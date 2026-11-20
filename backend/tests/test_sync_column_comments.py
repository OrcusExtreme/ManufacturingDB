"""models.py 주석을 운영 DB 에 맞추는 도구 (backend/DB/sync_column_comments.py) — DB 없이 계획·DDL 만 본다."""
import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import text

from DB import sync_column_comments as scc


def _col(table, column, comment, column_type="datetime", data_type="datetime", nullable="YES",
         default=None, extra="", charset=None, collation=None):
    return {"TABLE_NAME": table, "COLUMN_NAME": column, "COLUMN_COMMENT": comment, "COLUMN_TYPE": column_type,
            "DATA_TYPE": data_type, "IS_NULLABLE": nullable, "COLUMN_DEFAULT": default, "EXTRA": extra,
            "CHARACTER_SET_NAME": charset, "COLLATION_NAME": collation}


def test_model_comments_reflect_the_tdms_basis():
    comments = scc.model_comments()
    assert "TDMS" in comments[("job", "start_time")] and "XML" not in comments[("job", "start_time")]
    assert "NC 절삭 경로" in comments[("job", "cutting_moving_distance")]
    assert "점프 제외" in comments[("job", "moving_distance")]          # G43/G49 위치 점프를 뺀 실제 이동
    assert "TDMS" in scc.model_table_comments()["job_file_archive"]


def test_column_definition_keeps_the_current_definition():
    assert scc.column_definition(_col("job", "start_time", "XML: StartTime")) == "datetime NULL"
    assert scc.column_definition(_col("job", "work_id", "XML: WorkID", "varchar(50)", "varchar", "NO",
                                      charset="utf8mb4", collation="utf8mb4_0900_ai_ci")) == \
        "varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL"
    assert scc.column_definition(_col("job", "machining_window", "", "json", "json")) == "json NULL"
    # 기본값·자동 증가가 있는 칸은 다시 적다 틀릴 수 있어 건너뛴다
    assert scc.column_definition(_col("job", "job_id", "", "int", "int", "NO", extra="auto_increment")) is None
    assert scc.column_definition(_col("tool", "stock_count", "", "int", "int", default="0")) is None


def test_plan_lists_only_changed_comments():
    wanted = {("job", "start_time"): "가공 시작 (TDMS)", ("job", "end_time"): "가공 종료"}
    live = [_col("job", "start_time", "XML: StartTime"), _col("job", "end_time", "가공 종료"),
            _col("job", "job_id", "가공 이력 고유 ID", "int", "int", "NO", extra="auto_increment"),
            _col("other", "x", "주석")]
    items = scc.plan(wanted, live)
    assert [(i["column"], i["old"], i["new"]) for i in items] == [("start_time", "XML: StartTime", "가공 시작 (TDMS)")]
    assert scc.ddl(items[0]) == ("ALTER TABLE `job` MODIFY COLUMN `start_time` datetime NULL "
                                 "COMMENT '가공 시작 (TDMS)', ALGORITHM=INPLACE, LOCK=NONE")


def test_table_plan_and_quoting():
    items = scc.table_plan({"job_file_archive": "TDMS·Parquet, 장비 XML"},
                           [{"TABLE_NAME": "job_file_archive", "TABLE_COMMENT": "예전 설명"},
                            {"TABLE_NAME": "job", "TABLE_COMMENT": "같음"}])
    assert scc.ddl(items[0]) == ("ALTER TABLE `job_file_archive` COMMENT='TDMS·Parquet, 장비 XML', "
                                 "ALGORITHM=INPLACE, LOCK=NONE")
    quoted = scc.ddl({"table": "t", "new": "it's (예: 'open-meteo:temperature')"})
    assert "it''s" in quoted
    # 반영할 때 콜론을 이스케이프하므로 'open-meteo:temperature' 를 바인드 변수로 읽지 않는다
    assert text(quoted.replace(":", r"\:"))._bindparams == {}


def test_sync_only_runs_on_mysql():
    """설치 프로그램(setup_target.sync_comments)도 부르는 함수 — MySQL 이 아니면 아무것도 하지 않는다."""
    from sqlalchemy import create_engine
    assert scc.sync(apply=True, engine=create_engine("sqlite://"), verbose=False) is None


def test_verify_after_apply():
    """반영 뒤 다시 읽은 information_schema 로, 주석만 바뀌고 정의는 그대로인지 본다."""
    work_id = _col("job", "work_id", "XML: WorkID", "varchar(50)", "varchar", "NO",
                   charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    items = scc.plan({("job", "work_id"): "작업 ID (TDMS)"}, [work_id]) + \
        scc.table_plan({"job": "Job"}, [{"TABLE_NAME": "job", "TABLE_COMMENT": ""}])
    applied_cols = [{**work_id, "COLUMN_COMMENT": "작업 ID (TDMS)"}]
    applied_tables = [{"TABLE_NAME": "job", "TABLE_COMMENT": "Job"}]
    assert scc.verify(items, applied_cols, applied_tables) == []
    # 주석이 그대로이거나, 정의(NULL 여부)가 바뀌었거나, 표 설명이 안 바뀌었으면 알린다
    assert scc.verify(items, [work_id], applied_tables) == ["job.work_id: 새 주석이 보이지 않습니다"]
    changed = [{**applied_cols[0], "IS_NULLABLE": "YES"}]
    assert "정의가 바뀌었습니다" in scc.verify(items, changed, applied_tables)[0]
    assert scc.verify(items, applied_cols, [{"TABLE_NAME": "job", "TABLE_COMMENT": ""}]) == \
        ["job (표 설명): 새 설명이 보이지 않습니다"]
