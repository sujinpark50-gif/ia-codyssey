"""M4 인증·소유권·호출 제한·웹 화면 테스트. 실제 GPT·Firebase 호출 없음."""

import datetime as dt
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient as RawClient  # noqa: E402

from app.auth import FirebaseTokenVerifier  # noqa: E402
from app.config import Settings  # noqa: E402
from app.ratelimit import ChatRateLimiter, MemoryDailyCounter, RateLimited  # noqa: E402
from support import TestClient, create_app  # noqa: E402
from test_chat import FakeLLM  # noqa: E402

RUN = "B1x_slope_low_20261010_184532"
SECRET_KEY = "sk-test-SHOULDNEVERLEAK000111"
KEY_PATH = "C:/secret-dir/adair-service-account.json"

PROTECTED = [
    ("GET", "/api/data", None), ("GET", "/api/data/summary", None),
    ("GET", f"/api/data/{RUN}__ugv_speed_planar__1s__t00015__v1.0.0", None),
    ("POST", "/api/data", {"date": "2026-10-13T10:00:00+09:00", "value": 1.0}),
    ("PUT", "/api/data/manual-x", {"value": 1.0}), ("DELETE", "/api/data/manual-x", None),
    ("POST", "/api/chat", {"message": "평균은?"}),
    ("GET", "/api/conversations", None), ("POST", "/api/conversations", {}),
    ("GET", "/api/conversations/conv-" + "0" * 32, None), ("DELETE", "/api/conversations/conv-" + "0" * 32, None),
    ("GET", "/api/me", None),
]


def app_with(llm=None, **kw):
    s = Settings(**{"chat_rate_per_minute": 1000, "chat_rate_per_hour": 1000, **kw})
    return create_app(s, llm=llm or FakeLLM())


class AuthTest(unittest.TestCase):
    def test_no_token_blocked_everywhere(self):
        llm = FakeLLM()
        c = TestClient(app_with(llm), token=None)
        for method, path, body in PROTECTED:
            r = c.request(method, path, json=body)
            self.assertEqual(r.status_code, 401, (method, path))
            self.assertEqual(r.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(llm.calls, [])
        self.assertEqual(c.get("/health").status_code, 200)
        self.assertEqual(c.get("/api/config").status_code, 200)

    def test_bad_tokens(self):
        app = app_with()
        for token, status in (("garbage", 401), ("tok-outsider", 403), ("tok-unverified", 403)):
            r = TestClient(app, token=token).get("/api/data/summary")
            self.assertEqual(r.status_code, status, token)
        r = RawClient(app).get("/api/data", headers={"Authorization": "Basic abc"})
        self.assertEqual(r.status_code, 401)

    def test_no_allowed_emails_denies_all(self):
        from app.main import create_app as real_create_app
        from support import FakeVerifier
        app = real_create_app(Settings(), verifier=FakeVerifier(), llm=FakeLLM())
        self.assertEqual(TestClient(app).get("/api/data").status_code, 403)

    def test_auth_unconfigured_is_503_not_open(self):
        from app.main import create_app as real_create_app
        app = real_create_app(Settings(allowed_emails=("a@test.dev",)), llm=FakeLLM())  # FIREBASE_PROJECT_ID 없음
        r = TestClient(app).get("/api/data")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(RawClient(app).get("/api/data").status_code, 401)


class OwnershipTest(unittest.TestCase):
    def test_other_user_cannot_see_or_delete(self):
        llm = FakeLLM()
        app = app_with(llm)
        a, b = TestClient(app, token="tok-a"), TestClient(app, token="tok-b")
        cid = a.post("/api/chat", json={"message": "A의 질문"}).json()["conversation_id"]
        empty = a.post("/api/conversations", json={"title": "A 빈 대화"}).json()["id"]
        self.assertEqual(b.get("/api/conversations").json()["items"], [])
        for target in (cid, empty):
            self.assertEqual(b.get(f"/api/conversations/{target}").status_code, 404)
            self.assertEqual(b.delete(f"/api/conversations/{target}").status_code, 404)
        r = b.post("/api/chat", json={"message": "끼어들기", "conversation_id": cid})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(a.get(f"/api/conversations/{cid}").json()["message_count"], 2)
        self.assertEqual({i["id"] for i in a.get("/api/conversations").json()["items"]}, {cid, empty})
        bid = b.post("/api/chat", json={"message": "B의 질문"}).json()["conversation_id"]
        self.assertEqual([i["id"] for i in b.get("/api/conversations").json()["items"]], [bid])

    def test_quotas(self):
        llm = FakeLLM()
        c = TestClient(app_with(llm, max_conversations_per_user=2, max_messages_per_conversation=4))
        cid = c.post("/api/chat", json={"message": "1"}).json()["conversation_id"]
        c.post("/api/chat", json={"message": "2", "conversation_id": cid})
        r = c.post("/api/chat", json={"message": "3", "conversation_id": cid})
        self.assertEqual(r.status_code, 409)  # 대화당 메시지 4개 초과
        c.post("/api/conversations", json={})
        self.assertEqual(c.post("/api/conversations", json={}).status_code, 409)  # 대화 2개 초과
        self.assertEqual(c.post("/api/chat", json={"message": "새 대화"}).status_code, 409)
        self.assertEqual(len(llm.calls), 2)


class RateLimitTest(unittest.TestCase):
    def test_per_minute_api(self):
        llm = FakeLLM()
        c = TestClient(app_with(llm, chat_rate_per_minute=2))
        for _ in range(2):
            self.assertEqual(c.post("/api/chat", json={"message": "q"}).status_code, 200)
        r = c.post("/api/chat", json={"message": "q"})
        self.assertEqual(r.status_code, 429)
        self.assertGreaterEqual(int(r.headers["retry-after"]), 1)
        self.assertFalse(r.json()["saved"])
        self.assertEqual(len(llm.calls), 2)
        # 다른 사용자는 따로 센다
        self.assertEqual(TestClient(c.app, token="tok-b").post("/api/chat", json={"message": "q"}).status_code, 200)
        me = c.get("/api/me").json()["chat_limits"]
        self.assertEqual((me["used_last_minute"], me["per_minute"], me["used_today"]), (2, 2, 3))

    def test_limiter_windows_concurrency_daily(self):
        now = [1000.0]
        daily = MemoryDailyCounter()
        lim = ChatRateLimiter(per_minute=5, per_hour=6, max_concurrent=1, daily_limit=100, daily=daily,
                              clock=lambda: now[0])
        with lim.slot("u"):
            with self.assertRaises(RateLimited):  # 동시 요청 1개
                with lim.slot("u"):
                    pass
            with lim.slot("v"):  # 다른 사용자는 별도
                pass
        for _ in range(4):
            with lim.slot("u"):
                pass
        with self.assertRaises(RateLimited) as e:  # 분당 5회
            with lim.slot("u"):
                pass
        self.assertGreater(e.exception.retry_after_s, 0)
        now[0] += 61
        with lim.slot("u"):
            pass
        with self.assertRaises(RateLimited):  # 시간당 6회
            with lim.slot("u"):
                pass
        now[0] += 3600
        with lim.slot("u"):
            pass
        # 일일 상한: 공유 카운터 (재시작 후에도 같은 저장소면 유지)
        lim2 = ChatRateLimiter(per_minute=99, per_hour=99, max_concurrent=1, daily_limit=daily.get(_today()) + 1,
                               daily=daily, clock=lambda: now[0])
        with lim2.slot("w"):
            pass
        with self.assertRaises(RateLimited) as e:
            with lim2.slot("x"):
                pass
        self.assertIn("오늘 전체", e.exception.detail)

    def test_gpt_failure_still_counts_and_releases_slot(self):
        from app.llm import LLMError
        llm = FakeLLM(error=LLMError("x"))
        c = TestClient(app_with(llm, chat_daily_limit=1))
        self.assertEqual(c.post("/api/chat", json={"message": "q"}).status_code, 502)
        r = c.post("/api/chat", json={"message": "q"})
        self.assertEqual(r.status_code, 429)  # 실패한 호출도 일일 상한에 포함, 동시 슬롯은 풀림
        self.assertIn("오늘 전체", r.json()["detail"])


def _today():
    from app.ratelimit import kst_day
    return kst_day()


class RequestLimitTest(unittest.TestCase):
    def test_body_size(self):
        llm = FakeLLM()
        c = TestClient(app_with(llm))
        r = c.post("/api/chat", content=b'{"message": "' + b"a" * 20000 + b'"}', headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        r = c.post("/api/data", json={"date": "2026-10-13T10:00:00+09:00", "value": 1.0, "memo": "m" * 900})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(llm.calls, [])


class WebTest(unittest.TestCase):
    def setUp(self):
        s = Settings(openai_api_key=SECRET_KEY, openai_model="gpt-5.4", firebase_credentials=KEY_PATH,
                     firebase_project_id="demo-proj", firebase_web_api_key="AIza-public-web-key",
                     firebase_auth_domain="demo-proj.firebaseapp.com", firebase_web_app_id="1:2:web:3",
                     allowed_emails=("a@test.dev",), data_backend="memory")
        self.app = create_app(s, llm=FakeLLM())
        self.c = RawClient(self.app)

    def test_static_pages_and_no_secrets(self):
        pages = {}
        for path in ("/", "/index.html", "/app.js", "/styles.css", "/api/config"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            pages[path] = r.text
        self.assertIn("ADAIR Mission Intelligence", pages["/"])
        for path, text in pages.items():
            for secret in (SECRET_KEY, "SHOULDNEVERLEAK", KEY_PATH, "secret-dir", "private_key", "client_email"):
                self.assertNotIn(secret, text, (path, secret))
        cfg = self.c.get("/api/config").json()
        self.assertEqual(set(cfg), {"firebase", "metrics", "default_metric", "max_message_chars"})
        self.assertEqual(set(cfg["firebase"]), {"apiKey", "authDomain", "projectId", "appId"})
        self.assertEqual(cfg["metrics"]["ugv_speed_planar"]["unit"], "m/s")
        # 웹 코드는 Authorization 헤더로만 API 를 부르고, 키를 저장하지 않는다
        js = pages["/app.js"]
        self.assertIn("getIdToken", js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("OPENAI", js)

    def test_config_without_web_settings(self):
        cfg = RawClient(create_app(Settings(), llm=FakeLLM())).get("/api/config").json()
        self.assertIsNone(cfg["firebase"])

    def test_docs_toggle_and_cors_authorization(self):
        self.assertEqual(self.c.get("/docs").status_code, 200)
        off = RawClient(create_app(Settings(docs_enabled=False), llm=FakeLLM()))
        self.assertEqual(off.get("/docs").status_code, 404)
        self.assertEqual(off.get("/openapi.json").status_code, 404)
        app = create_app(Settings(cors_origins=("http://localhost:5500",)), llm=FakeLLM())
        r = RawClient(app).options("/api/chat", headers={"Origin": "http://localhost:5500",
                                                        "Access-Control-Request-Method": "POST",
                                                        "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("authorization", r.headers.get("access-control-allow-headers", "").lower())

    def test_rendering_escapes_html(self):
        js = self.c.get("/app.js").text
        self.assertIn("function esc(", js)
        self.assertIn("const inline = (s) => esc(s)", js)  # 답변 마크다운은 이스케이프 후 변환


class TokenVerifierTest(unittest.TestCase):
    """실제 RSA 서명 JWT 로 FirebaseTokenVerifier 검증 (네트워크 없음)."""

    @classmethod
    def setUpClass(cls):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
        cls.cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        cls.key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()

    def token(self, **over):
        from google.auth import crypt, jwt
        now = int(time.time())
        claims = {"iss": "https://securetoken.google.com/demo-proj", "aud": "demo-proj", "sub": "uid-a",
                  "user_id": "uid-a", "email": "a@test.dev", "email_verified": True,
                  "iat": now, "exp": now + 3600, "auth_time": now, **over}
        return jwt.encode(crypt.RSASigner.from_string(self.key_pem, key_id="k1"), claims).decode()

    def verifier(self):
        self.fetches = 0

        def fetch():
            self.fetches += 1
            return {"k1": self.cert_pem}, 3600.0
        return FirebaseTokenVerifier("demo-proj", fetch=fetch)

    def test_valid_and_invalid(self):
        v = self.verifier()
        self.assertEqual(v.verify(self.token())["email"], "a@test.dev")
        self.assertEqual(v.verify(self.token())["sub"], "uid-a")
        self.assertEqual(self.fetches, 1)  # 인증서 캐시
        now = int(time.time())
        for bad in ({"aud": "other-proj"}, {"iss": "https://securetoken.google.com/other"},
                    {"exp": now - 600, "iat": now - 4000}, {"sub": ""}, {"auth_time": now + 3600}):
            with self.assertRaises(ValueError, msg=str(bad)):
                v.verify(self.token(**bad))
        with self.assertRaises(ValueError):
            v.verify(self.token()[:-4] + "AAAA")  # 서명 변조

    def test_end_to_end_with_real_verifier(self):
        from app.main import create_app as real_create_app
        s = Settings(firebase_project_id="demo-proj", allowed_emails=("a@test.dev",))
        app = real_create_app(s, llm=FakeLLM(), verifier=self.verifier())
        ok = RawClient(app).get("/api/data/summary", headers={"Authorization": f"Bearer {self.token()}"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["count"], 435)
        other = RawClient(app).get("/api/data/summary",
                                   headers={"Authorization": f"Bearer {self.token(email='x@other.dev')}"})
        self.assertEqual(other.status_code, 403)

    def test_cert_fetch_failure_is_503(self):
        def fail():
            raise ConnectionError("down")
        from app.main import create_app as real_create_app
        app = real_create_app(Settings(firebase_project_id="demo-proj", allowed_emails=("a@test.dev",)),
                              llm=FakeLLM(), verifier=FirebaseTokenVerifier("demo-proj", fetch=fail))
        self.assertEqual(RawClient(app).get("/api/data", headers={"Authorization": "Bearer x.y.z"}).status_code, 503)


class ChatAnswerFormatTest(unittest.TestCase):
    def test_prompt_compact(self):
        llm = FakeLLM()
        TestClient(app_with(llm)).post("/api/chat", json={"message": "q"})
        system = llm.calls[0][0]["content"]
        self.assertLess(len(system), 1300)
        self.assertIn("3–6줄", system)
        self.assertNotIn("\n {", system)  # 들여쓴 JSON 없음


if __name__ == "__main__":
    unittest.main()
