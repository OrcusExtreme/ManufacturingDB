// 공작기계지능화실험실 제조 DB Controller (Orcus System Controller)
//
// 이 프로그램은 파이썬 코드를 품고 있지 않다. 디스크의 .py 를 그대로 실행하는
// 감독자(supervisor)일 뿐이라, backend/ 나 frontend/ 를 고치면 다시 빌드하지 않아도
// 다음 실행부터 반영된다. 파이썬이 설치되지 않은 PC 를 위해서는 runtime\python.exe
// 를 동봉할 수 있고, 있으면 그쪽을 먼저 쓴다 (app_config.h 의 Detect 참고).
//
// UI 는 Win32 기본 컨트롤의 회색 모양 대신 오너드로우로 직접 그린다. 색과 형태는
// Google Cloud 콘솔의 밝은 화면을 기준으로 하고, 강조색은 Streamlit 대시보드와 같은
// 파랑(#1A73E8)을 써서 두 화면의 인상을 맞춘다 (theme.h).

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <shlwapi.h>
#include <objidl.h>
#include <gdiplus.h>

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "theme.h"
#include "app_config.h"

using namespace Gdiplus;

// --- 컨트롤 ID ---
enum ControlIDs {
    IDC_BTN_TOGGLE_UI = 1001,
    IDC_BTN_TOGGLE_WATCHDOG,
    IDC_BTN_TOGGLE_TDMS,
    IDC_STATIC_STATUS_UI,
    IDC_STATIC_STATUS_WATCHDOG,
    IDC_STATIC_STATUS_TDMS,
    IDC_STATIC_NAME_UI,
    IDC_STATIC_NAME_WATCHDOG,
    IDC_STATIC_NAME_TDMS,

    IDC_BTN_START_ALL = 1031,
    IDC_BTN_STOP_ALL,
    IDC_BTN_OPEN_BROWSER,

    IDC_CHK_PARSER_BASE = 1041,   // +0..+5 가 파서 6종
    IDC_CHK_PAUSE = 1050,

    IDC_BTN_CLEAN_DUMMY = 1051,
    IDC_BTN_RESET_STORAGE,

    IDC_EDIT_LOG = 2001,
    IDC_CHK_AUTOSCROLL,
    IDC_BTN_CLEAR_LOG,
    IDC_BTN_COPY_LOG,
    IDC_STATIC_LOG_INFO
};

#define WM_USER_APPEND_LOG   (WM_USER + 101)
#define WM_USER_PROC_EXITED  (WM_USER + 102)

// --- 모듈 ---
enum ModuleIndex { MOD_UI = 0, MOD_WATCHDOG, MOD_TDMS, MOD_COUNT };

// 프로세스 핸들의 수명은 전부 UI 스레드가 쥔다. 리더 스레드는 읽기 파이프만
// 소유하고, 끝나면 WM_USER_PROC_EXITED 를 보내 UI 스레드가 정리하게 한다.
// (예전에는 리더 스레드가 hProcess 를 닫아서 WM_TIMER 의 상태 점검과 충돌했다)
struct ManagedProcess {
    std::wstring name;
    std::wstring scriptArgs;          // 인터프리터 뒤에 붙일 인자
    HANDLE hProcess = NULL;
    HANDLE hThread = NULL;
    DWORD pid = 0;
    std::atomic<bool> isRunning{false};
    std::atomic<bool> stopping{false};
    HWND hBtn = NULL;
    HWND hStatus = NULL;
    HWND hName = NULL;
};

// --- 전역 ---
HINSTANCE g_hInstance = NULL;
HWND g_hWndMain = NULL;
HWND g_hEditLog = NULL;
HWND g_hStaticLogInfo = NULL;
HFONT g_fontTitle = NULL;
HFONT g_fontCard = NULL;
HFONT g_fontBody = NULL;
HFONT g_fontSmall = NULL;
HFONT g_fontLog = NULL;
HBRUSH g_brushCard = NULL;
HBRUSH g_brushLog = NULL;
HANDLE g_hJobObject = NULL;
ULONG_PTR g_gdiplusToken = 0;
std::unique_ptr<Image> g_logo;

ManagedProcess g_modules[MOD_COUNT];
appcfg::Config g_cfg;
appcfg::Interpreter g_python;
std::wstring g_projectDir;

std::mutex g_logMutex;
std::vector<std::wstring> g_pendingLogs;
bool g_autoScroll = true;
size_t g_totalLogLines = 0;

HWND g_hChkParsers[appcfg::PARSER_COUNT] = {NULL};
HWND g_hChkPause = NULL;
HWND g_hChkAutoScroll = NULL;

// 오너드로우 버튼의 생김새. GWLP_USERDATA 에 넣어 WM_DRAWITEM 에서 꺼내 쓴다.
enum BtnStyle { STYLE_ACCENT = 1, STYLE_GHOST, STYLE_DANGER, STYLE_TOGGLE, STYLE_MODULE };

// ============================ 유틸리티 ============================

std::wstring GetProjectDir() {
    wchar_t exePath[MAX_PATH];
    GetModuleFileNameW(NULL, exePath, MAX_PATH);
    PathRemoveFileSpecW(exePath);
    return std::wstring(exePath);
}

std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), NULL, 0);
    if (n <= 0) return L"";
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

std::wstring GetTimestamp() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    wchar_t buf[32];
    swprintf(buf, 32, L"[%02d:%02d:%02d] ", st.wHour, st.wMinute, st.wSecond);
    return std::wstring(buf);
}

void PostLogMessage(const std::wstring& text) {
    {
        std::lock_guard<std::mutex> lock(g_logMutex);
        g_pendingLogs.push_back(text);
    }
    if (g_hWndMain) PostMessageW(g_hWndMain, WM_USER_APPEND_LOG, 0, 0);
}

std::wstring ConfigPath() { return g_projectDir + L"\\backend\\pipeline_config.json"; }

// .env 에서 key 값을 읽는다. 없으면 빈 문자열.
// 컨트롤러는 파이썬처럼 dotenv 를 쓰지 않으므로, 보관소 위치를 화면에 정확히 보여주려면
// 같은 .env 를 직접 읽어야 한다. (OS 환경변수가 있으면 그쪽이 우선 - 파이썬과 동일한 규칙)
static std::wstring ReadEnvSetting(const wchar_t* key) {
    wchar_t buf[1024];
    DWORD n = GetEnvironmentVariableW(key, buf, 1024);
    if (n > 0 && n < 1024) return std::wstring(buf, n);

    FILE* fp = _wfopen((g_projectDir + L"\\.env").c_str(), L"rb");
    if (!fp) return L"";
    std::string raw;
    char chunk[4096];
    size_t got;
    while ((got = fread(chunk, 1, sizeof(chunk), fp)) > 0) raw.append(chunk, got);
    fclose(fp);

    std::wstring text = Utf8ToWide(raw);
    std::wstring needle(key);
    size_t pos = 0;
    while (pos < text.size()) {
        size_t eol = text.find(L'\n', pos);
        if (eol == std::wstring::npos) eol = text.size();
        std::wstring line = text.substr(pos, eol - pos);
        pos = eol + 1;

        size_t b = line.find_first_not_of(L" \t\r");
        if (b == std::wstring::npos || line[b] == L'#') continue;
        size_t eq = line.find(L'=', b);
        if (eq == std::wstring::npos) continue;
        std::wstring name = line.substr(b, eq - b);
        size_t ne = name.find_last_not_of(L" \t\r");
        if (ne != std::wstring::npos) name = name.substr(0, ne + 1);
        if (name != needle) continue;

        std::wstring val = line.substr(eq + 1);
        size_t vb = val.find_first_not_of(L" \t\r");
        if (vb == std::wstring::npos) return L"";
        size_t ve = val.find_last_not_of(L" \t\r");
        val = val.substr(vb, ve - vb + 1);
        if (val.size() >= 2 && (val.front() == L'"' || val.front() == L'\'')
            && val.back() == val.front())
            val = val.substr(1, val.size() - 2);
        return val;
    }
    return L"";
}

// 원본 백업 보관소의 실제 경로. vault_manager.resolve_vault_root() 와 같은 우선순위를 따른다.
std::wstring ResolveVaultRootForDisplay() {
    std::wstring explicitRoot = ReadEnvSetting(L"ORCUS_VAULT_ROOT");
    if (!explicitRoot.empty()) return explicitRoot;

    std::wstring dbDir = ReadEnvSetting(L"ORCUS_DB_DATA_DIR");
    if (!dbDir.empty()) {
        if (dbDir.back() == L'\\' || dbDir.back() == L'/') dbDir.pop_back();
        return dbDir + L"\\archive_vault";
    }
    return g_projectDir + L"\\data\\archive_vault";
}

void SaveConfigFromUI() {
    if (!appcfg::Save(ConfigPath(), g_cfg)) {
        PostLogMessage(GetTimestamp() + L"[경고] pipeline_config.json 저장에 실패했습니다.\r\n");
    }
}

// 브랜드 자산은 프로젝트 루트의 assets/ 한 곳에 모아 두고, 웹 대시보드도 같은 파일을 쓴다.
void LoadLogo() {
    const wchar_t* candidates[] = {
        L"\\assets\\logo_card.png",
        L"\\assets\\logo.png",
    };
    for (const wchar_t* rel : candidates) {
        std::wstring path = g_projectDir + rel;
        if (!appcfg::FileExists(path)) continue;
        std::unique_ptr<Image> img(Image::FromFile(path.c_str()));
        if (img && img->GetLastStatus() == Ok) {
            g_logo = std::move(img);
            return;
        }
    }
}

// ============================ 프로세스 제어 ============================

void UpdateModuleUI(int idx);

void StartModule(int idx) {
    if (idx < 0 || idx >= MOD_COUNT) return;
    ManagedProcess& proc = g_modules[idx];
    if (proc.isRunning) return;

    if (!g_python.found) {
        PostLogMessage(GetTimestamp() + L"[오류] 파이썬 인터프리터를 찾지 못해 [" + proc.name +
                       L"] 을(를) 시작할 수 없습니다.\r\n"
                       L"        python.org 에서 설치하거나, 프로젝트 폴더에 runtime\\python.exe 를 두세요.\r\n");
        return;
    }

    SECURITY_ATTRIBUTES sa;
    ZeroMemory(&sa, sizeof(sa));
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;

    HANDLE hRead = NULL, hWrite = NULL;
    if (!CreatePipe(&hRead, &hWrite, &sa, 0)) {
        PostLogMessage(GetTimestamp() + L"[오류] 파이프 생성 실패 (" + proc.name + L")\r\n");
        return;
    }
    SetHandleInformation(hRead, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.hStdOutput = hWrite;
    si.hStdError = hWrite;
    si.wShowWindow = SW_HIDE;

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    std::wstring cmd = g_python.command + L" " + proc.scriptArgs;
    std::vector<wchar_t> cmdBuf(cmd.begin(), cmd.end());
    cmdBuf.push_back(0);

    BOOL ok = CreateProcessW(NULL, cmdBuf.data(), NULL, NULL, TRUE,
                             CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                             NULL, g_projectDir.c_str(), &si, &pi);
    CloseHandle(hWrite);   // 부모 쪽 쓰기 핸들을 닫아야 자식 종료 시 EOF 가 온다

    if (!ok) {
        CloseHandle(hRead);
        DWORD err = GetLastError();
        PostLogMessage(GetTimestamp() + L"[오류] 프로세스 생성 실패 (" + proc.name +
                       L", 코드 " + std::to_wstring(err) + L")\r\n");
        return;
    }

    proc.hProcess = pi.hProcess;
    proc.hThread = pi.hThread;
    proc.pid = pi.dwProcessId;
    proc.isRunning = true;
    proc.stopping = false;

    if (g_hJobObject) AssignProcessToJobObject(g_hJobObject, proc.hProcess);

    PostLogMessage(GetTimestamp() + L"▶ [" + proc.name + L"] 시작 (PID " +
                   std::to_wstring(proc.pid) + L")\r\n");

    // 리더 스레드는 읽기 파이프만 소유한다. 값으로 넘겨 UI 스레드와 공유하지 않는다.
    std::thread([idx, hRead]() {
        std::wstring name = g_modules[idx].name;
        char buffer[4096];
        DWORD bytesRead = 0;
        std::string accum;

        while (ReadFile(hRead, buffer, sizeof(buffer), &bytesRead, NULL) && bytesRead > 0) {
            accum.append(buffer, bytesRead);
            size_t pos;
            while ((pos = accum.find('\n')) != std::string::npos) {
                std::string line = accum.substr(0, pos);
                accum.erase(0, pos + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                PostLogMessage(L"[" + name + L"] " + Utf8ToWide(line) + L"\r\n");
            }
        }
        if (!accum.empty()) {
            if (accum.back() == '\r') accum.pop_back();
            PostLogMessage(L"[" + name + L"] " + Utf8ToWide(accum) + L"\r\n");
        }
        CloseHandle(hRead);

        if (g_hWndMain) PostMessageW(g_hWndMain, WM_USER_PROC_EXITED, (WPARAM)idx, 0);
    }).detach();

    UpdateModuleUI(idx);
}

void StopModule(int idx) {
    if (idx < 0 || idx >= MOD_COUNT) return;
    ManagedProcess& proc = g_modules[idx];
    if (!proc.isRunning || proc.pid == 0) return;

    proc.stopping = true;
    PostLogMessage(GetTimestamp() + L"■ [" + proc.name + L"] 종료 요청 (PID " +
                   std::to_wstring(proc.pid) + L")\r\n");

    // 파이썬이 다시 자식(streamlit 등)을 띄우므로 트리째 정리한다.
    std::wstring killCmd = L"taskkill.exe /F /T /PID " + std::to_wstring(proc.pid);
    std::vector<wchar_t> buf(killCmd.begin(), killCmd.end());
    buf.push_back(0);

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    if (CreateProcessW(NULL, buf.data(), NULL, NULL, FALSE, CREATE_NO_WINDOW,
                       NULL, NULL, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, 3000);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
}

// 프로세스가 끝났을 때의 뒷정리. 반드시 UI 스레드에서만 부른다.
void ReapModule(int idx) {
    if (idx < 0 || idx >= MOD_COUNT) return;
    ManagedProcess& proc = g_modules[idx];
    if (!proc.hProcess) {
        proc.isRunning = false;
        UpdateModuleUI(idx);
        return;
    }

    WaitForSingleObject(proc.hProcess, 3000);
    DWORD exitCode = 0;
    GetExitCodeProcess(proc.hProcess, &exitCode);
    CloseHandle(proc.hProcess);
    proc.hProcess = NULL;
    if (proc.hThread) {
        CloseHandle(proc.hThread);
        proc.hThread = NULL;
    }
    proc.isRunning = false;
    proc.pid = 0;

    std::wstring suffix = proc.stopping ? L"" : L"  ← 예기치 않은 종료";
    PostLogMessage(GetTimestamp() + L"● [" + proc.name + L"] 종료 (코드 " +
                   std::to_wstring(exitCode) + L")" + suffix + L"\r\n");
    proc.stopping = false;
    UpdateModuleUI(idx);
}

// 관리 스크립트(clean_dummy 등)를 콘솔 창 없이 돌리고 출력을 로그 패널로 가져온다.
// 예전 _wsystem 은 작업 폴더를 지정할 수 없고 cmd 창이 깜빡였으며 출력이 사라졌다.
void RunUtilityScript(const std::wstring& scriptRelPath, const std::wstring& label) {
    if (!g_python.found) {
        PostLogMessage(GetTimestamp() + L"[오류] 파이썬을 찾지 못해 " + label + L" 을(를) 실행할 수 없습니다.\r\n");
        return;
    }
    std::wstring cmd = g_python.command + L" -u " + scriptRelPath;
    std::wstring projDir = g_projectDir;

    PostLogMessage(GetTimestamp() + L"▷ " + label + L" 실행 중...\r\n");

    std::thread([cmd, projDir, label]() {
        SECURITY_ATTRIBUTES sa;
        ZeroMemory(&sa, sizeof(sa));
        sa.nLength = sizeof(sa);
        sa.bInheritHandle = TRUE;

        HANDLE hRead = NULL, hWrite = NULL;
        if (!CreatePipe(&hRead, &hWrite, &sa, 0)) {
            PostLogMessage(GetTimestamp() + L"[오류] " + label + L" 파이프 생성 실패\r\n");
            return;
        }
        SetHandleInformation(hRead, HANDLE_FLAG_INHERIT, 0);

        STARTUPINFOW si;
        ZeroMemory(&si, sizeof(si));
        si.cb = sizeof(si);
        si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
        si.hStdOutput = hWrite;
        si.hStdError = hWrite;
        si.wShowWindow = SW_HIDE;
        PROCESS_INFORMATION pi;
        ZeroMemory(&pi, sizeof(pi));

        std::vector<wchar_t> buf(cmd.begin(), cmd.end());
        buf.push_back(0);
        BOOL ok = CreateProcessW(NULL, buf.data(), NULL, NULL, TRUE, CREATE_NO_WINDOW,
                                 NULL, projDir.c_str(), &si, &pi);
        CloseHandle(hWrite);
        if (!ok) {
            CloseHandle(hRead);
            PostLogMessage(GetTimestamp() + L"[오류] " + label + L" 실행 실패 (코드 " +
                           std::to_wstring(GetLastError()) + L")\r\n");
            return;
        }

        char buffer[4096];
        DWORD n = 0;
        std::string accum;
        while (ReadFile(hRead, buffer, sizeof(buffer), &n, NULL) && n > 0) {
            accum.append(buffer, n);
            size_t pos;
            while ((pos = accum.find('\n')) != std::string::npos) {
                std::string line = accum.substr(0, pos);
                accum.erase(0, pos + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                PostLogMessage(L"[" + label + L"] " + Utf8ToWide(line) + L"\r\n");
            }
        }
        if (!accum.empty()) PostLogMessage(L"[" + label + L"] " + Utf8ToWide(accum) + L"\r\n");
        CloseHandle(hRead);

        WaitForSingleObject(pi.hProcess, INFINITE);
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);

        PostLogMessage(GetTimestamp() + (code == 0 ? L"✔ " : L"✘ ") + label +
                       L" 완료 (코드 " + std::to_wstring(code) + L")\r\n");
    }).detach();
}

// ============================ 레이아웃 ============================

struct Layout {
    int headerH = 92;
    RECT cardModules{}, cardParsers{}, cardUtils{};
    RECT logHeader{}, logEdit{}, logBar{};
    int leftX = 24, leftW = 392;
};

Layout g_layout;

void ComputeLayout(int cw, int ch) {
    Layout& L = g_layout;
    const int gap = 12;

    // 파서 카드 높이 = 제목(48) + 토글 6줄(6*30) + 구분선 여백 + 일시정지 줄 + 아래 여백.
    // 예전에는 마지막 토글(…224)과 일시정지(222)가 겹쳐 구분선이 가려졌다.
    int y = L.headerH + 16;
    L.cardModules = {L.leftX, y, L.leftX + L.leftW, y + 242};
    y = L.cardModules.bottom + gap;
    L.cardParsers = {L.leftX, y, L.leftX + L.leftW, y + 286};
    y = L.cardParsers.bottom + gap;
    L.cardUtils = {L.leftX, y, L.leftX + L.leftW, y + 130};

    int rx = L.leftX + L.leftW + 24;
    int rw = cw - rx - 24;
    if (rw < 320) rw = 320;

    L.logHeader = {rx, L.headerH + 16, rx + rw, L.headerH + 40};
    int barH = 34;
    int logBottom = ch - 24 - barH - 10;
    if (logBottom < L.logHeader.bottom + 120) logBottom = L.logHeader.bottom + 120;
    L.logEdit = {rx, L.logHeader.bottom + 6, rx + rw, logBottom};
    L.logBar = {rx, logBottom + 10, rx + rw, logBottom + 10 + barH};
}

void MoveCtl(HWND h, int x, int y, int w, int hh) {
    if (h) MoveWindow(h, x, y, w, hh, TRUE);
}

void ApplyLayout() {
    const Layout& L = g_layout;

    // 모듈 카드
    int cx = L.cardModules.left + 18;
    int cw = (L.cardModules.right - L.cardModules.left) - 36;
    int rowY = L.cardModules.top + 48;
    for (int i = 0; i < MOD_COUNT; i++) {
        MoveCtl(g_modules[i].hName, cx, rowY, cw - 110, 18);
        MoveCtl(g_modules[i].hStatus, cx + 16, rowY + 20, cw - 110, 16);
        MoveCtl(g_modules[i].hBtn, L.cardModules.right - 18 - 96, rowY + 4, 96, 30);
        rowY += 50;
    }
    // 전체 시작 / 전체 중지 / 브라우저 열기 (3등분)
    int allY = L.cardModules.top + 196;
    int thirdW = (cw - 16) / 3;
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_START_ALL), cx, allY, thirdW, 32);
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_STOP_ALL), cx + thirdW + 8, allY, thirdW, 32);
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_OPEN_BROWSER), cx + (thirdW + 8) * 2, allY,
            cw - (thirdW + 8) * 2, 32);

    // 파서 카드
    int py = L.cardParsers.top + 48;
    for (int i = 0; i < appcfg::PARSER_COUNT; i++) {
        MoveCtl(g_hChkParsers[i], L.cardParsers.left + 18, py, cw, 26);
        py += 30;
    }
    MoveCtl(g_hChkPause, L.cardParsers.left + 18, L.cardParsers.top + 246, cw, 26);

    // 유틸리티 카드
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_CLEAN_DUMMY), L.cardUtils.left + 18,
            L.cardUtils.top + 46, cw, 32);
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_RESET_STORAGE), L.cardUtils.left + 18,
            L.cardUtils.top + 84, cw, 32);

    // 로그 패널
    MoveCtl(g_hEditLog, L.logEdit.left, L.logEdit.top,
            L.logEdit.right - L.logEdit.left, L.logEdit.bottom - L.logEdit.top);
    MoveCtl(g_hChkAutoScroll, L.logBar.left, L.logBar.top + 4, 150, 26);
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_CLEAR_LOG), L.logBar.left + 160, L.logBar.top, 96, 30);
    MoveCtl(GetDlgItem(g_hWndMain, IDC_BTN_COPY_LOG), L.logBar.left + 264, L.logBar.top, 96, 30);
    MoveCtl(g_hStaticLogInfo, L.logBar.left + 372, L.logBar.top + 8,
            (L.logBar.right - L.logBar.left) - 372, 20);
}

// ============================ 컨트롤 생성 ============================

HWND MakeButton(HWND parent, int id, const wchar_t* text, BtnStyle style) {
    HWND h = CreateWindowExW(0, L"BUTTON", text,
                             WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
                             0, 0, 10, 10, parent, (HMENU)(INT_PTR)id, g_hInstance, NULL);
    SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)style);
    return h;
}

HWND MakeLabel(HWND parent, int id, const wchar_t* text, HFONT font) {
    HWND h = CreateWindowExW(0, L"STATIC", text, WS_CHILD | WS_VISIBLE | SS_LEFT,
                             0, 0, 10, 10, parent, (HMENU)(INT_PTR)id, g_hInstance, NULL);
    SendMessageW(h, WM_SETFONT, (WPARAM)font, TRUE);
    return h;
}

void CreateFonts() {
    auto mk = [](int height, int weight, const wchar_t* face) {
        return CreateFontW(height, 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                           OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                           DEFAULT_PITCH, face);
    };
    g_fontTitle = mk(-21, FW_SEMIBOLD, L"Malgun Gothic");
    g_fontCard  = mk(-15, FW_SEMIBOLD, L"Malgun Gothic");
    g_fontBody  = mk(-14, FW_NORMAL,   L"Malgun Gothic");
    g_fontSmall = mk(-12, FW_NORMAL,   L"Malgun Gothic");
    g_fontLog   = mk(-13, FW_NORMAL,   L"Consolas");
}

void CreateUIControls(HWND hWnd) {
    CreateFonts();
    g_brushCard = CreateSolidBrush(theme::CARD);
    g_brushLog = CreateSolidBrush(theme::SURFACE);

    const wchar_t* modNames[MOD_COUNT] = {
        L"웹 대시보드 (Port 8501)", L"Watchdog 수집기", L"TDMS 변환기"
    };
    const int nameIds[MOD_COUNT] = {IDC_STATIC_NAME_UI, IDC_STATIC_NAME_WATCHDOG, IDC_STATIC_NAME_TDMS};
    const int statusIds[MOD_COUNT] = {IDC_STATIC_STATUS_UI, IDC_STATIC_STATUS_WATCHDOG, IDC_STATIC_STATUS_TDMS};
    const int btnIds[MOD_COUNT] = {IDC_BTN_TOGGLE_UI, IDC_BTN_TOGGLE_WATCHDOG, IDC_BTN_TOGGLE_TDMS};

    for (int i = 0; i < MOD_COUNT; i++) {
        g_modules[i].hName = MakeLabel(hWnd, nameIds[i], modNames[i], g_fontBody);
        g_modules[i].hStatus = MakeLabel(hWnd, statusIds[i], L"중지됨", g_fontSmall);
        g_modules[i].hBtn = MakeButton(hWnd, btnIds[i], L"시작", STYLE_MODULE);
    }

    MakeButton(hWnd, IDC_BTN_START_ALL, L"전체 시작", STYLE_ACCENT);
    MakeButton(hWnd, IDC_BTN_STOP_ALL, L"전체 중지", STYLE_GHOST);
    MakeButton(hWnd, IDC_BTN_OPEN_BROWSER, L"브라우저", STYLE_GHOST);

    for (int i = 0; i < appcfg::PARSER_COUNT; i++) {
        g_hChkParsers[i] = MakeButton(hWnd, IDC_CHK_PARSER_BASE + i,
                                      appcfg::ParserLabel(i), STYLE_TOGGLE);
    }
    g_hChkPause = MakeButton(hWnd, IDC_CHK_PAUSE, L"파이프라인 일시 정지", STYLE_TOGGLE);

    MakeButton(hWnd, IDC_BTN_CLEAN_DUMMY, L"더미 데이터 청소", STYLE_GHOST);
    MakeButton(hWnd, IDC_BTN_RESET_STORAGE, L"DB · 스토리지 초기화", STYLE_DANGER);

    g_hEditLog = CreateWindowExW(0, L"EDIT", L"",
                                 WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE |
                                 ES_AUTOVSCROLL | ES_READONLY,
                                 0, 0, 10, 10, hWnd, (HMENU)IDC_EDIT_LOG, g_hInstance, NULL);
    SendMessageW(g_hEditLog, WM_SETFONT, (WPARAM)g_fontLog, TRUE);
    SendMessageW(g_hEditLog, EM_SETLIMITTEXT, 10 * 1024 * 1024, 0);

    g_hChkAutoScroll = MakeButton(hWnd, IDC_CHK_AUTOSCROLL, L"자동 스크롤", STYLE_TOGGLE);
    MakeButton(hWnd, IDC_BTN_CLEAR_LOG, L"비우기", STYLE_GHOST);
    MakeButton(hWnd, IDC_BTN_COPY_LOG, L"복사", STYLE_GHOST);
    g_hStaticLogInfo = MakeLabel(hWnd, IDC_STATIC_LOG_INFO, L"로그 0줄", g_fontSmall);
}

void UpdateModuleUI(int idx) {
    if (idx < 0 || idx >= MOD_COUNT) return;
    ManagedProcess& proc = g_modules[idx];
    if (proc.hBtn) {
        SetWindowTextW(proc.hBtn, proc.isRunning ? L"중지" : L"시작");
        InvalidateRect(proc.hBtn, NULL, TRUE);
    }
    if (proc.hStatus) {
        std::wstring txt = proc.isRunning
            ? (L"실행 중 · PID " + std::to_wstring(proc.pid))
            : std::wstring(L"중지됨");
        SetWindowTextW(proc.hStatus, txt.c_str());
    }
    if (g_hWndMain) {
        // 상태 점(●)은 부모가 그리므로 해당 줄을 다시 칠하게 한다.
        RECT r = g_layout.cardModules;
        r.top += 40 + idx * 50;
        r.bottom = r.top + 44;
        InvalidateRect(g_hWndMain, &r, FALSE);
    }
}

// ============================ 그리기 ============================

void DrawToggleItem(LPDRAWITEMSTRUCT dis, const std::wstring& label, bool checked) {
    RECT rc = dis->rcItem;
    int h = rc.bottom - rc.top;

    // 배경은 GDI FillRect 로 지운다.
    // BUTTON 클래스는 먼저 COLOR_BTNFACE(밝은 회색)로 지우는데, GDI+ 로 안티앨리어싱을 켠 채
    // 사각형을 채우면 가장자리 한 줄이 부분 커버라서 그 회색이 비쳐 밝은 선으로 남았다.
    // FillRect 는 픽셀 단위로 정확히 채우므로 그 틈이 생기지 않는다.
    FillRect(dis->hDC, &rc, g_brushCard);

    Graphics g(dis->hDC);
    g.SetSmoothingMode(SmoothingModeAntiAlias);

    const int trackW = 40, trackH = 20;
    int ty = (h - trackH) / 2;
    Rect track(0, ty, trackW, trackH);
    COLORREF trackColor = checked ? theme::ACCENT : theme::TRACK_OFF;
    theme::FillRoundRect(g, track, trackH / 2, trackColor, trackColor, 1);

    int knobR = (trackH - 6) / 2;
    int knobX = checked ? (trackW - knobR - 4) : (knobR + 4);
    theme::FillCircle(g, knobX, ty + trackH / 2, knobR, theme::KNOB);

    SetBkMode(dis->hDC, TRANSPARENT);
    SetTextColor(dis->hDC, checked ? theme::TEXT : theme::TEXT_DIM);
    SelectObject(dis->hDC, g_fontBody);
    RECT tr = {trackW + 12, rc.top, rc.right - rc.left, rc.bottom};
    DrawTextW(dis->hDC, label.c_str(), -1, &tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);

    if (dis->itemState & ODS_FOCUS) {
        Pen pen(theme::ToGdi(theme::ACCENT, 120), 1.0f);
        g.DrawRectangle(&pen, 0, 0, rc.right - rc.left - 1, h - 1);
    }
}

void DrawPillButton(LPDRAWITEMSTRUCT dis, const std::wstring& text, BtnStyle style, bool active) {
    RECT rc = dis->rcItem;
    int w = rc.right - rc.left, h = rc.bottom - rc.top;
    bool pressed = (dis->itemState & ODS_SELECTED) != 0;

    // GDI+ 안티앨리어싱이 가장자리를 덜 덮어 BUTTON 기본 배경색이 비치지 않도록
    // 배경은 GDI 로 정확히 지운다 (DrawToggleItem 의 설명 참고).
    FillRect(dis->hDC, &rc, g_brushCard);

    Graphics g(dis->hDC);
    g.SetSmoothingMode(SmoothingModeAntiAlias);

    // Google Cloud 콘솔의 버튼 규칙을 따른다.
    //  - 주 동작: 파랑으로 채우고 글자는 흰색
    //  - 보조 동작: 흰 바탕 + 회색 테두리 + 파란 글자 (눌리면 연한 파란 배경)
    //  - 파괴적 동작: 흰 바탕 + 빨간 테두리 + 빨간 글자
    COLORREF fill = theme::CARD, edge = theme::CARD_EDGE, fg = theme::TEXT;
    switch (style) {
    case STYLE_ACCENT:
        fill = pressed ? theme::ACCENT_DK : theme::ACCENT;
        edge = pressed ? theme::ACCENT_DK : theme::ACCENT;
        fg = RGB(255, 255, 255);
        break;
    case STYLE_DANGER:
        fill = pressed ? theme::DANGER_BG : theme::CARD;
        edge = theme::DANGER;
        fg = theme::DANGER;
        break;
    case STYLE_MODULE:
        // 실행 중이면 "중지"(빨간 외곽선), 아니면 "시작"(파란 채움)
        if (active) { fill = pressed ? theme::DANGER_BG : theme::CARD; edge = theme::DANGER;
                      fg = theme::DANGER; }
        else        { fill = pressed ? theme::ACCENT_DK : theme::ACCENT;
                      edge = pressed ? theme::ACCENT_DK : theme::ACCENT;
                      fg = RGB(255, 255, 255); }
        break;
    case STYLE_GHOST:
    default:
        fill = pressed ? theme::ACCENT_BG : theme::CARD;
        edge = theme::CARD_EDGE;
        fg = theme::ACCENT;
        break;
    }

    Rect r(0, 0, w - 1, h - 1);
    theme::FillRoundRect(g, r, 8, fill, edge, 1);

    SetBkMode(dis->hDC, TRANSPARENT);
    SetTextColor(dis->hDC, fg);
    SelectObject(dis->hDC, g_fontBody);
    RECT tr = {0, 0, w, h};
    DrawTextW(dis->hDC, text.c_str(), -1, &tr, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
}

void DrawCard(Graphics& g, const RECT& rc, const wchar_t* title, HDC hdc) {
    Rect r(rc.left, rc.top, rc.right - rc.left - 1, rc.bottom - rc.top - 1);
    theme::FillRoundRect(g, r, 10, theme::CARD, theme::CARD_EDGE, 1);

    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, theme::TEXT);
    SelectObject(hdc, g_fontCard);
    RECT tr = {rc.left + 18, rc.top + 14, rc.right - 18, rc.top + 38};
    DrawTextW(hdc, title, -1, &tr, DT_LEFT | DT_TOP | DT_SINGLELINE);
}

void OnPaint(HWND hWnd) {
    PAINTSTRUCT ps;
    HDC hdc = BeginPaint(hWnd, &ps);

    RECT client;
    GetClientRect(hWnd, &client);

    // 더블 버퍼링 (리사이즈 중 깜빡임 방지)
    HDC mem = CreateCompatibleDC(hdc);
    HBITMAP bmp = CreateCompatibleBitmap(hdc, client.right, client.bottom);
    HGDIOBJ oldBmp = SelectObject(mem, bmp);

    HBRUSH bgBrush = CreateSolidBrush(theme::BG);
    FillRect(mem, &client, bgBrush);
    DeleteObject(bgBrush);

    Graphics g(mem);
    g.SetSmoothingMode(SmoothingModeAntiAlias);

    // --- 헤더 ---
    Rect header(0, 0, client.right, g_layout.headerH);
    SolidBrush headerBrush(theme::ToGdi(theme::CARD));
    g.FillRectangle(&headerBrush, header);
    Pen headerLine(theme::ToGdi(theme::CARD_EDGE), 1.0f);
    g.DrawLine(&headerLine, 0, g_layout.headerH - 1, client.right, g_layout.headerH - 1);

    if (g_logo) {
        g.SetInterpolationMode(InterpolationModeHighQualityBicubic);
        g.DrawImage(g_logo.get(), Rect(24, 14, 64, 64));
    }

    SetBkMode(mem, TRANSPARENT);
    SelectObject(mem, g_fontTitle);
    SetTextColor(mem, theme::TEXT);
    RECT t1 = {104, 20, client.right - 24, 48};
    DrawTextW(mem, L"공작기계지능화실험실 제조 DB Controller", -1, &t1,
              DT_LEFT | DT_TOP | DT_SINGLELINE);

    SelectObject(mem, g_fontSmall);
    SetTextColor(mem, theme::TEXT_DIM);
    std::wstring sub = L"Orcus System Controller  ·  파이썬: " + g_python.description;
    RECT t2 = {104, 52, client.right - 24, 74};
    DrawTextW(mem, sub.c_str(), -1, &t2, DT_LEFT | DT_TOP | DT_SINGLELINE);

    // --- 카드 ---
    DrawCard(g, g_layout.cardModules, L"핵심 시스템 모듈", mem);
    DrawCard(g, g_layout.cardParsers, L"파서 기능 On / Off", mem);
    DrawCard(g, g_layout.cardUtils, L"관리 유틸리티", mem);

    // 모듈 상태 점
    for (int i = 0; i < MOD_COUNT; i++) {
        int cy = g_layout.cardModules.top + 48 + i * 50 + 28;
        theme::FillCircle(g, g_layout.cardModules.left + 24, cy, 4,
                          g_modules[i].isRunning ? theme::SUCCESS : theme::DOT_OFF);
    }

    // 파서 카드 구분선 (일시 정지 토글 위)
    Pen divider(theme::ToGdi(theme::DIVIDER), 1.0f);
    int dy = g_layout.cardParsers.top + 234;
    g.DrawLine(&divider, g_layout.cardParsers.left + 18, dy, g_layout.cardParsers.right - 18, dy);

    // --- 로그 헤더 ---
    SelectObject(mem, g_fontCard);
    SetTextColor(mem, theme::TEXT);
    RECT lh = g_layout.logHeader;
    DrawTextW(mem, L"실시간 로그", -1, &lh, DT_LEFT | DT_TOP | DT_SINGLELINE);

    // 로그 패널도 카드처럼 보이도록 테두리를 두른다. EDIT 컨트롤이 자기 영역을 덮으므로
    // 한 픽셀 바깥에 그려야 테두리만 남는다.
    const RECT& le = g_layout.logEdit;
    Rect logFrame(le.left - 1, le.top - 1,
                  (le.right - le.left) + 1, (le.bottom - le.top) + 1);
    theme::FillRoundRect(g, logFrame, 8, theme::SURFACE, theme::CARD_EDGE, 1);

    BitBlt(hdc, 0, 0, client.right, client.bottom, mem, 0, 0, SRCCOPY);

    SelectObject(mem, oldBmp);
    DeleteObject(bmp);
    DeleteDC(mem);
    EndPaint(hWnd, &ps);
}

// ============================ 윈도우 프로시저 ============================

void ToggleParser(int index) {
    g_cfg.parsers[index] = !g_cfg.parsers[index];
    SaveConfigFromUI();
    InvalidateRect(g_hChkParsers[index], NULL, TRUE);
    PostLogMessage(GetTimestamp() + L"· " + appcfg::ParserLabel(index) + L" 파서 → " +
                   (g_cfg.parsers[index] ? L"사용" : L"중지") + L"\r\n");
}

LRESULT CALLBACK WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_CREATE: {
        g_hWndMain = hWnd;
        CreateUIControls(hWnd);
        RECT rc; GetClientRect(hWnd, &rc);
        ComputeLayout(rc.right, rc.bottom);
        ApplyLayout();

        PostLogMessage(L"═══════════════════════════════════════════════════\r\n");
        PostLogMessage(L"  공작기계지능화실험실 제조 DB Controller\r\n");
        PostLogMessage(L"═══════════════════════════════════════════════════\r\n");
        PostLogMessage(GetTimestamp() + L"파이썬 인터프리터: " + g_python.description + L"\r\n");
        if (!g_python.found) {
            PostLogMessage(GetTimestamp() + L"[경고] 파이썬을 찾지 못했습니다. 모듈을 시작할 수 없습니다.\r\n");
        }
        PostLogMessage(GetTimestamp() + L"준비 완료. 모듈을 시작하거나 파서를 토글하세요.\r\n\r\n");
        SetTimer(hWnd, 1, 1000, NULL);
        break;
    }

    case WM_SIZE:
        ComputeLayout(LOWORD(lParam), HIWORD(lParam));
        ApplyLayout();
        InvalidateRect(hWnd, NULL, FALSE);
        break;

    case WM_GETMINMAXINFO: {
        MINMAXINFO* mmi = (MINMAXINFO*)lParam;
        mmi->ptMinTrackSize.x = 1060;
        mmi->ptMinTrackSize.y = 880;   // 왼쪽 카드 3개가 잘리지 않는 최소 높이
        break;
    }

    case WM_ERASEBKGND:
        return 1;   // WM_PAINT 에서 전부 칠한다

    case WM_PAINT:
        OnPaint(hWnd);
        break;

    case WM_CTLCOLORSTATIC: {
        HDC hdc = (HDC)wParam;
        int id = GetDlgCtrlID((HWND)lParam);

        // 읽기 전용 EDIT 는 WM_CTLCOLOREDIT 가 아니라 이 메시지를 받는다.
        // 로그 창에는 배경 모드를 OPAQUE 로 둬야 한다. TRANSPARENT 로 두면 글자를 그릴 때
        // 이전 글자를 지우지 않아, 스크롤이 시작되는 순간부터 줄이 겹쳐 그려진다.
        if (id == IDC_EDIT_LOG) {
            SetBkMode(hdc, OPAQUE);
            SetTextColor(hdc, theme::TEXT);
            SetBkColor(hdc, theme::SURFACE);
            return (LRESULT)g_brushLog;
        }

        SetBkMode(hdc, TRANSPARENT);
        if (id == IDC_STATIC_STATUS_UI || id == IDC_STATIC_STATUS_WATCHDOG ||
            id == IDC_STATIC_STATUS_TDMS) {
            int mi = (id == IDC_STATIC_STATUS_UI) ? MOD_UI
                   : (id == IDC_STATIC_STATUS_WATCHDOG) ? MOD_WATCHDOG : MOD_TDMS;
            SetTextColor(hdc, g_modules[mi].isRunning ? theme::SUCCESS : theme::TEXT_DIM);
        } else if (id == IDC_STATIC_LOG_INFO) {
            SetTextColor(hdc, theme::TEXT_DIM);
        } else {
            SetTextColor(hdc, theme::TEXT);
        }
        return (LRESULT)g_brushCard;
    }

    case WM_CTLCOLOREDIT: {
        HDC hdc = (HDC)wParam;
        SetTextColor(hdc, theme::TEXT);
        SetBkColor(hdc, theme::SURFACE);
        return (LRESULT)g_brushLog;
    }

    case WM_DRAWITEM: {
        LPDRAWITEMSTRUCT dis = (LPDRAWITEMSTRUCT)lParam;
        BtnStyle style = (BtnStyle)GetWindowLongPtrW(dis->hwndItem, GWLP_USERDATA);
        wchar_t text[256] = {0};
        GetWindowTextW(dis->hwndItem, text, 255);

        if (style == STYLE_TOGGLE) {
            bool checked = false;
            int id = (int)dis->CtlID;
            if (id >= IDC_CHK_PARSER_BASE && id < IDC_CHK_PARSER_BASE + appcfg::PARSER_COUNT) {
                checked = g_cfg.parsers[id - IDC_CHK_PARSER_BASE];
            } else if (id == IDC_CHK_PAUSE) {
                checked = g_cfg.paused;
            } else if (id == IDC_CHK_AUTOSCROLL) {
                checked = g_autoScroll;
            }
            DrawToggleItem(dis, text, checked);
        } else {
            bool active = false;
            if (style == STYLE_MODULE) {
                int id = (int)dis->CtlID;
                int mi = (id == IDC_BTN_TOGGLE_UI) ? MOD_UI
                       : (id == IDC_BTN_TOGGLE_WATCHDOG) ? MOD_WATCHDOG : MOD_TDMS;
                active = g_modules[mi].isRunning;
            }
            DrawPillButton(dis, text, style, active);
        }
        return TRUE;
    }

    case WM_TIMER: {
        // 리더 스레드가 EOF 를 못 본 경우를 대비한 예비 점검
        for (int i = 0; i < MOD_COUNT; i++) {
            if (g_modules[i].isRunning && g_modules[i].hProcess) {
                DWORD code = 0;
                if (GetExitCodeProcess(g_modules[i].hProcess, &code) && code != STILL_ACTIVE) {
                    ReapModule(i);
                }
            }
        }
        break;
    }

    case WM_USER_PROC_EXITED:
        ReapModule((int)wParam);
        break;

    case WM_USER_APPEND_LOG: {
        std::vector<std::wstring> logs;
        {
            std::lock_guard<std::mutex> lock(g_logMutex);
            logs.swap(g_pendingLogs);
        }
        if (!logs.empty() && g_hEditLog) {
            std::wstring combined;
            for (const auto& s : logs) { combined += s; g_totalLogLines++; }
            int len = GetWindowTextLengthW(g_hEditLog);
            SendMessageW(g_hEditLog, EM_SETSEL, len, len);
            SendMessageW(g_hEditLog, EM_REPLACESEL, FALSE, (LPARAM)combined.c_str());
            if (g_autoScroll) SendMessageW(g_hEditLog, EM_SCROLLCARET, 0, 0);
            if (g_hStaticLogInfo) {
                std::wstring info = L"로그 " + std::to_wstring(g_totalLogLines) + L"줄";
                SetWindowTextW(g_hStaticLogInfo, info.c_str());
            }
        }
        break;
    }

    case WM_COMMAND: {
        int id = LOWORD(wParam);

        if (id >= IDC_CHK_PARSER_BASE && id < IDC_CHK_PARSER_BASE + appcfg::PARSER_COUNT) {
            ToggleParser(id - IDC_CHK_PARSER_BASE);
            break;
        }

        switch (id) {
        case IDC_BTN_TOGGLE_UI:
        case IDC_BTN_TOGGLE_WATCHDOG:
        case IDC_BTN_TOGGLE_TDMS: {
            int mi = (id == IDC_BTN_TOGGLE_UI) ? MOD_UI
                   : (id == IDC_BTN_TOGGLE_WATCHDOG) ? MOD_WATCHDOG : MOD_TDMS;
            if (g_modules[mi].isRunning) StopModule(mi); else StartModule(mi);
            break;
        }

        case IDC_BTN_OPEN_BROWSER:
            ShellExecuteW(NULL, L"open", L"http://localhost:8501", NULL, NULL, SW_SHOWNORMAL);
            break;

        case IDC_BTN_START_ALL:
            StartModule(MOD_WATCHDOG);
            StartModule(MOD_UI);
            StartModule(MOD_TDMS);
            break;

        case IDC_BTN_STOP_ALL:
            for (int i = 0; i < MOD_COUNT; i++) StopModule(i);
            break;

        case IDC_CHK_PAUSE:
            g_cfg.paused = !g_cfg.paused;
            SaveConfigFromUI();
            InvalidateRect(g_hChkPause, NULL, TRUE);
            PostLogMessage(GetTimestamp() + (g_cfg.paused
                           ? L"■ 파이프라인을 일시 정지했습니다 (모든 파서 무시).\r\n"
                           : L"▶ 파이프라인을 재개했습니다.\r\n"));
            break;

        case IDC_CHK_AUTOSCROLL:
            g_autoScroll = !g_autoScroll;
            InvalidateRect(g_hChkAutoScroll, NULL, TRUE);
            break;

        case IDC_BTN_CLEAN_DUMMY:
            RunUtilityScript(L"backend\\clean_dummy_data.py", L"더미 데이터 청소");
            break;

        case IDC_BTN_RESET_STORAGE: {
            // 백업 보관소는 프로젝트를 지워도 남으라고 밖으로 뺀 폴더이고,
            // 초기화 대상이 아니다. 무엇이 지워지고 무엇이 남는지 창에서 분명히 나눠 보여준다.
            std::wstring vaultLine = ResolveVaultRootForDisplay();
            std::wstring msg =
                L"[초기화 대상] 되돌릴 수 없습니다.\n"
                L"    · DB('orcus') 의 모든 테이블 내용\n"
                L"    · 프로젝트 폴더의 data\\machining_raw_data\n"
                L"    · 프로젝트 폴더의 data\\processed_data\n"
                L"    · 프로젝트 폴더의 data\\failed_data\n\n"
                L"[보존] 원본 백업 보관소는 지우지 않습니다.\n    " + vaultLine + L"\n\n"
                L"정말 진행하시겠습니까?";
            int ret = MessageBoxW(hWnd, msg.c_str(),
                L"초기화 확인", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2);
            if (ret == IDYES) {
                RunUtilityScript(L"backend\\reset_database_and_storage.py", L"DB · 스토리지 초기화");
            }
            break;
        }

        case IDC_BTN_CLEAR_LOG:
            SetWindowTextW(g_hEditLog, L"");
            g_totalLogLines = 0;
            SetWindowTextW(g_hStaticLogInfo, L"로그 0줄");
            break;

        case IDC_BTN_COPY_LOG: {
            int len = GetWindowTextLengthW(g_hEditLog);
            if (len > 0 && OpenClipboard(hWnd)) {
                std::vector<wchar_t> buf(len + 1);
                GetWindowTextW(g_hEditLog, buf.data(), len + 1);
                EmptyClipboard();
                HGLOBAL hGlob = GlobalAlloc(GMEM_MOVEABLE, (len + 1) * sizeof(wchar_t));
                if (hGlob) {
                    void* dst = GlobalLock(hGlob);
                    if (dst) {
                        memcpy(dst, buf.data(), (len + 1) * sizeof(wchar_t));
                        GlobalUnlock(hGlob);
                        SetClipboardData(CF_UNICODETEXT, hGlob);
                    }
                }
                CloseClipboard();
                PostLogMessage(GetTimestamp() + L"로그를 클립보드에 복사했습니다.\r\n");
            }
            break;
        }

        default:
            return DefWindowProcW(hWnd, msg, wParam, lParam);
        }
        break;
    }

    case WM_CLOSE:
        KillTimer(hWnd, 1);
        for (int i = 0; i < MOD_COUNT; i++) {
            if (g_modules[i].isRunning) StopModule(i);
        }
        DestroyWindow(hWnd);
        break;

    case WM_DESTROY:
        if (g_fontTitle) DeleteObject(g_fontTitle);
        if (g_fontCard) DeleteObject(g_fontCard);
        if (g_fontBody) DeleteObject(g_fontBody);
        if (g_fontSmall) DeleteObject(g_fontSmall);
        if (g_fontLog) DeleteObject(g_fontLog);
        if (g_brushCard) DeleteObject(g_brushCard);
        if (g_brushLog) DeleteObject(g_brushLog);
        g_logo.reset();
        if (g_hJobObject) CloseHandle(g_hJobObject);
        PostQuitMessage(0);
        break;

    default:
        return DefWindowProcW(hWnd, msg, wParam, lParam);
    }
    return 0;
}

// ============================ 진입점 ============================

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE, PWSTR, int nCmdShow) {
    g_hInstance = hInstance;
    g_projectDir = GetProjectDir();

    GdiplusStartupInput gdiIn;
    if (GdiplusStartup(&g_gdiplusToken, &gdiIn, NULL) != Ok) {
        MessageBoxW(NULL, L"GDI+ 초기화에 실패했습니다.", L"오류", MB_OK | MB_ICONERROR);
        return 1;
    }
    LoadLogo();

    g_cfg = appcfg::Load(ConfigPath());
    g_python = appcfg::Detect(g_projectDir);

    // 실시간 로그 창은 자식 프로세스의 출력을 CP_UTF8 로 해석한다(Utf8ToWide).
    // 그런데 윈도우 파이썬은 출력이 파이프일 때 UTF-8 이 아니라 시스템 ANSI 코드페이지
    // (한국어 환경이면 cp949)로 내보낸다. 그래서 파서들이 찍는 한글이 로그 창에서 깨졌다.
    // (실행한 쉘에 PYTHONIOENCODING 이 우연히 있으면 멀쩡해 보여서 더 헷갈린다.)
    // CreateProcessW 에 lpEnvironment=NULL 을 주면 자식이 이 프로세스의 환경을 물려받으므로,
    // 여기서 한 번만 지정해 두면 모듈 세 개 모두 UTF-8 로 출력한다.
    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");

    // 제어기가 비정상 종료돼도 자식 프로세스가 남지 않도록 Job Object 에 묶는다.
    g_hJobObject = CreateJobObjectW(NULL, NULL);
    if (g_hJobObject) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli;
        ZeroMemory(&jeli, sizeof(jeli));
        jeli.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(g_hJobObject, JobObjectExtendedLimitInformation, &jeli, sizeof(jeli));
    }

    g_modules[MOD_UI].name = L"Streamlit UI";
    g_modules[MOD_UI].scriptArgs = L"-m streamlit run frontend\\dashboard.py --server.port 8501";
    g_modules[MOD_WATCHDOG].name = L"Watchdog";
    g_modules[MOD_WATCHDOG].scriptArgs = L"-u backend\\data_insert_recognization.py";
    g_modules[MOD_TDMS].name = L"TDMS Visualizer";
    g_modules[MOD_TDMS].scriptArgs = L"-u backend\\tdms_visualizer.py";

    INITCOMMONCONTROLSEX icc;
    icc.dwSize = sizeof(icc);
    icc.dwICC = ICC_STANDARD_CLASSES;
    InitCommonControlsEx(&icc);

    HICON hIcon = (HICON)LoadImageW(NULL, (g_projectDir + L"\\assets\\logo.ico").c_str(), IMAGE_ICON,
                                    0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE);
    if (!hIcon) {
        hIcon = LoadIconW(hInstance, MAKEINTRESOURCEW(1));
    }
    HICON hIconSm = (HICON)LoadImageW(hInstance, MAKEINTRESOURCEW(1), IMAGE_ICON,
                                     GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), 0);
    if (!hIconSm) hIconSm = hIcon;

    WNDCLASSEXW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = NULL;
    wc.lpszClassName = L"OrcusSystemControllerClass";
    wc.hIcon = hIcon;
    wc.hIconSm = hIconSm;

    if (!RegisterClassExW(&wc)) {
        MessageBoxW(NULL, L"윈도우 클래스 등록에 실패했습니다.", L"오류", MB_OK | MB_ICONERROR);
        GdiplusShutdown(g_gdiplusToken);
        return 1;
    }

    RECT want = {0, 0, 1280, 840};
    AdjustWindowRect(&want, WS_OVERLAPPEDWINDOW, FALSE);
    HWND hWnd = CreateWindowExW(0, wc.lpszClassName,
                                L"공작기계지능화실험실 제조 DB Controller",
                                WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                                CW_USEDEFAULT, CW_USEDEFAULT,
                                want.right - want.left, want.bottom - want.top,
                                NULL, NULL, hInstance, NULL);
    if (!hWnd) {
        MessageBoxW(NULL, L"윈도우 생성에 실패했습니다.", L"오류", MB_OK | MB_ICONERROR);
        GdiplusShutdown(g_gdiplusToken);
        return 1;
    }

    SendMessageW(hWnd, WM_SETICON, ICON_BIG, (LPARAM)hIcon);
    SendMessageW(hWnd, WM_SETICON, ICON_SMALL, (LPARAM)hIconSm);

    ShowWindow(hWnd, nCmdShow);
    UpdateWindow(hWnd);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    GdiplusShutdown(g_gdiplusToken);
    return (int)msg.wParam;
}
