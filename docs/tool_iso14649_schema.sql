-- 공구 마스터 (ISO 14649 기반) — 매거진에 장착된 공구와 미장착 재고를 함께 담는다.
--
-- 원안 대비 [변경] 은 미장착 재고를 넣기 위한 것이다.
--   · tool_code 는 매거진 포트 번호라 장착된 공구에만 있다. 미장착이면 NULL.
--     (UNIQUE 는 NULL 을 여러 개 허용하므로 미장착 공구끼리 충돌하지 않는다)
--   · 재고에는 정체·직경·날 수를 모르는 것(시트에 '?'), 공구가 아닌 인서트가 섞여 있어
--     iso14649_class / cutter_diameter / number_of_teeth 의 NOT NULL 을 풀었다.
--   · 대신 '장착된 공구'에는 그 셋을 CHECK 로 강제한다. NC/XML 이 T번호로 부르는 공구는
--     원안처럼 빠짐없이 채워져 있어야 하기 때문이다. (MySQL 8.0.16 이상에서 강제됨)
--
-- 데이터: docs/tool_iso14649.csv · 근거: docs/tool_iso14649_sources.md
-- 검증:   python backend/tests/verify_tool_csv.py
CREATE TABLE tool (
    -- [1] 공구 식별 및 제조사 정보
    tool_id                 INT AUTO_INCREMENT PRIMARY KEY,
    tool_code               VARCHAR(20) NULL UNIQUE,      -- [변경] 매거진 포트 T번호 (예: T04, T09). 미장착 재고는 NULL
    manufacturer            VARCHAR(50),                  -- 제조사명 (OSG, Kyocera, NIKKEN 등)
    model_code              VARCHAR(100),                 -- 제조사 공식 카탈로그 형번 (예: MFWN90125R, GP3D1068K)

    -- [2] ISO 14649 공구 분류 (ISO 14649-111 Entity)
    iso14649_class          VARCHAR(50),                  -- [변경] NOT NULL 해제. ENDMILL, FACEMILL, TWIST_DRILL ... 정체 불명·인서트는 NULL
    hand_of_cut             VARCHAR(10) DEFAULT 'RIGHT',  -- 절삭 방향 (RIGHT, LEFT, NEUTRAL)

    -- [3] 공통 기하학적 치수 (Dimensions - 단위: mm)
    cutter_diameter         DECIMAL(6,3),                 -- [변경] NOT NULL 해제. 직경 (Dc) — 직경 미상 재고·인서트는 NULL
    shank_diameter          DECIMAL(6,3),                 -- 샹크 직경 (Ds / Dcon)
    cutting_edge_length     DECIMAL(6,2),                 -- 절삭날 길이/날장 (APMX / Lc)
    overall_length          DECIMAL(6,2),                 -- 전체 길이 (OAL / L)
    usable_length           DECIMAL(6,2),                 -- 유효장 / 목 깊이 (LU)

    -- [4] 공구 타입별 특화 기하학적 치수 (Type-Specific Parameters)
    corner_radius           DECIMAL(5,3) DEFAULT 0.000,   -- 코너 R (엔드밀/페이스밀 Tip Radius, RE)
    point_angle             DECIMAL(5,2),                 -- 선단각 (드릴 SIG, 보통 118° 또는 140°)
    approach_angle          DECIMAL(5,2),                 -- 진입각 / 리드각 (페이스밀 등, 예: 90°, 45°)
    helix_angle             DECIMAL(4,2),                 -- 비틀림각 (Helix angle)

    -- [5] 절삭날 및 가공 특성 (ISO 14649 Technology)
    number_of_teeth         INT DEFAULT 1,                -- [변경] NOT NULL 해제. 인선 수 / 날 수 (Z / Flutes)
    tool_material           VARCHAR(30),                  -- 모재 재질 (CARBIDE, HSS, CERMET, PCD 등)
    coating_type            VARCHAR(50),                  -- 코팅 (TiAlN, DLC, UNCOATED 등)
    coolant_through         BOOLEAN DEFAULT FALSE,        -- 내부 급유(쿨런트) 지원 여부

    -- [6] 샵 플로어 / 재고 관리 속성
    stock_count             INT DEFAULT 0,                -- 현재 재고 수량
    gauge_length            DECIMAL(6,3),                 -- 홀더 장착 후 게이지 라인 길이 (세팅 시)
    memo                    TEXT,                         -- 비고
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- [변경] 매거진에 장착된 공구는 분류·직경·날 수가 반드시 있어야 한다
    CONSTRAINT chk_tool_mounted_complete CHECK (
        tool_code IS NULL
        OR (iso14649_class IS NOT NULL
            AND cutter_diameter IS NOT NULL
            AND number_of_teeth IS NOT NULL)
    )
);
