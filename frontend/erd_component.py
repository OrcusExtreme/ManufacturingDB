import os
import json
import streamlit.components.v1 as components

# ERD Table metadata definitions matching DB schema and Korean descriptions
TABLE_METADATA = {
    "part": {
        "label": "Part",
        "kr_name": "가공 부품 마스터",
        "desc": "가공 대상 부품의 기본 코드 및 프로젝트 코드 마스터 정보",
        "color": "#38bdf8"
    },
    "cad_file_archive": {
        "label": "CadFileArchive",
        "kr_name": "CAD 3D 모델 아카이브",
        "desc": "부품(Part)에 종속된 3D CAD 모델(.step, .stp, .stl) 파일 및 바이너리",
        "color": "#818cf8"
    },
    "workplan": {
        "label": "Workplan",
        "kr_name": "ISO 14649 가공 계획",
        "desc": "단일 NC 프로그램(코드) 단위의 정적 가공 계획 마스터",
        "color": "#38bdf8"
    },
    "tool": {
        "label": "Tool",
        "kr_name": "공구 마스터",
        "desc": "절삭 가공 공구 사양(직경, 날수, 규격, 재고) 마스터 정보",
        "color": "#fb923c"
    },
    "workingstep": {
        "label": "Workingstep",
        "kr_name": "공구 호출 단위 공정",
        "desc": "Workplan 내의 개별 가공 단위(공구 번호, 가공방식, 호출 순서)",
        "color": "#fb923c"
    },
    "job": {
        "label": "Job",
        "kr_name": "실가공 이력 (Job)",
        "desc": "실제 장비에서 1회 수행된 실가공 이력 데이터의 핵심 엔티티",
        "color": "#4ade80"
    },
    "workplan_file_archive": {
        "label": "WorkplanFileArchive",
        "kr_name": "NC 코드 파일 아카이브",
        "desc": "Workplan에 연결된 원본 NC 프로그램 코드(.nc) 파일 보관",
        "color": "#818cf8"
    },
    "machine_log": {
        "label": "MachineLog",
        "kr_name": "CNC 로그 요약 통계",
        "desc": "가공 중 수집된 1Hz CNC 로그의 최대 부하, RPM, 알람 통계",
        "color": "#2dd4bf"
    },
    "log_file_archive": {
        "label": "LogFileArchive",
        "kr_name": "로그 원본 파일 아카이브",
        "desc": "CNC 기계 로그 원본 파일(.log, _ext.log)의 Vault 저장 경로",
        "color": "#818cf8"
    },
    "inspection": {
        "label": "Inspection",
        "kr_name": "가공 품질 검사 결과",
        "desc": "가공 후 치수 측정, 공차 오차, 외관 검사 및 품질 합격 여부",
        "color": "#f472b6"
    },
    "env_memo": {
        "label": "EnvMemo",
        "kr_name": "환경 및 작업자 메모",
        "desc": "작업자 정보, 소재 로트 번호, 공장 온도/습도, 절삭유 상태 메모",
        "color": "#facc15"
    },
    "surface_roughness": {
        "label": "SurfaceRoughness",
        "kr_name": "표면 조도 측정 통계",
        "desc": "Ra, Rz, Rq 표면 조도 파라미터 측정 통계 데이터",
        "color": "#f472b6"
    },
    "surface_roughness_archive": {
        "label": "SurfaceRoughnessArchive",
        "kr_name": "표면 조도 CSV 파일 아카이브",
        "desc": "표면 조도 통계 CSV 및 프로파일 곡선 CSV 원본 파일 보관",
        "color": "#818cf8"
    },
    "job_file_archive": {
        "label": "JobFileArchive",
        "kr_name": "Job XML 메타데이터 아카이브",
        "desc": "Job의 원본 metadata.xml 파일 및 시각화 Parquet 파일 보관",
        "color": "#818cf8"
    }
}

# Streamlit Bidirectional Custom Component Declaration
_COMPONENT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "erd_viewer")
_erd_component = components.declare_component("erd_viewer", path=_COMPONENT_PATH)

def render_interactive_erd(selected_table="part", table_row_counts=None, height=540, key="erd_viewer_component"):
    """
    Streamlit 화면에 양방향 통신 인터랙티브 ERD 다이어그램 컴포넌트를 렌더링하고,
    사용자가 클릭한 테이블 식별자(tableKey)를 실시간 반환합니다.
    (Light/Dark 적응형 테마, 줌/팬/리셋/테마토글 지원)
    """
    if table_row_counts is None:
        table_row_counts = {}
        
    clicked_value = _erd_component(
        selected_table=selected_table,
        table_row_counts=table_row_counts,
        height=height,
        default=selected_table,
        key=key
    )
    return clicked_value

def generate_erd_html(selected_table="part", table_row_counts=None, height=540, theme_mode="dark"):
    """
    테스트 및 정적 HTML 렌더링용 헬퍼 함수
    """
    html_path = os.path.join(_COMPONENT_PATH, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Test override theme
        if theme_mode == "light":
            content = content.replace("<body>", '<body class="light-theme">')
            
        counts_json = json.dumps(table_row_counts or {})
        init_script = f"""
        <script>
            window.addEventListener('DOMContentLoaded', () => {{
                currentSelected = "{selected_table}";
                rowCounts = {counts_json};
                renderEdges();
                renderNodes();
            }});
        </script>
        """
        return content.replace("</body>", f"{init_script}</body>")
    return ""
