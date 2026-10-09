"""DB · 스토리지 초기화 창 — 지울 항목을 골라 초기화한다.

제조 DB Controller 의 'DB · 스토리지 초기화' 버튼이 이 파일을 실행한다. 컨트롤러는 창을 띄우기만
하고, 항목 선택 · 확인 · 실행은 여기서(파이썬) 한다. 실제로 지우는 일은
reset_database_and_storage.run_reset 이 하므로 컨트롤러 없이도 돈다.

    python backend/reset_manager.py

항목 (고른 것만 지운다)
  가공 데이터 DB  공구 목록과 로그인 계정을 뺀 테이블을 models.py 로 다시 만든다
  스토리지        프로젝트 data/ 의 machining_raw_data · processed_data · failed_data 를 비운다
  공구 목록       tool 테이블을 비운다
원본 백업 보관소와 로그인 계정은 어떤 경우에도 지우지 않는다.

안전장치
  - 수집기 · 대시보드 · TDMS 변환기가 실행 중이면 실행 버튼이 잠긴다 (실행 직전에도 다시 확인한다).
    수집기가 켜져 있으면 지운 Job 폴더를 보관소에서 자동 복원해 초기화가 무효가 된다.
  - '되돌릴 수 없음을 확인했습니다' 에 체크하고, 마지막 확인창에서 한 번 더 '예' 를 눌러야 한다.
  - 실행 중에는 창을 닫을 수 없다.
진행 내용은 창 아래 기록란과 컨트롤러 실시간 로그에 함께 나온다.
"""
import contextlib
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import reset_database_and_storage as core
from admin_ui import (BG, DANGER, EDGE, FONT, SUCCESS, SURFACE, TEXT, TEXT_DIM,
                      claim_single_instance, enable_dpi_awareness, setup_window, short_error)

# 단일 실행 판정이 이 제목으로 기존 창을 찾는다. 바꾸면 두 곳이 함께 바뀐다.
WINDOW_TITLE = "DB · 스토리지 초기화 · 공작기계지능화실험실 제조 DB"
MUTEX_NAME = "Local\\OrcusResetManager"
POLL_MS = 80


class Backend:
    """실제 DB · 폴더를 다루는 쪽. 시험에서는 시험용 DB · 임시 폴더를 넘겨 바꿔 끼운다."""

    def __init__(self, engine=None, roots=None, raw_root=None):
        self.engine = engine
        self.roots = tuple(roots) if roots else core.STORAGE_ROOTS
        self.raw_root = raw_root or core.RAW_DATA_ROOT
        self.vault_root = core.VAULT_ROOT

    def snapshot(self):
        return core.snapshot(self.engine, self.roots)

    def running_modules(self):
        return core.running_modules()

    def run_reset(self, db, storage, tools):
        return core.run_reset(db=db, storage=storage, tools=tools, engine=self.engine,
                              roots=self.roots, raw_root=self.raw_root)


class _QueueWriter:
    """print 를 창 기록란(큐)과 원래 표준 출력(컨트롤러 실시간 로그) 양쪽으로 보낸다."""

    def __init__(self, q, passthrough):
        self.q = q
        self.passthrough = passthrough

    def write(self, text):
        if text:
            self.q.put(("log", text))
            if self.passthrough is not None:
                try:
                    self.passthrough.write(text)
                    self.passthrough.flush()
                except (OSError, ValueError):
                    pass
        return len(text)

    def flush(self):
        pass


class ResetWindow:
    ITEMS = (
        ("db", "가공 데이터 DB",
         "부품 · 워크플랜 · Job · 측정 · 로그 · 검사 기록을 지우고 테이블을 다시 만듭니다. "
         "공구 목록과 로그인 계정은 남습니다. 수집기를 다시 켜면 감시 폴더에 남은 파일로 DB 를 다시 채웁니다."),
        ("storage", "스토리지",
         "프로젝트 data/ 의 machining_raw_data · processed_data · failed_data 를 비웁니다."),
        ("tools", "공구 목록",
         "tool 테이블을 비웁니다. 보관소의 '공구 마스터.csv' 는 남아, 보관소에서 복원하면 다시 채워집니다."),
    )

    def __init__(self, root, backend=None):
        self.root = root
        self.backend = backend or Backend()
        self.queue = queue.Queue()
        self.running = False
        self.done_count = 0        # 이 창에서 초기화를 끝낸 횟수 (닫을 때 로그에 남긴다)
        self.blocked = []          # 실행 중인 모듈
        self.vars = {key: tk.BooleanVar(value=False) for key, _t, _d in self.ITEMS}
        self.confirm = tk.BooleanVar(value=False)
        for var in list(self.vars.values()) + [self.confirm]:
            var.trace_add("write", lambda *_: self._sync())

        self.px = setup_window(root, WINDOW_TITLE, size=(660, 700), min_size=(560, 620))
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.bind("<F5>", lambda _e: self.refresh())
        self.refresh()
        self._poll()

    # --- 모양 ---------------------------------------------------------------
    def _build(self):
        px = self.px
        outer = tk.Frame(self.root, bg=BG, padx=px(24), pady=px(20))
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="DB · 스토리지 초기화", bg=BG, fg=TEXT,
                 font=(FONT, 15, "bold"), anchor="w").pack(fill="x")
        tk.Label(outer, text="지울 항목을 고르세요. 되돌릴 수 없습니다.", bg=BG, fg=TEXT_DIM,
                 font=(FONT, 9), anchor="w").pack(fill="x", pady=(px(4), px(12)))

        # 항목 카드: 1px 테두리 안에 항목마다 체크박스 · 지워질 양 · 설명
        card = tk.Frame(outer, bg=EDGE, padx=1, pady=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=BG)
        inner.pack(fill="x")
        self.checks, self.amounts, self.descs = {}, {}, []
        for i, (key, title, desc) in enumerate(self.ITEMS):
            if i:
                tk.Frame(inner, bg=EDGE, height=1).pack(fill="x")
            row = tk.Frame(inner, bg=BG, padx=px(14), pady=px(10))
            row.pack(fill="x")
            head = tk.Frame(row, bg=BG)
            head.pack(fill="x")
            self.checks[key] = ttk.Checkbutton(head, text=title, variable=self.vars[key])
            self.checks[key].pack(side="left")
            self.amounts[key] = tk.Label(head, bg=BG, fg=TEXT_DIM, font=(FONT, 9))
            self.amounts[key].pack(side="right")
            label = tk.Label(row, text=desc, bg=BG, fg=TEXT_DIM, font=(FONT, 9),
                             anchor="w", justify="left")
            label.pack(fill="x", padx=(px(22), 0), pady=(px(2), 0))
            self.descs.append(label)
        for label in self.descs:
            label.bind("<Configure>", lambda e, lb=label: lb.configure(wraplength=e.width))

        self.keep = tk.Label(outer, bg=BG, fg=TEXT_DIM, font=(FONT, 9), anchor="w", justify="left")
        self.keep.pack(fill="x", pady=(px(8), px(10)))
        self.keep.bind("<Configure>", lambda e: self.keep.configure(wraplength=e.width))

        # 실행 중인 모듈
        mod_row = tk.Frame(outer, bg=SURFACE, padx=px(12), pady=px(8))
        mod_row.pack(fill="x")
        self.recheck = ttk.Button(mod_row, text="다시 확인", style="Ghost.TButton",
                                  command=self.refresh)
        self.recheck.pack(side="right")
        self.modules = tk.Label(mod_row, bg=SURFACE, font=(FONT, 9), anchor="w", justify="left")
        self.modules.pack(side="left", fill="x", expand=True)
        self.modules.bind("<Configure>", lambda e: self.modules.configure(wraplength=e.width))

        # 확인 · 실행
        act = tk.Frame(outer, bg=BG)
        act.pack(fill="x", pady=(px(12), px(10)))
        self.run_btn = ttk.Button(act, text="선택한 항목 초기화", style="DangerFill.TButton",
                                  command=self.run)
        self.run_btn.pack(side="right")
        self.close_btn = ttk.Button(act, text="닫기", style="Ghost.TButton", command=self._close)
        self.close_btn.pack(side="right", padx=(0, px(8)))
        self.confirm_check = ttk.Checkbutton(act, text="되돌릴 수 없음을 확인했습니다",
                                             variable=self.confirm)
        self.confirm_check.pack(side="left")

        self.status = tk.Label(outer, bg=BG, fg=TEXT_DIM, font=(FONT, 9), anchor="w", justify="left")
        self.status.pack(fill="x")
        self.status.bind("<Configure>", lambda e: self.status.configure(wraplength=e.width))

        # 진행 기록
        box = tk.Frame(outer, bg=EDGE, padx=1, pady=1)
        box.pack(fill="both", expand=True, pady=(px(8), 0))
        self.log = tk.Text(box, bg=SURFACE, fg=TEXT, font=("Consolas", 9), relief="flat",
                           height=8, wrap="word", padx=px(8), pady=px(6), state="disabled")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self._append("실행하면 진행 내용이 여기에 나옵니다.\n")

    # --- 상태 ---------------------------------------------------------------
    def refresh(self):
        """지워질 양 · 실행 중인 모듈을 다시 센다."""
        try:
            snap = self.backend.snapshot()
        except Exception as e:
            for label in self.amounts.values():
                label.configure(text="—")
            self._set_status(f"DB 에 연결하지 못했습니다 — {short_error(e)}", error=True)
            snap = None
        if snap:
            self.amounts["db"].configure(text=f"{snap['db_tables']}개 테이블 · {snap['db_rows']:,}행")
            self.amounts["storage"].configure(text=f"파일 {snap['storage_total']:,}개")
            self.amounts["tools"].configure(text=f"공구 {snap['tool_rows']:,}개")
            self.keep.configure(text=f"지우지 않음: 원본 백업 보관소 ({self.backend.vault_root}) · "
                                     f"로그인 계정 {snap['accounts']:,}개")

        try:
            self.blocked = self.backend.running_modules()
        except Exception as e:
            self.blocked = [(f"모듈 확인 실패: {short_error(e)}", 0)]
        if self.blocked:
            names = " · ".join(f"{label}(PID {pid})" if pid else label for label, pid in self.blocked)
            self.modules.configure(fg=DANGER, text=f"● 실행 중: {names} — Controller 에서 「전체 중지」 "
                                                   f"한 뒤 '다시 확인' 을 누르세요.")
        else:
            self.modules.configure(fg=SUCCESS, text="● 실행 중인 모듈 없음 — 초기화할 수 있습니다.")
        self._sync()

    def _selected(self):
        return {key: var.get() for key, var in self.vars.items()}

    def _sync(self):
        chosen = any(self._selected().values())
        ready = chosen and self.confirm.get() and not self.blocked and not self.running
        self.run_btn.state(["!disabled"] if ready else ["disabled"])
        for widget in list(self.checks.values()) + [self.confirm_check, self.recheck, self.close_btn]:
            widget.state(["disabled"] if self.running else ["!disabled"])

    def _set_status(self, text, error=False, ok=False):
        self.status.configure(text=text, fg=DANGER if error else SUCCESS if ok else TEXT_DIM)

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- 실행 ---------------------------------------------------------------
    def run(self):
        chosen = self._selected()
        if not any(chosen.values()) or not self.confirm.get() or self.running:
            return
        self.refresh()                          # 그 사이 모듈이 켜졌을 수 있다
        if self.blocked:
            self._set_status("실행 중인 모듈이 있어 초기화하지 않았습니다.", error=True)
            return

        names = [title for key, title, _d in self.ITEMS if chosen[key]]
        keep = ["원본 백업 보관소", "로그인 계정"] + ([] if chosen["tools"] else ["공구 목록"])
        if not messagebox.askyesno(
                "초기화 실행",
                "다음 항목을 초기화합니다. 되돌릴 수 없습니다.\n\n"
                + "\n".join(f"  · {n}" for n in names)
                + "\n\n남는 것: " + ", ".join(keep) + "\n\n진행하시겠습니까?",
                icon="warning", default="no", parent=self.root):
            return

        self.running = True
        self._sync()
        self._set_status("초기화하는 중입니다… 끝날 때까지 창을 닫을 수 없습니다.")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        threading.Thread(target=self._worker, args=(chosen,), daemon=True).start()

    def _worker(self, chosen):
        writer = _QueueWriter(self.queue, sys.__stdout__)
        with contextlib.redirect_stdout(writer):
            try:
                summary = self.backend.run_reset(db=chosen["db"], storage=chosen["storage"],
                                                 tools=chosen["tools"])
                self.queue.put(("done", summary))
            except core.ResetError as e:
                self.queue.put(("refused", str(e)))
            except Exception as e:
                traceback.print_exc(file=writer)
                self.queue.put(("error", short_error(e)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append(payload)
                else:
                    self._finish(kind, payload)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _finish(self, kind, payload):
        self.running = False
        if kind == "done":
            self.done_count += 1
            self._set_status(f"초기화 완료 — {self._describe(payload)}", ok=True)
            for var in self.vars.values():
                var.set(False)
            self.confirm.set(False)
        elif kind == "refused":
            self._set_status(payload, error=True)
        else:
            self._set_status(f"초기화 중 오류가 났습니다 — {payload} (기록란과 컨트롤러 로그를 확인하세요)",
                             error=True)
        self.refresh()

    @staticmethod
    def _describe(summary):
        parts = []
        if "db" in summary:
            # 항목 옆 숫자(공구 · 계정을 뺀 가공 테이블 수)와 같은 기준으로 센다
            data = [t for t in summary["db"]["dropped"] if t != core.TOOL_TABLE]
            parts.append(f"DB {len(data)}개 테이블 다시 만듦")
        if "tools" in summary:
            parts.append(f"공구 {summary['tools']['deleted']}개 삭제")
        if "storage" in summary:
            parts.append("스토리지 비움")
        parts.append(f"보관소 파일 {summary.get('vault_files', 0):,}개 그대로")
        return " · ".join(parts)

    def _close(self):
        if self.running:
            self.root.bell()
            return
        # 컨트롤러 로그에는 창이 끝날 때 '… 완료' 가 붙는다. 실제로 지웠는지는 여기서 분명히 남긴다.
        if self.done_count:
            print(f"이번에 {self.done_count}번 초기화하고 창을 닫았습니다.", flush=True)
        else:
            print("초기화하지 않고 창을 닫았습니다.", flush=True)
        self.root.destroy()


def main():
    if not claim_single_instance(MUTEX_NAME, WINDOW_TITLE):
        print("초기화 창이 이미 열려 있어 앞으로 가져왔습니다.", flush=True)
        return 0
    enable_dpi_awareness()
    root = tk.Tk()
    ResetWindow(root)
    root.lift()
    root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
