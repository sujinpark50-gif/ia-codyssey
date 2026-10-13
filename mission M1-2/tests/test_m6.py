"""M6: Vercel 프론트엔드 → Render API 직접 호출 (CORS), Swagger 공개 정책, 최근 추세, 정적 파일 보안.

node 가 있으면 web/config.js·build.mjs 를 실제로 실행해 검증하고, 없으면 해당 테스트는 건너뛴다(UNVERIFIED).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient as RawClient  # noqa: E402

from app.config import Settings, cors_origins  # noqa: E402
from support import TestClient, create_app  # noqa: E402
from test_chat import FakeLLM  # noqa: E402

VERCEL = "https://adair-mission-intelligenceweb.vercel.app"
RENDER = "https://adair-mission-intelligence.onrender.com"
NODE = shutil.which("node")
SK = re.compile(r"sk-[A-Za-z0-9_\-]{10,}")  # GPT 키 형태 (단어 속 "sk-" 오탐 방지)


def app_with(**kw):
    return create_app(Settings(**{"cors_origins": (VERCEL,), **kw}), llm=FakeLLM())


class CorsTest(unittest.TestCase):
    def setUp(self):
        self.app = app_with()

    def preflight(self, origin, method="POST", headers="authorization,content-type"):
        return RawClient(self.app).options("/api/chat", headers={
            "Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers})

    def test_allowed_origin_preflight(self):
        for method in ("GET", "POST", "PUT", "DELETE"):
            r = self.preflight(VERCEL, method)
            self.assertEqual(r.status_code, 200, method)
            self.assertEqual(r.headers["access-control-allow-origin"], VERCEL)
            self.assertIn("authorization", r.headers["access-control-allow-headers"].lower())
            self.assertIn(method, r.headers["access-control-allow-methods"])
        self.assertNotIn("access-control-allow-credentials", self.preflight(VERCEL).headers)  # 쿠키 안 씀

    def test_disallowed_origin(self):
        for origin in ("https://evil.example", "https://adair-mission-intelligenceweb-git-x.vercel.app",
                       "http://adair-mission-intelligenceweb.vercel.app"):
            r = self.preflight(origin)
            self.assertNotEqual(r.headers.get("access-control-allow-origin"), origin, origin)
            g = TestClient(self.app).get("/api/data/summary", headers={"Origin": origin})
            self.assertIsNone(g.headers.get("access-control-allow-origin"), origin)

    def test_cross_origin_requests_keep_auth(self):
        c = TestClient(self.app)
        h = {"Origin": VERCEL}
        r = c.get("/api/data/summary", headers=h)
        self.assertEqual((r.status_code, r.headers["access-control-allow-origin"]), (200, VERCEL))
        self.assertEqual(r.json()["count"], 435)
        d = c.post("/api/data", json={"date": "2026-10-13T10:00:00+09:00", "value": 1.0}, headers=h)
        self.assertEqual((d.status_code, d.headers["access-control-allow-origin"]), (201, VERCEL))
        u = c.put(f"/api/data/{d.json()['id']}", json={"value": 2.0}, headers=h)
        self.assertEqual(u.status_code, 200)
        self.assertEqual(c.delete(f"/api/data/{d.json()['id']}", headers=h).status_code, 204)
        # 토큰 없음 → 401 이지만 브라우저가 읽을 수 있게 CORS 헤더는 붙는다
        anon = TestClient(self.app, token=None).get("/api/data", headers=h)
        self.assertEqual((anon.status_code, anon.headers["access-control-allow-origin"]), (401, VERCEL))
        # 미들웨어가 막는 413 에도 CORS 헤더 (CORS 가 가장 바깥)
        big = c.post("/api/chat", content=b'{"message":"' + b"a" * 20000 + b'"}',
                     headers={**h, "Content-Type": "application/json"})
        self.assertEqual((big.status_code, big.headers.get("access-control-allow-origin")), (413, VERCEL))

    def test_render_same_origin_unchanged(self):
        # Origin 헤더 없는 같은 출처 요청 (Render 화면) 은 그대로
        r = TestClient(self.app).get("/api/data/summary")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(RawClient(self.app).get("/").status_code, 200)

    def test_env_names(self):
        self.assertEqual(cors_origins(f"{VERCEL}/, http://localhost:5500", None), (VERCEL, "http://localhost:5500"))
        self.assertEqual(cors_origins(None, VERCEL), (VERCEL,))
        self.assertEqual(cors_origins(VERCEL, VERCEL), (VERCEL,))
        for bad in ((VERCEL, "https://other.example"), ("*", None)):
            with self.assertRaises(ValueError):
                cors_origins(*bad)
        keys = ("ALLOWED_ORIGINS", "CORS_ORIGINS")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.pop("CORS_ORIGINS", None)
            os.environ["ALLOWED_ORIGINS"] = VERCEL
            self.assertEqual(Settings.from_env(env_file=None).cors_origins, (VERCEL,))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class SwaggerTest(unittest.TestCase):
    def setUp(self):
        s = Settings(docs_enabled=True, openai_api_key="sk-test-SWAGGERLEAK123", firebase_credentials="C:/k/sa.json",
                     firebase_project_id="demo-proj", firebase_web_api_key="AIza-public", allowed_emails=("a@test.dev",))
        self.app = create_app(s, llm=FakeLLM())
        self.c = RawClient(self.app)

    def test_docs_public_api_still_protected(self):
        self.assertEqual(self.c.get("/docs").status_code, 200)
        spec = self.c.get("/openapi.json")
        self.assertEqual(spec.status_code, 200)
        text = spec.text
        for leak in ("sk-test", "SWAGGERLEAK", "sa.json", "C:/k", "a@test.dev", "AIza", "private_key", "demo-proj"):
            self.assertNotIn(leak, text, leak)
        doc = spec.json()
        self.assertEqual(doc["components"]["securitySchemes"]["FirebaseIdToken"],
                         {"type": "http", "scheme": "bearer",
                          "description": "Firebase Authentication 으로 로그인해 받은 ID 토큰 (허용된 계정만)"})
        protected = [(p, m) for p, ops in doc["paths"].items() for m, op in ops.items()
                     if p.startswith("/api/") and p != "/api/config"]
        self.assertGreaterEqual(len(protected), 10)
        for p, m in protected:  # 모든 보호 API 에 Bearer 표시
            self.assertIn({"FirebaseIdToken": []}, doc["paths"][p][m].get("security", []), (p, m))
        self.assertNotIn("security", doc["paths"]["/api/config"]["get"])
        # 문서가 열려도 실제 호출은 토큰 검증 (Swagger "Try it out" 에 엉터리 토큰을 넣는 경우와 같다)
        for method, path in (("GET", "/api/data"), ("GET", "/api/data/summary"), ("POST", "/api/chat"),
                             ("GET", "/api/conversations")):
            self.assertEqual(self.c.request(method, path, json={} if method == "POST" else None).status_code, 401)
            r = self.c.request(method, path, headers={"Authorization": "Bearer not-a-real-token"},
                               json={} if method == "POST" else None)
            self.assertIn(r.status_code, (401, 503))  # 503: 테스트 환경에서 공개 인증서를 못 받는 경우

    def test_docs_off_by_setting(self):
        off = RawClient(create_app(Settings(docs_enabled=False), llm=FakeLLM()))
        self.assertEqual((off.get("/docs").status_code, off.get("/openapi.json").status_code), (404, 404))


class TrendTest(unittest.TestCase):
    def test_summary_trend_and_prompt(self):
        llm = FakeLLM()
        c = TestClient(create_app(Settings(chat_rate_per_minute=100), llm=llm))
        rc = c.get("/api/data/summary").json()["recent_comparison"]
        self.assertEqual(rc["trend"], "감소")
        self.assertAlmostEqual(rc["change_pct"], round(-0.728445 / 5.279502 * 100, 2), places=2)
        c.post("/api/chat", json={"message": "추세는?"})
        self.assertIn("추세 감소", llm.calls[0][0]["content"])
        self.assertIn("(-13.8%)", llm.calls[0][0]["content"])

    def test_trend_rules(self):
        from app.service import TREND_FLAT_PCT
        self.assertEqual(TREND_FLAT_PCT, 1.0)


@unittest.skipUnless(NODE, "node 없음 — web JS 실행 검증은 UNVERIFIED")
class WebBuildTest(unittest.TestCase):
    def run_node(self, code, cwd):
        r = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=cwd, capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def build(self, env):
        tmp = Path(tempfile.mkdtemp())
        for f in WEB.iterdir():
            if f.is_file():
                shutil.copy(f, tmp / f.name)
        r = subprocess.run([NODE, "build.mjs"], cwd=tmp, capture_output=True, text=True, timeout=30,
                           env={**os.environ, **env})
        return tmp, r

    def test_same_origin_default(self):
        out = self.run_node("import {apiUrl, API_BASE} from './config.js'; console.log(JSON.stringify([API_BASE, apiUrl('/api/me')]))", WEB)
        self.assertEqual(json.loads(out), ["", "/api/me"])

    def test_vercel_build_uses_env(self):
        tmp, r = self.build({"API_BASE_URL": RENDER + "/", "VERCEL_ENV": "production"})
        self.assertEqual(r.returncode, 0, r.stderr)
        dist = tmp / "dist"
        self.assertEqual(sorted(p.name for p in dist.iterdir()),
                         ["app.js", "config.js", "env-config.js", "index.html", "styles.css"])
        out = self.run_node("import {apiUrl} from './config.js'; console.log(apiUrl('/api/data/summary'))", dist)
        self.assertEqual(out, RENDER + "/api/data/summary")
        for f in dist.iterdir():  # 빌드 결과에 비밀값 없음
            t = f.read_text(encoding="utf-8")
            self.assertIsNone(SK.search(t), f.name)
            for leak in ("private_key", "BEGIN PRIVATE", "GOOGLE_APPLICATION_CREDENTIALS", "OPENAI_API_KEY"):
                self.assertNotIn(leak, t, (f.name, leak))

    def test_build_rejects_bad_or_missing_url(self):
        for env in ({"API_BASE_URL": "http://insecure.example"}, {"API_BASE_URL": RENDER + "/api?x=1"},
                    {"API_BASE_URL": "", "VERCEL_ENV": "production"}):
            _, r = self.build(env)
            self.assertNotEqual(r.returncode, 0, env)
        _, r = self.build({"API_BASE_URL": "", "VERCEL_ENV": "preview"})  # Preview 는 같은 출처(정적 확인용)
        self.assertEqual(r.returncode, 0, r.stderr)


class StaticFrontendTest(unittest.TestCase):
    def test_single_api_entry_and_ux(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('import { apiUrl } from "./config.js"', js)
        self.assertEqual(js.count("fetch("), 1)                 # 모든 요청이 request() 한 곳을 거친다
        self.assertIn("fetch(apiUrl(path)", js)
        self.assertIn("Authorization: `Bearer ${token}`", js)  # 토큰 전달 유지
        self.assertIn('request("/api/config")', js)             # 로그인 초기화 전에 서버 설정 필요
        self.assertIn("WAKE_HINT_MS", js)                       # 콜드 스타트 안내
        self.assertIn("e.status === 0", js)                     # 네트워크 오류와
        self.assertIn("e.status === 401", js)                   # 인증 오류 구분
        self.assertNotIn("FIREBASE_WEB_API_KEY", js)          # 사용자 화면에 서버 설정 이름 노출 안 함
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="wake"', html)
        self.assertIn("서버를 깨우는 중", html)

    def test_vercel_json_static_only(self):
        cfg = json.loads((WEB / "vercel.json").read_text(encoding="utf-8"))
        self.assertNotIn("rewrites", cfg)  # B안: 프록시 없음, 브라우저가 Render 를 직접 호출
        names = {h["key"] for h in cfg["headers"][0]["headers"]}
        self.assertEqual(names, {"X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy"})
        self.assertNotIn("Content-Security-Policy", names)  # gstatic SDK·Google 팝업·Render 호출을 막지 않도록

    def test_no_secrets_in_web(self):
        for f in WEB.iterdir():
            if f.is_file():
                t = f.read_text(encoding="utf-8")
                self.assertIsNone(SK.search(t), f.name)
                for leak in ("private_key", "BEGIN PRIVATE", "AIza", "client_email"):
                    self.assertNotIn(leak, t, (f.name, leak))

    def test_render_serves_new_files(self):
        c = RawClient(create_app(Settings(), llm=FakeLLM()))
        for path in ("/", "/app.js", "/config.js", "/env-config.js"):
            self.assertEqual(c.get(path).status_code, 200, path)
        self.assertIn('API_BASE_URL = ""', c.get("/env-config.js").text)  # Render 화면은 같은 출처


class RenderYamlTest(unittest.TestCase):
    def test_cors_and_docs(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("key: ALLOWED_ORIGINS", text)
        self.assertIn(VERCEL, text)
        self.assertNotIn('value: "*"', text)


if __name__ == "__main__":
    unittest.main()
