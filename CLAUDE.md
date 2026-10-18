# weekly_pick — 투자공학 주간 종목 추천 파이프라인

## 0. 이 문서의 역할

Claude Code가 이 저장소에서 작업할 때 항상 참조하는 프로젝트 스펙이다.
구현 대상, 설계 근거, 제약 조건, 금지 사항이 모두 여기에 있다.
프롬프트 전문은 `docs/prompts_v2.md`, 외부 참고자료는 `docs/references.md`에 있다.

**wk8부터 추천의 기본 경로는 에이전트(10절, `src/agent/`, prompt v4)다.** 2·4·5절의 고정 파이프라인은
지우지 않고 비교·백업용으로 남긴다. 둘이 충돌하면 10절이 이긴다.

---

## 1. 과제 개요 (도메인 컨텍스트)

연세대 2026-2 투자공학 수업의 주간 과제. 실제 매매는 없고 가격 기준으로만 채점된다.

| 항목 | 내용 |
|---|---|
| 주기 | 매주 KR 1종목 + US 1종목 추천 |
| 추천 기한 | 금~일 (다음 주 월요일 장 시작 전) |
| 평가 구간 | 월요일 **시가** 매수 → 그 주 마지막 거래일 **종가** 매도 (평시 5영업일) |
| 채점 | 주차별 수익률의 **단순합**으로 순위 (복리 아님) |
| 대상 시장 | 한국(KRX) / 미국(US). **둘 다 운용한다** (config_version 3부터) |
| 부가 요건 | 주간 상위 3위는 8슬라이드 내외 발표 |
| 프롬프트 제출 | **학기 전체에 1개 고정.** 수정 시 새 버전으로 추가되고 이전 버전도 기록으로 남음 |

발표에 필요한 항목: 6개월 일봉 차트 / 사용한 LLM 모델 및 프롬프트 / 도메인·종목 설명 / 상승 사유 / 기술적 지표 판단 근거.

### 이 구조에서 나오는 설계 결론 (중요)

0. **휴장이 끼면 시장마다 보유 영업일 수가 달라진다.** 매도일과 보유일수는
   `config.markets.{시장}.sell_date` / `holding_days`로 시장별로 덮어쓰고, 프롬프트에는
   `{{sell_date}}` / `{{holding_days}}`로 주입한다. 양쪽에 같은 금요일을 쓰면 열리지도
   않는 날의 종가를 목표로 삼게 된다. (첫 사례: 2026 추석 주간 — KR 3영업일 / US 5영업일)
1. **Horizon이 5영업일이다.** 밸류에이션·성장성은 이 구간에서 노이즈다. 실제로 가격을 움직이는 것은 catalyst(실적·공시·정책·수주), 단기 수급, 섹터 로테이션뿐이다. fundamental analysis 가중치는 0으로 둔다.
2. **주간 독립 시행이고 손실이 다음 주로 이월되지 않는다.** 따라서 geometric drag가 없고, 변동성을 낮출 이유가 구조적으로 없다. 기대수익률 +2%/변동성 3%짜리 대형주는 상위 3위에 절대 들지 못한다. → **고변동성을 의도적으로 선호한다.**
   **(wk8 수정)** 순위가 주차별 수익률의 단순합이라는 점이 확인됐다. 단순합에서 변동성은 기대수익을 키우지도
   줄이지도 않으므로, 고변동성은 기대수익이 양(+)일 때만 쓰는 수단이다. wk6·7 하락장에서 고변동 후보군이
   벤치마크보다 크게 빠진 것(wk7 US 후보 평균 -12.8% vs S&P500 -2.0%)이 그 비용이다. 에이전트는 국면에 따라
   aggressive / balanced / defensive / hedge 중 자세를 고른다 (10절).
3. **프롬프트가 학기 내내 고정이다.** 따라서 프롬프트는 특정 주차·특정 종목에 대한 내용을 담으면 안 되고, 매주 바뀌는 값은 전부 placeholder로 빠져야 한다. 파이프라인 전체가 "템플릿 + 주간 변수 주입" 구조여야 하는 이유다.
4. **실제 체결이 없다.** slippage·호가 스프레드·거래비용을 모델링할 필요가 없다. 유동성 필터는 체결 목적이 아니라 거래정지 회피와 발표 방어 목적으로만 남긴다.

---

## 2. 파이프라인 구조

```
KR과 US를 완전히 분리해 같은 파이프라인을 두 번 돌린다.

[Step 1] screener.py   기계적 필터        LLM 호출 없음    → outputs/wk{N}/{시장}/candidates.csv (20종목)
   ↓
[Step 2] agents.py     Analyst            LLM + web search → 후보 20개 중 3개로 압축 (탈락시키는 단계)
   ↓
[Step 3] agents.py     Bull / Bear / Trader  LLM           → 최종 1종목 (선택하는 단계) → pick.json
   ↓ (KR·US 둘 다 끝나면 한 번)
[Step 4] selector.py   Selector       LLM + web search → KR 추천 vs US 추천 중 1종목 → final.json
   ↓ (금요일 장마감 후)
         settle.py     실제 수익률 정산 → log.csv 누적
   ↓
         src/deck/     발표 자료 8슬라이드 pptx  ← 파이프라인과 분리된 모듈
```

### 시장을 분리하는 이유

한 프롬프트에 KR·US 후보를 함께 넣으면 LLM이 "어느 시장이 나은가"를 따지기 시작한다.
그건 판단 기준(5영업일 내 catalyst의 구체성)과 무관한 축이고, 시장별로 1종목씩 내는
제출 형식과도 맞지 않는다. 시장 간 비교는 파이프라인 밖의 문제로 둔다.
시장별로 다른 것은 **유니버스와 데이터 소스, 그리고 프롬프트에 주입되는 시장 특성
블록**뿐이고, 필터 파라미터와 단계 구조는 동일하다.

### Step 4 Selector (prompt v3부터)

과제는 주당 1종목만 내므로 두 시장의 결과를 마지막에 한 번은 비교해야 한다. 처음에는 규칙
비교(`app/advice.py` — 예측 상단·ATR×√보유일·촉매 확정성 다수결)만 두었으나, 기준이 갈리면
판정을 못 내려(wk6) LLM 단계를 추가했다. 위의 '시장 분리' 원칙과 충돌하지 않게 **각 시장
파이프라인이 끝난 뒤의 결과물만** 입력으로 준다 — `pick.json`, Trader 응답 전문, 스크리닝 지표.
탈락한 후보는 넣지 않는다 (넣으면 Step 3의 선택을 다시 뒤집는 단계가 된다).

- 산출물은 주차 공통 폴더 `outputs/wk{N}/final/`: `rendered_selector.txt`, `responses/selector.md`,
  `selection.json`, `run_meta.json`
- `selection.json`이 생기면 `outputs/wk{N}/final.json`(최종 선택)을 채우되 `decided_by: selector`를
  붙인다. **사용자가 직접 저장한 최종 선택은 덮어쓰지 않는다** (decided_by가 selector일 때만 갱신)
- 판단 기준은 1순위 자기 시장 보유기간 내 catalyst 구체성, 2순위 실현 시 상승 폭, 3순위 미해소
  핵심 반박, 4순위 시장 구조. 시장 선호·밸류에이션·하방 크기는 쓰지 않는다 (토너먼트 구조)
- 모델과 검색 예산은 `llm.models.selector`, `llm.search_budget.selector`

### 단계 분리의 근거

- **Step 1과 2를 분리하는 이유**: LLM에게 종목 스캔을 시키면 학습 데이터에 각인된 유명주나 이미 지나간 테마를 뱉는다. universe 확정은 반드시 실시간 가격 데이터로 한다.
- **Step 2와 3을 분리하는 이유**: 같은 호출에서 검증과 선택을 동시에 시키면, LLM이 먼저 마음에 든 종목의 변호인이 되어 나머지를 형식적으로 처리한다. Step 2는 **탈락**만, Step 3은 **선택**만 담당한다.
- **Bull/Bear를 별도 호출로 두는 이유**: 단일 LLM 추천의 최대 실패 모드가 확증편향이다. Bear 에이전트에게 "동의하지 말 것"을 명시적으로 강제한다. 근거는 multiagent debate 연구(Du et al. 2023)와 투자위원회의 devil's advocate 관행.

---

## 3. 디렉토리 구조

```
weekly_pick/
├── CLAUDE.md                  # 이 문서
├── config.yaml                # 필터 파라미터·시장 정의·LLM 설정 (버전 관리 대상)
├── data/
│   └── us_universe.csv        # S&P500 + NDX 합집합 (분기 1회 갱신)
├── docs/
│   ├── prompts_v1.md          # v1 프롬프트 전문 (보존)
│   ├── prompts_v2.md          # 고정 파이프라인 프롬프트 전문
│   ├── agent_design.md        # 에이전트 설계 (10절의 상세)
│   └── references.md          # 외부 참고자료
├── src/
│   ├── screener.py            # Step 1 (KR + US)
│   ├── us_universe.py         # US 유니버스 로드·갱신
│   ├── llm.py                 # LLM 호출 계층 (openai_compat / anthropic)
│   ├── agents.py              # Step 2, 3 (manual / auto)
│   ├── selector.py            # Step 4 — KR·US 추천 중 최종 1종목 (manual / auto)
│   ├── settle.py              # 금요일 정산 → log.csv
│   ├── agent/                 # 에이전트 (10절): data · tools · loop · memory · schedule · run
│   └── deck/                  # 발표 자료 — 지워도 파이프라인은 돈다
│       ├── chart.py           #   6개월 일봉 PNG
│       └── build.py           #   8슬라이드 pptx
├── prompts/
│   ├── v1/                    # analyst.txt, bull.txt, bear.txt, trader.txt (보존)
│   ├── v2/                    # 시장 컨텍스트 블록 추가 (보존)
│   ├── v3/                    # 고정 파이프라인 현행. v2 4개 + selector.txt
│   └── v4/                    # 에이전트: agent_system.txt · critic.txt · review.txt
├── outputs/
│   └── wk{N}/
│       └── {KR|US}/
│           ├── candidates.csv
│           ├── screen_meta.json      # 확정 기준일 등 스크리너 메타
│           ├── rendered_*.txt        # 실제로 LLM에 들어간 전문
│           ├── responses/*.md        # LLM 응답 원문
│           ├── run_meta.json         # 모델·토큰 사용량·소요시간
│           ├── pick.json             # trader의 구조화 출력
│           ├── chart_{ticker}.png          # deck 산출물
│           └── deck_wk{N}_{시장}_{ticker}.pptx
│       ├── final/                    # Step 4 Selector 산출물 (+ 최종 선택 리포트)
│       └── final.json                # 이번 주 최종 선택 1종목
├── requirements.txt           # 파이프라인 의존성
├── requirements-deck.txt      # 발표 자료 전용 (deck을 지우면 같이 지운다)
└── log.csv                    # 주차별 누적 기록
```

---

## 4. Step 1 — screener.py 명세

### 입력
- `config.yaml`의 필터 파라미터
- 기준일: **직전 금요일 종가** (그 시점까지의 데이터만 사용)

### 유니버스
- **KR**: KOSPI + KOSDAQ 전종목 (FinanceDataReader. KRX가 2025-12-27부터 회원제로 전환되어 pykrx는 `KRX_ID`/`KRX_PW` 없이는 전종목 조회가 불가하다. 종목 선정 로직은 소스와 무관하게 동일하다)
  - 제외: 우선주(종목명 말미 `우`/`우B`/`1우` 등), 스팩, ETF/ETN/ELW, 리츠, 관리종목, 거래정지
- **US**: S&P 500 + Nasdaq 100 합집합 — `data/us_universe.csv`로 정적 관리하고 분기 1회
  `python src/us_universe.py --refresh`로 갱신한다. 매주 긁으면 구성종목 변경이 조용히
  결과를 바꿔서 재현이 깨진다. 시세는 yfinance.
  - 제외: 복수 클래스 주식의 비주력 클래스 (GOOG/GOOGL, FOX/FOXA — 거래대금이 큰 쪽만 남긴다)
  - 수익률은 분할·배당 조정된 `Adj Close`, 표시 가격·거래대금은 미조정 `Close`로 계산한다
  - **기준일을 달력으로 고정하지 말 것.** 한국시간 금요일에는 미국 금요일장이 아직 열리지
    않았다. 받아온 시세에서 다수 종목의 마지막 거래일을 찾아 기준일로 쓰고
    (`resolve_us_ref`), 확정된 날짜를 `screen_meta.json`에 남겨 프롬프트에 주입한다
  - 시가총액은 yfinance `fast_info`로 받는다. 조회 실패분은 **탈락시키지 않고 통과**시킨다.
    S&P500·NDX 구성종목이라 $2B 하한이 사실상 비구속적이고, 조회 실패를 탈락으로 처리하면
    그 자체가 의도치 않은 필터가 되기 때문이다

### 필터 (config.yaml에서 조정)

| # | 조건 | 기본값 | 목적 | 근거 |
|---|---|---|---|---|
| 1 | 20일 평균 거래대금 | KR 상위 300위 / US $50M 이상 | 거래정지·작전주 회피, 발표 방어 | 알파와 무관. 위생 필터 |
| 2 | 시가총액 하한 | KR 2,000억원 / US $2B | 동상 | 동상 |
| 3 | 6개월 모멘텀(최근 1개월 제외) 상위 + 최근 1주일 눌림 | ret_mom 상위 40%, ret5 ≤ 0 | 중기 모멘텀은 타되 단기 과열은 피함 | **핵심 설계.** 아래 설명 참조 |
| 4 | ATR20 / 종가 | 상위 순으로 **정렬** (컷이 아님) | 상위 3위 진입 확률 확보 | 알파 아님. tournament 최적화 |

**제거된 필터 — 52주 신고가 근접 (구 4번).** 초안에서는 George & Hwang (2004, JF)을 근거로 "신고가 대비 -10% 이내"를 걸었으나 wk3에서 제거했다. (a) 해당 논문의 검정 구간은 6~12개월 보유이고 5영업일 horizon에서의 증거가 없다. (b) 같은 논문의 핵심 주장이 "52주 고점 근접도가 과거 수익률 모멘텀을 대체(dominates)한다"는 것이므로, 필터 3과 함께 하드 컷으로 걸면 같은 신호를 이중으로 세는 셈이다. (c) 실제로 KOSPI가 52주 고점 대비 -24%인 국면에서 이 필터는 돌파 임박 종목이 아니라 방어주(보험업)만 통과시켜, 기술적 필터가 아니라 사실상 섹터 필터로 작동했다. `pct_from_52wh`는 참고 지표로 candidates.csv에 계속 출력한다.

**#3에 대한 주의 — 부호가 반대인 두 효과를 조합한 것이다. 측정 구간을 반드시 분리할 것.**

수익률의 자기상관은 horizon에 따라 부호가 뒤집힌다. 3~12개월 구간에서는 momentum(Jegadeesh & Titman 1993, 순방향), 1개월 이내에서는 short-term reversal(Jegadeesh 1990 월간, Lehmann 1990 주간, 역방향)이 지배한다. 과제의 horizon이 정확히 1주일이므로 **"지난주 급등주를 사는" 단순 전략은 문헌상 역효과**다.

**여기서 가장 자주 나오는 구현 실수가 20일(≈1개월) 수익률을 순방향 모멘텀 신호로 쓰는 것이다.** J&T의 모멘텀은 형성구간이 3~12개월이고, 표준 관행은 형성구간과 보유구간 사이에 **최근 1개월을 의도적으로 건너뛴다(skip-month)**. 건너뛰는 이유가 정확히 Jegadeesh(1990)의 1개월 반전 효과 때문이다. 즉 20일 구간은 모멘텀이 아니라 반전이 지배하는 구간이고, 이걸 순방향으로 걸면 두 효과가 상쇄되거나 부호가 거꾸로 붙는다.

그래서 구현은 이렇게 나눈다.
- **모멘텀(순방향)**: 최근 20영업일을 제외한 120영업일 수익률 = `close[t-20] / close[t-140] - 1`
- **반전(역방향)**: 최근 5영업일 수익률 ≤ 0

두 지표는 **겹치지 않는 구간에서 측정된다.** 이 분리를 깨고 한 구간에서 두 조건을 걸지 말 것. `ret20`은 필터에 쓰지 않고 Analyst 참고용으로만 CSV에 남긴다.

*변경 이력: wk3까지는 `ret20 상위 20% & ret3 ≤ +3%`였다. 인용 문헌과 부호가 어긋나 있어 위 설계로 교체했다. 같은 데이터에서 두 설계의 통과 종목이 1개만 겹쳤을 만큼 영향이 크다.*

**#4에 대한 주의 — 이것은 알파 팩터가 아니라 오히려 마이너스다.**
low-volatility anomaly / betting-against-beta(Frazzini & Pedersen 2014)에 따르면 고변동성 종목의 risk-adjusted return은 오히려 낮다. 이 필터는 기대수익률을 깎는 대신 상위 3위 진입 확률을 올리는 순수한 tournament 베팅이며, 주간 독립 시행 구조 때문에만 정당화된다. 코드 주석에 이 사실을 명시할 것. 발표에서도 "평가 구조에 최적화한 의도적 선택"으로 설명한다.

**필터 3·4의 파라미터는 KR과 US가 동일하다.** 시장별로 다르게 둘 문헌적 근거가 없고,
다르게 두면 두 추천이 같은 기준으로 만들어졌다는 주장이 무너진다. 시장별로 다른 것은
유니버스와 필터 1·2의 임계값(통화 단위 차이)뿐이다.

### 출력
`outputs/wk{N}/{KR|US}/candidates.csv` — 시장별 상위 20종목 (config `universe.quota`)

컬럼: `market, exchange, ticker, name, sector, close, mktcap, adv20, ret_mom, ret20, ret5, pct_from_52wh, atr20_pct, score`

`close`·`mktcap`·`adv20`의 단위는 해당 시장의 통화 그대로다 (KR 원, US USD). 표시 단위
변환은 `agents.py`가 프롬프트를 만들 때 `config.markets.{시장}.cap_divisor` 등으로 한다.

**20이라는 숫자에 이론적 근거는 없다.** LLM이 한 번에 성실히 검증할 수 있는 분량의 어림값이며, 30개를 넘기면 뒷부분 처리가 부실해지는 경향 때문에 잡은 상한이다.

### 구현 주의사항
- 휴장일: 매수일이 휴장이면 그 주 첫 영업일 시가, 매도일이 휴장이면 마지막 영업일 종가. `pykrx.stock.get_previous_business_days` / `pandas_market_calendars` 사용
- 미국장 기준일은 KST 기준 금요일이면 미국 현지 목요일 종가까지만 확정 — 시차 처리를 명시적으로 할 것
- pykrx는 전종목 루프 시 느리다. 날짜별 일괄 조회(`get_market_ohlcv(date, market=...)`) 위주로 짜고 결과를 `cache/`에 parquet으로 저장
- API 실패 시 조용히 빈 DataFrame을 반환하지 말 것. 필터 통과 종목 수를 단계별로 로깅한다

---

## 5. Step 2, 3 — agents.py 명세

### 실행 모드 2가지 (config로 전환)

| 모드 | 동작 | 용도 |
|---|---|---|
| `manual` | 렌더링된 프롬프트 txt만 생성하고 종료. 사용자가 Claude 웹에 붙여넣고 응답을 `responses/`에 저장 | 게이트웨이에 웹 검색이 없어 analyst·bear 품질이 중요할 때 |
| `auto` | **기본값.** `src/llm.py`를 통해 4단계 순차 호출 | 평상시 |

`manual` 모드에서도 렌더링 결과와 응답 저장 경로는 `auto`와 동일해야 한다. `report.py`가 두 모드를 구분하지 않게 만들 것. 경로가 같으므로 **단계별로 모드를 섞을 수 있다** — 검색이 필요한 analyst·bear만 manual로 돌리고 나머지를 auto로 돌리는 운용이 가능하다.

### LLM 백엔드 (`src/llm.py`)

| provider | 설명 | 웹 검색 |
|---|---|---|
| `openai_compat` | OpenAI `/chat/completions` 호환 게이트웨이. 수업에서 받은 Letsur AI 게이트웨이가 여기에 해당한다. `base_url`과 API 키 환경변수만 채우면 된다 | 게이트웨이가 중계할 때만 (`llm.openai_compat.web_search`) |
| `anthropic` | Anthropic Messages API 직접 호출 (공식 SDK) | 서버사이드 web search tool |

- **API 키를 저장소에 커밋하지 말 것.** 환경변수 또는 저장소 루트의 `.env`(`llm.load_dotenv`가 읽는다)로만 받는다 (`llm.*.api_key_env`). `.env`와 `*.key`는 이미 `.gitignore`에 있다.
- 본 실행 전에 `python src/agents.py --check`로 게이트웨이 연결과 단계별 모델(에셋) 이름을 먼저 확인한다. 모델명 오타는 실행 중반에야 터진다.
- **검색 예산은 단계별로 따로 준다** (`llm.search_budget`). analyst는 후보 20종목을 처음 훑어야 하지만 bear는 3종목만 검증한다. 같은 예산을 주면 bear가 남는 만큼 더 검색하고 그 결과가 전부 입력 토큰으로 쌓인다 — wk4 실측에서 bear 한 단계가 전체 비용의 절반(input 514k)을 썼다. 프롬프트에 알려 주는 숫자와 실제로 거는 한도는 반드시 같은 값이어야 한다.
- 비용은 검색 루프가 지배한다. 검색할 때마다 직전까지의 대화 전체가 다시 입력으로 들어간다. 줄이는 순서: 단계별 검색 예산 → `universe.quota` → (최후) 검색 끄기.
- 웹 검색을 쓸 수 없는 실행에서는 analyst·bear 프롬프트의 `{{search_policy}}`에 "검색 도구가 없으니 확인 불가는 확인 불가로 표기하라"는 지침이 자동으로 들어간다. **검색이 없는데 검색이 있는 것처럼 물으면 LLM이 날짜와 수치를 지어낸다.** 이 치환을 없애지 말 것.
- 현행 세대 모델(Opus 5 / Sonnet 5 등)은 `temperature`를 받지 않는다. `config.llm.temperature`가 `null`이면 파라미터 자체를 보내지 않는 것이 기본 동작이다.

### 호출 체인 (auto 모드)

```
analyst  : 후보 20개 + 주간 날짜 → 검증 테이블 + 3개 압축   [web search 필수]
bull     : 후보 3개 → 각각의 상승 시나리오와 트리거
bear     : 후보 3개 + bull 응답 → 반박, 하방 시나리오        [web search 필수]
trader   : bull + bear 응답 → 최종 1종목, JSON 출력
```

- 각 단계는 이전 단계의 **응답 텍스트를 입력으로 받는다.** 전체 대화 히스토리를 누적하지 말 것 (bear가 bull에 동조하는 것을 막기 위해 컨텍스트를 분리한다)
- `trader` 단계는 반드시 구조화 JSON을 반환하게 강제하고 파싱 실패 시 1회 재시도한다
- 모델 선택: analyst/bear는 검색 호출이 많으므로 중간 티어, trader는 최상위 티어를 권장. 정확한 모델 문자열과 web search tool 스펙은 `docs/references.md`의 API 문서에서 확인할 것 (기억에 의존하지 말 것)
- 모델·토큰 사용량·소요시간·프롬프트 버전을 `outputs/wk{N}/{시장}/run_meta.json`에 기록. 발표 자료에 그대로 들어간다
- 이미 응답이 있는 단계는 건너뛴다. 중간에 끊겨도 이어서 돌릴 수 있고, 같은 단계를 다시 돌리려면 해당 `responses/{stage}.md`를 지우거나 단계명을 인자로 준다

### trader 출력 스키마

`market` 값은 그 파이프라인의 시장으로 고정되어 프롬프트에 주입된다.

```json
{
  "market": "KR",
  "ticker": "005930",
  "name": "삼성전자",
  "thesis": ["근거1", "근거2", "근거3"],
  "catalyst": "5영업일 내 실현 가능한 구체적 이벤트",
  "expected_return_range": [-5.0, 15.0],
  "primary_risk": "가장 큰 리스크 1개",
  "rejected": [{"ticker": "...", "reason": "..."}]
}
```

---

## 6. chart.py / report.py / settle.py

### src/deck/ — 발표 자료 (독립 모듈)

**파이프라인에서 완전히 분리되어 있다.** `outputs/` 산출물과 `log.csv`, `config.yaml`만
읽고 screener·agents·llm을 import하지 않는다. **`src/deck/`와 `requirements-deck.txt`를
통째로 지워도 파이프라인은 그대로 돈다.** 학기가 끝나고 추천 엔진만 계속 쓸 때를 위한
경계이므로, 여기에 파이프라인 로직을 넣지 말 것 (역방향 import도 금지).

- `deck/chart.py` — 6개월 일봉 캔들 + MA20/MA60 + 거래량 + RSI(14), 평가 구간 음영
  - **한글 폰트는 `make_mpf_style(rc=...)`에 직접 넣어야 한다.** `matplotlib.rcParams`에만
    설정하면 mplfinance 스타일이 덮어써서 조용히 DejaVu Sans로 되돌아가 네모로 깨진다
  - addplot에 `secondary_y=False`를 명시하지 않으면 RSI가 오른쪽 축에 그려지고 왼쪽 축에
    엉뚱한 눈금이 남는다
- `deck/build.py` — 8슬라이드 pptx 생성

```bash
python src/deck/build.py --week 3 --market KR
```

**원칙은 '간단하게'다.** 발표하는 주차가 아니면 손대지 않고 그대로 제출할 수 있어야 하므로,
모든 칸을 파일에서 기계적으로 채우고 없는 값은 비워 둔다. **없는 값을 그럴듯하게 채우지
말 것** — 예컨대 manual 모드는 `web_search`를 기록하지 않는데, 이를 '미사용'으로 적으면
거짓이 된다 (단계 규약으로 대체하거나 '기록 없음'으로 둔다).

### 8슬라이드 구성

| 슬라이드 | 내용 | 소스 |
|---|---|---|
| 1 | 종목·주차·최종 수익률 | log.csv |
| 2 | 파이프라인 구조도 | 고정 |
| 3 | 사용 모델 및 프롬프트 | run_meta.json |
| 4 | Step 1 스크리닝 결과 (20 → 3) | candidates.csv + screen_meta.json |
| 5 | 종목 설명·선정 근거 | pick.json (thesis / catalyst) |
| 6 | Bull vs Bear 요약 | bull/bear 응답에서 해당 종목 섹션 추출 |
| 7 | 6개월 일봉 + 기술적 근거 | chart PNG + candidates.csv 지표값 |
| 8 | 사후 검증: 예측 대비 실제 | log.csv + pick.json |

슬라이드 4는 상위 10종목만 싣되 **선택된 종목이 10위 밖이면 그 한 줄을 반드시 붙인다**
(wk3 OCI홀딩스가 10위였다). 슬라이드 6의 섹션 추출은 후보 중 **가장 깊은 헤딩**을 고른다 —
문서 맨 위의 `# 상승 논거 — A · B · C` 제목에도 종목명이 들어 있어서, 얕은 헤딩을 잡으면
다른 종목의 논거를 가져온다.

**제출용 프롬프트 문서**(`outputs/prompt_submission_v{n}.md`)는 report.py가 생성하지 않는다. 학기 전체에 1회 제출하고 필터를 바꿀 때만 갱신하는 문서라 자동 생성할 실익이 없어, **수기로 관리**한다. 주차별 내용(후보 종목, 선택 결과, 매크로 일정)은 절대 넣지 않는다 — 매주 바뀌는 값은 전부 placeholder로 남긴다. `config.yaml`의 필터 파라미터를 바꾸면 이 문서의 2절(Step 1 기준)을 직접 갱신하고 버전을 올릴 것. 제출 범위는 아래 7절 참조.

### settle.py
- 매수일 시가 / 매도일 종가를 실제 데이터로 조회해 수익률 계산. 휴장이면 실제 거래일로 스냅한다
- **과거 주차를 정산할 때 config를 믿으면 안 된다.** config의 `run` 블록은 '이번 주차' 값이라,
  몇 주 뒤에 wk3을 정산하면 wk3이 아닌 날짜가 들어간다. `screen_meta.json`에 기록해 둔
  매수·매도일을 먼저 보고, 없으면 `--buy-date/--sell-date`로 받는다
- 벤치마크(KOSPI/S&P500)는 yfinance로 받는다. FDR의 KS11은 며칠 늦게 채워진다
- **yfinance는 장 마감 후 몇 시간 동안 그날 행의 종가를 NaN으로 비워 둘 수 있다** (wk4 US: KST 토 10시에
  금요일 종가가 비어 전 종목 nan). NaN 행을 지우면 직전 거래일로 조용히 스냅되므로 지우지 말고
  `settle.prices()`에서 실패시킨다. 확정 여부는 KST 날짜가 아니라 시장 현지 시각으로 본다 (`close_final`)
- 정산 전 주차의 잠정 수익률은 `cohort.live()` → `/api/live` (성과 화면 '새로고침'). 파일에 기록하지 않는다
- `log.csv`에 append하며 같은 (week, market)은 덮어쓰되 `rank`·`notes` 수동 입력분은 보존한다
- 누적되면 학기 말에 "LLM 에이전트 기반 종목 선정의 hit rate 분석"이 공짜로 나온다. 발표 차별화 포인트이므로 초기부터 빠짐없이 기록할 것

---

## 7. 프롬프트 제출 규칙 (반드시 지킬 것)

수업 사이트는 **학기 전체에 프롬프트 1개**를 받고, 수정 시 버전이 누적된다.

### 제출 문서에 포함할 것
1. 파이프라인 개요 (3단계 구조와 각 단계 목적, 5줄)
2. **Step 1 스크리닝 기준 전문** — LLM 호출이 아니라는 점을 명시
3. Analyst 프롬프트 전문 (placeholder 포함 상태)
4. Bull / Bear / Trader 프롬프트 전문
5. Selector 프롬프트 전문 (v3부터 — KR·US 추천 중 1종목)
6. 사용 모델 및 도구 (web search 사용 여부)

**Step 1을 빼면 안 된다.** "LLM에게 무엇을 물었나"의 절반은 "무엇을 후보로 주었나"에서 결정되므로, 스크리닝 기준이 없으면 프롬프트만으로 재현이 불가능하다. placeholder를 남긴 템플릿 형태로 내는 것이 규칙(학기 전체 1개 고정)과도 맞다.

### 버전 관리 규칙
- `prompts/v1` → `v2` 승격은 프롬프트 문구를 고쳤을 때
- **`config.yaml`의 필터 파라미터를 바꾼 경우에도 새 버전을 제출한다.** 규칙상 강제되지는 않지만, 스크리닝 기준은 사실상 프롬프트의 일부이고 사후에 조용히 바꾸면 재현성 주장이 무너진다
- 성적이 나쁘다고 프롬프트를 즉흥적으로 고치지 말 것. 최소 3주 고정 운용 후 log.csv 근거로 수정하고, 무엇을 왜 바꿨는지 버전 노트에 남긴다. 이 기록 자체가 발표 소재다

---

## 8. 하지 말 것

- LLM에게 "다음 주에 오를 종목을 추천해줘"라고 묻는 형태의 프롬프트 (Step 1의 존재 이유가 사라진다)
- LLM 응답에서 검색으로 확인되지 않은 수치·일정을 그대로 리포트에 옮기는 것
- bull 응답을 bear에게 전체 대화 형태로 넘기는 것 (동조 유발)
- 매주 프롬프트를 새로 쓰는 것 (규칙 위반이자 hit rate 분석 불가)
- 시총·거래대금 필터를 완전히 제거하는 것 (거래정지 리스크와 발표 방어 불가)
- 백테스트를 근거로 필터 파라미터를 과하게 튜닝하는 것. 표본이 주당 1개뿐이라 과적합만 남는다

---

## 9. 구현 순서

1. ~~`config.yaml` + `screener.py` — KR~~ **완료**
2. ~~`screener.py`에 US 유니버스 추가, 시차 처리~~ **완료** (config_version 3)
3. ~~`prompts/*.txt` 배치 + `agents.py` manual 모드~~ **완료**
4. ~~`agents.py` auto 모드 (API 연동)~~ **완료** (`src/llm.py`)
5. ~~`settle.py` + `log.csv` 스키마 확정~~ **완료**
6. ~~`chart.py` → `src/deck/chart.py`~~ **완료**
7. ~~발표 자료 생성 → `src/deck/build.py` (8슬라이드 pptx)~~ **완료**
8. 슬라이드 5·6의 요약 품질 — 현재는 파일에서 기계적으로 추출한다. 발표하는 주차에만
   LLM으로 요약하는 `--full` 경로를 붙일 수 있다 (deck은 llm을 import하지 않으므로
   지연 import로 처리하고, 없으면 기계 추출로 떨어지게 할 것)
9. ~~에이전트 (`src/agent/`, prompt v4) + 작업 스케줄러 tick (`agent_register.ps1`)~~ **완료** (wk8)
10. ~~데스크 '에이전트' 화면 (`app/agent_view.py`, `app/static/app/agent.js`)~~ **완료**. 기존 도면 데스크는
    '이전 데스크 v3' 탭으로 남겼다
11. ~~휴장일·대형 일정 주간 자동 갱신 (`src/agent/calendar_refresh.py`)~~ **완료**

---

## 10. 에이전트 (prompt v4~)

상세 설계는 `docs/agent_design.md`. 여기에는 지켜야 할 규칙만 둔다.

- **구조**: 도구 사용 루프 하나(`src/agent/loop.py`). 국면 조사 → 탐색 범위 → 후보 탐색 → 비판 → 판단(되돌아가기
  가능) → 제출. 순서는 모델이 정하고, 코드는 예산과 제출 검증만 강제한다.
- **후보는 데이터에서**: 주식 패널(KR 거래대금 상위 ~500, US S&P500∪NDX)과 ETF 목록에 지표를 붙여 주고,
  필터·정렬 조건은 에이전트가 정한다. 위생 필터(4절 필터 1·2)는 `screen_stocks` 기본값으로 항상 걸린다.
  LLM 기억 속 종목을 바로 제출하지 못하도록 제출 종목은 시세 확인을 통과해야 한다.
- **비판자 분리**: `request_critique`는 별도 LLM 호출이고 매수 논거 텍스트만 받는다. 메인 대화 히스토리를
  넘기지 말 것 (8절의 bull→bear 동조 금지와 같은 이유). 최종 종목은 비판 없이 제출되지 않는다.
- **상품**: 주식과 ETF(지수·섹터·해외·현금성·원자재·인버스·레버리지). 인버스·레버리지는 수업 규칙상
  허용된다 (2026-10-18 확인). 현금성 ETF는 마지막 수단이다 (현금 아닌 후보 4개 이상 검토 후에만 접수).
- **스케줄**: `tick`이 매시간 돈다. 추천은 다음 평가 주간 첫 거래일 전날(일요일 또는 연휴 마지막 날)
  `agent.schedule.recommend_at` 이후, 보고는 고른 시장의 매도일 종가가 현지 시각으로 확정된 뒤. 매수 시가가
  지난 시장은 후보에서 뺀다. 휴장일은 `app/calendar_data.py`의 고정 HOLIDAYS에 주 1회(토·일) 웹 검색으로
  찾은 휴장일(`data/calendar_auto.json`)을 더한 것이다. 자동 갱신은 고정값을 지우지 않고, 휴장일이 바뀌면
  알림을 남긴다. 같은 검색이 앞으로 3~5주의 대형 일정(금리 결정·물가·고용·초대형주 실적)도 채운다.
- **피드백**: 회고는 운/실력 분해와 비교군 수익률을 본 뒤 교훈을 `data/agent_lessons.json`에 넣는다. 순지지
  3주 미만은 '가설'로만 노출한다. 결과 교훈(특정 섹터·종목)보다 과정 교훈을 우선한다. 이 규칙을 느슨하게
  만들지 말 것 — 주당 표본 1개라 과적합만 남는다.
- **산출물 호환**: 고른 시장 폴더에 기존 형식(pick.json·candidates.csv·responses/bull·bear.md·run_meta.json)을
  같이 쓴다. settle·cohort·deck·autotrade는 고치지 않고 그대로 읽는다. `final.json`은 `decided_by: agent`이며
  사용자가 직접 정한 값은 덮어쓰지 않는다.
- **알림**: 메일은 보내지 않는다. `app/notify.py`가 `private/notify.jsonl`에 남기고 Windows 알림을 띄운다.
  데스크 '에이전트'(추천·결과·오류)와 '매매'(체결·오류) 화면이 이 기록을 보여 준다.
- **스키마**: 게이트웨이는 도구 입력 스키마를 강제하지 않는다 (wk8에 bull_case가 빈 문자열로 왔다). 형식 검사는
  `submit_recommendation` 코드에서 한다.
- **검색 도구**: 에이전트 경로는 `web_search_20250305`(기본형). `_20260209`는 내부 코드 실행이 실패해 예산만
  소진했다 (wk8 1차 실행).
- **비용**: 추천 1회 상한 `agent.cost_limit_unit`(4 unit, 70%에서 제출 재촉). 주차 장부(`llm_spend.jsonl`)를 기존 파이프라인과 공유하고, 캐시 읽기는 `cache_read_weight`로
  가중한다. 상한은 `agent.token_budget`. 처음 쓰기 전과 게이트웨이를 바꾼 뒤에는 `run.py check`.
- **프롬프트 버전**: `prompts/v4/`의 세 파일과 `agent:` 블록의 도구 구성이 제출 대상이다 (7절 규칙 그대로).
  주차별 내용은 placeholder로만 들어간다.
