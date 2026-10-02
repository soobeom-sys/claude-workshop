"""키움증권 OAuth2 접근토큰 관리 모듈.

키움 REST API는 **앱키당 유효한 토큰이 1개**뿐이다. 새 토큰을 발급받으면
기존 토큰은 즉시 무효화되므로, 같은 앱키를 쓰는 여러 프로세스(kiwoom_mcp,
백엔드 트레이딩 루프, 웹 대시보드)가 각자 발급하면 서로의 토큰을 끊어버린다.
이를 막기 위해 Supabase `kiwoom_token` 테이블(env='mock'|'real')을 공유
캐시로 사용한다. 로컬 파일이나 파일 락은 쓰지 않는다.

캐시는 2단계다.

1. 메모리 캐시(`_cache`) — 같은 프로세스 안에서의 빠른 반환
2. Supabase 공유 캐시 — 프로세스·호스트 간 공유

Supabase 접근은 best-effort다. SUPABASE_URL/KEY가 없거나 호출이 실패하면
로그만 남기고 조용히 건너뛰며, 토큰 발급 자체는 언제나 계속 진행한다.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any
from zoneinfo import ZoneInfo

import httpx

import config

logger = logging.getLogger(__name__)

# 만료 60초 전부터 갱신 대상으로 본다.
_EXPIRY_MARGIN_SEC = 60
_HTTP_TIMEOUT_SEC = 10.0
_KST = ZoneInfo("Asia/Seoul")

# {"access_token": str, "expires_at": float}
_cache: dict[str, Any] | None = None
# 같은 프로세스 안에서 동시에 발급하는 것을 막는다.
_lock = threading.Lock()


def _env() -> str:
    """kiwoom_mcp와 동일한 규칙으로 토큰 네임스페이스를 구분한다."""
    return "mock" if config.USE_MOCK else "real"


def _now() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp()


def _is_valid(entry: dict[str, Any] | None) -> bool:
    if not entry or not entry.get("access_token"):
        return False
    try:
        expires_at = float(entry["expires_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return expires_at - _EXPIRY_MARGIN_SEC > _now()


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def get_token() -> str:
    """유효한 접근토큰을 반환한다. 메모리 → Supabase → 신규 발급 순으로 찾는다."""
    global _cache

    if _is_valid(_cache):
        return _cache["access_token"]

    env = _env()
    shared = _read_supabase_cache(env)
    if _is_valid(shared):
        _cache = shared
        logger.info("Supabase 공유 캐시에서 토큰을 로드했습니다 (env=%s)", env)
        return shared["access_token"]

    return _issue_and_store()


def force_refresh() -> str:
    """캐시를 모두 비우고 토큰을 강제로 재발급한다.

    401 응답 등으로 기존 토큰이 무효해진 것이 확인됐을 때만 호출한다.
    같은 앱키를 쓰는 다른 프로세스의 토큰도 함께 무효화된다.
    """
    global _cache

    env = _env()
    _cache = None
    _clear_supabase_cache(env)
    logger.warning("토큰을 강제 재발급합니다 (env=%s)", env)
    # 공유 캐시에 남아 있는 토큰은 방금 무효하다고 판단한 바로 그 토큰이므로
    # 발급 직전 재확인을 건너뛴다. (DELETE가 실패했거나 다른 프로세스가
    # 그 사이에 같은 행을 다시 써넣었을 수 있다.)
    return _issue_and_store(check_shared=False)


# ---------------------------------------------------------------------------
# 발급
# ---------------------------------------------------------------------------
def _issue_and_store(check_shared: bool = True) -> str:
    """토큰을 발급하고 메모리·Supabase 캐시에 저장한다.

    발급 직전에 Supabase를 한 번 더 확인해, 다른 프로세스가 방금 발급해둔
    토큰이 있으면 그것을 그대로 쓴다. 분산 락이 아니므로 동시 발급을 완전히
    막지는 못하지만, 충돌 확률을 크게 낮춘다.

    check_shared=False면 그 재확인을 건너뛰고 무조건 새로 발급한다
    (force_refresh 전용).
    """
    global _cache

    env = _env()
    with _lock:
        # 락을 기다리는 동안 다른 스레드가 이미 채워놨을 수 있다.
        if check_shared and _is_valid(_cache):
            return _cache["access_token"]

        shared = _read_supabase_cache(env) if check_shared else None
        if _is_valid(shared):
            _cache = shared
            logger.info("다른 프로세스가 방금 발급한 토큰을 사용합니다 (env=%s)", env)
            return shared["access_token"]

        access_token, expires_at = _issue_token()
        entry = {"access_token": access_token, "expires_at": expires_at}
        _cache = entry
        _write_supabase_cache(env, entry)
        logger.info(
            "토큰을 신규 발급했습니다 (env=%s, 만료=%s)",
            env,
            dt.datetime.fromtimestamp(expires_at, _KST).isoformat(),
        )
        return access_token


def _issue_token() -> tuple[str, float]:
    """키움 au10001로 접근토큰을 발급받아 (토큰, 만료 unix timestamp)를 반환한다."""
    url = f"{config.BASE_URL}/oauth2/token"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "api-id": "au10001",
    }
    body = {
        "grant_type": "client_credentials",
        "appkey": config.APP_KEY,
        "secretkey": config.SECRET_KEY,
    }

    resp = httpx.post(url, headers=headers, json=body, timeout=_HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()

    return_code = data.get("return_code")
    if return_code not in (None, 0, "0"):
        raise RuntimeError(
            f"토큰 발급 실패: return_code={return_code} return_msg={data.get('return_msg')}"
        )

    access_token = data.get("token") or data.get("access_token")
    if not access_token:
        raise RuntimeError(f"토큰 발급 응답에 토큰이 없습니다: {data}")

    return access_token, _parse_expires_dt(data.get("expires_dt"))


def _parse_expires_dt(expires_dt: str | None) -> float:
    """expires_dt(KST 기준 YYYYMMDDHHmmss)를 unix timestamp로 변환한다."""
    if not expires_dt:
        raise RuntimeError("토큰 발급 응답에 expires_dt가 없습니다")
    try:
        naive = dt.datetime.strptime(str(expires_dt), "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise RuntimeError(f"expires_dt 형식이 올바르지 않습니다: {expires_dt}") from exc
    return naive.replace(tzinfo=_KST).timestamp()


# ---------------------------------------------------------------------------
# Supabase 공유 캐시 (모든 실패는 비치명적)
# ---------------------------------------------------------------------------
def _supabase_ready() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_KEY)


def _supabase_headers() -> dict[str, str]:
    return {
        "apikey": config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _read_supabase_cache(env: str) -> dict[str, Any] | None:
    """kiwoom_token에서 해당 env 행을 읽는다. 행이 없거나 실패하면 None."""
    if not _supabase_ready():
        return None
    try:
        resp = httpx.get(
            f"{config.SUPABASE_URL}/rest/v1/kiwoom_token",
            headers=_supabase_headers(),
            params={"env": f"eq.{env}", "select": "access_token,expires_at"},
            timeout=_HTTP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        if not rows:
            return None
        row = rows[0]
        return {
            "access_token": row.get("access_token"),
            "expires_at": float(row.get("expires_at") or 0),
        }
    except Exception:
        logger.warning("Supabase 토큰 캐시 읽기 실패 (무시하고 진행): env=%s", env, exc_info=True)
        return None


def _write_supabase_cache(env: str, entry: dict[str, Any]) -> None:
    """kiwoom_token의 해당 env 행을 upsert한다."""
    if not _supabase_ready():
        return
    try:
        resp = httpx.post(
            f"{config.SUPABASE_URL}/rest/v1/kiwoom_token",
            headers={
                **_supabase_headers(),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            params={"on_conflict": "env"},
            json={
                "env": env,
                "access_token": entry["access_token"],
                "expires_at": entry["expires_at"],
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            timeout=_HTTP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
    except Exception:
        logger.warning("Supabase 토큰 캐시 쓰기 실패 (무시하고 진행): env=%s", env, exc_info=True)


def _clear_supabase_cache(env: str) -> None:
    """kiwoom_token에서 해당 env 행을 삭제한다."""
    if not _supabase_ready():
        return
    try:
        resp = httpx.delete(
            f"{config.SUPABASE_URL}/rest/v1/kiwoom_token",
            headers={**_supabase_headers(), "Prefer": "return=minimal"},
            params={"env": f"eq.{env}"},
            timeout=_HTTP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
    except Exception:
        logger.warning("Supabase 토큰 캐시 삭제 실패 (무시하고 진행): env=%s", env, exc_info=True)
