"""solved.ac 연동 흐름. 가짜 solved.ac, 가짜 Redis, 메모리 저장소로 실제 라우터를 돌린다."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

import solvedac_client as sc
import solvedac_link as sl

CONFIG = sc.SolvedacConfig("client-id", "client-secret", "https://loj.example/api/integrations/solvedac/callback")
FAR = 4_000_000_000.0


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttl = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttl[key] = ex

    async def get(self, key):
        return self.data.get(key)

    async def getdel(self, key):
        return self.data.pop(key, None)


class MemoryStore:
    """SupabaseTokenStore와 같은 규칙을 메모리에서 지킨다."""

    def __init__(self):
        self.rows = {}

    async def account(self, user_id):
        row = self.rows.get(user_id)
        return None if row is None else {k: row[k] for k in ("solvedac_user_id", "handle", "scopes", "status", "linked_at")}

    async def load(self, user_id):
        row = self.rows.get(user_id)
        return row["tokens"] if row and row["status"] == "active" else None

    async def save(self, user_id, tokens):
        self.rows[user_id].update(tokens=tokens, status="active")

    async def mark_expired(self, user_id):
        self.rows[user_id]["status"] = "expired"

    async def link(self, user_id, solvedac_user_id, handle, tokens):
        if any(uid != user_id and row["solvedac_user_id"] == solvedac_user_id for uid, row in self.rows.items()):
            raise sl.AlreadyLinked()
        self.rows[user_id] = {"solvedac_user_id": solvedac_user_id, "handle": handle, "scopes": tokens.scopes,
                              "status": "active", "linked_at": "2026-11-18T00:00:00+00:00", "tokens": tokens}

    async def delete(self, user_id):
        self.rows.pop(user_id, None)


class FakeSolvedac:
    """code마다 어떤 solved.ac 계정이 승인했는지 정해 두고 그대로 답한다."""

    def __init__(self):
        self.accounts = {"code-alice": (7, "alice"), "code-bob": (8, "bob")}
        self.issued = {}
        self.revoked = []
        self.requests = []
        self.token_status = 200

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v4/oauth/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            access = f"access-{code}-{len(self.issued)}"
            self.issued[access] = self.accounts[code]
            return httpx.Response(200, json={"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                                             "refresh_token": f"refresh-{access}", "scope": "profile solves offline.access"})
        if path == "/api/v4/me":
            account_id, handle = self.issued[request.headers["authorization"].removeprefix("Bearer ")]
            return httpx.Response(200, json={"id": account_id, "handle": handle})
        if path == "/api/v4/oauth/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200, json={})
        if path.startswith("/api/v4/users/"):
            return httpx.Response(200, json={"handle": path.rsplit("/", 1)[1], "tier": 13, "rating": 1234, "solvedCount": 321})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": path}})

    def oauth_requests(self, path):
        return [r for r in self.requests if r.url.path == path]


def current_user(x_test_user: str = Header(default="")):
    if not x_test_user:
        raise HTTPException(401, "로그인이 필요합니다.")
    return x_test_user


class LinkFlowTests(unittest.TestCase):
    def setUp(self):
        self.solvedac = FakeSolvedac()
        self.redis = FakeRedis()
        self.store = MemoryStore()
        self.client = self.make_client()

    def make_client(self, config=CONFIG, store="default"):
        http = httpx.AsyncClient(base_url=sc.BASE_URL, transport=httpx.MockTransport(self.solvedac))
        store = self.store if store == "default" else store
        api = sc.SolvedacClient(config, store=store, http=http)
        app = FastAPI()
        app.include_router(sl.create_router(client=api, store=store, redis=self.redis, current_user=current_user))
        return TestClient(app, base_url="https://loj.example", follow_redirects=False)

    def start(self, user="user-1", client=None):
        client = client or self.client
        response = client.post("/integrations/solvedac/start", headers={"x-test-user": user})
        self.assertEqual(response.status_code, 200)
        query = parse_qs(urlsplit(response.json()["authorize_url"]).query)
        return query["state"][0], query

    def callback(self, state, code="code-alice", client=None, **extra):
        client = client or self.client
        response = client.get("/integrations/solvedac/callback", params={"state": state, "code": code, **extra})
        self.assertEqual(response.status_code, 303)
        location = urlsplit(response.headers["location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", "https://loj.example/settings")
        return parse_qs(location.query)["solvedac"][0]

    def test_start_requires_a_loj_login(self):
        self.assertEqual(self.client.post("/integrations/solvedac/start").status_code, 401)

    def test_start_hands_out_a_pkce_authorize_url_and_binds_the_browser(self):
        response = self.client.post("/integrations/solvedac/start", headers={"x-test-user": "user-1"})
        query = parse_qs(urlsplit(response.json()["authorize_url"]).query)
        self.assertEqual((query["code_challenge_method"], query["redirect_uri"]), (["S256"], [CONFIG.redirect_uri]))
        self.assertEqual(query["scope"], ["profile solves offline.access"])
        saved = json.loads(self.redis.data["solvedac:oauth:" + query["state"][0]])
        self.assertEqual(saved["user_id"], "user-1")
        self.assertEqual(sc.code_challenge(saved["verifier"]), query["code_challenge"][0])
        self.assertEqual(self.redis.ttl["solvedac:oauth:" + query["state"][0]], 600)
        cookie = response.headers["set-cookie"].lower()
        for flag in ("httponly", "secure", "samesite=lax", "max-age=600"):
            self.assertIn(flag, cookie)

    def test_full_link_then_status_then_unlink(self):
        state, _ = self.start()
        self.assertEqual(self.callback(state), "linked")
        self.assertEqual(self.store.rows["user-1"]["solvedac_user_id"], 7)
        # 코드 교환에 PKCE verifier를 실어 보냈다.
        exchange = parse_qs(self.solvedac.oauth_requests("/api/v4/oauth/token")[0].content.decode())
        self.assertEqual(exchange["grant_type"], ["authorization_code"])
        self.assertIn("code_verifier", exchange)

        status = self.client.get("/integrations/solvedac", headers={"x-test-user": "user-1"}).json()
        self.assertEqual({k: status[k] for k in ("linked", "handle", "status", "tier", "rating", "solved_count")},
                         {"linked": True, "handle": "alice", "status": "active", "tier": 13, "rating": 1234, "solved_count": 321})
        # 프로필은 연동한 사용자의 토큰으로 불렀다.
        profile_call = [r for r in self.solvedac.requests if r.url.path == "/api/v4/users/alice"][0]
        self.assertTrue(profile_call.headers["authorization"].startswith("Bearer access-code-alice"))

        refresh = self.store.rows["user-1"]["tokens"].refresh_token
        response = self.client.delete("/integrations/solvedac", headers={"x-test-user": "user-1"})
        self.assertEqual(response.json(), {"configured": True, "linked": False})
        self.assertEqual((self.solvedac.revoked, self.store.rows), ([refresh], {}))

    def test_state_works_only_once(self):
        state, _ = self.start()
        self.assertEqual(self.callback(state), "linked")
        self.assertEqual(self.callback(state), "expired")
        self.assertEqual(len(self.solvedac.oauth_requests("/api/v4/oauth/token")), 1)

    def test_unknown_state_is_refused(self):
        self.assertEqual(self.callback("made-up"), "expired")
        self.assertEqual(self.solvedac.requests, [])

    def test_link_finished_in_another_browser_is_refused(self):
        # 공격자가 자기 state가 든 인증 링크를 피해자에게 보낸 상황: 피해자 브라우저에는 쿠키가 없다.
        state, _ = self.start(user="attacker")
        victim = self.make_client()
        self.assertEqual(self.callback(state, client=victim), "browser")
        self.assertEqual((self.store.rows, self.solvedac.oauth_requests("/api/v4/oauth/token")), ({}, []))

    def test_user_declining_on_solvedac_is_reported(self):
        state, _ = self.start()
        self.assertEqual(self.callback(state, code="", error="access_denied"), "denied")
        self.assertEqual(self.store.rows, {})

    def test_failed_code_exchange_is_reported(self):
        self.solvedac.token_status = 400
        state, _ = self.start()
        self.assertEqual(self.callback(state), "failed")
        self.assertEqual(self.store.rows, {})

    def test_one_solvedac_account_links_to_one_loj_account(self):
        state, _ = self.start(user="user-1")
        self.assertEqual(self.callback(state), "linked")
        other = self.make_client()
        state, _ = self.start(user="user-2", client=other)
        self.assertEqual(self.callback(state, client=other), "taken")
        self.assertNotIn("user-2", self.store.rows)
        # 쓰지 못하게 된 새 토큰은 바로 끝낸다.
        self.assertEqual(len(self.solvedac.revoked), 1)
        self.assertNotEqual(self.solvedac.revoked[0], self.store.rows["user-1"]["tokens"].refresh_token)

    def test_relinking_replaces_and_revokes_the_previous_sign_in(self):
        state, _ = self.start()
        self.callback(state, code="code-alice")
        old_refresh = self.store.rows["user-1"]["tokens"].refresh_token
        state, _ = self.start()
        self.assertEqual(self.callback(state, code="code-bob"), "linked")
        self.assertEqual((self.store.rows["user-1"]["handle"], self.solvedac.revoked), ("bob", [old_refresh]))

    def test_expired_link_is_shown_without_using_its_token(self):
        state, _ = self.start()
        self.callback(state)
        self.store.rows["user-1"]["status"] = "expired"
        status = self.client.get("/integrations/solvedac", headers={"x-test-user": "user-1"}).json()
        self.assertEqual((status["status"], status["tier"]), ("expired", 13))
        profile_call = [r for r in self.solvedac.requests if r.url.path == "/api/v4/users/alice"][-1]
        self.assertNotIn("authorization", profile_call.headers)

    def test_not_linked_status(self):
        status = self.client.get("/integrations/solvedac", headers={"x-test-user": "user-1"}).json()
        self.assertEqual(status, {"configured": True, "linked": False})

    def test_everything_is_off_without_settings(self):
        for client in (self.make_client(config=None), self.make_client(store=None)):
            with self.subTest():
                self.assertEqual(client.get("/integrations/solvedac", headers={"x-test-user": "u"}).json(),
                                 {"configured": False, "linked": False})
                self.assertEqual(client.post("/integrations/solvedac/start", headers={"x-test-user": "u"}).status_code, 503)


class SupabaseStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MagicMock()
        self.query = self.db.table.return_value
        for method in ("select", "eq", "limit", "update", "upsert", "delete"):
            getattr(self.query, method).return_value = self.query
        self.cipher = sl.TokenCipher([Fernet.generate_key().decode()])
        self.store = sl.SupabaseTokenStore(self.db, self.cipher)
        self.tokens = sc.TokenSet("access-secret", 1_800_000_000.0, "profile solves", "refresh-secret")

    async def test_tokens_are_encrypted_at_rest_and_read_back(self):
        self.query.execute.return_value = SimpleNamespace(data=[])
        await self.store.link("user-1", 7, "alice", self.tokens)
        row = self.query.upsert.call_args.args[0]
        self.assertNotIn("secret", json.dumps(row))
        self.assertEqual(self.query.upsert.call_args.kwargs, {"on_conflict": "user_id"})

        self.query.execute.return_value = SimpleNamespace(data=[{**row}])
        self.assertEqual(await self.store.load("user-1"), self.tokens)

    async def test_account_owned_by_someone_else_is_refused(self):
        self.query.execute.return_value = SimpleNamespace(data=[{"user_id": "user-2"}])
        with self.assertRaises(sl.AlreadyLinked):
            await self.store.link("user-1", 7, "alice", self.tokens)
        self.query.upsert.assert_not_called()

    async def test_unique_violation_from_a_race_is_already_linked(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[]), type("APIError", (Exception,), {"code": "23505"})()]
        with self.assertRaises(sl.AlreadyLinked):
            await self.store.link("user-1", 7, "alice", self.tokens)

    async def test_expired_rows_are_not_used(self):
        self.query.execute.return_value = SimpleNamespace(data=[{"status": "expired"}])
        self.assertIsNone(await self.store.load("user-1"))

    async def test_tokens_from_a_lost_key_expire_the_link(self):
        other = sl.TokenCipher([Fernet.generate_key().decode()])
        row = {"access_token_enc": other.encrypt("a"), "refresh_token_enc": None, "scopes": "",
               "expires_at": "2027-01-01T00:00:00+00:00", "status": "active"}
        self.query.execute.return_value = SimpleNamespace(data=[row])
        with self.assertLogs("solvedac_link", "ERROR"):
            self.assertIsNone(await self.store.load("user-1"))
        self.assertEqual(self.query.update.call_args.args[0]["status"], "expired")

    def test_key_rotation_reads_old_tokens(self):
        old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        stored = sl.TokenCipher([old_key]).encrypt("token")
        self.assertEqual(sl.TokenCipher([new_key, old_key]).decrypt(stored), "token")

    def test_bad_key_turns_the_feature_off(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"SOLVEDAC_TOKEN_KEY": "not-a-key"}), self.assertLogs("solvedac_link", "ERROR"):
            self.assertIsNone(sl.TokenCipher.from_env())
        with patch.dict("os.environ", {"SOLVEDAC_TOKEN_KEY": ""}):
            self.assertIsNone(sl.TokenCipher.from_env())


if __name__ == "__main__":
    unittest.main()
