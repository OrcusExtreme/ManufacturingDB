"""유저 관리 창 — 가입 신청 승인·거절, 가입 멤버 조회·삭제.

제조 DB Controller 의 '유저 관리' 버튼이 이 파일을 실행한다. 컨트롤러는 창을 띄우기만 하고
조회·승인·거절·삭제는 전부 여기서(파이썬) 하므로, 컨트롤러 없이 단독으로도 돈다.

    python backend/user_manager.py

관리 권한은 이 창을 열 수 있는 사람, 즉 서버 PC 와 .env 의 DB 계정에 접근할 수 있는 사람에게
있다. 대시보드에는 관리 화면을 두지 않는다 — 두면 로그인한 누구나 남을 들이거나 지울 수 있다.

창은 하나만 뜬다. 버튼을 두 번 눌러도 새로 띄우지 않고 이미 열린 창을 앞으로 가져온다.
열려 있는 동안 REFRESH_MS 마다 두 목록을 다시 읽어, 새 신청이 들어오면 바로 보인다.
"""
import datetime
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import auth
# 팔레트 · 버튼 규칙 · 창 하나만 띄우기 · DPI 대응은 관리 창들이 함께 쓴다 (admin_ui.py)
from admin_ui import (ACCENT, BG, DANGER, EDGE, FONT, TEXT, TEXT_DIM,
                      claim_single_instance, enable_dpi_awareness, setup_window, short_error)

# 단일 실행 판정이 이 제목으로 기존 창을 찾는다. 바꾸면 두 곳이 함께 바뀐다.
WINDOW_TITLE = "유저 관리 · 공작기계지능화실험실 제조 DB"
MUTEX_NAME = "Local\\OrcusUserManager"
REFRESH_MS = 10_000


# ---------------------------------------------------------------------------
# 표시 도우미
# ---------------------------------------------------------------------------
def _when(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def _describe(targets, limit=8):
    lines = [f"  · {r['username']}  ({r['worker_name']})" for r in targets[:limit]]
    if len(targets) > limit:
        lines.append(f"  … 외 {len(targets) - limit}건")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 구성 요소
# ---------------------------------------------------------------------------
class TabBar:
    """Google 콘솔식 탭: 글자 + 선택된 탭 아래 파란 밑줄.

    ttk.Notebook 은 clam 테마에서 폴더 모양 테두리를 그리고 그 안에 또 테두리를 둘러,
    표의 1px 테두리와 겹쳐 두 겹으로 보인다. 필요한 것은 전환뿐이라 직접 그린다.
    """

    def __init__(self, parent, titles, on_change, px):
        self.frame = tk.Frame(parent, bg=BG)
        self.labels, self.marks = [], []
        for i, title in enumerate(titles):
            cell = tk.Frame(self.frame, bg=BG, cursor="hand2")
            cell.pack(side="left", padx=(0, px(4)))
            label = tk.Label(cell, text=title, bg=BG, font=(FONT, 10), cursor="hand2",
                             padx=px(14), pady=px(7))
            label.pack()
            mark = tk.Frame(cell, bg=BG, height=px(3))
            mark.pack(fill="x")
            for w in (cell, label):
                w.bind("<Button-1>", lambda _e, i=i: on_change(i))
            self.labels.append(label)
            self.marks.append(mark)
        # 탭 줄 아래 가로선. 선택 표시(밑줄)는 이 선 바로 위에 붙는다.
        self.rule = tk.Frame(parent, bg=EDGE, height=1)

    def pack(self):
        self.frame.pack(fill="x")
        self.rule.pack(fill="x")

    def set_active(self, index):
        for i, (label, mark) in enumerate(zip(self.labels, self.marks)):
            on = i == index
            label.configure(fg=ACCENT if on else TEXT_DIM)
            mark.configure(bg=ACCENT if on else BG)

    def set_title(self, index, text):
        self.labels[index].configure(text=text)


class RecordTable:
    """1px 테두리 표 + 넘칠 때만 보이는 스크롤바 + 비었거나 오류일 때 가운데 안내문."""

    def __init__(self, parent, columns, px, on_select):
        self.box = tk.Frame(parent, bg=EDGE, padx=1, pady=1)       # 1px 테두리
        inner = tk.Frame(self.box, bg=BG)
        inner.pack(fill="both", expand=True)
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(inner, columns=[c[0] for c in columns],
                                 show="headings", selectmode="extended")
        for key, title, width in columns:
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=px(width), minwidth=px(80), anchor="w", stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: on_select())

        # 스크롤바는 행이 넘칠 때만 보인다. 늘 띄워 두면 몇 건 안 될 때 빈 막대만 남는다.
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")

        def autohide(first, last):
            if float(first) <= 0.0 and float(last) >= 1.0:
                scroll.grid_remove()
            else:
                scroll.grid()
            scroll.set(first, last)
        self.tree.configure(yscrollcommand=autohide)

        self.placeholder = tk.Label(inner, bg=BG, fg=TEXT_DIM, font=(FONT, 10),
                                    justify="center")
        self.placeholder.bind(
            "<Configure>",
            lambda _e: self.placeholder.configure(
                wraplength=max(inner.winfo_width() - 40, 100)))

    def render(self, items, empty_text):
        """items = [(iid, values), ...]. 자동 새로고침이 고르던 행을 풀지 않게 선택을 이어 간다."""
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for iid, values in items:
            self.tree.insert("", "end", iid=iid, values=values)
        keep = [iid for iid in selected if self.tree.exists(iid)]
        if keep:
            self.tree.selection_set(keep)
        self._placeholder(None if items else empty_text)

    def show_error(self, text):
        # 예전 목록을 남겨 두면 이미 처리된 항목을 보고 누를 수 있다. 비우고 이유를 보인다.
        self.tree.delete(*self.tree.get_children())
        self._placeholder(text, error=True)

    def selection(self):
        return list(self.tree.selection())

    def _placeholder(self, text, error=False):
        if text is None:
            self.placeholder.place_forget()
            return
        self.placeholder.configure(text=text, fg=DANGER if error else TEXT_DIM)
        self.placeholder.place(relx=0.5, rely=0.55, anchor="center")


class Page:
    """탭 한 장: 표 + 아래 버튼 줄. 목록을 읽어 그리는 일까지 맡는다."""

    def __init__(self, parent, px, *, title, columns, empty_text, summary, loader,
                 row_id, row_values):
        self.title = title
        self.empty_text = empty_text
        self.summary = summary          # 건수 -> 상태 줄 문구
        self.loader = loader            # DB 에서 목록을 읽는 함수 (오류는 예외로)
        self.row_id = row_id
        self.row_values = row_values
        self.px = px
        self.rows = {}
        self.needs_selection = []

        self.frame = tk.Frame(parent, bg=BG)
        # 버튼 줄을 먼저 붙인다. pack 은 먼저 붙인 것에 자리를 먼저 주므로, 창을 줄여도
        # 버튼이 잘리지 않고 표가 줄어든다.
        self.bar = tk.Frame(self.frame, bg=BG)
        self.bar.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.status = tk.Label(self.bar, bg=BG, fg=TEXT_DIM, font=(FONT, 9), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.table = RecordTable(self.frame, columns, px, on_select=self.sync_buttons)
        self.table.box.pack(fill="both", expand=True)

    def add_button(self, text, style, command, needs_selection=True):
        """오른쪽부터 차례로 붙인다. before=status 로 상태 글보다 자리를 먼저 받게 한다."""
        button = ttk.Button(self.bar, text=text, style=style, command=command)
        # 오른쪽에 이미 붙은 버튼과의 간격은 이 버튼의 오른쪽 여백이다.
        gap = self.px(8) if len(self.bar.pack_slaves()) > 1 else 0
        button.pack(side="right", padx=(0, gap), before=self.status)
        if needs_selection:
            self.needs_selection.append(button)
        return button

    def load(self, stamp, note=None):
        """목록을 다시 읽어 그린다. 건수를 돌려주고, DB 에 못 붙으면 None."""
        try:
            rows = self.loader()
        except Exception as e:
            self.rows = {}
            self.table.show_error(
                f"DB 에 연결하지 못했습니다.\nMySQL 이 켜져 있는지, .env 의 DB 설정이 맞는지 "
                f"확인하세요.\n\n{short_error(e)}")
            self.set_status(f"DB 연결 실패 · {stamp}", error=True)
            self.sync_buttons()
            return None
        self.rows = {str(self.row_id(r)): r for r in rows}
        self.table.render([(iid, self.row_values(r)) for iid, r in self.rows.items()],
                          self.empty_text)
        text = f"{self.summary(len(rows))} · {stamp} 확인"
        self.set_status(f"{note}  ·  {text}" if note else text)
        self.sync_buttons()
        return len(rows)

    def selected(self):
        return [self.rows[iid] for iid in self.table.selection() if iid in self.rows]

    def set_status(self, text, error=False):
        self.status.configure(text=text, fg=DANGER if error else TEXT_DIM)

    def sync_buttons(self):
        state = ["!disabled"] if self.table.selection() else ["disabled"]
        for button in self.needs_selection:
            button.state(state)


# ---------------------------------------------------------------------------
# 창
# ---------------------------------------------------------------------------
class UserManagerWindow:
    REQUEST_COLUMNS = (
        ("requested_at", "신청 시각", 150),
        ("username", "아이디", 170),
        ("worker_name", "이름", 150),
    )
    MEMBER_COLUMNS = (
        ("username", "아이디", 150),
        ("worker_name", "이름", 130),
        ("created_at", "가입 시각", 140),
        ("last_login_at", "마지막 로그인", 140),
    )

    def __init__(self, root):
        self.root = root
        self.after_id = None
        self.px = setup_window(root, WINDOW_TITLE, size=(720, 540), min_size=(560, 420))
        self._build()
        root.bind("<F5>", lambda _e: self.refresh())
        root.bind("<Control-Tab>", lambda _e: self.show_page((self.current + 1) % len(self.pages)))
        self.refresh()

    def _build(self):
        outer = tk.Frame(self.root, bg=BG, padx=self.px(24), pady=self.px(20))
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="유저 관리", bg=BG, fg=TEXT,
                 font=(FONT, 15, "bold"), anchor="w").pack(fill="x")
        desc = tk.Label(outer, bg=BG, fg=TEXT_DIM, font=(FONT, 9), anchor="w",
                        justify="left",
                        text="대시보드 로그인 계정을 관리합니다. 가입 신청을 승인하거나 거절하고, "
                             "가입한 멤버를 조회하거나 삭제합니다.")
        desc.pack(fill="x", pady=(self.px(4), self.px(12)))
        # 창 폭을 따라 줄바꿈 (wraplength 는 픽셀이라 폭이 바뀔 때마다 맞춰 줘야 한다)
        desc.bind("<Configure>", lambda e: desc.configure(wraplength=e.width))

        self.tabs = TabBar(outer, ["가입 신청", "가입 멤버"], self.show_page, self.px)
        self.tabs.pack()
        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, pady=(self.px(14), 0))

        self.requests = Page(
            body, self.px, title="가입 신청", columns=self.REQUEST_COLUMNS,
            empty_text="대기 중인 가입 신청이 없습니다.",
            summary=lambda n: f"대기 중인 신청 {n}건",
            loader=auth.list_signup_requests,
            row_id=lambda r: r["request_id"],
            row_values=lambda r: (_when(r["requested_at"]), r["username"], r["worker_name"]))
        self.requests.add_button("승인", "Accent.TButton", self.approve)
        self.requests.add_button("거절", "Danger.TButton", self.reject)
        self.requests.add_button("새로고침", "Ghost.TButton", self.refresh,
                                 needs_selection=False)

        self.members = Page(
            body, self.px, title="가입 멤버", columns=self.MEMBER_COLUMNS,
            empty_text="가입한 멤버가 없습니다.",
            summary=lambda n: f"가입 멤버 {n}명",
            loader=auth.list_users,
            row_id=lambda r: r["user_id"],
            row_values=lambda r: (r["username"], r["worker_name"],
                                  _when(r["created_at"]), _when(r["last_login_at"])))
        self.members.add_button("삭제", "Danger.TButton", self.delete_members)
        self.members.add_button("새로고침", "Ghost.TButton", self.refresh,
                                needs_selection=False)

        self.pages = [self.requests, self.members]
        self.current = -1       # 아직 아무 탭도 보이지 않음
        self.show_page(0)

    def show_page(self, index):
        if index == self.current:
            return
        for page in self.pages:
            page.frame.pack_forget()
        self.pages[index].frame.pack(fill="both", expand=True)
        self.tabs.set_active(index)
        self.current = index

    # --- 목록 ---------------------------------------------------------------
    def refresh(self, notes=None):
        """두 목록을 다시 읽는다. notes = {page: '2건 승인'} 처럼 방금 한 일을 상태 줄 앞에 붙인다.

        승인하면 신청이 멤버로 옮겨 가므로, 한쪽에서 무엇을 하든 두 목록을 함께 읽는다.
        """
        notes = notes or {}
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        stamp = f"{datetime.datetime.now():%H:%M:%S}"
        for i, page in enumerate(self.pages):
            count = page.load(stamp, note=notes.get(page))
            self.tabs.set_title(i, page.title if count is None else f"{page.title} ({count})")
        self.after_id = self.root.after(REFRESH_MS, self.refresh)

    # --- 동작 ---------------------------------------------------------------
    def approve(self):
        targets = self.requests.selected()
        if not targets:
            return
        if not messagebox.askyesno(
                "가입 승인",
                f"다음 {len(targets)}건을 승인합니다.\n"
                f"승인하면 그 아이디로 바로 대시보드에 로그인할 수 있습니다.\n\n"
                f"{_describe(targets)}",
                parent=self.root):
            return
        self._apply(self.requests, targets, auth.approve_signup, "request_id", "건 승인")

    def reject(self):
        targets = self.requests.selected()
        if not targets:
            return
        if not messagebox.askyesno(
                "가입 거절",
                f"다음 {len(targets)}건을 거절합니다. 신청이 지워지며, 다시 가입하려면 "
                f"처음부터 신청해야 합니다.\n\n{_describe(targets)}",
                icon="warning", default="no", parent=self.root):
            return
        self._apply(self.requests, targets, auth.reject_signup, "request_id", "건 거절")

    def delete_members(self):
        targets = self.members.selected()
        if not targets:
            return
        if not messagebox.askyesno(
                "멤버 삭제",
                f"다음 {len(targets)}명을 삭제합니다. 되돌릴 수 없습니다.\n\n"
                f"{_describe(targets)}\n\n"
                f"삭제하면 열려 있던 대시보드도 바로 로그아웃되고, 그 아이디로는 로그인할 수 "
                f"없습니다. 가공 이력에 기록된 작업자 이름은 그대로 남습니다.",
                icon="warning", default="no", parent=self.root):
            return
        self._apply(self.members, targets, auth.delete_user, "user_id", "명 삭제")

    def _apply(self, page, targets, action, id_key, done_suffix):
        failures = []
        for r in targets:
            ok, message = action(r[id_key])
            if not ok:
                failures.append(message)
        done = len(targets) - len(failures)
        if failures:
            messagebox.showwarning("처리하지 못한 항목", "\n".join(failures), parent=self.root)
        self.refresh(notes={page: f"{done}{done_suffix}"} if done else None)


def main():
    if not claim_single_instance(MUTEX_NAME, WINDOW_TITLE):
        print("유저 관리 창이 이미 열려 있어 앞으로 가져왔습니다.", flush=True)
        return 0
    enable_dpi_awareness()
    root = tk.Tk()
    UserManagerWindow(root)
    root.lift()
    root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
