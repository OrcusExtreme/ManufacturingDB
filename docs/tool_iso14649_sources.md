# tool_iso14649.csv — 값의 근거와 확인할 것

| 파일 | 내용 |
| :--- | :--- |
| `docs/tool_iso14649.csv` | 새 `tool` 테이블에 넣을 데이터 50행 (매거진 장착 18 + 미장착 재고 32) |
| `docs/tool_iso14649_schema.sql` | 새 `tool` 테이블 DDL (원안 + 미장착 재고를 위한 [변경]) |
| `backend/tests/verify_tool_csv.py` | CSV · `models.Tool` · 실제 tool 테이블 대조 (임시 테이블만 쓰고 실제 테이블은 읽기만) |
| `backend/migrate_tool_iso14649.py` | 옛 tool 테이블을 이 구조로 바꾸고 CSV 를 적재한 도구 (2026-10-02 적용) |

원본은 `실험실 공구 정리.xlsx`(LAB실 공구정리함 시트)이고, 비어 있던 사양은 제조사 카탈로그를
웹에서 찾아 채웠습니다. **출처로 확인된 값만** 넣었고, 후보가 여럿이라 갈리는 값은 비우고
`memo` 에 후보를 적었습니다. CSV 를 고친 뒤에는 `python backend/tests/verify_tool_csv.py` 를
다시 돌리면 됩니다.

## 1. 파일 규칙

| 항목 | 규칙 |
| :--- | :--- |
| 인코딩 | UTF-8 (BOM 포함 — 엑셀에서 한글이 깨지지 않게) |
| 열 | 테이블 열 순서 그대로. `tool_id`·`created_at`·`updated_at` 은 DB 가 채우므로 뺐습니다 |
| 행 순서 | 장착 공구(T01~T29) 다음에 미장착 재고를 **시트 순서대로** |
| 빈 칸 | 값을 모름. 공구 입력기가 **NULL 로 넣습니다** — DEFAULT 로 채우지 않음 (2 참고) |
| `tool_code` | 장착 공구는 표준 표기 `T01`~`T29`(`backend/tool_codes.py`), **미장착은 빈 칸** |
| `coolant_through` | `1` 확인됨 / `0` 해당 없음(프로브·마스터 툴) / 빈 칸 미확인 |
| `hand_of_cut` | 절삭 공구는 `RIGHT` — 이 가공기의 NC 가 정회전(M3)으로 돌리고, 카탈로그가 밝힌 공구도 모두 우회전. 프로브·마스터 툴은 `NEUTRAL`, 정체 불명은 빈 칸 |
| `number_of_teeth` | 프로브·마스터 툴은 `0` (절삭날 없음). 날 수를 계산에 쓰는 코드는 현재 없음 |
| `memo` | 형번이 확정되지 않은 미장착 행은 시트의 원래 규격 문자열과 사용 중 수량을 남김 |
| 단위 | mm, 도(°) |

## 2. 스키마 변경 (`tool_iso14649_schema.sql`)

미장착 재고를 넣으려면 원안의 NOT NULL 네 개를 풀어야 했습니다.

| 열 | 원안 | 변경 | 걸리는 행 |
| :--- | :--- | :--- | :--- |
| `tool_code` | NOT NULL UNIQUE | **NULL** UNIQUE | 미장착 32개 전부 (T번호가 없음) |
| `iso14649_class` | NOT NULL | NULL | 정체 불명 1개(엑셀 2행), Sandvik 인서트 |
| `cutter_diameter` | NOT NULL | NULL | 직경 미상 볼 엔드밀(엑셀 3행), 인서트, 선반 홀더 |
| `number_of_teeth` | NOT NULL DEFAULT 1 | NULL DEFAULT 1 | 정체 불명, 인서트, 선반 홀더 |

대신 **장착 공구에는 원안의 엄격함을 CHECK 로 유지**합니다. NC/XML 이 T번호로 부르는 공구는
분류·직경·날 수가 빠짐없이 있어야 하기 때문입니다.

```sql
CONSTRAINT chk_tool_mounted_complete CHECK (
    tool_code IS NULL
    OR (iso14649_class IS NOT NULL AND cutter_diameter IS NOT NULL AND number_of_teeth IS NOT NULL))
```

- MySQL 의 UNIQUE 는 NULL 을 여러 개 허용하므로 미장착 공구끼리 충돌하지 않습니다.
- CHECK 는 MySQL 8.0.16 이상에서 강제됩니다 (현재 서버 8.0.46).
- 파서(`nc_parser`·`xml_parser`·`tool_inserter`)는 이미 `WHERE tool_code IS NOT NULL` 로
  공구를 찾으므로, 미장착 공구는 T번호 매칭에 끼어들지 않습니다.
- **빈 칸은 NULL 로 넣어야 합니다.** `corner_radius DEFAULT 0.000` 때문에 "열 생략"으로 넣으면
  R 을 몰라서 비운 행(인서트형 T29·T20, 정체 불명 볼 엔드밀 등)이 R0 으로 저장되어 거짓이 됩니다.
  공구 입력기(`tool_inserter`)는 빈 칸을 항상 명시적 NULL 로 넣습니다.

## 3. 범위

- 시트 51행 → CSV 50행. **T19** 는 바디(KRUZ 215)와 팁(IDP 215) 두 행이 같은 포트를 써서 한
  공구로 합쳤습니다. `stock_count` 는 바디 수(3), 팁 재고(9)는 `memo` 에 적었습니다.
- 재고 합계는 시트 330 / CSV 321 로, 차이는 정확히 이 팁 9개입니다.
- 미장착 32행은 시트 순서대로이며, 행마다 시트의 직경·재고와 일치함을 확인했습니다.

## 4. ISO 14649 분류명 (`iso14649_class`)

ISO 14649-111:2010 의 EXPRESS 엔티티 이름을 대문자로 썼습니다. 예시로 주신 `REAMER` 는 옛
초안의 이름이고 2010 표준에서는 `REAMING_CUTTING_TOOL` 입니다(이 CSV 에는 리머가 없음).

| 값 | 공구 | 비고 |
| :--- | :--- | :--- |
| `ENDMILL` | 시트 분류가 '엔드밀'·'플랫 엔드밀'인 20개 (T01~T04 T06 T08 T09 + 미장착 13) | 플랫, 코너 R 0 (7 참고) |
| `BALLNOSE_ENDMILL` | T11, YG-1 볼 6종, JJTOOLS 4SUB, 엑셀 3행 | 코너 R = 직경/2. `ball_endmill` 은 옛 초안 이름 |
| `SHOULDERMILL` | T07, R390-016A16L-11L | 90° 직각 숄더 인서트 커터 |
| `FACEMILL` | T29, Kyocera 125·80 | |
| `TWIST_DRILL` | T16 T17 T18, OSG GP-3D 4종, YG-1 Ø7 | |
| `SPADE_DRILL` | T19 | 표준 정의 "홀더에 교체식 날/인서트를 물린 드릴"에 해당 |
| `ROTATING_BORING_CUTTING_TOOL` | T20 | 리머가 아니라 보링 헤드 |
| `TOUCH_PROBE` | T05 T22 | ISO 14649-10 의 엔티티 (-111 공구가 아님) |
| `GROOVING_TOOL` | Kyocera KGMR 홀더 | **선반 공구** — ISO 14649-121 의 엔티티 |
| `REFERENCE_TOOL` | T23 | **표준에 없는 자체 분류.** 마스터/게이지 툴 엔티티가 없음 |
| (빈 칸) | 엑셀 2행, Sandvik 인서트 | 정체 불명 / 인서트는 공구 본체가 아니라 ISO 14649 의 `cutting_component` |

근거: ISO 14649-111:2010 목차(https://cdn.standards.iteh.ai/samples/34746/8b24c587fe044222ba5e2839f0628f00/ISO-14649-111-2010.pdf),
STEP Tools AP238 ARM EXPRESS(https://www.steptools.com/stds/stepnc/tech_resources/stepnc_arm_merged.exp),
grooving_tool(https://www.steptools.com/stds/stepnc/tech_resources/stepnc_arm_html/t_grooving_tool.html).

## 5. 시트와 다르게 넣은 값

| 공구 | 시트 | CSV | 근거 |
| :--- | :--- | :--- | :--- |
| OSG 드릴 6종 (T16 T18 + 미장착 4) | 형번 `GD-3D…K` | `GP3D…K` | OSG 에 GD-3D 시리즈 없음. 한국OSG 드릴 카탈로그 GP-3D 표에 같은 직경 행 |
| T19 | 직경 20 | **21.5** | YESTOOL 사이즈 코드 215 = Ø21.5, KRUZ 215 바디는 Ø21.5~21.9 팁 전용 |
| T20 | 리머, `9-ZMA20-40V`, 20~25Ø | 보링 헤드, `9-ZMAC20-40V`, 19.8 | NIKKEN 카탈로그: ZMAC 조정식 보링 헤드, 범위 Ø19.8~25.2. 장착 공구라 직경이 필수여서 범위 최솟값 |
| Kyocera 125·80 | 형번 `MTP90125R-22`·`MTP90080R` | 형번 빈 칸 | Kyocera MTP90 은 Ø50·63 생크 엔드밀뿐. 125·8날, 80·5날은 MFWN 파인피치(-8T, -5T)와 일치 — **추정 식별**, 치수는 그 표에서 |
| Kyocera `KGMR1212X-2` | 종류·직경 빈 칸 | `GROOVING_TOOL` | 자동선반용 외경 홈가공 홀더. KGMR1212JX-2 로 추정 |
| `4SUB`, `JJ TOOL` | 제조사 | `JJTOOLS` | 둘 다 JJTOOLS(제이제이툴스) 형번. 4SUB 는 시리즈 코드 |
| JJTOOLS 3SUE | 규격 `3SUE 100 220 S10` | 전장 70 | 형번의 100 은 직경 코드(Ø10), 카탈로그 전장 70 |
| OSG 2날 `80*30*12` | 직경 빈 칸 | 12 | 이 치수(전장 80·날장 30·생크 12)의 OSG 2날 엔드밀은 모두 Ø12 |

참고: `콜렛 규격` 시트의 `K32-ZMAC20-60` 은 콜렛이 아니라 T20 헤드를 끼우는 Ø32 직선 생크
보링 바(`K32-ZMAC20-60V`)로 보입니다.

## 6. 실물을 보고 확정할 것

생크 각인·포장의 형번만 보면 대부분 풀립니다.

**장착 공구**

| 공구 | 확인할 것 | 정해지는 값 |
| :--- | :--- | :--- |
| T01 T02 | 무코팅(E5D70) / DLC(GED70) | `model_code`, `coating_type` |
| T03 | ALU-CUT(45°) / ALU-CUT HPC(37°), 코팅 | `model_code`, `helix_angle`, `coating_type` |
| T06 | 날장 32 / 45 (카탈로그 WAR503120 / 121), 실물이 정말 플랫인지 | `model_code`, 날장·전장 |
| T08 | 실물이 정말 플랫인지 (카탈로그 WAR303 은 R1~R5) | `model_code` |
| T09 | GS-EMS / WXL-EMS / WX-EMS | `model_code`, `coating_type` |
| T11 | **직경** — 시리즈 최소가 Ø6 이라 시트의 Ø5 와 안 맞음 (R5=Ø10 일 수 있음) | 직경과 나머지 치수 전부 |
| T17 | 형번(Dream Drill 시리즈), 오일홀 유무 | `model_code`, 선단각, 코팅, 급유 |
| T19 | 바디 깊이 기호 T/P/H/L (3·5·7·10xD), 플랜지(Ø32) 유무 | 전장·유효장, 생크 |
| T20 | 현재 세팅 직경, 인서트 R0.2/R0.4 | `cutter_diameter`, `corner_radius` |
| T29 | 접미사 -6T-M / -6T (보어 Ø40 / Ø38.1), 인서트 형번 | `shank_diameter`, `corner_radius`, 재종 |
| T04 | 제조사·형번 (시트에 '?') | 전부 |
| T05 · T22 | 프로브 모델, 생크 형식 | 모델 |

**미장착 재고**

| 공구 | 확인할 것 | 정해지는 값 |
| :--- | :--- | :--- |
| 엑셀 2행 (9.7Ø) · 3행 (4날 볼) | 정체 · 직경 · 수량 | 거의 전부 |
| Kyocera 125 · 80 | 각인 (MFWN90125R-8T(-M) / MFWN90080R-5T(-M) 인지), 보어 | `model_code`, 보어, 인서트 |
| Kyocera KGMR | 전장 120 / 85 (JX / F-85) | `model_code`, 전장 |
| OSG 4날 Ø10~20 · 2날 Ø12 · Ø9 | 시리즈(GS-EMS 등), Ø9 의 날장(19/22) | `model_code`, 코팅, 비틀림각(Ø10) |
| WIDIN WAE302 · WAE303 | 날장·전장 변형 | `model_code`, 날장·전장 |
| WIDIN WAR302 | 실물이 정말 플랫인지 (카탈로그 WAR302 는 R1~R5) | `model_code` |
| YG-1 볼 6종 | 시리즈 (HSS-Co8 / 분말HSS / 초경) | `model_code`, 재질, 코팅 |
| YG-1 3날 3종 · 드릴 Ø7 | 코팅·시리즈, 오일홀 | `model_code`, 코팅, 비틀림각·선단각 |
| Sandvik 인서트 | 재종 (예: 4220) | 재질, 코팅 |

**모든 공구** — `gauge_length` 는 가공기 공구 보정 테이블에서 (T23 만 시트에 있음).
가공 XML 의 `cutterDiameter`·`toolLength` 도 확인했지만 모든 공구가 기본값(D10, 길이 0)이라
판별에 쓸 수 없었습니다.

## 7. 시트의 '엔드밀' = 플랫 엔드밀

실험실 시트에서 공구 종류가 **'엔드밀'인 행은 모두 플랫(스퀘어) 엔드밀**로 둡니다
(`ENDMILL`, `corner_radius` 0). 실험실이 이 공구들을 플랫으로 쓰고 관리한다는 결정입니다.

카탈로그와 어긋나는 곳이 세 개 있어 `memo` 에 남겼습니다. 시트에 적힌 WIDIN 형번 WAR503(T06) ·
WAR303(T08) · WAR302(미장착)는 카탈로그상 **코너 R 시리즈**입니다(같은 계열의 스퀘어는 WAE).
치수는 시트 형번의 카탈로그 값 중 R 과 무관하게 공통인 것만 채웠습니다(T08 · WAR302 의 날장 45 ·
전장 100). 실물이 코너 R 공구로 확인되면 그 행만 `BULLNOSE_ENDMILL` 과 실제 R 로 고치면 됩니다.

검증: 시트 51행을 CSV 행과 하나씩 짝지어, '엔드밀' 19행 + '플랫 엔드밀' 1행이 모두 `ENDMILL` ·
R 0 이고 '볼 엔드밀' 9행은 R = 직경/2 임을 확인했습니다.

## 8. DB 반영 (2026-10-02)

`python backend/migrate_tool_iso14649.py --apply` 로 실제 `tool` 테이블을 이 구조로 바꾸고 50행을
넣었습니다.

- 옛 내용 백업: `backups/tool_before_iso14649_20261002_192436.csv` (옛 18행),
  `backups/workingstep_tool_links_20261002_192436.csv` (가공 이력의 공구 연결)
- 외래키 `workingstep_ibfk_2` 는 같은 이름 · `ON DELETE SET NULL` 로 다시 걸었고, 가공 이력 1건(T6 호출)은
  새 T06 행에 다시 연결됐습니다.
- 보관소: `공구 마스터.csv` (= 이 CSV). 옛 시트 `실험실 공구 정리.xlsx` 는 원본 기록으로 보관소에
  그대로 두었습니다 — 감시 폴더로 들어와도 입력기가 형식을 보고 반영하지 않습니다.
- 대조: `python backend/tests/verify_tool_csv.py` — 실제 테이블의 열 · 인덱스 · CHECK 가 DDL 과 같고,
  DB 를 내보낸 CSV 가 이 파일과 바이트까지 같으며, 가공 이력이 T번호대로 연결됨을 확인합니다.

CSV 를 고친 뒤에는 대시보드 '공구 마스터' 화면에서 올리면 됩니다(같은 검사 · 같은 교체 코드).
감시 폴더에 `공구 마스터.csv` 를 넣어도 반영되지만, 지금 DB 와 같은 내용이거나 마지막으로 반영된
보관본보다 오래된 파일은 수집기가 건너뜁니다(재시작 때 옛 파일로 되돌아가는 것을 막기 위해).

## 9. 공구별 출처

**YG-1** — 공식 e-카탈로그 데이터에서 치수 일치 행을 찾음
- 치수 표: https://product.yg1.solutions/resource/getEdpListForEcDetail (e-카탈로그 내부 데이터)
- ALU-CUT 3날 스퀘어 E5D70: https://product.yg1.solutions/resource/e_catalog/detail?root_category=3&category_idx=10&unit=Metric&brand_idx=35&series_idx=2101&lang=KOR
- ALU-CUT (DLC) GED70: https://product.yg1.solutions/resource/e_catalog/detail?root_category=3&category_idx=10&unit=Metric&brand_idx=35&series_idx=2109&lang=KOR
- ALU-CUT HPC E5I44: https://product.yg1.solutions/resource/e_catalog/detail?root_category=3&category_idx=10&unit=Metric&brand_idx=945&series_idx=16579&lang=KOR
- 2날 볼 E2480 (R 값): https://product.yg1.solutions/resource/e_catalog/detail?root_category=3&category_idx=10&unit=Metric&brand_idx=48&series_idx=2609&lang=ENG , 국내판 https://product.yg1.solutions/resource/e_catalog/detail?root_category=3&category_idx=10&unit=Metric&brand_idx=946&series_idx=16587&lang=KOR
- 2날 볼 SG8A38 / SEM813 / EP933: series_idx 3438 / 3474 / 3191 (같은 e-카탈로그)
- Dream Drill DH423: https://product.yg1.solutions/resource/e_catalog/detail?root_category=1&category_idx=6&unit=Metric&brand_idx=23&series_idx=109&lang=ENG

**OSG**
- GP-3D 드릴 표(인쇄 24~25쪽): http://www.osg.co.kr/ebook_new/OSG_drills.pdf
- WD Ⅰ 코팅: http://www.osg.co.kr/page.asp?mnu_siteid=ko&mnu_uid=584
- GS-EMS(4날, 61쪽) · GS-EDS(2날, 47쪽) — 2018 엔드밀 카탈로그: http://www.osg.co.kr/ebook_new/OSG_Endmills.pdf
- STEEL-PRO 등 현행 후보: http://www.osg.co.kr/ebook_new/OSG_Endmills_230224.pdf
- WXL-EMS: https://osgtool.com/3130580/ , https://nl.osgeurope.com/media/pdf/Brochure-WXL-WXS-VOL2-EU-EN-WEB.pdf
- WX-EMS(단종): https://www.orange-book.com/ja/c/products/index.html?itemCd=WXEMS8++++++++++++++++++++++++8669

**WIDIN**
- Alu-Wave 카탈로그 2020 (WAB312 24쪽, WAE302 28쪽, WAE303 30쪽, WAR302 35쪽, WAR303 36~37쪽, WAR503 39쪽): https://www.productivity.com/wp-content/uploads/2020/05/Widin-Alu-Wave-Catalog-2020-Interactive.pdf
- 제품 페이지: https://widinus.com/products/id_war503120 , https://widinus.com/products/id_war3032010 , https://widinus.com/products/id_wae302200 , https://widinus.com/products/id_war3022010 , https://widinus.com/products/id_wae303050

**JJTOOLS (제이제이툴스)**
- 4SUB (199쪽): https://www.jjtools.co.kr/assets/original/public-documents/1712621190046988.pdf
- 3SUE (200쪽): https://www.jjtools.co.kr/assets/original/public-documents/1712621199655412.pdf
- 제품군: https://www.jjtools.co.kr/products/end-mill/for-sus-titanium

**Sandvik Coromant**
- R390-016A16-11L (T07): https://www.sandvik.coromant.com/en-gb/product-details?c=r390-016a16-11l
- R390-016A16L-11L: https://www.sandvik.coromant.com/en-gb/product-details?c=r390-016a16l-11l
- 인서트 R390-11T308M-PM: https://www.sandvik.coromant.com/en-gb/product-details?c=R390-11T308M-PM

**KYOCERA**
- MFWN 홀더 표 (90125R-6T-M / -6T 행): https://www.kyocera.co.jp/prdct/tool/wp-content/uploads/2026/07/MFWNCP376.pdf
- MFWN 브로셔 (파인피치 -8T · -5T, APMX 8): https://www.kyoceraprecisiontools.com/indexable/pdf/MFWN_M-Six.pdf
- MTP90 이 생크 엔드밀뿐인 근거: https://asia.kyocera.com/products/cuttingtools/wp-content/uploads/2021/05/M_compressed.pdf (M129쪽)
- KGM 홈가공 홀더 (G41~G43쪽): https://asia.kyocera.com/products/cuttingtools/wp-content/uploads/2021/05/G_compressed.pdf

**YESTOOL (T19)**
- 종합 카탈로그 2025 (사이즈 코드 8쪽, KRUZ 215 표 14쪽, IDP 11~12쪽): http://yestool.com/wp-content/uploads/2025/04/%EC%98%88%EC%8A%A4%ED%88%B4%EC%A2%85%ED%95%A9%EC%B9%B4%EB%8B%A4%EB%A1%9D(2025).pdf
- https://yestool.com/en/classes/insert/idp/ , https://yestool.com/en/classes/body/kruz-fsl/

**NIKKEN (T20 T22)**
- 보링 카탈로그 (9-ZMAC20-40V 27쪽, 인서트 88쪽): https://www.nikken-kosakusho.co.jp/en/download/pdf/EN_Boring_9922.pdf
- Universal Micro Touch (T22 후보): https://www.nikken-kosakusho.co.jp/upload/product/detail_attach/attach00001316.pdf

**Renishaw (T05)**
- 프로브 교정 개념(전기적 반경을 따로 관리하는 이유): https://www.renishaw.com/media/pdf/en/16e3072937914b54968e8476692b76e4.pdf
