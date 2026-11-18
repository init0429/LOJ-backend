"""solvedac_client를 가짜 HTTP와 가짜 Redis로 시험한다. 실제 solved.ac는 부르지 않는다."""
import asyncio
import json
import unittest
from urllib.parse import parse_qs, urlparse

import httpx
import redis.exceptions

import solvedac_client as sc

CONFIG = sc.SolvedacConfig("client-id", "client-secret", "https://loj.example/api/integrations/solvedac/callback")
NOW = 1_000_000.0
USER = "loj-user-1"


class FakeRedis:
    """get/set(ex)/lock만 흉내 낸다. broken이면 모든 호출이 RedisError를 낸다."""

    def __init__(self, broken=False):
        self.data = {}
        self.ttl = {}
        self.broken = broken
        self.locks = {}

    async def get(self, key):
        if self.broken:
            raise redis.exceptions.ConnectionError("down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise redis.exceptions.ConnectionError("down")
        self.data[key] = value
        self.ttl[key] = ex

    def lock(self, name, timeout=None, blocking_timeout=None):
        fake = self

        class Lock:
            async def acquire(self):
                if fake.broken:
                    raise redis.exceptions.ConnectionError("down")
                await fake.locks.setdefault(name, asyncio.Lock()).acquire()
                return True

            async def release(self):
                fake.locks[name].release()

        return Lock()


class MemoryStore:
    def __init__(self, tokens=None):
        self.tokens = dict(tokens or {})
        self.expired = []
        self.saved = []

    async def load(self, user_id):
        return self.tokens.get(user_id)

    async def save(self, user_id, tokens):
        self.tokens[user_id] = tokens
        self.saved.append(tokens)

    async def mark_expired(self, user_id):
        self.expired.append(user_id)


def json_response(status, body, headers=None):
    return httpx.Response(status, content=json.dumps(body).encode(), headers={
        "content-type": "application/json", **(headers or {}),
    })


def token_body(access, refresh="refresh-2", expires_in=3600):
    body = {"access_token": access, "token_type": "Bearer", "expires_in": expires_in, "scope": "profile solves offline.access"}
    if refresh:
        body["refresh_token"] = refresh
    return body


class ClientTestCase(unittest.IsolatedAsyncioTestCase):
    def make(self, handler, *, store=None, cache=None, config=CONFIG):
        self.requests = []
        self.slept = []

        async def record(request):
            self.requests.append(request)
            return handler(request)

        async def sleep(seconds):
            self.slept.append(seconds)

        http = httpx.AsyncClient(base_url=sc.BASE_URL, transport=httpx.MockTransport(record))
        self.addAsyncCleanup(http.aclose)
        self.store = store if store is not None else MemoryStore()
        return sc.SolvedacClient(config, store=self.store, cache=cache, http=http, sleep=sleep, clock=lambda: NOW)

    def linked(self, access="access-1", refresh="refresh-1", expires_at=NOW + 3600):
        return MemoryStore({USER: sc.TokenSet(access, expires_at, "profile solves offline.access", refresh)})


class CredentialTests(ClientTestCase):
    async def test_every_request_carries_the_app_credentials(self):
        client = self.make(lambda request: json_response(200, []))
        await client.tags()
        request = self.requests[0]
        self.assertEqual(request.url.path, "/api/v4/tags")
        self.assertEqual(request.headers["x-client-id"], "client-id")
        self.assertEqual(request.headers["x-client-secret"], "client-secret")
        self.assertNotIn("authorization", request.headers)

    async def test_user_calls_add_the_bearer_token_beside_the_credentials(self):
        client = self.make(lambda request: json_response(200, {"id": 7, "handle": "alice"}), store=self.linked())
        self.assertEqual(await client.me(USER), {"id": 7, "handle": "alice"})
        request = self.requests[0]
        self.assertEqual(request.headers["authorization"], "Bearer access-1")
        self.assertEqual(request.headers["x-client-secret"], "client-secret")

    async def test_unconfigured_client_refuses_without_calling_out(self):
        client = self.make(lambda request: json_response(200, []), config=None)
        self.assertFalse(client.configured)
        with self.assertRaises(sc.SolvedacNotConfigured):
            await client.tags()
        self.assertEqual(self.requests, [])

    async def test_unlinked_user_is_reported(self):
        client = self.make(lambda request: json_response(200, {}))
        with self.assertRaises(sc.SolvedacNotLinked):
            await client.me(USER)
        self.assertEqual(self.requests, [])

    async def test_handles_cannot_escape_their_path_segment(self):
        client = self.make(lambda request: json_response(200, {}))
        await client.user_profile("../me")
        self.assertEqual(self.requests[0].url.raw_path, b"/api/v4/users/..%2Fme")


class RateLimitTests(ClientTestCase):
    async def test_429_waits_for_retry_after_and_retries(self):
        responses = iter([
            json_response(429, {}, {"retry-after": "3"}),
            json_response(200, [], {"x-ratelimit-limit": "600", "x-ratelimit-remaining": "5"}),
        ])
        client = self.make(lambda request: next(responses))
        with self.assertLogs("solvedac_client", "DEBUG") as logs:
            self.assertEqual(await client.tags(), [])
        self.assertEqual(self.slept, [3.0])
        self.assertTrue(any("5/600" in line and line.startswith("WARNING") for line in logs.output))

    async def test_gives_up_after_two_retries(self):
        client = self.make(lambda request: json_response(429, {}, {"retry-after": "1"}))
        with self.assertRaises(sc.SolvedacRateLimited) as caught:
            await client.tags()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.slept, [1.0, 1.0])
        self.assertEqual(caught.exception.retry_after, 1.0)

    async def test_long_waits_fail_fast_instead_of_holding_the_request(self):
        client = self.make(lambda request: json_response(429, {}, {"retry-after": "120"}))
        with self.assertRaises(sc.SolvedacRateLimited) as caught:
            await client.tags()
        self.assertEqual((len(self.requests), self.slept), (1, []))
        self.assertEqual(caught.exception.retry_after, 120.0)

    async def test_search_limits_are_checked_before_sending(self):
        client = self.make(lambda request: json_response(200, {}))
        with self.assertRaises(ValueError):
            await client.random_problem("a " * 9)
        with self.assertRaises(ValueError):
            await client.random_problem("x" * 101)
        self.assertEqual(self.requests, [])

    async def test_no_matching_random_problem_is_none(self):
        client = self.make(lambda request: json_response(404, {"error": {"code": "NOT_FOUND", "message": "none"}}))
        self.assertIsNone(await client.random_problem("tier:g1 tag:dp"))
        self.assertEqual(parse_qs(urlparse(str(self.requests[0].url)).query), {"q": ["tier:g1 tag:dp"]})


class TokenRefreshTests(ClientTestCase):
    def oauth_handler(self, api_responses, token_response=None):
        api = iter(api_responses)

        def handle(request):
            if request.url.path == "/api/v4/oauth/token":
                return token_response or json_response(200, token_body("access-2"))
            return next(api)
        return handle

    def token_requests(self):
        return [parse_qs(r.content.decode()) for r in self.requests if r.url.path == "/api/v4/oauth/token"]

    async def test_token_close_to_expiry_is_refreshed_before_the_call(self):
        store = self.linked(expires_at=NOW + 30)
        client = self.make(self.oauth_handler([json_response(200, {"id": 7, "handle": "alice"})]), store=store)
        await client.me(USER)
        self.assertEqual(self.token_requests(), [{"grant_type": ["refresh_token"], "refresh_token": ["refresh-1"]}])
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer access-2")
        # 갱신마다 새 refresh token이 오고, 예전 것은 다시 쓸 수 없으므로 새 것을 저장한다.
        self.assertEqual(store.tokens[USER].refresh_token, "refresh-2")
        self.assertEqual(store.tokens[USER].expires_at, NOW + 3600)

    async def test_401_refreshes_once_and_retries_the_call(self):
        store = self.linked()
        client = self.make(self.oauth_handler([
            json_response(401, {"error": {"code": "USER_TOKEN_REQUIRED", "message": "no"}}),
            json_response(200, {"id": 7, "handle": "alice"}),
        ]), store=store)
        self.assertEqual(await client.me(USER), {"id": 7, "handle": "alice"})
        self.assertEqual(len(self.token_requests()), 1)
        self.assertEqual(store.expired, [])

    async def test_rejected_refresh_marks_the_link_expired(self):
        store = self.linked(expires_at=NOW)
        client = self.make(self.oauth_handler([], json_response(400, {"error": "invalid_grant"})), store=store)
        with self.assertRaises(sc.SolvedacLinkExpired):
            await client.me(USER)
        self.assertEqual(store.expired, [USER])

    async def test_still_401_after_refresh_marks_the_link_expired(self):
        store = self.linked()
        unauthorized = json_response(401, {"error": {"code": "USER_TOKEN_REQUIRED", "message": "no"}})
        client = self.make(self.oauth_handler([unauthorized, unauthorized]), store=store)
        with self.assertRaises(sc.SolvedacLinkExpired):
            await client.me(USER)
        self.assertEqual(store.expired, [USER])

    async def test_expired_without_refresh_token_needs_relinking(self):
        store = self.linked(refresh=None, expires_at=NOW)
        client = self.make(self.oauth_handler([]), store=store)
        with self.assertRaises(sc.SolvedacLinkExpired):
            await client.me(USER)
        self.assertEqual((store.expired, self.token_requests()), ([USER], []))

    async def test_transient_refresh_failure_keeps_the_link(self):
        store = self.linked(expires_at=NOW)
        client = self.make(self.oauth_handler([], json_response(503, {})), store=store)
        with self.assertRaises(sc.SolvedacUnavailable):
            await client.me(USER)
        self.assertEqual(store.expired, [])

    async def test_search_limit_401_is_not_treated_as_a_bad_token(self):
        store = self.linked()
        refused = json_response(401, {"error": {"code": "SIGN_IN_REQUIRED", "message": "too long", "details": {"limit": "terms"}}})
        client = self.make(self.oauth_handler([refused]), store=store)
        with self.assertRaises(sc.SolvedacError) as caught:
            await client.random_problem("tier:g1", as_user=USER)
        self.assertEqual((caught.exception.code, caught.exception.details), ("SIGN_IN_REQUIRED", {"limit": "terms"}))
        self.assertEqual((self.token_requests(), store.expired), ([], []))

    async def test_concurrent_calls_spend_the_refresh_token_only_once(self):
        # 같은 refresh token을 두 번 내면 solved.ac가 로그인 전체를 끊는다.
        store = self.linked(expires_at=NOW)

        def handle(request):
            if request.url.path == "/api/v4/oauth/token":
                return json_response(200, token_body("access-2"))
            return json_response(200, {"id": 7, "handle": "alice"})

        for cache in (FakeRedis(), None):
            with self.subTest(lock="redis" if cache else "local"):
                store.tokens[USER] = sc.TokenSet("access-1", NOW, "profile", "refresh-1")
                client = self.make(handle, store=store, cache=cache)
                await asyncio.gather(*(client.me(USER) for _ in range(5)))
                self.assertEqual(len(self.token_requests()), 1)
                self.assertTrue(all(r.headers["authorization"] == "Bearer access-2"
                                    for r in self.requests if r.url.path == "/api/v4/me"))


class CacheTests(ClientTestCase):
    async def test_metadata_is_cached_for_a_day(self):
        cache = FakeRedis()
        client = self.make(lambda request: json_response(200, {"problemId": 1000}), cache=cache)
        self.assertEqual(await client.problem(1000), {"problemId": 1000})
        self.assertEqual(await client.problem(1000), {"problemId": 1000})
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(list(cache.ttl.values()), [24 * 60 * 60])

    async def test_user_stats_are_cached_per_viewer_for_fifteen_minutes(self):
        cache = FakeRedis()
        client = self.make(lambda request: json_response(200, {"handle": "alice"}), store=self.linked(), cache=cache)
        await client.user_profile("alice")
        await client.user_profile("alice", as_user=USER)
        await client.user_profile("alice", as_user=USER)
        # 비로그인 관점과 사용자 관점은 따로 캐시한다.
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(set(cache.ttl.values()), {15 * 60})

    async def test_errors_are_not_cached(self):
        responses = iter([json_response(404, {"error": {"code": "NOT_FOUND", "message": "x"}}), json_response(200, {"ok": 1})])
        client = self.make(lambda request: next(responses), cache=FakeRedis())
        with self.assertRaises(sc.SolvedacError):
            await client.problem(1)
        self.assertEqual(await client.problem(1), {"ok": 1})

    async def test_random_problems_are_never_cached(self):
        client = self.make(lambda request: json_response(200, {"problemId": 1}), cache=FakeRedis())
        await client.random_problem("tier:s1")
        await client.random_problem("tier:s1")
        self.assertEqual(len(self.requests), 2)

    async def test_broken_cache_does_not_break_calls(self):
        client = self.make(lambda request: json_response(200, []), cache=FakeRedis(broken=True))
        with self.assertLogs("solvedac_client", "WARNING"):
            self.assertEqual(await client.tags(), [])


class OAuthTests(ClientTestCase):
    def test_pkce_matches_the_rfc_7636_example(self):
        self.assertEqual(sc.code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        verifier, challenge = sc.new_pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(challenge, sc.code_challenge(verifier))

    def test_authorize_url_names_every_required_field(self):
        client = self.make(lambda request: json_response(200, {}))
        url = urlparse(client.authorize_url("state-1", "challenge-1"))
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", "https://solved.ac/oauth/authorize")
        self.assertEqual(parse_qs(url.query), {
            "response_type": ["code"], "client_id": ["client-id"], "redirect_uri": [CONFIG.redirect_uri],
            "scope": ["profile solves offline.access"], "state": ["state-1"],
            "code_challenge": ["challenge-1"], "code_challenge_method": ["S256"],
        })

    async def test_code_exchange_sends_the_verifier_and_redirect_uri(self):
        client = self.make(lambda request: json_response(200, token_body("access-1", "refresh-1")))
        tokens = await client.exchange_code("code-1", "verifier-1")
        self.assertEqual(tokens, sc.TokenSet("access-1", NOW + 3600, "profile solves offline.access", "refresh-1"))
        self.assertEqual(parse_qs(self.requests[0].content.decode()), {
            "grant_type": ["authorization_code"], "code": ["code-1"],
            "redirect_uri": [CONFIG.redirect_uri], "code_verifier": ["verifier-1"],
        })
        self.assertEqual(self.requests[0].headers["x-client-id"], "client-id")

    async def test_rejected_code_exchange_reports_the_oauth_error(self):
        client = self.make(lambda request: json_response(400, {"error": "invalid_grant", "error_description": "used"}))
        with self.assertRaises(sc.SolvedacError) as caught:
            await client.exchange_code("code-1", "verifier-1")
        self.assertEqual((caught.exception.status, caught.exception.code, caught.exception.message),
                         (400, "invalid_grant", "used"))

    async def test_revoke_posts_the_token(self):
        client = self.make(lambda request: json_response(200, {}))
        await client.revoke("refresh-1")
        self.assertEqual((self.requests[0].url.path, parse_qs(self.requests[0].content.decode())),
                         ("/api/v4/oauth/revoke", {"token": ["refresh-1"]}))


class ConfigTests(unittest.TestCase):
    def test_config_needs_all_three_values(self):
        from unittest.mock import patch
        names = dict(SOLVEDAC_CLIENT_ID="id", SOLVEDAC_CLIENT_SECRET="secret", SOLVEDAC_REDIRECT_URI="https://x/cb")
        with patch.dict("os.environ", names):
            self.assertEqual(sc.SolvedacConfig.from_env(), sc.SolvedacConfig("id", "secret", "https://x/cb"))
        with patch.dict("os.environ", {**names, "SOLVEDAC_CLIENT_SECRET": " "}):
            with self.assertLogs("solvedac_client", "WARNING") as logs:
                self.assertIsNone(sc.SolvedacConfig.from_env())
            self.assertIn("SOLVEDAC_CLIENT_SECRET", logs.output[0])
        with patch.dict("os.environ", {name: "" for name in names}):
            self.assertIsNone(sc.SolvedacConfig.from_env())


if __name__ == "__main__":
    unittest.main()
