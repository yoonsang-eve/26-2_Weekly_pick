# 참고자료

## A. Claude Code 세션에 실제로 붙일 것

작업 시작 시 이 정도만 컨텍스트에 넣으면 된다. 전부 다 넣으면 오히려 희석된다.

| 우선순위 | 자료 | 이유 |
|---|---|---|
| 필수 | `CLAUDE.md`, `docs/prompts_v1.md` | 프로젝트 루트에 두면 Claude Code가 자동 인식 |
| 필수 | 수업 사이트 **규칙 탭 / 일정표 탭 캡처** | 휴장일 처리, 매수·매도 기준가 정의, 순위 산정 방식의 원문 확인용. 내 요약이 아니라 원문이 필요하다 |
| 필수 | pykrx README | 함수 시그니처가 자주 바뀐다. 기억으로 짜면 틀린다 |
| 필수 | yfinance README | 동상 |
| 상황별 | Anthropic API 문서 (auto 모드 구현 시) | 모델 문자열, web search tool 스펙 |
| 상황별 | mplfinance 문서 (chart.py 작업 시) | |
| 참고 | 이전 프로젝트 repo | pykrx 데이터 로딩·캐싱 코드 재활용 |

TradingAgents repo는 **붙이지 않는다.** 아키텍처 아이디어만 빌려온 것이고, 코드를 넣으면 LangGraph 기반 구조를 그대로 따라가려는 압력이 생긴다. 이 프로젝트는 그보다 훨씬 가벼워야 한다.

---

## B. 데이터·라이브러리

| 라이브러리 | URL | 용도 |
|---|---|---|
| pykrx | https://github.com/sharebook-kr/pykrx | KRX 전종목 OHLCV, 시가총액, 투자자별 매매동향, 공매도 잔고 |
| FinanceDataReader | https://github.com/FinanceData/FinanceDataReader | 종목 마스터, 보조 |
| yfinance | https://github.com/ranaroussi/yfinance | 미국 주식 OHLCV, earnings calendar |
| mplfinance | https://github.com/matplotlib/mplfinance | 캔들차트 |
| OpenDART | https://opendart.fss.or.kr | 공시 원문 (유상증자, 전환사채, 보호예수) |
| pandas_market_calendars | https://github.com/rsheftel/pandas_market_calendars | 휴장일 처리 |

**pykrx 주의**: 전종목 루프는 매우 느리다. 날짜 단위 일괄 조회 후 캐싱.
**yfinance 주의**: 비공식 스크래핑 기반이라 간헐적으로 깨진다. 실패 시 재시도와 명시적 에러 로깅 필요.

---

## C. Anthropic API

`agents.py`의 auto 모드 구현 시에만 필요하다. **모델 문자열과 tool 스펙은 기억에 의존하지 말고 아래에서 확인할 것.**

- API 문서: https://docs.claude.com/en/api/overview
- 문서 맵: https://docs.claude.com/en/docs_site_map.md
- Claude Code 문서: https://docs.claude.com/en/docs/claude-code/overview

확인해야 할 것: Messages API 요청 형식, 현재 사용 가능한 모델 문자열, web search tool의 type 문자열과 사용법, 토큰 한도.

---

## D. 아키텍처 레퍼런스

**TradingAgents** — 3단계 구조의 원형
- repo: https://github.com/TauricResearch/TradingAgents
- 논문: https://arxiv.org/abs/2412.20138

구조: analyst team(fundamental / sentiment / news / technical) → researcher team(bull vs bear debate) → trader → risk management team → portfolio manager.

**가져온 것**: 역할 분리, bull/bear 적대적 토론, 구조화된 리포트 산출.
**버린 것**: risk management team(포지션 사이징이 없으므로 무의미), fundamental analyst 비중(5일 horizon에서 노이즈), LangGraph 의존.
**없어서 직접 만든 것**: universe screening. TradingAgents는 **티커를 이미 정해놓고 시작한다.** "AAPL을 오늘 살까"를 푸는 도구지 "2,700개 중 무엇을 살까"를 푸는 도구가 아니다. Step 1이 필요한 이유가 이것이다.

**multiagent debate**
- Du et al. 2023, "Improving Factuality and Reasoning in Language Models through Multiagent Debate" — https://arxiv.org/abs/2305.14325
- bear에게 "동의하지 말 것"을 강제하는 설계의 근거

---

## E. 필터 근거 논문 (발표 슬라이드 7번용)

코드 작성에는 필요 없고, 기술적 지표 판단 근거를 설명할 때 인용한다.

| 필터 | 논문 | 요지 |
|---|---|---|
| 120일 모멘텀 (최근 20일 제외) | Jegadeesh & Titman (1993), Journal of Finance | 3~12개월 horizon에서 수익률 순방향 자기상관. 형성구간과 보유구간 사이에 최근 1개월을 건너뛰는 skip-month가 표준 관행 |
| 최근 5일 눌림 | Jegadeesh (1990) / Lehmann (1990) | 1주~1개월 horizon에서는 부호가 뒤집혀 reversal이 지배. skip-month로 건너뛴 바로 그 구간이라 역방향으로 건다 |
| ~~52주 신고가 근접~~ **(필터에서 제거됨)** | George & Hwang (2004), *The 52-Week High and Momentum Investing*, Journal of Finance | 52주 신고가 근접도가 전통적 모멘텀보다 강한 예측력. 메커니즘은 anchoring bias. **뺀 이유**: 검정 구간이 6~12개월 보유라 5영업일에 적용할 근거가 없고, 논문 주장 자체가 "52주 고점 근접도는 과거 수익률 모멘텀을 대체한다"이므로 모멘텀 필터와 함께 걸면 같은 신호를 이중으로 세게 된다. 발표에서는 "왜 뺐는가"의 근거로 인용 |
| 고변동성 선호 | Frazzini & Pedersen (2014), *Betting Against Beta*, JFE | 고변동성 종목의 risk-adjusted return은 **낮다**. 이 필터가 알파가 아니라 tournament 베팅임을 보여주는 근거로 인용 |

발표에서 마지막 항목을 솔직하게 밝히는 편이 낫다. "평가 구조가 주간 독립 시행이므로 기대수익률이 아니라 상위 3위 진입 확률을 최적화했다"는 설명은 방어 가능하고, 오히려 평가 구조를 이해했다는 신호가 된다.

---

## F. 스크리닝 방법론의 계보

Step 1 → Step 2의 "정량 스크리닝 후 정성 분석" 구조는 quantamental 워크플로우의 표준형이다. 리테일 쪽에서 명시적으로 이 구조를 쓰는 것은 William O'Neil의 CAN SLIM, Mark Minervini의 SEPA. 특별한 방법론이 아니라 관행이며, 필터 조합 자체는 이 프로젝트에서 horizon에 맞춰 새로 구성한 것이다.
