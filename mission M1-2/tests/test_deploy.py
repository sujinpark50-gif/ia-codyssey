"""M5 배포 준비: Firestore 읽기 최적화, 보안 헤더, 운영 설정."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient as RawClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.importer import import_documents, load_documents  # noqa: E402
from app.repository import CachedRepository, InMemoryRepository  # noqa: E402
from support import TestClient, create_app  # noqa: E402
from test_chat import FakeLLM  # noqa: E402

RUN = "B1x_slope_low_20261010_184532"


class CountingRepo(InMemoryRepository):
    """Firestore 읽기 대신 '전체 조회 횟수 × 문서 수'를 센다."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def list(self, **kw):
        out = super().list(**kw)
        self.reads += len(out)
        return out

    def get(self, doc_id):
        self.reads += 1
        return super().get(doc_id)


def seeded():
    repo = CountingRepo()
    import_documents(repo, load_documents(Settings().seed_path))
    repo.reads = 0
    return repo


def initial_load(c):
    """web/app.js 의 첫 화면 요청 순서."""
    c.get("/api/me")
    c.get("/api/data/summary", params={"metric": "ugv_speed_planar"})
    c.get("/api/data", params={"metric": "ugv_speed_planar", "run_id": RUN, "limit": 1000})
    c.get("/api/data", params={"metric": "ugv_speed_planar", "run_id": RUN, "limit": 50, "offset": 0})
    c.get("/api/conversations")


class ReadOptimizationTest(unittest.TestCase):
    def test_initial_load_reads_once(self):
        before = seeded()
        initial_load(TestClient(create_app(Settings(summary_cache_ttl_s=0), repository=before, llm=FakeLLM())))
        after = seeded()
        c = TestClient(create_app(Settings(summary_cache_ttl_s=60), repository=after, llm=FakeLLM()))
        initial_load(c)
        self.assertEqual(before.reads, 435 * 3)  # 캐시 없음: summary + 그래프 + 표
        self.assertEqual(after.reads, 435)       # 스냅샷 1회
        for off in (50, 100, 150):                # 페이지 이동은 추가 읽기 없음
            c.get("/api/data", params={"metric": "ugv_speed_planar", "run_id": RUN, "limit": 50, "offset": off})
        self.assertEqual(after.reads, 435)

    def test_writes_invalidate_and_stats_stay_exact(self):
        repo = seeded()
        c = TestClient(create_app(Settings(summary_cache_ttl_s=600), repository=repo, llm=FakeLLM()))
        s0 = c.get("/api/data/summary").json()
        d = c.post("/api/data", json={"date": "2026-10-13T10:00:00+09:00", "value": 10.0, "run_id": RUN,
                                       "sim_time_s": 450}).json()
        s1 = c.get("/api/data/summary").json()
        self.assertEqual((s1["count"], s1["max"]), (436, 10.0))
        self.assertEqual(c.get(f"/api/data/{d['id']}").json()["value"], 10.0)
        c.put(f"/api/data/{d['id']}", json={"value": 1.0})
        self.assertEqual(c.get("/api/data/summary").json()["max"], s0["max"])
        c.delete(f"/api/data/{d['id']}")
        s3 = c.get("/api/data/summary").json()
        self.assertEqual((s3["count"], s3["mean"], s3["min"], s3["max"]), (435, 4.896189, 0.0, 5.808594))
        self.assertEqual(s3["recent_comparison"], s0["recent_comparison"])

    def test_external_change_after_ttl(self):
        inner = seeded()
        now = [0.0]
        cached = CachedRepository(inner, ttl_s=60, clock=lambda: now[0])
        self.assertEqual(len(cached.list()), 435)
        inner.create("manual-ext", {**inner.list()[0][1], "record_type": "manual", "sim_time_s": None})
        self.assertEqual(len(cached.list()), 435)  # TTL 안: 바깥 변경은 아직 안 보임 (문서화된 동작)
        now[0] = 61
        self.assertEqual(len(cached.list()), 436)


class SecurityHeaderTest(unittest.TestCase):
    def test_headers_on_api_and_static(self):
        c = RawClient(create_app(Settings(), llm=FakeLLM()))
        for path in ("/", "/app.js", "/api/config", "/health", "/api/data"):
            h = c.get(path).headers
            self.assertEqual(h.get("x-content-type-options"), "nosniff", path)
            self.assertEqual(h.get("x-frame-options"), "DENY", path)
            self.assertEqual(h.get("referrer-policy"), "strict-origin-when-cross-origin", path)

    def test_production_settings(self):
        app = create_app(Settings(docs_enabled=False), llm=FakeLLM())
        c = RawClient(app)
        self.assertEqual(c.get("/docs").status_code, 404)
        self.assertEqual(c.get("/openapi.json").status_code, 404)
        self.assertEqual(c.get("/health").json(), {"status": "ok"})  # 헬스체크는 내부 정보 없음


class DeployFilesTest(unittest.TestCase):
    def test_render_blueprint(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        for needle in ("uvicorn app.main:create_app --factory", "--host 0.0.0.0", "$PORT", "healthCheckPath: /health",
                       "DOCS_ENABLED", "GOOGLE_APPLICATION_CREDENTIALS", "/etc/secrets/"):
            self.assertIn(needle, text, needle)
        for secret in ("sk-", "AIza", "private_key", "BEGIN PRIVATE"):
            self.assertNotIn(secret, text)

    def test_firestore_rules_deny_all(self):
        rules = (ROOT / "firestore.rules").read_text(encoding="utf-8")
        self.assertIn("allow read, write: if false;", rules)


if __name__ == "__main__":
    unittest.main()
