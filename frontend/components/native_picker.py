"""파일 선택창의 '파일 형식' 필터 이름을 한글 명칭으로 표시하기 위한 컴포넌트.

st.file_uploader가 만드는 <input accept="..."> 만으로는 Windows/Chrome이 필터 이름을
"사용자 지정 파일"로 붙여버린다(HTML accept 속성에는 이름을 넣는 자리가 없다).
이름을 지정할 수 있는 것은 File System Access API의 types[].description 뿐이라,
파일 '선택' 단계만 이 컴포넌트가 가로채고 업로드는 기존 Streamlit 파이프라인이 그대로 처리한다.

미지원 브라우저(Firefox/Safari)에서는 아무 것도 하지 않고 기본 동작으로 되돌아간다.
"""
import os

import streamlit.components.v1 as components

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native_picker_frontend")

_native_picker = components.declare_component("native_file_picker", path=_FRONTEND_DIR)


# 업로더는 accept에 실린 확장자 목록으로 구분한다(정렬해서 비교하므로 선언 순서와 무관).
# description은 파일 선택창의 형식 필터에 그대로 노출되는 문구다.
FILE_TYPE_FILTERS = [
    {"description": "CAD File(.stl, .stp, .step)", "extensions": [".stl", ".stp", ".step"]},
    {"description": "XML File(.xml)", "extensions": [".xml"]},
    {"description": "NC File(.nc)", "extensions": [".nc"]},
    {"description": "CSV File(.csv)", "extensions": [".csv"]},
    {"description": "TDMS File(.tdms)", "extensions": [".tdms"]},
    {"description": "LOG File(.log)", "extensions": [".log"]},
    {"description": "Excel File(.xlsx)", "extensions": [".xlsx"]},
]


def render_native_file_picker():
    """앱 어디서든 한 번만 호출하면 페이지 안의 모든 업로더에 적용된다(문서 이벤트 위임)."""
    _native_picker(filters=FILE_TYPE_FILTERS, key="native_file_picker", default=None)
