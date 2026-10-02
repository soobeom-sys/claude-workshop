"""키움증권 REST API 호출 모듈: 시세 조회, 계좌 잔고, 주문(매수/매도/취소).

모든 함수는 동기(httpx) 함수다. asyncio 코드에서는 asyncio.to_thread()로 감싸 호출한다.
실패는 KiwoomAPIError로 올리며, 재시도·보류 판단은 호출하는 쪽(오케스트레이터)이 한다.

api-id와 요청/응답 필드는 kiwoom_mcp 서버(.mcp.json)와 동일하게 맞춘다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

import config
from market import token

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SEC = 10.0
_EXCHANGE = "KRX"
# 잔고 연속조회 최대 페이지 수 (무한 루프 방지)
_MAX_PAGES = 10


class KiwoomAPIError(RuntimeError):
    """키움 REST API 호출 실패 (HTTP 오류 또는 return_code != 0)."""


@dataclass(frozen=True)
class StockBasicInfo:
    stock_code: str
    stock_name: str
    current_price: int
    change_pct: float | None


@dataclass(frozen=True)
class Holding:
    """계좌 보유 종목 1건. quantity는 보유수량(rmnd_qty), avg_price는 매입가(pur_pric)."""

    stock_code: str
    quantity: int
    avg_price: int


# ---------------------------------------------------------------------------
# 공통 호출
# ---------------------------------------------------------------------------
def _looks_like_token_error(status_code: int, data: dict[str, Any]) -> bool:
    if status_code == 401:
        return True
    msg = str(data.get("return_msg", ""))
    return data.get("return_code") not in (None, 0, "0") and ("토큰" in msg or "token" in msg.lower())


def _call(
    api_id: str,
    path: str,
    body: dict[str, Any],
    cont_yn: str = "N",
    next_key: str = "",
    _retry: bool = True,
) -> tuple[dict[str, Any], httpx.Headers]:
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token.get_token()}",
        "api-id": api_id,
        "cont-yn": cont_yn,
        "next-key": next_key,
    }
    try:
        resp = httpx.post(f"{config.BASE_URL}{path}", json=body, headers=headers, timeout=_HTTP_TIMEOUT_SEC)
    except httpx.HTTPError as exc:
        raise KiwoomAPIError(f"{api_id} 요청 실패: {exc}") from exc

    try:
        data = resp.json()
    except ValueError:
        data = {"return_code": -1, "return_msg": resp.text}

    if _retry and _looks_like_token_error(resp.status_code, data):
        # 다른 프로세스가 토큰을 재발급해 무효화된 경우. 1회만 재발급 후 재시도한다.
        token.force_refresh()
        return _call(api_id, path, body, cont_yn, next_key, _retry=False)

    if resp.status_code >= 400 or data.get("return_code") not in (None, 0, "0"):
        raise KiwoomAPIError(
            f"{api_id} 실패: http={resp.status_code} return_code={data.get('return_code')} "
            f"return_msg={data.get('return_msg')}"
        )
    return data, resp.headers


def _to_int(raw: Any) -> int:
    """'+12,345' / '-1200' / '000000000010' 형태를 절댓값 정수로 바꾼다. 비었으면 0."""
    text = str(raw or "").strip().replace(",", "").lstrip("+-")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _to_float(raw: Any) -> float | None:
    text = str(raw or "").strip().replace(",", "")
    if not text or text in {"+", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_code(raw: Any) -> str:
    """잔고 응답의 'A005930' 같은 표기에서 'A' prefix를 떼어 6자리 종목코드로 만든다."""
    code = str(raw or "").strip().split("_")[0]
    if code[:1].isalpha():
        code = code[1:]
    return code


# ---------------------------------------------------------------------------
# 시세
# ---------------------------------------------------------------------------
def get_stock_basic_info(stock_code: str) -> StockBasicInfo:
    """주식기본정보요청(ka10001)으로 종목명·현재가·등락률을 조회한다."""
    data, _ = _call("ka10001", "/api/dostk/stkinfo", {"stk_cd": stock_code})
    price = _to_int(data.get("cur_prc"))
    if price <= 0:
        raise KiwoomAPIError(f"ka10001 현재가가 비어 있습니다: {stock_code}")
    return StockBasicInfo(
        stock_code=stock_code,
        stock_name=str(data.get("stk_nm") or "").strip(),
        current_price=price,
        change_pct=_to_float(data.get("flu_rt")),
    )


def get_current_price(stock_code: str) -> int:
    return get_stock_basic_info(stock_code).current_price


# ---------------------------------------------------------------------------
# 계좌
# ---------------------------------------------------------------------------
def get_holdings() -> dict[str, Holding]:
    """계좌평가잔고내역요청(kt00018)으로 보유 종목을 {종목코드: Holding}으로 반환한다.

    보유수량이 0인 행은 제외한다. 조회 실패는 KiwoomAPIError로 올린다
    (빈 dict는 '보유 종목 없음'이라는 정상 응답이다).
    """
    holdings: dict[str, Holding] = {}
    cont_yn, next_key = "N", ""
    for _ in range(_MAX_PAGES):
        data, headers = _call(
            "kt00018",
            "/api/dostk/acnt",
            {"qry_tp": "1", "dmst_stex_tp": _EXCHANGE},
            cont_yn=cont_yn,
            next_key=next_key,
        )
        for row in data.get("acnt_evlt_remn_indv_tot") or []:
            code = _clean_code(row.get("stk_cd"))
            qty = _to_int(row.get("rmnd_qty"))
            if code and qty > 0:
                holdings[code] = Holding(stock_code=code, quantity=qty, avg_price=_to_int(row.get("pur_pric")))

        cont_yn = headers.get("cont-yn", "N")
        next_key = headers.get("next-key", "")
        if cont_yn != "Y" or not next_key:
            break
    return holdings


# ---------------------------------------------------------------------------
# 주문
# ---------------------------------------------------------------------------
def _order_no(data: dict[str, Any], api_id: str) -> str:
    order_no = str(data.get("ord_no") or "").strip()
    if not order_no:
        raise KiwoomAPIError(f"{api_id} 응답에 주문번호가 없습니다: {data}")
    return order_no


def place_buy_order(stock_code: str, quantity: int, trde_tp: str = "3") -> str:
    """가격 미지정 매수 주문(kt10000). trde_tp="3"은 시장가, "6"은 최유리지정가. 주문번호를 반환한다."""
    data, _ = _call(
        "kt10000",
        "/api/dostk/ordr",
        {
            "dmst_stex_tp": _EXCHANGE,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": "",
            "trde_tp": trde_tp,
            "cond_uv": "",
        },
    )
    return _order_no(data, "kt10000")


def place_sell_order(stock_code: str, quantity: int, price: int | None = None, trde_tp: str = "0") -> str:
    """매도 주문(kt10001). trde_tp="0"은 지정가(price 필수), "3"은 시장가. 주문번호를 반환한다."""
    if trde_tp == "0" and not price:
        raise ValueError("지정가 매도에는 price가 필요합니다")
    data, _ = _call(
        "kt10001",
        "/api/dostk/ordr",
        {
            "dmst_stex_tp": _EXCHANGE,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": str(price) if trde_tp == "0" else "",
            "trde_tp": trde_tp,
            "cond_uv": "",
        },
    )
    return _order_no(data, "kt10001")


def cancel_order(stock_code: str, orig_order_no: str, quantity: int = 0) -> str:
    """취소 주문(kt10003). quantity=0이면 잔량 전부 취소. 취소 주문번호를 반환한다."""
    data, _ = _call(
        "kt10003",
        "/api/dostk/ordr",
        {
            "dmst_stex_tp": _EXCHANGE,
            "orig_ord_no": orig_order_no,
            "stk_cd": stock_code,
            "cncl_qty": str(quantity),
        },
    )
    return _order_no(data, "kt10003")


# ---------------------------------------------------------------------------
# 호가 단위
# ---------------------------------------------------------------------------
def tick_size(price: int) -> int:
    """KRX 호가가격단위 (2023년 개편 기준, 코스피·코스닥 공통)."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000

