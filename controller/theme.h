// 제어기 UI 팔레트와 그리기 도우미.
//
// Google Cloud 콘솔의 밝은 화면을 기준으로 삼는다. 흰 바탕에 옅은 회색 테두리로 면을 나누고,
// 색은 파랑 하나만 강조로 쓴다. 강조색 #1A73E8 은 Streamlit 대시보드 사이드바
// (frontend/dashboard.py)에서 쓰는 값과 같아서 두 화면의 인상이 이어진다.
#pragma once

#include <windows.h>
#include <objidl.h>
#include <gdiplus.h>
#include <string>

namespace theme {

// --- 팔레트 (COLORREF 는 0x00BBGGRR 이라 RGB 매크로로 만든다) ---
const COLORREF BG         = RGB(0xFF, 0xFF, 0xFF);   // 페이지 바탕
const COLORREF CARD       = RGB(0xFF, 0xFF, 0xFF);   // 카드 면
const COLORREF CARD_EDGE  = RGB(0xDA, 0xDC, 0xE0);   // 카드 테두리 (Google grey 300)
const COLORREF DIVIDER    = RGB(0xE8, 0xEA, 0xED);   // 카드 내부 구분선
const COLORREF TEXT       = RGB(0x20, 0x21, 0x24);   // 본문 (Google grey 900)
const COLORREF TEXT_DIM   = RGB(0x5F, 0x63, 0x68);   // 보조 설명 (Google grey 700)
const COLORREF ACCENT     = RGB(0x1A, 0x73, 0xE8);   // 강조 (대시보드와 동일)
const COLORREF ACCENT_DK  = RGB(0x17, 0x65, 0xCC);   // 강조 눌림
const COLORREF ACCENT_BG  = RGB(0xE8, 0xF0, 0xFE);   // 강조 연한 배경
const COLORREF SUCCESS    = RGB(0x1E, 0x8E, 0x3E);   // 실행 중
const COLORREF DANGER     = RGB(0xD9, 0x30, 0x25);   // 파괴적 동작
const COLORREF DANGER_BG  = RGB(0xFC, 0xE8, 0xE6);   // 파괴적 동작 눌림 배경
const COLORREF TRACK_OFF  = RGB(0xBD, 0xC1, 0xC6);   // 토글 꺼짐 트랙 (Google grey 400)
const COLORREF KNOB       = RGB(0xFF, 0xFF, 0xFF);   // 토글 손잡이
const COLORREF DOT_OFF    = RGB(0x9A, 0xA0, 0xA6);   // 중지된 모듈 표시점
const COLORREF SURFACE    = RGB(0xF8, 0xF9, 0xFA);   // 한 단계 낮은 면 (로그 패널)

inline Gdiplus::Color ToGdi(COLORREF c, BYTE alpha = 255) {
    return Gdiplus::Color(alpha, GetRValue(c), GetGValue(c), GetBValue(c));
}

// 모서리가 둥근 사각형 경로. 네 귀퉁이를 같은 반지름으로 깎는다.
inline void RoundRectPath(Gdiplus::GraphicsPath& path, const Gdiplus::Rect& r, int radius) {
    int d = radius * 2;
    path.Reset();
    if (radius <= 0) {
        path.AddRectangle(r);
        path.CloseFigure();
        return;
    }
    path.AddArc(r.X, r.Y, d, d, 180.0f, 90.0f);
    path.AddArc(r.GetRight() - d, r.Y, d, d, 270.0f, 90.0f);
    path.AddArc(r.GetRight() - d, r.GetBottom() - d, d, d, 0.0f, 90.0f);
    path.AddArc(r.X, r.GetBottom() - d, d, d, 90.0f, 90.0f);
    path.CloseFigure();
}

inline void FillRoundRect(Gdiplus::Graphics& g, const Gdiplus::Rect& r, int radius,
                          COLORREF fill, COLORREF edge, int edgeWidth = 1) {
    Gdiplus::GraphicsPath path;
    RoundRectPath(path, r, radius);
    Gdiplus::SolidBrush brush(ToGdi(fill));
    g.FillPath(&brush, &path);
    if (edgeWidth > 0) {
        Gdiplus::Pen pen(ToGdi(edge), (Gdiplus::REAL)edgeWidth);
        g.DrawPath(&pen, &path);
    }
}

inline void FillCircle(Gdiplus::Graphics& g, int cx, int cy, int radius, COLORREF fill) {
    Gdiplus::SolidBrush brush(ToGdi(fill));
    g.FillEllipse(&brush, cx - radius, cy - radius, radius * 2, radius * 2);
}

}  // namespace theme
