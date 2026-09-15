# claude-workshop

키움증권 REST API를 활용한 AI 주식 자동매매 시스템.

## 목적

키움증권 REST API로 실시간 시세/조건검색을 수신하고, Claude Code CLI를 통한 AI 판단으로 진입(매수) 시점을 결정하며, 코드 기반 Position Guard로 청산(매도) 시점을 관리하는 자동매매 시스템이다.

## 핵심 아키텍처

- **QuantTradingSystem 오케스트레이터**가 조건검색 수신 → AI 진입 판단 → 주문 실행 → 포지션 관리 → 청산까지 전체 흐름을 관리한다.
- **Claude Code CLI**를 subprocess로 호출하여 AI가 진입(entry) 여부를 판단한다. 청산 판단에는 사용하지 않는다.
- **Position Guard**가 코드 기반으로 청산(exit) 판단을 수행한다: 손절, 익절, 보유시간 제한, 트레일링 스탑.
- **분할 매수/매도 전략(Split Buy/Sell)**: 1차/2차 트랜치로 나누어 매수하고, 목표가 도달 시 일부 물량을 우선 익절한다.
- 모든 데이터 모델은 **frozen dataclass**로 정의하여 불변성을 보장한다.

## 기술 스택

- Python 3.11+, asyncio
- 키움증권 REST API + WebSocket (실시간 조건검색/시세)
- Supabase (데이터 저장)
- Slack (알림)
- 패키지 매니저: uv

## 디렉토리 구조

```
agents/          Claude Code CLI subprocess 호출, AI 진입 판단
market/          키움증권 REST API 호출, 시세 조회
trading/         주문 실행, 포지션 관리, Position Guard
condition/       조건 검색 (WebSocket 실시간 수신)
notifier/        Slack 알림 전송
.claude/agents/  Claude Code agent 정의 파일
```

### 모듈 역할

- **agents/** — Claude Code CLI를 subprocess로 호출하고, 응답을 파싱하여 진입 여부/확신도를 판단한다.
- **market/** — 키움증권 REST API를 호출해 시세, 호가, 계좌 잔고 등을 조회한다.
- **trading/** — 매수/매도 주문을 실행하고, 보유 포지션을 추적하며, Position Guard로 청산 조건을 상시 감시한다.
- **condition/** — WebSocket으로 조건검색식 실시간 편입/이탈 이벤트를 수신한다.
- **notifier/** — 조건검색 신호, 매매 체결, 시스템 상태 등을 Slack으로 전송한다.
- **.claude/agents/** — 진입 판단 등에 사용하는 Claude Code agent 정의 파일을 보관한다.

## 설정

환경변수는 `.env`에서 로드하여 `config.py`가 모듈 수준 상수로 노출한다. 다른 모듈에서는 `import config` 후 `config.APP_KEY`, `config.TRADE_AMOUNT` 처럼 직접 접근한다.

조건식별 전략 파라미터(목표가, 손절가, 최대 보유시간 등)는 `config.py`의 `STRATEGY_MAP` 딕셔너리에 하드코딩하며, 조건식을 새로 만들 때마다 항목을 추가한다. 등록되지 않은 조건식은 `.env`의 기본값(`TARGET_PCT`, `STOP_LOSS_PCT` 등)을 사용한다.

## 실행 방법

```
uv run python main.py
```

## 주의사항

- 실계좌 매매 전에는 `KIWOOM_USE_MOCK=true`로 모의투자 환경에서 충분히 검증할 것.
- Claude Code CLI는 진입 판단에만 사용하며, 청산 판단(손절/익절/시간제한/트레일링스탑)은 반드시 코드 기반 Position Guard가 담당한다.
