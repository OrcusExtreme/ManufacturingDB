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

RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "machining_raw_data")


@st.cache_data(ttl=60)
def load_data(query, params=None):
    if params:
        return pd.read_sql(text(query), con=engine, params=params)
    return pd.read_sql(text(query), con=engine)


