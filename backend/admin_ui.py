"""Controller 가 띄우는 관리 창(유저 관리 · DB·스토리지 초기화)의 공통 부분.

- 컨트롤러(controller/theme.h)와 같은 팔레트 · 버튼 규칙 — 창들이 한 프로그램처럼 보이게 한다
- 창 하나만 띄우기 — 버튼을 두 번 눌러도 이미 열린 창을 앞으로 가져온다
- 고해상도(DPI) 대응 — 글자가 번지지 않게 하고, 픽셀로 준 크기를 화면 배율에 맞춘다
"""
import ctypes
import os
import sys
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
ICON_PATH = os.path.join(PROJECT_DIR, "assets", "logo.ico")

# 컨트롤러(controller/theme.h)와 같은 팔레트
BG = "#FFFFFF"
EDGE = "#DADCE0"
TEXT = "#202124"
TEXT_DIM = "#5F6368"
ACCENT = "#1A73E8"
ACCENT_DK = "#1765CC"
ACCENT_BG = "#E8F0FE"
DANGER = "#D93025"
DANGER_BG = "#FCE8E6"
SUCCESS = "#1E8E3E"
SURFACE = "#F8F9FA"
FONT = "Malgun Gothic"

_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9


def claim_single_instance(mutex_name, window_title):
    """처음 뜬 창이면 True. 이미 떠 있으면 그 창(제목으로 찾음)을 앞으로 가져오고 False.

    이름 있는 뮤텍스는 프로세스가 끝나면 OS 가 거둬 가므로, 창이 비정상 종료해도 잠금이
    남지 않는다(잠금 파일은 남아서 다음 실행을 막는다). 핸들은 일부러 닫지 않는다 —
    프로세스가 사는 동안 쥐고 있어야 잠금이 유지된다.
    """
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle or ctypes.get_last_error() != _ERROR_ALREADY_EXISTS:
        return True     # 뮤텍스를 못 만들었으면 막지 않는다 (창이 두 개 뜰 뿐 해는 없다)

    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = user32.FindWindowW(None, window_title)
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        # 컨트롤러(전경 프로세스)가 띄운 프로세스라 전경을 넘겨받을 수 있다.
        user32.SetForegroundWindow(hwnd)
    return False


def enable_dpi_awareness():
    """고해상도 화면에서 글자가 번지지 않게 한다. Tk 창을 만들기 전에 불러야 한다."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)     # 시스템 DPI 인식
    except Exception:
        pass


def short_error(e, limit=200):
    """SQLAlchemy 오류는 SQL 과 안내 링크까지 여러 줄이라 첫 줄만 추린다."""
    first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return first if len(first) <= limit else first[:limit] + "…"


def setup_window(root, title, size, min_size):
    """제목 · 바탕 · 아이콘 · 스타일 · 크기를 맞추고, 화면 배율을 반영하는 px 함수를 돌려준다.

    DPI 인식을 켜면 글꼴(포인트)은 배율을 따라 커지지만 픽셀로 준 크기는 그대로라, 창 크기와
    여백은 px() 로 감싸 같은 비율로 키운다.
    """
    scale = root.winfo_fpixels("1i") / 96.0

    def px(value):
        return int(round(value * scale))

    root.title(title)
    root.configure(bg=BG)
    root.minsize(px(min_size[0]), px(min_size[1]))
    if os.path.exists(ICON_PATH):
        try:
            root.iconbitmap(ICON_PATH)
        except tk.TclError:
            pass
    init_styles(root, px)
    w, h = px(size[0]), px(size[1])
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{max((sw - w) // 2, 0)}+{max((sh - h) // 3, 0)}")
    return px


def init_styles(root, px):
    style = ttk.Style(root)
    # 기본 테마(vista)는 버튼 색 지정을 무시한다. clam 은 색을 바꿀 수 있다.
    style.theme_use("clam")
    body = (FONT, 10)

    style.configure(".", background=BG, foreground=TEXT, font=body)
    style.configure("Treeview", background=BG, fieldbackground=BG, foreground=TEXT,
                    rowheight=px(30), borderwidth=0, font=body)
    style.configure("Treeview.Heading", background=SURFACE, foreground=TEXT_DIM,
                    font=(FONT, 9, "bold"), relief="flat", borderwidth=0,
                    padding=(px(8), px(6)))
    style.map("Treeview", background=[("selected", ACCENT_BG)],
              foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", SURFACE)])
    # 표의 기본 테두리를 걷어낸다. 테두리는 바깥 프레임이 1px 로 그린다.
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    # clam 기본 스크롤바는 베이지빛 회색이라 팔레트와 어긋난다. 연회색 막대로 맞춘다.
    style.configure("Vertical.TScrollbar", troughcolor=SURFACE, background=EDGE,
                    bordercolor=SURFACE, lightcolor=EDGE, darkcolor=EDGE,
                    arrowcolor=TEXT_DIM, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", "#BDC1C6")])

    # 체크박스: 회색 테두리 상자, 고르면 파랑 채움 + 흰 ✓ (컨트롤러 토글의 켜짐 색과 같다)
    style.configure("TCheckbutton", background=BG, foreground=TEXT, font=body,
                    indicatorsize=px(14), indicatormargin=(0, 0, px(8), 0),
                    indicatorbackground=BG, indicatorforeground=BG,
                    upperbordercolor=EDGE, lowerbordercolor=EDGE, focuscolor=BG)
    style.map("TCheckbutton",
              background=[("active", BG)],
              foreground=[("disabled", TEXT_DIM)],
              indicatorbackground=[("disabled", SURFACE), ("selected", ACCENT)],
              indicatorforeground=[("selected", "#FFFFFF")],
              upperbordercolor=[("selected", ACCENT)],
              lowerbordercolor=[("selected", ACCENT)])
    _use_check_images(root, style, px)

    # 컨트롤러 버튼 규칙과 같다: 주 동작은 파랑 채움, 보조는 흰 바탕 + 파란 글자,
    # 파괴적 동작은 흰 바탕 + 빨간 테두리·글자.
    # relief 를 flat 으로 두면 clam 이 테두리(bordercolor)를 아예 그리지 않아, 흰 버튼이
    # 글자만 떠 있는 링크처럼 보인다. 기본 relief 에 명암색을 바탕색과 같게 주면 입체감
    # 없이 1px 테두리만 남는다.
    pad = (px(18), px(6))

    def button(name, fg, bg, edge, bg_active):
        style.configure(name, foreground=fg, background=bg, bordercolor=edge,
                        lightcolor=bg, darkcolor=bg, padding=pad, font=body)
        state_bg = [("disabled", SURFACE), ("pressed", bg_active), ("active", bg_active)]
        style.map(name, background=state_bg, lightcolor=state_bg, darkcolor=state_bg,
                  foreground=[("disabled", TEXT_DIM)],
                  bordercolor=[("disabled", EDGE)])

    button("Accent.TButton", "#FFFFFF", ACCENT, ACCENT, ACCENT_DK)
    button("Danger.TButton", DANGER, BG, DANGER, DANGER_BG)
    button("Ghost.TButton", ACCENT, BG, EDGE, ACCENT_BG)
    # 되돌릴 수 없는 실행 버튼: 빨강 채움 (초기화 실행처럼 마지막 한 번만 쓰는 곳)
    button("DangerFill.TButton", "#FFFFFF", DANGER, DANGER, "#B3261E")


def _use_check_images(root, style, px):
    """체크박스 표시를 그림(둥근 상자 + ✓)으로 바꾼다. 안 되면 clam 기본 표시를 그대로 쓴다.

    clam 은 고른 상태를 X 모양으로 그린다. 무엇을 지울지 고르는 창에서 X 는 '제외'로 읽힐 수
    있어 ✓ 를 직접 그린다. 4배로 그려 줄여서 가장자리를 매끄럽게 한다. 이미지는 창 객체에 붙여
    둬야 한다 — 참조가 사라지면 Tk 가 빈칸을 그린다.
    """
    try:
        from PIL import Image, ImageDraw, ImageTk
    except ImportError:
        return
    try:
        size, gap, k = px(16), px(8), 4
        big = size * k

        def make(fill, edge, check):
            img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([k, k, big - k - 1, big - k - 1], radius=int(big * 0.2),
                                   fill=fill, outline=edge, width=max(k, int(big * 0.07)))
            if check:
                draw.line([(big * 0.26, big * 0.52), (big * 0.43, big * 0.69), (big * 0.75, big * 0.33)],
                          fill="#FFFFFF", width=max(k, int(big * 0.13)), joint="curve")
            small = img.resize((size, size), Image.LANCZOS)
            canvas = Image.new("RGBA", (size + gap, size), (0, 0, 0, 0))   # 오른쪽 여백 = 글자와의 간격
            canvas.paste(small, (0, 0))
            return ImageTk.PhotoImage(canvas, master=root)

        images = {
            "off": make(BG, EDGE, False),
            "on": make(ACCENT, ACCENT, True),
            "off_disabled": make(SURFACE, EDGE, False),
            "on_disabled": make("#A8C7FA", "#A8C7FA", True),
        }
        root._admin_check_images = images
        style.element_create("Admin.Checkbutton.indicator", "image", images["off"],
                             ("disabled", "selected", images["on_disabled"]),
                             ("disabled", images["off_disabled"]),
                             ("selected", images["on"]), sticky="")
        style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Admin.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
    except tk.TclError:
        pass        # 같은 Tk 에서 두 번 부르면 요소가 이미 있다 — 처음 만든 것을 쓴다
