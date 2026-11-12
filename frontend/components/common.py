import os
import sys

import pandas as pd
import streamlit as st
from sqlalchemy import text

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BACKEND_DIR = os.path.join(PROJECT_ROOT, 'backend')
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

for _p in (BACKEND_DIR, FRONTEND_DIR):
    if _p not in sys.path:
        sys.path.append(_p)

from DB.database import engine  # noqa: E402
from DB.schema_patch import ensure_schema  # noqa: E402
from vault_manager import RAW_DATA_ROOT as RAW_DATA_DIR  # noqa: E402  경로 기준은 vault_manager 한 곳

# 화면 쿼리가 새로 생긴 컬럼(예: workingstep.depth_of_cut)을 읽는다. 대시보드가 수집기보다
# 먼저 뜨면 그 컬럼이 아직 없을 수 있으므로, 이 모듈을 처음 불러올 때(프로세스당 한 번) 맞춘다.
# 이미 있으면 아무 것도 하지 않고, DB 에 붙지 못해도 경고만 찍고 넘어간다.
ensure_schema(verbose=False)


@st.cache_data(ttl=60)
def load_data(query, params=None):
    if params:
        return pd.read_sql(text(query), con=engine, params=params)
    return pd.read_sql(text(query), con=engine)


