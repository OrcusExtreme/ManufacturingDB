"""
백엔드 데이터 수집 파이프라인 동적 제어 모듈.

system_controller GUI(C++) 또는 CLI에서 파서별 활성화 여부나 파이프라인 일시 정지를
동적으로 설정하면, Watchdog 수집기(data_insert_recognization.py)가 실시간으로 이를 감지하여
특정 파서 동작을 On/Off 합니다.
"""
import os
import json

_here = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_here, "pipeline_config.json")

DEFAULT_CONFIG = {
    "enable_xml": True,
    "enable_nc": True,
    "enable_tdms": True,
    "enable_log": True,
    "enable_roughness": True,
    "enable_cad": True,
    "paused": False
}


def get_config():
    """현재 파이프라인 설정 딕셔너리를 반환합니다."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 누락된 키가 있으면 기본값으로 보정
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception as e:
        print(f"[설정 로드 경고] {CONFIG_PATH} 읽기 실패 ({e}), 기본값을 사용합니다.")
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """설정을 pipeline_config.json 파일에 저장합니다."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[설정 저장 오류] {CONFIG_PATH} 쓰기 실패: {e}")
        return False


def set_parser_enabled(parser_name, enabled=True):
    """특정 파서의 활성화 여부를 변경합니다.
    parser_name: 'xml', 'nc', 'tdms', 'log', 'roughness', 'cad'
    """
    key = f"enable_{parser_name.lower()}"
    cfg = get_config()
    cfg[key] = bool(enabled)
    save_config(cfg)


def set_paused(paused=True):
    """파이프라인 전체 일시 정지 여부를 설정합니다."""
    cfg = get_config()
    cfg["paused"] = bool(paused)
    save_config(cfg)


def pause_pipeline():
    """파이프라인 전체 일시 정지"""
    set_paused(True)


def resume_pipeline():
    """파이프라인 전체 재개"""
    set_paused(False)


def get_pipeline_status():
    """파이프라인 상태 조회 헬퍼"""
    cfg = get_config()
    return {
        "pipeline_paused": cfg.get("paused", False),
        "parsers": {
            "xml": cfg.get("enable_xml", True),
            "nc": cfg.get("enable_nc", True),
            "tdms": cfg.get("enable_tdms", True),
            "log": cfg.get("enable_log", True),
            "roughness": cfg.get("enable_roughness", True),
            "cad": cfg.get("enable_cad", True),
        },
        "raw_config": cfg
    }


def is_parser_enabled(parser_name):
    """해당 파서가 현재 활성화되어 있는지 확인합니다."""
    cfg = get_config()
    if cfg.get("paused", False):
        return False
    key = f"enable_{parser_name.lower()}"
    return cfg.get(key, True)


# 모듈 임포트 시점에 설정 파일이 없으면 기본값으로 생성
if not os.path.exists(CONFIG_PATH):
    save_config(DEFAULT_CONFIG)

