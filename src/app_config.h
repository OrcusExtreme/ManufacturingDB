// pipeline_config.json 읽기/쓰기와 파이썬 인터프리터 탐색.
//
// 설정 파일은 backend/pipeline_control.py 와 공유한다. 파이썬 쪽이 키를 더 붙이거나
// 순서를 바꿔도 깨지지 않도록, 아는 키만 갱신하고 모르는 키는 원문 그대로 보존한다.
#pragma once

#include <windows.h>
#include <shlwapi.h>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace appcfg {

// 파서 토글 키 (pipeline_control.DEFAULT_CONFIG 와 이름이 같아야 한다)
const int PARSER_COUNT = 6;
inline const char* ParserKey(int i) {
    static const char* keys[PARSER_COUNT] = {
        "enable_xml", "enable_nc", "enable_tdms", "enable_log", "enable_roughness", "enable_cad"
    };
    return keys[i];
}
inline const wchar_t* ParserLabel(int i) {
    static const wchar_t* labels[PARSER_COUNT] = {
        L"XML 메타데이터",
        L"NC 코드 · 절삭조건",
        L"TDMS 고주파 센서",
        L"CNC 1Hz 상태 로그",
        L"표면 조도 (Roughness)",
        L"3D CAD 도면 (STEP/STL)"
    };
    return labels[i];
}

struct Config {
    bool parsers[PARSER_COUNT] = {true, true, true, true, true, true};
    bool paused = false;
};

// --- 아주 작은 JSON 불리언 접근자 -------------------------------------------
// 전체 JSON 파서를 들일 만큼 복잡한 파일이 아니라서, "키": true/false 한 쌍만 다룬다.
// 문자열 값 안에 키 이름이 들어 있는 경우를 피하려고 따옴표까지 포함해 찾는다.

inline bool FindBool(const std::string& json, const std::string& key, bool fallback) {
    const std::string needle = "\"" + key + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return fallback;
    size_t colon = json.find(':', pos + needle.size());
    if (colon == std::string::npos) return fallback;

    // 콜론 뒤 공백을 건너뛰고 바로 오는 토큰만 본다 (뒤쪽 다른 키의 값을 읽지 않도록)
    size_t v = colon + 1;
    while (v < json.size() && (json[v] == ' ' || json[v] == '\t' || json[v] == '\r' || json[v] == '\n')) v++;
    if (json.compare(v, 4, "true") == 0) return true;
    if (json.compare(v, 5, "false") == 0) return false;
    return fallback;
}

// 값을 제자리에서 바꾼다. 키가 없으면 false 를 돌려주고 아무것도 건드리지 않는다.
inline bool ReplaceBool(std::string& json, const std::string& key, bool value) {
    const std::string needle = "\"" + key + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return false;
    size_t colon = json.find(':', pos + needle.size());
    if (colon == std::string::npos) return false;

    size_t v = colon + 1;
    while (v < json.size() && (json[v] == ' ' || json[v] == '\t' || json[v] == '\r' || json[v] == '\n')) v++;
    size_t len = 0;
    if (json.compare(v, 4, "true") == 0) len = 4;
    else if (json.compare(v, 5, "false") == 0) len = 5;
    else return false;

    json.replace(v, len, value ? "true" : "false");
    return true;
}

inline std::string ReadFileUtf8(const std::wstring& path) {
    std::ifstream f(path.c_str(), std::ios::binary);
    if (!f) return "";
    return std::string((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

inline bool WriteFileUtf8(const std::wstring& path, const std::string& content) {
    std::ofstream f(path.c_str(), std::ios::binary | std::ios::trunc);
    if (!f) return false;
    f << content;
    return f.good();
}

inline std::string Serialize(const Config& cfg) {
    std::ostringstream ss;
    ss << "{\n";
    for (int i = 0; i < PARSER_COUNT; i++) {
        ss << "  \"" << ParserKey(i) << "\": " << (cfg.parsers[i] ? "true" : "false") << ",\n";
    }
    ss << "  \"paused\": " << (cfg.paused ? "true" : "false") << "\n";
    ss << "}\n";
    return ss.str();
}

inline Config Load(const std::wstring& path) {
    Config cfg;
    std::string json = ReadFileUtf8(path);
    if (json.empty()) return cfg;
    for (int i = 0; i < PARSER_COUNT; i++) {
        cfg.parsers[i] = FindBool(json, ParserKey(i), true);
    }
    cfg.paused = FindBool(json, "paused", false);
    return cfg;
}

// 저장은 "있던 파일을 고쳐 쓰기"를 우선한다. 파이썬이 나중에 키를 추가하더라도
// 제어기가 토글 한 번에 그 키를 지워버리지 않게 하기 위해서다.
inline bool Save(const std::wstring& path, const Config& cfg) {
    std::string json = ReadFileUtf8(path);
    if (!json.empty()) {
        bool allReplaced = true;
        for (int i = 0; i < PARSER_COUNT && allReplaced; i++) {
            allReplaced = ReplaceBool(json, ParserKey(i), cfg.parsers[i]);
        }
        if (allReplaced && ReplaceBool(json, "paused", cfg.paused)) {
            return WriteFileUtf8(path, json);
        }
    }
    // 파일이 없거나 형태가 예상과 달라 제자리 수정이 불가능하면 새로 쓴다.
    return WriteFileUtf8(path, Serialize(cfg));
}

// --- 파이썬 인터프리터 탐색 ---------------------------------------------------
// 우선순위:
//   1) 프로젝트에 동봉한 runtime\python.exe  -> 파이썬 미설치 PC 에서도 동작
//   2) py -3 런처                            -> 표준 Windows 설치
//   3) PATH 의 python.exe
// 어느 경우든 실행되는 것은 디스크의 .py 파일이므로, 파이썬 소스를 고치면
// 제어기를 다시 빌드하지 않아도 다음 실행부터 반영된다.

struct Interpreter {
    std::wstring command;     // 명령줄 앞부분 (따옴표 포함 가능)
    std::wstring description; // 로그에 남길 설명
    bool found = false;
};

inline bool FileExists(const std::wstring& p) {
    DWORD attr = GetFileAttributesW(p.c_str());
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

// 런처에게 진짜 인터프리터 경로(sys.executable)를 물어본다.
//
// PATH 의 python.exe / py.exe 는 Microsoft Store 파이썬에서 0바이트 "앱 실행 별칭"
// (reparse point)인 경우가 많다. 그런 별칭은 표준 입출력을 리다이렉트하고 핸들을 상속시키는
// CreateProcess 호출에서 ERROR_CANT_ACCESS_FILE(1920)로 실패한다. 모듈 로그를 파이프로
// 받아야 하는 이 프로그램에서는 치명적이므로, 별칭을 한 번 실행해 실제 exe 경로를 받아
// 이후에는 그 절대 경로만 쓴다.
inline std::wstring QueryExecutablePath(const std::wstring& launcher) {
    SECURITY_ATTRIBUTES sa;
    ZeroMemory(&sa, sizeof(sa));
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;

    HANDLE hRead = NULL, hWrite = NULL;
    if (!CreatePipe(&hRead, &hWrite, &sa, 0)) return L"";
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

    std::wstring probe = launcher + L" -c \"import sys; print(sys.executable)\"";
    std::vector<wchar_t> buf(probe.begin(), probe.end());
    buf.push_back(0);

    // 여기서는 핸들 상속을 켜고 부른다. 별칭이면 이 단계에서 실패하므로
    // "쓸 수 있는 런처인지"까지 같은 조건으로 걸러진다.
    BOOL ok = CreateProcessW(NULL, buf.data(), NULL, NULL, TRUE,
                             CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    CloseHandle(hWrite);
    if (!ok) {
        CloseHandle(hRead);
        return L"";
    }

    std::string out;
    char chunk[512];
    DWORD n = 0;
    while (ReadFile(hRead, chunk, sizeof(chunk), &n, NULL) && n > 0) {
        out.append(chunk, n);
        if (out.size() > 4096) break;
    }
    CloseHandle(hRead);

    WaitForSingleObject(pi.hProcess, 10000);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (code != 0) return L"";

    while (!out.empty() && (out.back() == '\r' || out.back() == '\n' || out.back() == ' ')) out.pop_back();
    if (out.empty()) return L"";

    int wlen = MultiByteToWideChar(CP_UTF8, 0, out.c_str(), (int)out.size(), NULL, 0);
    if (wlen <= 0) return L"";
    std::wstring path(wlen, 0);
    MultiByteToWideChar(CP_UTF8, 0, out.c_str(), (int)out.size(), &path[0], wlen);
    return FileExists(path) ? path : L"";
}

inline Interpreter Detect(const std::wstring& projectDir) {
    Interpreter it;

    std::wstring bundled = projectDir + L"\\runtime\\python.exe";
    if (FileExists(bundled)) {
        it.command = L"\"" + bundled + L"\"";
        it.description = L"동봉 런타임 (runtime\\python.exe)";
        it.found = true;
        return it;
    }

    // 별칭이 아니라 실제 exe 경로를 확보한다 (QueryExecutablePath 의 설명 참고).
    struct { const wchar_t* launcher; const wchar_t* label; } probes[] = {
        {L"py -3",  L"Windows 파이썬 런처"},
        {L"python", L"PATH 의 python"},
    };
    for (const auto& p : probes) {
        std::wstring real = QueryExecutablePath(p.launcher);
        if (!real.empty()) {
            it.command = L"\"" + real + L"\"";
            it.description = std::wstring(p.label) + L" → " + real;
            it.found = true;
            return it;
        }
    }

    it.command = L"python";
    it.description = L"찾지 못함";
    it.found = false;
    return it;
}

}  // namespace appcfg
