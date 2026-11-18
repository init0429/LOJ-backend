"""
solved.ac 계정 연동: OAuth(Authorization Code + PKCE) 흐름과 토큰 보관.

흐름
1. POST /integrations/solvedac/start  (LOJ 로그인 필요)
   state와 code_verifier를 만들어 Redis에 10분 두고, solved.ac 인증 주소를 돌려준다.
   로그인 토큰이 브라우저 저장소에 있어 페이지 이동으로는 실어 보낼 수 없으므로, 화면이
   fetch로 이 주소를 받은 뒤 직접 이동한다. 이 브라우저에만 짧게 쿠키를 심어 둔다.
2. solved.ac에서 승인하면 브라우저가 GET /integrations/solvedac/callback 으로 돌아온다.
   state를 한 번만 꺼내 쓰고, 쿠키가 1번에서 심은 것과 같은지 확인한다. 남이 자기 state가
   든 인증 링크를 보내 피해자의 solved.ac를 자기 LOJ 계정에 붙이는 것을 막기 위함이다.
   code를 토큰으로 바꾸고 계정 주인을 확인해 저장한 뒤 설정 화면으로 돌려보낸다.
3. DELETE /integrations/solvedac  토큰을 무효화(revoke)하고 연동 기록을 지운다.

한 solved.ac 계정은 LOJ 계정 하나에만 붙는다. 기준은 바뀌지 않는 solved.ac 계정 id다
(handle은 사용자가 바꿀 수 있다). 토큰은 Fernet으로 암호화해 저장한다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
import redis.exceptions

import solvedac_client as sc

logger = logging.getLogger(__name__)

TABLE = "solvedac_accounts"
STATE_TTL = 10 * 60
STATE_PREFIX = "solvedac:oauth:"
COOKIE = "solvedac_link"


class AlreadyLinked(Exception):
    """이 solved.ac 계정은 다른 LOJ 계정에 붙어 있다."""


class TokenCipher:
    """
    SOLVEDAC_TOKEN_KEY(Fernet 키)로 토큰을 암호화한다. 쉼표로 여러 개를 적으면 첫 키로
    암호화하고 모든 키로 풀어 본다(키를 바꿀 때 새 키를 앞에 두고 예전 키를 뒤에 남겨 둠).
    """

    def __init__(self, keys: list[str]):
        self._fernet = MultiFernet([Fernet(key.encode()) for key in keys])

    @classmethod
    def from_env(cls) -> Optional["TokenCipher"]:
        keys = [key.strip() for key in os.getenv("SOLVEDAC_TOKEN_KEY", "").split(",") if key.strip()]
        if not keys:
            return None
        try:
            return cls(keys)
        except ValueError:
            logger.error("SOLVEDAC_TOKEN_KEY가 올바른 Fernet 키가 아닙니다. solved.ac 연동을 끕니다.")
            return None

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseTokenStore:
    """solvedac_accounts 테이블. 서버의 secret 키로만 읽고 쓴다(브라우저에는 RLS로 막혀 있음)."""

    def __init__(self, supabase, cipher: TokenCipher):
        self._db = supabase
        self._cipher = cipher

    def _token_columns(self, tokens: sc.TokenSet) -> dict:
        return {
            "access_token_enc": self._cipher.encrypt(tokens.access_token),
            "refresh_token_enc": self._cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
            "scopes": tokens.scopes,
            "expires_at": _iso(tokens.expires_at),
        }

    async def account(self, user_id: str) -> Optional[dict]:
        """토큰 열을 뺀 연동 정보, 없으면 None."""
        def query():
            return (self._db.table(TABLE)
                    .select("solvedac_user_id, handle, scopes, status, linked_at")
                    .eq("user_id", user_id).limit(1).execute().data)
        rows = await run_in_threadpool(query)
        return rows[0] if rows else None

    async def load(self, user_id: str) -> Optional[sc.TokenSet]:
        def query():
            return (self._db.table(TABLE)
                    .select("access_token_enc, refresh_token_enc, scopes, expires_at, status")
                    .eq("user_id", user_id).limit(1).execute().data)
        rows = await run_in_threadpool(query)
        if not rows or rows[0]["status"] != "active":
            return None
        row = rows[0]
        try:
            return sc.TokenSet(
                access_token=self._cipher.decrypt(row["access_token_enc"]),
                refresh_token=self._cipher.decrypt(row["refresh_token_enc"]) if row["refresh_token_enc"] else None,
                scopes=row["scopes"],
                expires_at=_timestamp(row["expires_at"]),
            )
        except InvalidToken:
            # 키를 잃었거나 바꿨다. 이 토큰은 다시 쓸 수 없으므로 다시 연결하게 한다.
            logger.error("solved.ac 토큰을 풀지 못했습니다 (%s). SOLVEDAC_TOKEN_KEY를 확인하세요.", user_id)
            await self.mark_expired(user_id)
            return None

    async def save(self, user_id: str, tokens: sc.TokenSet) -> None:
        columns = {**self._token_columns(tokens), "status": "active", "updated_at": _now_iso()}
        await run_in_threadpool(
            lambda: self._db.table(TABLE).update(columns).eq("user_id", user_id).execute()
        )

    async def mark_expired(self, user_id: str) -> None:
        await run_in_threadpool(
            lambda: self._db.table(TABLE).update({"status": "expired", "updated_at": _now_iso()})
            .eq("user_id", user_id).execute()
        )

    async def link(self, user_id: str, solvedac_user_id: int, handle: str, tokens: sc.TokenSet) -> None:
        """연동을 저장한다. 이 사용자의 예전 연동은 덮어쓴다. 남의 계정이면 AlreadyLinked."""
        row = {
            "user_id": user_id,
            "solvedac_user_id": solvedac_user_id,
            "handle": handle,
            **self._token_columns(tokens),
            "status": "active",
            "linked_at": _now_iso(),
            "updated_at": _now_iso(),
        }

        def write():
            owner = (self._db.table(TABLE).select("user_id")
                     .eq("solvedac_user_id", solvedac_user_id).limit(1).execute().data)
            if owner and owner[0]["user_id"] != user_id:
                raise AlreadyLinked()
            try:
                self._db.table(TABLE).upsert(row, on_conflict="user_id").execute()
            except Exception as exc:
                # 위 확인과 저장 사이에 다른 사람이 먼저 붙인 경우. solvedac_user_id의 unique가 막는다.
                if getattr(exc, "code", None) == "23505":
                    raise AlreadyLinked() from exc
                raise

        await run_in_threadpool(write)

    async def delete(self, user_id: str) -> None:
        await run_in_threadpool(lambda: self._db.table(TABLE).delete().eq("user_id", user_id).execute())


def _binding_hash(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


def create_router(*, client: sc.SolvedacClient, store: Optional[SupabaseTokenStore], redis,
                  current_user: Callable) -> APIRouter:
    """
    client: solved.ac 클라이언트, store: 토큰 저장소(암호화 키가 없으면 None),
    redis: redis.asyncio 클라이언트, current_user: LOJ 로그인을 확인해 user_id를 주는 의존성.
    """
    router = APIRouter(prefix="/integrations/solvedac")
    enabled = client.configured and store is not None
    redirect_uri = client.redirect_uri if client.configured else ""
    # 연동이 끝나면 돌려보낼 화면. 콜백 주소와 같은 사이트의 설정 화면이다.
    parts = urlsplit(redirect_uri)
    settings_url = f"{parts.scheme}://{parts.netloc}/settings"
    secure_cookie = parts.scheme == "https"

    def require_enabled():
        if not enabled:
            raise HTTPException(503, "solved.ac 연동이 아직 설정되지 않았습니다.")

    def back_to_settings(result: str) -> RedirectResponse:
        response = RedirectResponse(f"{settings_url}?{urlencode({'solvedac': result})}", status_code=303)
        response.delete_cookie(COOKIE, path="/", secure=secure_cookie, httponly=True, samesite="lax")
        return response

    async def revoke_quietly(tokens: Optional[sc.TokenSet]) -> None:
        """refresh token을 내면 access token까지 함께 끝난다. 실패해도 연동 해제는 계속한다."""
        if tokens is None:
            return
        try:
            await client.revoke(tokens.refresh_token or tokens.access_token)
        except sc.SolvedacError:
            logger.warning("solved.ac 토큰을 무효화하지 못했습니다", exc_info=True)

    @router.get("")
    async def link_status(user_id: str = Depends(current_user)):
        if not enabled:
            return {"configured": False, "linked": False}
        account = await store.account(user_id)
        if account is None:
            return {"configured": True, "linked": False}
        result = {
            "configured": True,
            "linked": True,
            "handle": account["handle"],
            "status": account["status"],
            "linked_at": account["linked_at"],
            "tier": None,
            "rating": None,
            "solved_count": None,
        }
        try:
            # 연동한 사용자 토큰으로 부르면 앱 전체가 나눠 쓰는 비로그인 한도를 쓰지 않는다.
            viewer = user_id if account["status"] == "active" else None
            profile = await client.user_profile(account["handle"], as_user=viewer)
            result.update(tier=profile.get("tier"), rating=profile.get("rating"),
                          solved_count=profile.get("solvedCount"))
        except sc.SolvedacLinkExpired:
            result["status"] = "expired"
        except sc.SolvedacError:
            logger.warning("solved.ac 프로필을 불러오지 못했습니다", exc_info=True)
        return result

    @router.post("/start")
    async def start(user_id: str = Depends(current_user)):
        require_enabled()
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        verifier, challenge = sc.new_pkce_pair()
        payload = json.dumps({"user_id": user_id, "verifier": verifier, "binding": _binding_hash(binding)})
        try:
            await redis.set(STATE_PREFIX + state, payload, ex=STATE_TTL)
        except redis.exceptions.RedisError:
            logger.exception("solved.ac 연동 state를 저장하지 못했습니다")
            raise HTTPException(503, "잠시 후 다시 시도하세요.")
        response = JSONResponse({"authorize_url": client.authorize_url(state, challenge)})
        response.set_cookie(COOKIE, binding, max_age=STATE_TTL, path="/",
                            secure=secure_cookie, httponly=True, samesite="lax")
        return response

    @router.get("/callback")
    async def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        if not enabled:
            return back_to_settings("unavailable")
        try:
            raw = await redis.getdel(STATE_PREFIX + state) if state else None
        except redis.exceptions.RedisError:
            logger.exception("solved.ac 연동 state를 읽지 못했습니다")
            return back_to_settings("failed")
        if raw is None:
            return back_to_settings("expired")  # 10분이 지났거나 이미 쓴 링크
        saved = json.loads(raw)
        binding = request.cookies.get(COOKIE, "")
        if not hmac.compare_digest(_binding_hash(binding), saved["binding"]):
            return back_to_settings("browser")  # 연동을 시작한 브라우저가 아니다
        if error or not code:
            return back_to_settings("denied" if error == "access_denied" else "failed")

        user_id = saved["user_id"]
        try:
            tokens = await client.exchange_code(code, saved["verifier"])
        except sc.SolvedacError:
            logger.warning("solved.ac code 교환 실패", exc_info=True)
            return back_to_settings("failed")
        try:
            me = await client.me_for_token(tokens.access_token)
            previous = await store.load(user_id)
            await store.link(user_id, int(me["id"]), me["handle"], tokens)
        except AlreadyLinked:
            await revoke_quietly(tokens)
            return back_to_settings("taken")
        except Exception:
            logger.exception("solved.ac 연동을 저장하지 못했습니다")
            await revoke_quietly(tokens)
            return back_to_settings("failed")
        # 다시 연결했으면 예전 로그인의 토큰은 끝낸다. 승인할 때마다 별개의 로그인이라
        # 같은 solved.ac 계정이어도 새 토큰에는 영향이 없다.
        await revoke_quietly(previous)
        logger.info("solved.ac 연동: %s -> %s", user_id, me["handle"])
        return back_to_settings("linked")

    @router.delete("")
    async def unlink(user_id: str = Depends(current_user)):
        require_enabled()
        await revoke_quietly(await store.load(user_id))
        await store.delete(user_id)
        return {"configured": True, "linked": False}

    return router
