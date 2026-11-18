"""
solved.ac API v4 클라이언트.

경로, 파라미터, 권한(scope)은 docs/solvedac-openapi.json을 따른다. 모든 요청에 앱의
client id/secret이 실리므로 서버에서만 부른다.

- 유저 토큰이 필요한 호출은 user_id(LOJ 사용자)를 받아 TokenStore에서 토큰을 꺼내 쓴다.
  access token은 1시간이면 끝나므로 만료 직전이면 미리, 401이 오면 그때 refresh token으로
  갱신하고 원래 요청을 한 번 더 보낸다. 갱신이 거절되면 연동을 expired로 표시한다.
- refresh token은 한 번 쓰면 사라지고, 쓴 것을 다시 내면 solved.ac가 그 로그인 전체를
  끊는다. 그래서 같은 유저의 갱신은 Redis 잠금으로 한 번에 하나만 하고, 잠금을 잡은 뒤
  저장된 토큰을 다시 읽어 다른 요청이 이미 갱신했으면 그 토큰을 쓴다.
- 429가 오면 Retry-After만큼 쉬었다가 두 번까지 다시 보낸다.
- 문제·태그 메타데이터는 24시간, 유저 통계는 15분 Redis에 캐시한다.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Optional, Protocol
from urllib.parse import quote, urlencode

import httpx
import redis.exceptions

logger = logging.getLogger(__name__)

BASE_URL = "https://solved.ac"
AUTHORIZE_URL = "https://solved.ac/oauth/authorize"

# 연동할 때 요청하는 권한. profile은 모든 토큰에 들어간다. solves는 태그별 실력과 문제의
# 풀이 여부, offline.access는 refresh token(없으면 1시간 뒤 다시 연결해야 함)을 위한 것.
DEFAULT_SCOPES = ("profile", "solves", "offline.access")

METADATA_TTL = 24 * 60 * 60
USER_STATS_TTL = 15 * 60
# access token이 이 시간(초) 안에 끝나면 요청하기 전에 미리 갱신한다.
REFRESH_MARGIN = 60
MAX_RATE_LIMIT_RETRIES = 2
# 이보다 오래 기다리라고 하면 요청을 붙잡고 있지 않고 바로 실패한다(웹 요청 안에서 부르므로).
MAX_RETRY_WAIT = 10.0
REQUEST_TIMEOUT = 10.0
# 유저 없이 하는 문제 검색의 한도. 넘으면 solved.ac가 401 SIGN_IN_REQUIRED로 거절한다.
SEARCH_MAX_CHARS = 100
SEARCH_MAX_TERMS = 8

ENV_NAMES = ("SOLVEDAC_CLIENT_ID", "SOLVEDAC_CLIENT_SECRET", "SOLVEDAC_REDIRECT_URI")


class SolvedacError(Exception):
    """solved.ac가 요청을 거절했다. code는 응답의 error.code(OAuth는 error) 값."""

    def __init__(self, status: Optional[int], code: Optional[str], message: str = "",
                 details: Optional[dict] = None):
        super().__init__(f"solved.ac {status} {code}: {message}".strip())
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class SolvedacNotConfigured(SolvedacError):
    """앱 credential이 설정되지 않았다."""


class SolvedacUnavailable(SolvedacError):
    """네트워크 오류, 5xx, 잠금 실패처럼 잠시 뒤 다시 하면 될 수 있는 실패."""


class SolvedacRateLimited(SolvedacError):
    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(429, "RATE_LIMITED", message)
        self.retry_after = retry_after


class SolvedacNotLinked(SolvedacError):
    """이 LOJ 사용자는 solved.ac를 연결하지 않았다."""


class SolvedacLinkExpired(SolvedacError):
    """토큰을 갱신할 수 없다. 사용자가 다시 연결해야 한다."""


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_at: float  # 유닉스 시각(초)
    scopes: str
    refresh_token: Optional[str] = None

    def expiring(self, now: float) -> bool:
        return self.expires_at - REFRESH_MARGIN <= now

    @classmethod
    def from_response(cls, body: dict, now: float) -> "TokenSet":
        return cls(
            access_token=body["access_token"],
            expires_at=now + float(body["expires_in"]),
            scopes=body.get("scope") or "",
            refresh_token=body.get("refresh_token") or None,
        )


class TokenStore(Protocol):
    """연동 토큰을 보관하는 곳. 2단계에서 DB(암호화) 구현을 붙인다."""

    async def load(self, user_id: str) -> Optional[TokenSet]: ...

    async def save(self, user_id: str, tokens: TokenSet) -> None: ...

    async def mark_expired(self, user_id: str) -> None: ...


@dataclass(frozen=True)
class SolvedacConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> Optional["SolvedacConfig"]:
        """셋 다 있으면 설정을, 하나라도 비었으면 None을 돌려준다(연동 기능만 꺼짐)."""
        values = [os.getenv(name, "").strip() for name in ENV_NAMES]
        if not all(values):
            if any(values):
                missing = [name for name, value in zip(ENV_NAMES, values) if not value]
                logger.warning("solved.ac 연동이 꺼져 있습니다. 비어 있는 설정: %s", ", ".join(missing))
            return None
        return cls(*values)


def code_challenge(verifier: str) -> str:
    """PKCE S256: BASE64URL(SHA256(code_verifier)), 끝의 = 없이."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge). verifier는 RFC 7636이 정한 43~128자 범위 안이다."""
    verifier = secrets.token_urlsafe(64)
    return verifier, code_challenge(verifier)


def check_search_query(query: str) -> None:
    """유저 없는 검색 한도(100자, 8텀)를 넘으면 보내기 전에 막는다."""
    if len(query) > SEARCH_MAX_CHARS:
        raise ValueError(f"검색어는 {SEARCH_MAX_CHARS}자까지 쓸 수 있습니다.")
    if len(query.split()) > SEARCH_MAX_TERMS:
        raise ValueError(f"검색어는 {SEARCH_MAX_TERMS}개 조건까지 쓸 수 있습니다.")


def _error_code(response: httpx.Response) -> Optional[str]:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    if isinstance(error, dict):
        return error.get("code")
    return error if isinstance(error, str) else None


def _to_error(response: httpx.Response) -> SolvedacError:
    """
    응답을 예외로 바꾼다. 일반 API는 {"error": {"code", "message", "details"}},
    OAuth 엔드포인트는 {"error": "invalid_grant", "error_description"} 모양이다.
    """
    status = response.status_code
    code, message, details = None, "", None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code, message, details = error.get("code"), error.get("message") or "", error.get("details")
        elif isinstance(error, str):
            code, message = error, body.get("error_description") or ""
    if status >= 500:
        return SolvedacUnavailable(status, code, message or "solved.ac 서버 오류", details)
    return SolvedacError(status, code, message, details)


class SolvedacClient:
    def __init__(self, config: Optional[SolvedacConfig], *, store: TokenStore, cache=None,
                 http: Optional[httpx.AsyncClient] = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time):
        """
        cache는 redis.asyncio 클라이언트. 캐시와 갱신 잠금에 쓴다. None이면 캐시 없이
        프로세스 안의 잠금만 쓴다(테스트나 API 프로세스가 하나뿐일 때).
        """
        self._config = config
        self._store = store
        self._cache = cache
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=REQUEST_TIMEOUT)
        self._sleep = sleep
        self._clock = clock
        self._local_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def from_env(cls, *, store: TokenStore, cache=None) -> "SolvedacClient":
        return cls(SolvedacConfig.from_env(), store=store, cache=cache)

    @property
    def configured(self) -> bool:
        return self._config is not None

    async def aclose(self) -> None:
        await self._http.aclose()

    def _require_config(self) -> SolvedacConfig:
        if self._config is None:
            raise SolvedacNotConfigured(None, "NOT_CONFIGURED", "solved.ac 연동이 설정되지 않았습니다.")
        return self._config

    # ── HTTP ──────────────────────────────────────────────────────────

    def _retry_after(self, response: httpx.Response) -> float:
        value = response.headers.get("retry-after", "").strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - self._clock())
        except (TypeError, ValueError, IndexError):
            return 1.0

    def _log_budget(self, method: str, path: str, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is None:
            return
        limit = response.headers.get("x-ratelimit-limit")
        try:
            low = limit is not None and float(remaining) < float(limit) * 0.1
        except ValueError:
            low = False
        logger.log(logging.WARNING if low else logging.DEBUG,
                   "solved.ac %s %s: 남은 호출 %s/%s", method, path, remaining, limit or "?")

    async def _send(self, method: str, path: str, *, params=None, data=None,
                    token: Optional[str] = None) -> httpx.Response:
        """credential 헤더를 붙여 보낸다. 429면 Retry-After만큼 쉬고 두 번까지 다시 보낸다."""
        config = self._require_config()
        headers = {
            "x-client-id": config.client_id,
            "x-client-secret": config.client_secret,
            "accept": "application/json",
        }
        if token:
            headers["authorization"] = f"Bearer {token}"
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = await self._http.request(method, path, params=params, data=data, headers=headers)
            except httpx.HTTPError as exc:
                raise SolvedacUnavailable(None, "NETWORK", f"solved.ac에 연결하지 못했습니다: {exc}") from exc
            self._log_budget(method, path, response)
            if response.status_code != 429:
                return response
            wait = self._retry_after(response)
            if attempt == MAX_RATE_LIMIT_RETRIES or wait > MAX_RETRY_WAIT:
                raise SolvedacRateLimited(wait, "solved.ac 호출 한도를 넘었습니다.")
            logger.warning("solved.ac %s %s: 429, %.1f초 뒤 다시 시도 (%d/%d)",
                           method, path, wait, attempt + 1, MAX_RATE_LIMIT_RETRIES)
            await self._sleep(wait)
        raise AssertionError("unreachable")

    # ── 캐시 ─────────────────────────────────────────────────────────

    @staticmethod
    def _cache_key(path: str, params: Optional[dict], viewer: Optional[str]) -> str:
        query = urlencode(sorted((params or {}).items()))
        # 유저 토큰을 실어 보낸 응답은 그 유저 관점이므로 유저마다 따로 둔다.
        return f"solvedac:v1:{('u:' + viewer) if viewer else 'anon'}:{path}?{query}"

    async def _cache_get(self, key: str):
        if self._cache is None:
            return None
        try:
            raw = await self._cache.get(key)
        except redis.exceptions.RedisError:
            logger.warning("solved.ac 캐시를 읽지 못했습니다", exc_info=True)
            return None
        return json.loads(raw) if raw is not None else None

    async def _cache_set(self, key: str, value, ttl: int) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except redis.exceptions.RedisError:
            logger.warning("solved.ac 캐시에 쓰지 못했습니다", exc_info=True)

    # ── OAuth ───────────────────────────────────────────────────────

    def authorize_url(self, state: str, challenge: str, scopes=DEFAULT_SCOPES) -> str:
        config = self._require_config()
        return AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })

    async def _token_request(self, form: dict) -> TokenSet:
        response = await self._send("POST", "/api/v4/oauth/token", data=form)
        if response.status_code != 200:
            raise _to_error(response)
        return TokenSet.from_response(response.json(), self._clock())

    async def exchange_code(self, code: str, code_verifier: str) -> TokenSet:
        config = self._require_config()
        return await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "code_verifier": code_verifier,
        })

    async def revoke(self, token: str) -> None:
        """access/refresh 어느 쪽을 내도 그 로그인의 토큰이 모두 끝난다."""
        response = await self._send("POST", "/api/v4/oauth/revoke", data={"token": token})
        if response.status_code != 200:
            raise _to_error(response)

    # ── 유저 토큰 ────────────────────────────────────────────────────

    @asynccontextmanager
    async def _refresh_lock(self, user_id: str):
        if self._cache is None:
            async with self._local_locks.setdefault(user_id, asyncio.Lock()):
                yield
            return
        # 갱신 요청 한 번(최대 REQUEST_TIMEOUT × 재시도)보다 길게 잡는다.
        lock = self._cache.lock(f"solvedac:refresh:{user_id}", timeout=60, blocking_timeout=30)
        try:
            acquired = await lock.acquire()
        except redis.exceptions.RedisError as exc:
            raise SolvedacUnavailable(None, "LOCK", "토큰 갱신 잠금을 잡지 못했습니다.") from exc
        if not acquired:
            raise SolvedacUnavailable(None, "LOCK", "다른 요청이 토큰을 갱신하는 중입니다.")
        try:
            yield
        finally:
            try:
                await lock.release()
            except redis.exceptions.RedisError:
                logger.warning("solved.ac 갱신 잠금을 풀지 못했습니다 (시간이 지나면 풀림)", exc_info=True)

    async def _expire(self, user_id: str, reason: str) -> SolvedacLinkExpired:
        logger.info("solved.ac 연동 만료 (%s): %s", user_id, reason)
        await self._store.mark_expired(user_id)
        return SolvedacLinkExpired(401, "LINK_EXPIRED", "solved.ac 연결이 끊어졌습니다. 다시 연결해 주세요.")

    async def _refresh(self, user_id: str, stale_access: str) -> TokenSet:
        """stale_access가 더는 쓸 수 없을 때 새 토큰을 받아 저장하고 돌려준다."""
        async with self._refresh_lock(user_id):
            tokens = await self._store.load(user_id)
            if tokens is None:
                raise SolvedacNotLinked(None, "NOT_LINKED", "solved.ac가 연결되어 있지 않습니다.")
            if tokens.access_token != stale_access and not tokens.expiring(self._clock()):
                return tokens  # 잠금을 기다리는 동안 다른 요청이 갱신했다
            if not tokens.refresh_token:
                raise await self._expire(user_id, "refresh token 없음")
            try:
                fresh = await self._token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                })
            except (SolvedacUnavailable, SolvedacRateLimited):
                raise  # 잠시 뒤에 다시 하면 되는 실패. 연동은 그대로 둔다.
            except SolvedacError as exc:
                raise await self._expire(user_id, f"갱신 거절 {exc.code}") from exc
            # 여기서 저장에 실패하면 새 refresh token을 잃는다. 예전 것은 이미 썼으므로
            # 다음 갱신은 거절되고 사용자가 다시 연결해야 한다.
            await self._store.save(user_id, fresh)
            return fresh

    async def _tokens_for(self, user_id: str) -> TokenSet:
        tokens = await self._store.load(user_id)
        if tokens is None:
            raise SolvedacNotLinked(None, "NOT_LINKED", "solved.ac가 연결되어 있지 않습니다.")
        if tokens.expiring(self._clock()):
            return await self._refresh(user_id, tokens.access_token)
        return tokens

    # ── 호출 ─────────────────────────────────────────────────────────

    async def call(self, method: str, path: str, *, params: Optional[dict] = None,
                   user_id: Optional[str] = None, cache_ttl: Optional[int] = None):
        """
        API를 불러 JSON 본문을 돌려준다. user_id를 주면 그 사용자의 토큰을 함께 보낸다.
        cache_ttl을 주면 GET 응답을 그 시간(초)만큼 캐시한다.
        """
        self._require_config()
        key = self._cache_key(path, params, user_id) if cache_ttl and method == "GET" else None
        if key and (cached := await self._cache_get(key)) is not None:
            return cached

        if user_id is None:
            response = await self._send(method, path, params=params)
        else:
            tokens = await self._tokens_for(user_id)
            response = await self._send(method, path, params=params, token=tokens.access_token)
            # 검색 한도(SIGN_IN_REQUIRED)는 토큰 문제가 아니므로 갱신하지 않는다.
            if response.status_code == 401 and _error_code(response) != "SIGN_IN_REQUIRED":
                tokens = await self._refresh(user_id, tokens.access_token)
                response = await self._send(method, path, params=params, token=tokens.access_token)
                if response.status_code == 401 and _error_code(response) != "SIGN_IN_REQUIRED":
                    raise await self._expire(user_id, "갱신한 토큰도 401")

        if response.status_code != 200:
            raise _to_error(response)
        body = response.json()
        if key:
            await self._cache_set(key, body, cache_ttl)
        return body

    # ── 엔드포인트 ──────────────────────────────────────────────────
    # as_user를 주면 그 사용자의 토큰으로 부른다. 유저 토큰 없는 호출은 앱 전체가 초당
    # 2회 남짓을 나눠 쓰므로, 연동한 사용자의 데이터는 가능하면 그 사용자 토큰으로 부른다.

    async def me(self, user_id: str) -> dict:
        """토큰 주인의 {id, handle}. id는 바뀌지 않고 handle은 바뀔 수 있다."""
        return await self.call("GET", "/api/v4/me", user_id=user_id)

    async def tags(self) -> Any:
        return await self.call("GET", "/api/v4/tags", cache_ttl=METADATA_TTL)

    async def problem(self, problem_id: int) -> dict:
        return await self.call("GET", f"/api/v4/problems/{int(problem_id)}", cache_ttl=METADATA_TTL)

    async def tag_progress(self, user_id: str) -> Any:
        return await self.call("GET", "/api/v4/me/tag-progress", user_id=user_id, cache_ttl=USER_STATS_TTL)

    async def user_profile(self, handle: str, *, as_user: Optional[str] = None) -> dict:
        return await self.call("GET", f"/api/v4/users/{_segment(handle)}",
                               user_id=as_user, cache_ttl=USER_STATS_TTL)

    async def tag_histogram(self, handle: str, tag_key: str, *, as_user: Optional[str] = None) -> Any:
        return await self.call("GET", f"/api/v4/users/{_segment(handle)}/tags/{_segment(tag_key)}/histogram",
                               user_id=as_user, cache_ttl=USER_STATS_TTL)

    async def rating_history(self, handle: str, range_: str = "all", *, as_user: Optional[str] = None) -> Any:
        return await self.call("GET", f"/api/v4/users/{_segment(handle)}/rating/history",
                               params={"range": range_}, user_id=as_user, cache_ttl=USER_STATS_TTL)

    async def activity_days(self, handle: str, start: str, end: str, *, as_user: Optional[str] = None) -> Any:
        """start, end는 YYYY-MM-DD."""
        return await self.call("GET", f"/api/v4/users/{_segment(handle)}/activity/days",
                               params={"from": start, "to": end}, user_id=as_user, cache_ttl=USER_STATS_TTL)

    async def random_problem(self, query: str, *, as_user: Optional[str] = None) -> Optional[dict]:
        """검색 조건에 맞는 문제 하나. 맞는 문제가 없으면 None. 매번 달라야 하므로 캐시하지 않는다."""
        check_search_query(query)
        try:
            return await self.call("GET", "/api/v4/search/random-problem", params={"q": query}, user_id=as_user)
        except SolvedacError as exc:
            if exc.status == 404:
                return None
            raise


def _segment(value: str) -> str:
    """경로 한 칸에 들어갈 값. / 같은 문자로 다른 경로를 가리키지 못하게 인코딩한다."""
    return quote(str(value), safe="")
