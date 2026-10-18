# 26-2_Weekly_pick

연세대 2026-2학기 투자공학 주간 종목 추천 과제 파이프라인.

매주 **한국 1종목 + 미국 1종목**을 추천하고 **월요일 시가 매수 → 금요일 종가 매도(5영업일)** 수익률로 상대 순위가 매겨진다. 이 저장소는 그 추천을 만들어내는 과정 전체다.

두 시장은 **완전히 독립된 파이프라인**으로 돌린다. 후보 스크리닝부터 최종 선택까지 한 번도 섞이지 않는다. 섞으면 LLM이 "미국이 나은가 한국이 나은가"를 따지기 시작하는데, 그건 이 과제의 판단 기준(5영업일 내 catalyst의 구체성)과 무관한 축이고 매주 1종목씩 내는 제출 형식과도 맞지 않는다.

## wk8부터: 에이전트

고정 체인 대신 **도구를 쥔 LLM 하나가 국면 조사 → 탐색 → 비판 → (되돌아가기) → 제출**을 스스로 진행한다.
하락 국면에서는 방어주·현금성 ETF·인버스 ETF도 고르고, 주차가 끝나면 정산·회고·교훈 갱신·발표 자료까지
자동으로 만든다. 설계는 [docs/agent_design.md](docs/agent_design.md), 코드는 `src/agent/`, 프롬프트는 `prompts/v4/`.

```
python src/agent/run.py status        # 이번 평가 주간과 추천 예정 시각
python src/agent/run.py recommend     # 지금 추천 (LLM)
python src/agent/run.py review --week 8
powershell -ExecutionPolicy Bypass -File agent_register.ps1   # 매시간 자동 실행 등록
```

아래는 wk3~7에 쓴 고정 파이프라인 설명이다 (비교·백업용으로 유지).

## 설계 요약

LLM에게 "다음 주에 오를 종목을 추천해줘"라고 묻지 않는다. 그렇게 물으면 학습 데이터에 각인된 유명주나 이미 지나간 테마가 나온다. 대신 후보를 기계적으로 먼저 확정하고, LLM은 그 안에서만 탈락과 선택을 수행한다.

```
                      KR 파이프라인              US 파이프라인
[Step 1] screener.py  KOSPI+KOSDAQ 전종목        S&P500 + Nasdaq100
                      기계적 필터 (LLM 없음)      기계적 필터 (LLM 없음)
                            → 후보 20종목              → 후보 20종목
   ↓
[Step 2] Analyst      뉴스·공시 검증 (20 → 3)     뉴스·공시 검증 (20 → 3)
   ↓
[Step 3] Bull → Bear → Trader  (3 → 1)          Bull → Bear → Trader (3 → 1)
   ↓
                      KR 추천 1종목              US 추천 1종목
```

Step 2·3은 `manual`(프롬프트를 렌더링해 Claude 웹에 붙여넣기)과 `auto`(LLM API 직접 호출) 두 모드로 돌아간다. 어느 쪽이든 산출물 경로와 형식이 같다.

단계를 쪼갠 이유는 세 가지다.

- **Step 1과 2의 분리** — 후보 확정은 반드시 실시간 가격 데이터로 한다.
- **Step 2와 3의 분리** — 검증과 선택을 같은 호출에 두면 LLM이 먼저 마음에 든 종목의 변호인이 되어 나머지를 형식적으로 처리한다.
- **Bull과 Bear의 분리** — 단일 LLM 추천의 최대 실패 모드가 확증편향이다. Bear에게 "매수 측에 동의하지 말 것"을 명시적으로 강제하고, 두 단계를 **각각 다른 대화**에서 실행해 컨텍스트를 물리적으로 떼어놓는다.

## 스크리닝 기준 (Step 1)

기준일은 매수일 직전 금요일 종가. KR은 KOSPI + KOSDAQ 전종목에서 우선주·스팩·리츠·관리종목·거래정지를 제외하고, US는 S&P 500 + Nasdaq 100 합집합(정적 CSV, 분기 1회 갱신)에서 복수 클래스 주식의 비주력 클래스를 제외한 뒤 아래를 적용한다. 필터 3·4의 파라미터는 두 시장이 동일하다 — 다르게 두면 시장 간 비교 가능성이 사라지고, 다르게 둘 근거도 없다.

**US 기준일은 달력이 아니라 데이터에서 확정한다.** 한국시간 금요일에 돌리면 미국 현지 금요일장은 아직 열리지도 않았다. 그래서 받아온 시세에서 다수 종목의 마지막 거래일을 찾아 그것을 기준일로 쓰고, 확정된 날짜를 로그와 `screen_meta.json`에 남긴다.

| # | 조건 | 값 | 성격 |
|---|---|---|---|
| 1 | 20일 평균 거래대금 | KR 상위 300위 / US $50M 이상 | 위생 필터 (알파 아님) |
| 2 | 시가총액 하한 | KR 2,000억원 / US $2B | 위생 필터 |
| 3 | 중기 모멘텀 + 단기 반전 | 모멘텀 상위 40% & 최근 5일 수익률 ≤ 0 | **핵심 설계** |
| 4 | ATR20 / 종가 | 내림차순 정렬 후 상위 20 | 컷이 아니라 정렬 |

**필터 3이 이 스크리너의 핵심이다.** 수익률의 자기상관은 측정 구간에 따라 부호가 뒤집힌다. 3~12개월 구간에서는 모멘텀이 순방향으로 작동하지만(Jegadeesh & Titman 1993), 1개월 이내에서는 단기 반전이 지배한다(Jegadeesh 1990, Lehmann 1990). 보유 기간이 정확히 1주일이므로 "지난주 급등주를 사는" 전략은 문헌상 역효과다.

그래서 두 지표를 **겹치지 않는 구간에서** 측정한다. 모멘텀은 최근 20영업일을 건너뛴 120영업일 구간(`close[t-20]/close[t-140]-1`)에서, 반전은 그와 겹치지 않는 최근 5영업일에서 잰다. 모멘텀 측정 시 최근 1개월을 건너뛰는 skip-month는 J&T의 표준 관행이며, 건너뛰는 이유가 바로 그 구간을 반전이 지배하기 때문이다.

**필터 4는 알파 팩터가 아니라 오히려 마이너스다.** betting-against-beta(Frazzini & Pedersen 2014)에 따르면 고변동성 종목의 위험조정수익률은 낮다. 그럼에도 고변동성을 선호하는 이유는 평가 구조 때문이다 — 매주 독립 시행이고 손실이 이월되지 않아 복리 손실이 없으므로, 기대수익률을 깎는 대신 주간 상위권 진입 확률을 올리는 의도적 선택이다.

### 제거한 필터

초안에는 "52주 신고가 대비 -10% 이내"(George & Hwang 2004)가 있었으나 제거했다. 해당 논문의 검정 구간이 6~12개월 보유라 5영업일에 적용할 근거가 없고, 논문의 핵심 주장 자체가 "52주 고점 근접도가 과거 수익률 모멘텀을 *대체*한다"는 것이라 필터 3과 함께 걸면 같은 신호를 이중으로 세게 된다. 실제로 지수가 고점 대비 -24%인 국면에서 이 필터는 돌파 임박 종목이 아니라 방어 업종만 통과시켰다.

## 구조

```
config.yaml                     필터 파라미터·시장 정의·LLM 설정 (하드코딩 없음)
data/us_universe.csv            S&P500 + NDX 합집합 (분기 1회 갱신)
docs/prompts_v2.md              프롬프트 전문 = 제출본의 원본
docs/references.md              참고 문헌
prompts/v1/*.txt                v1 (보존)
prompts/v2/*.txt                analyst / bull / bear / trader — 현행
src/screener.py                 Step 1 (KR + US)
src/us_universe.py              US 유니버스 관리·갱신
src/llm.py                      LLM 호출 계층 (openai_compat / anthropic)
src/agents.py                   Step 2·3 (manual / auto)
src/events.py                   데스크 UI용 이벤트 기록 (events.jsonl)
src/settle.py · src/cohort.py   금요일 정산 · 후보군 정산
src/final.py                    사용자 최종 선택 정산 (final_log.csv)
src/broker/                     증권사 연동 (한국투자증권 Open API)
src/deck/                       주간 리포트 pptx
app/                            로컬 데스크 서버 (데스크·시장·성과·계좌·안내)
tools/export_static.py          보기 전용 정적 사이트 굽기
.github/workflows/publish.yml   정적 사이트 자동 게시 (Cloudflare Pages)
setup.bat / start.bat / publish.bat   설치 / 데스크 켜기 / 결과 게시
outputs/prompt_submission_v2.md 수업 제출용 프롬프트 문서
outputs/wk{N}/{KR,US,final}/    시장별 후보·렌더링 결과·LLM 응답 원문·pick.json·리포트
```

## 처음 설치 (Windows)

1. Python 3.11 이상과 Git을 설치한다.
2. 저장소를 받는다: `git clone https://github.com/yoonsang-eve/26-2_Weekly_pick.git`
3. 폴더 안의 `setup.bat`을 더블클릭한다. 가상환경(.venv)을 만들고 필요한 패키지를 설치하며, `.env.example`을 `.env`로 복사한다.
4. `.env`에 자기 키를 채운다. LLM 키(auto 모드용)와 한국투자증권 키(계좌 화면용)는 없어도 된다. 없으면 manual 모드(Claude 웹에 붙여넣기)와 스크리닝·시장·성과 화면만 쓴다.
5. `config.yaml`의 `llm.anthropic.base_url`은 이 저장소 주인이 쓰는 게이트웨이 주소다. Anthropic API를 직접 쓰려면 빈 값("")으로 두고 `api_key_env`를 `ANTHROPIC_API_KEY`로 바꾼다.
6. `start.bat`을 더블클릭하면 데스크가 열린다.

## 실행

```bash
pip install -r requirements.txt
```

**Step 1 — 스크리닝**

```bash
python src/screener.py                 # config.run.markets 전부 (KR, US)
python src/screener.py --market US     # 한 시장만
python src/us_universe.py --refresh    # US 유니버스 갱신 (분기 1회면 충분)
```

**Step 2·3 — auto 모드 (LLM API 직접 호출)**

저장소 루트에 `.env`를 만들고 API 키 한 줄을 넣는다 (`.gitignore`에 있어 커밋되지 않는다).

```
LETSUR_API_KEY=발급받은_키
```

```bash
python src/agents.py --check           # 게이트웨이 연결·모델 이름 확인
```

```bash
python src/agents.py                   # KR·US 각각 analyst → bull → bear → trader
```

4단계를 순차 호출하고 응답 원문을 `outputs/wk{N}/{시장}/responses/*.md`에, 최종 선택을 `pick.json`에 저장한다. 이미 응답이 있는 단계는 건너뛰므로 중간에 끊겨도 이어서 돌릴 수 있다. 호출에 쓴 모델·토큰·소요시간은 `run_meta.json`에 기록된다 (발표 자료의 '사용 모델' 항목에 그대로 들어간다).

**Step 2·3 — manual 모드**

```bash
python src/agents.py --mode manual --market KR
```

응답이 아직 없는 첫 단계를 골라 프롬프트를 렌더링하고 클립보드로 복사한다. Claude 웹에 붙여넣고 응답 전문을 `outputs/wk{N}/{시장}/responses/{stage}.md`로 저장한 뒤 다시 실행하면 다음 단계로 넘어간다. 각 단계는 **새 대화**에서 실행해야 한다 — 히스토리를 누적하면 Bear가 Bull에 동조한다.

게이트웨이가 웹 검색 툴을 중계하지 않으면 (`llm.openai_compat.web_search: false`) analyst·bear 프롬프트에 "검색 도구가 없으니 확인 불가는 확인 불가로 표기하라"는 지침이 자동으로 붙는다. 검색이 중요한 주차에는 그 두 단계만 manual로 돌리는 혼합 운용이 가능하다 — 단계별로 모드를 바꿔도 산출물 경로가 같기 때문이다.

## 데스크 (로컬 웹 화면)

```bash
pip install -r requirements-app.txt
.venv/Scripts/python -m uvicorn app.server:app --port 8765     # -> http://localhost:8765
```

한 화면에서 이번 주 작업을 처음부터 끝까지 한다. 터미널 명령은 서버 켜기 하나만 남는다.

- **데스크** — 왼쪽은 파이프라인 도면(방 = 단계). 지금 도는 실행은 실시간(SSE), 지난 주차는 재생. 방을 누르면 후보 목록과 프롬프트·응답 원문. 오른쪽은 **이번 주 작업** 패널: ① 주차 설정(날짜 제안, config.yaml에 저장) ② 스크리닝 ③ 에이전트(manual은 프롬프트 복사 + 응답 붙여넣기, auto는 API 사용 확인 후 실행) ④ 추천 확정(제출 문구 복사) ⑤ 정산(추천 + 후보 20종목) ⑥ 발표자료(pptx 생성, PowerPoint로 열기). 산출물은 모달에서 바로 본다.
- **성과** — 수익률, 초과수익, 예측범위 적중, 후보군 안에서 추천 종목의 자리, 단계별 기여.
- **계좌** — 한국투자증권 Open API 잔고·비중·평가손익, 숫자 기반 브리핑, 추천 종목 매수 초안. 키는 `.env`의 `KIS_*`에만 둔다 (`.env.example` 참고). 조회 기록은 `private/`(커밋 제외)에 쌓인다. 주문 실행은 아직 연결하지 않았다.

**켜기** — `start.bat`을 더블클릭하면 서버가 켜지고 브라우저가 열린다.

**보기 전용 사이트 (남에게 보여 주기)** — PC를 꺼도 열린다. GitHub Actions(`.github/workflows/publish.yml`)가 `tools/export_static.py`로 보기 전용 화면과 데이터를 파일로 구워 Cloudflare Pages에 올린다. 결과가 푸시될 때(`publish.bat`), 매일 07:00·16:00 KST(시세 갱신), 수동 실행 때 돈다. 주소는 `https://<프로젝트>.pages.dev/<SITE_KEY>/#desk`이고 SITE_KEY 경로를 아는 사람만 들어온다. 서버가 없으므로 실행·계좌 기능은 애초에 없다. 저장소 Secrets에 `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `SITE_KEY`가 필요하다.

**후보군 정산** — 단계별 기여도는 추천 1종목뿐 아니라 후보 20종목 전체의 같은 주 수익률이 있어야 나온다.

```bash
python src/settle.py --week 4          # 추천 종목 → log.csv
python src/cohort.py --week 4          # 후보 20종목 → outputs/wk4/{시장}/cohort.csv
```

## 데이터 소스

- **KR**: FinanceDataReader. 한국거래소가 2025-12-27부터 정보데이터시스템을 회원제로 전환해 pykrx로는 계정 없이 전종목 조회가 불가능해졌다. 종목 선정 로직은 데이터 소스와 무관하게 동일하다.
- **US**: yfinance (시세·시가총액) + 위키피디아 (지수 구성종목). 수익률은 분할·배당 조정된 `Adj Close`로, 표시 가격과 거래대금은 미조정 `Close`로 계산한다.

## 주의

실제 매매가 아닌 수업 과제용이다. 필터 4가 명시적으로 위험조정수익률을 희생하는 구조라 실전 투자에 그대로 쓸 수 없다. 투자 권유가 아니다.
