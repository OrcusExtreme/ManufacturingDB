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
from vault_manager import RAW_DATA_ROOT as RAW_DATA_DIR  # noqa: E402  경로 기준은 vault_manager 한 곳


@st.cache_data(ttl=60)
def load_data(query, params=None):
    if params:
        return pd.read_sql(text(query), con=engine, params=params)
    return pd.read_sql(text(query), con=engine)


