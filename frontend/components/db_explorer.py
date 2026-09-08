import os

import pandas as pd
import streamlit as st
from sqlalchemy import text, inspect, create_engine

from erd_component import render_interactive_erd, TABLE_METADATA


@st.cache_data(ttl=60)
def get_table_statistics(_engine, all_db_tables):
    counts = {}
    with _engine.connect() as conn:
        for t in all_db_tables:
            try:
                res = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
                counts[t] = res or 0
            except Exception:
                counts[t] = 0
    return counts


@st.dialog("데이터베이스 Root 인증 및 저장")
def db_auth_and_save_dialog(engine, current_table, pk_cols, edited_rows, added_rows, deleted_rows):
    st.warning("경고: 데이터베이스의 실제 레코드가 수정됩니다. 인증을 위해 Root 계정 정보를 입력하세요.")

    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME", "lab_db")

    root_id = st.text_input("Root ID", value="root")
    root_pw = st.text_input("Root Password", type="password")

    if st.button("인증 및 저장 실행"):
        if not root_pw:
            st.error("비밀번호를 입력하세요.")
            return

        try:
            temp_db_url = f"mysql+pymysql://{root_id}:{root_pw}@{db_host}:{db_port}/{db_name}"
            temp_engine = create_engine(temp_db_url)

            with temp_engine.begin() as temp_conn:
                for row_idx, edits in edited_rows.items():
                    original_df = st.session_state[f"original_df_{current_table}"]
                    pk_values = {pk: original_df.iloc[row_idx][pk] for pk in pk_cols}

                    set_clauses = []
                    for col, val in edits.items():
                        if col not in pk_cols:
                            set_clauses.append(f"`{col}` = :{col}_{row_idx}")

                    if set_clauses:
                        where_clauses = [f"`{pk}` = :pk_{pk}_{row_idx}" for pk in pk_cols]
                        update_sql = f"UPDATE `{current_table}` SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)}"

                        params = {f"{col}_{row_idx}": val for col, val in edits.items() if col not in pk_cols}
                        for pk in pk_cols:
                            params[f"pk_{pk}_{row_idx}"] = pk_values[pk]

                        temp_conn.execute(text(update_sql), params)

                for i, row_data in enumerate(added_rows):
                    cols = list(row_data.keys())
                    vals = [f":v_{i}_{col}" for col in cols]
                    if cols:
                        insert_sql = f"INSERT INTO `{current_table}` ({', '.join([f'`{c}`' for c in cols])}) VALUES ({', '.join(vals)})"
                        params = {f"v_{i}_{col}": val for col, val in row_data.items()}
                        temp_conn.execute(text(insert_sql), params)

                for row_idx in deleted_rows:
                    original_df = st.session_state[f"original_df_{current_table}"]
                    pk_values = {pk: original_df.iloc[row_idx][pk] for pk in pk_cols}
                    where_clauses = [f"`{pk}` = :pk_{pk}_{row_idx}" for pk in pk_cols]
                    delete_sql = f"DELETE FROM `{current_table}` WHERE {' AND '.join(where_clauses)}"
                    params = {f"pk_{pk}_{row_idx}": pk_values[pk] for pk in pk_cols}
                    temp_conn.execute(text(delete_sql), params)

            st.success("데이터베이스에 변경사항이 성공적으로 반영되었습니다!")
            if f"editor_{current_table}" in st.session_state:
                del st.session_state[f"editor_{current_table}"]
            st.cache_data.clear()
            st.rerun()

        except Exception as e:
            st.error(f"인증 실패 또는 쿼리 실행 오류: {e}")


def render_db_explorer(engine):
    st.header("DB 테이블 조회 및 편집")
    st.markdown("아래 **데이터베이스 관계도(ERD)**에서 테이블을 선택하면 실시간 데이터를 조회할 수 있습니다. "
                 "정렬·검색·CSV 내보내기는 표의 컬럼 메뉴와 우측 상단 아이콘에서 바로 처리하고, 값을 고칠 때만 "
                 "**편집 모드**를 켜서 저장하면 Root 인증을 거쳐 실제 데이터베이스에 반영됩니다.")

    inspector = inspect(engine)
    all_db_tables = inspector.get_table_names()

    table_counts = get_table_statistics(engine, tuple(all_db_tables))

    query_params_table = st.query_params.get("table")
    if query_params_table and query_params_table in all_db_tables:
        st.session_state['selected_erd_table'] = query_params_table
    elif 'selected_erd_table' not in st.session_state or st.session_state['selected_erd_table'] not in all_db_tables:
        st.session_state['selected_erd_table'] = "part"

    current_table = st.session_state['selected_erd_table']

    with st.container(border=True):
        clicked_table = render_interactive_erd(
            selected_table=current_table,
            table_row_counts=table_counts,
            height=540,
            key="erd_viewer_component",
        )

    if clicked_table and clicked_table in all_db_tables and clicked_table != current_table:
        st.session_state['selected_erd_table'] = clicked_table
        st.query_params['table'] = clicked_table
        current_table = clicked_table
        st.rerun()

    meta_info = TABLE_METADATA.get(current_table, {
        "label": current_table,
        "kr_name": current_table,
        "desc": "데이터베이스 테이블",
    })

    with st.container(border=True):
        m1, m2, m3 = st.columns([4, 1, 1])
        with m1:
            st.markdown(f"### `{current_table}` ({meta_info.get('kr_name')})")
            st.write(f"**테이블 설명:** {meta_info.get('desc')}")
        with m2:
            st.metric(label="총 레코드 수", value=f"{table_counts.get(current_table, 0):,} 건")
        with m3:
            cols_info = inspector.get_columns(current_table)
            st.metric(label="컬럼 수", value=f"{len(cols_info)} 개")

    tab_data, tab_schema = st.tabs(["📑 테이블 데이터 조회 및 편집", "📐 테이블 스키마 및 관계 정의"])

    pk_info = inspector.get_pk_constraint(current_table)
    pk_cols = pk_info.get('constrained_columns', []) if pk_info else []
    fks = inspector.get_foreign_keys(current_table)
    fk_cols = [c for fk in fks for c in fk['constrained_columns']]

    disabled_cols = list(set(pk_cols + fk_cols))

    with tab_data:
        try:
            with engine.connect() as conn:
                f1, f2 = st.columns([1, 3])
                with f1:
                    row_limit = st.selectbox("조회 행 수 제한", [100, 300, 500, 1000, "전체"], index=0, key=f"limit_{current_table}")
                with f2:
                    st.write("")
                    edit_mode = st.toggle(
                        "✏️ 편집 모드", key=f"edit_mode_{current_table}",
                        help="켜면 표를 직접 수정·추가·삭제할 수 있습니다. 편집 중에는 Streamlit 제약으로 컬럼 정렬이 비활성화됩니다.",
                    )

                limit_clause = f"LIMIT {row_limit}" if row_limit != "전체" else ""
                df = pd.read_sql(text(f"SELECT * FROM `{current_table}` {limit_clause}"), conn)
                st.session_state[f"original_df_{current_table}"] = df.copy()

                st.caption(f"조회 결과: 총 **{len(df):,}** 행(Row) / **{len(df.columns)}** 열(Column)")

                if not edit_mode:
                    if df.empty:
                        st.warning("선택한 테이블에 데이터가 없습니다.")
                    else:
                        st.dataframe(df, width="stretch", height=420, hide_index=True)
                        st.caption("💡 컬럼 이름을 클릭하면 **오름/내림차순 정렬**과 통계를 볼 수 있고, 표 오른쪽 위 아이콘으로 "
                                   "**검색(🔍)** · **CSV 내보내기(⬇)** · 컬럼 표시/숨김을 사용할 수 있습니다.")
                else:
                    st.data_editor(
                        df, width="stretch", height=420, num_rows="dynamic",
                        key=f"editor_{current_table}", disabled=disabled_cols,
                    )

                    editor_state = st.session_state.get(f"editor_{current_table}", {})
                    edited_rows = editor_state.get("edited_rows", {})
                    added_rows = editor_state.get("added_rows", [])
                    deleted_rows = editor_state.get("deleted_rows", [])

                    if edited_rows or added_rows or deleted_rows:
                        st.info(f"수정: {len(edited_rows)}건 | 추가: {len(added_rows)}건 | 삭제: {len(deleted_rows)}건")
                        if st.button("변경사항 저장 (Save Changes)", type="primary"):
                            if not pk_cols:
                                st.error("이 테이블에는 Primary Key가 없어 수정이 불가능합니다.")
                            else:
                                db_auth_and_save_dialog(engine, current_table, pk_cols, edited_rows, added_rows, deleted_rows)

        except Exception as e:
            st.error(f"데이터 조회/편집 중 오류가 발생했습니다: {e}")

    with tab_schema:
        try:
            fk_map = {}
            for fk in fks:
                for c, rc in zip(fk['constrained_columns'], fk['referred_columns']):
                    fk_map[c] = f"🔗 {fk['referred_table']}.{rc}"

            schema_data = []
            for col in cols_info:
                c_name = col['name']
                c_type = str(col['type'])
                is_pk = "🔑 PK" if c_name in pk_cols else ""
                fk_target = fk_map.get(c_name, "")
                is_nullable = "YES" if col.get('nullable', True) else "NO (NOT NULL)"
                default_val = str(col.get('default', '')) if col.get('default') is not None else "NULL"
                comment = col.get('comment', '') or ""

                schema_data.append({
                    "컬럼명": c_name,
                    "데이터 타입": c_type,
                    "기본키(PK)": is_pk,
                    "외래키(FK) 참조": fk_target,
                    "Null 허용": is_nullable,
                    "기본값": default_val,
                    "설명(Comment)": comment,
                })

            st.dataframe(pd.DataFrame(schema_data), use_container_width=True, hide_index=True)
            st.info("PK(기본키) 및 FK(외래키)는 무결성 보호를 위해 편집이 비활성화되어 있습니다.")
        except Exception as se:
            st.error(f"스키마 조회 중 오류: {se}")
