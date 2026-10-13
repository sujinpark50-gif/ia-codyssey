"""M2 API 자동 테스트. Firestore 없이 메모리 저장소와 가짜 클라이언트로 검증한다.

실행: .venv/Scripts/python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from support import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.importer import import_documents, load_documents  # noqa: E402
from support import create_app  # noqa: E402
from app.repository import (DataRepository, DocumentExists, FirestoreRepository,  # noqa: E402
                            InMemoryRepository, RepositoryUnavailable)

SEED = ROOT / "data/ugv_speed_1s.json"
IMPORTED_ID = "B1x_slope_low_20261010_184532__ugv_speed_planar__1s__t00015__v1.0.0"
RUN = "B1x_slope_low_20261010_184532"
# M1-2 기준값 (docs/DATA_VALIDATION.md)
BASE = {"count": 435, "mean": 4.896189, "min": 0.0, "max": 5.808594, "recent": 4.551057, "previous": 5.279502}

MANUAL = {"date": "2026-10-13T10:00:00+09:00", "value": 3.5, "memo": "수동 입력 테스트"}


def client(**kw) -> TestClient:
    return TestClient(create_app(Settings(**kw)))


class DataApiTest(unittest.TestCase):
    def setUp(self):
        self.c = client()

    def create(self, **over):
        r = self.c.post("/api/data", json={**MANUAL, **over})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    # 1
    def test_01_create(self):
        d = self.create()
        self.assertTrue(d["id"].startswith("manual-"))
        self.assertEqual(d["record_type"], "manual")
        self.assertEqual(d["date"], "2026-10-13T10:00:00.000+09:00")
        self.assertEqual(d["value"], 3.5)
        self.assertEqual(self.c.get(f"/api/data/{d['id']}").json(), d)
        # UTC로 보내도 같은 시점을 KST로 돌려준다
        u = self.create(date="2026-10-13T01:00:00Z")
        self.assertEqual(u["date"], "2026-10-13T10:00:00.000+09:00")

    # 2
    def test_02_list(self):
        r = self.c.get("/api/data", params={"limit": 10, "offset": 5})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 435)
        self.assertEqual(len(body["items"]), 10)
        self.assertEqual(body["items"][0]["sim_time_s"], 20)
        dates = [i["date"] for i in self.c.get("/api/data", params={"limit": 1000}).json()["items"]]
        self.assertEqual(dates, sorted(dates))
        self.create()
        self.assertEqual(self.c.get("/api/data", params={"record_type": "manual"}).json()["total"], 1)
        self.assertEqual(self.c.get("/api/data", params={"run_id": RUN}).json()["total"], 435)
        self.assertEqual(self.c.get("/api/data", params={"metric": "nope"}).json()["total"], 0)

    # 3
    def test_03_update(self):
        d = self.create()
        r = self.c.put(f"/api/data/{d['id']}", json={"value": 4.25, "memo": "수정됨"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["value"], r.json()["memo"], r.json()["date"]), (4.25, "수정됨", d["date"]))
        self.assertEqual(self.c.get(f"/api/data/{d['id']}").json()["value"], 4.25)
        # null 로 바꿀 수 없는 필드, 빈 수정, record_type 변경 시도 거부
        for body in ({"value": None}, {}, {"record_type": "imported"}):
            self.assertEqual(self.c.put(f"/api/data/{d['id']}", json=body).status_code, 422, body)

    # 4
    def test_04_delete(self):
        d = self.create()
        self.assertEqual(self.c.delete(f"/api/data/{d['id']}").status_code, 204)
        self.assertEqual(self.c.get(f"/api/data/{d['id']}").status_code, 404)
        self.assertEqual(self.c.get("/api/data").json()["total"], 435)

    # 5
    def test_05_imported_read_only(self):
        before = self.c.get(f"/api/data/{IMPORTED_ID}").json()
        r = self.c.put(f"/api/data/{IMPORTED_ID}", json={"value": 1.0})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.delete(f"/api/data/{IMPORTED_ID}").status_code, 403)
        self.assertEqual(self.c.get(f"/api/data/{IMPORTED_ID}").json(), before)
        # 클라이언트가 imported 를 만들 수도 없다
        r = self.c.post("/api/data", json={**MANUAL, "record_type": "imported"})
        self.assertEqual(r.status_code, 422)

    # 6
    def test_06_bad_date(self):
        for bad in ("2026-10-13T10:00:00", "2026-10-13", "not-a-date", "2026-13-40T00:00:00+09:00",
                    1791625532, None, ""):
            r = self.c.post("/api/data", json={**MANUAL, "date": bad})
            self.assertEqual(r.status_code, 422, bad)
        d = self.create()
        self.assertEqual(self.c.put(f"/api/data/{d['id']}", json={"date": "2026-10-13 10:00"}).status_code, 422)

    # 7
    def test_07_bad_number(self):
        for bad in ("3.5", True, None, [], {"v": 1}, -0.1):
            r = self.c.post("/api/data", json={**MANUAL, "value": bad})
            self.assertEqual(r.status_code, 422, bad)
        for raw in ('NaN', 'Infinity', '-Infinity'):  # JSON 표준 밖이지만 파이썬 파서는 받아들인다
            body = '{"date": "2026-10-13T10:00:00+09:00", "value": %s, "memo": ""}' % raw
            r = self.c.post("/api/data", content=body, headers={"Content-Type": "application/json"})
            self.assertEqual(r.status_code, 422, raw)
        # 속도가 아닌 metric 은 음수 허용
        self.create(metric="temperature_delta", unit="C", value=-2.0)
        self.assertEqual(self.c.post("/api/data", json={**MANUAL, "sim_time_s": 1.5}).status_code, 422)

    # 8
    def test_08_not_found(self):
        for method in ("get", "delete"):
            r = getattr(self.c, method)("/api/data/manual-doesnotexist")
            self.assertEqual(r.status_code, 404)
        self.assertEqual(self.c.put("/api/data/manual-doesnotexist", json={"value": 1}).status_code, 404)
        self.assertEqual(self.c.get("/api/data/summary", params={"run_id": "nope"}).status_code, 404)

    # 9
    def test_09_initial_summary(self):
        s = self.c.get("/api/data/summary").json()
        self.assertEqual(s["count"], BASE["count"])
        self.assertEqual((s["run_id"], s["metric"], s["unit"]), (RUN, "ugv_speed_planar", "m/s"))
        for k in ("mean", "min", "max"):
            self.assertAlmostEqual(s[k], BASE[k], delta=1e-6, msg=k)
        rc = s["recent_comparison"]
        self.assertEqual(rc["basis"], "sim_time_s")
        self.assertEqual((rc["recent"]["from_sim_s"], rc["recent"]["to_sim_s"]), (390, 450))
        self.assertEqual((rc["previous"]["from_sim_s"], rc["previous"]["to_sim_s"]), (330, 390))
        self.assertAlmostEqual(rc["recent"]["mean"], BASE["recent"], delta=1e-6)
        self.assertAlmostEqual(rc["previous"]["mean"], BASE["previous"], delta=1e-6)
        self.assertEqual((s["start"], s["end"]), ("2026-10-10T18:45:32.747+09:00", "2026-10-10T18:52:54.614+09:00"))
        joined = " ".join(s["cautions"])
        self.assertIn("2,234개(27.7%)", joined)
        self.assertIn("1초 집계값의 비율이 아니다", joined)
        self.assertNotIn("위반이다", joined)

    # 10
    def test_10_summary_follows_changes(self):
        d = self.create(run_id=RUN, sim_time_s=450, value=10.0)
        s = self.c.get("/api/data/summary").json()
        self.assertEqual((s["count"], s["manual_count"]), (436, 1))
        self.assertAlmostEqual(s["mean"], (BASE["mean"] * 435 + 10.0) / 436, delta=1e-6)
        self.assertEqual(s["max"], 10.0)
        self.assertEqual(s["recent_comparison"]["recent"]["to_sim_s"], 451)
        self.c.put(f"/api/data/{d['id']}", json={"value": 0.0})
        s = self.c.get("/api/data/summary").json()
        self.assertAlmostEqual(s["mean"], BASE["mean"] * 435 / 436, delta=1e-6)
        self.assertAlmostEqual(s["max"], BASE["max"], delta=1e-6)
        self.c.delete(f"/api/data/{d['id']}")
        s = self.c.get("/api/data/summary").json()
        self.assertEqual(s["count"], 435)
        self.assertAlmostEqual(s["mean"], BASE["mean"], delta=1e-6)
        # 같은 run·sim 구간에 두 번째 값은 거부
        self.assertEqual(self.c.post("/api/data", json={**MANUAL, "run_id": RUN, "sim_time_s": 100}).status_code, 409)

    # 11
    def test_11_reimport_no_duplicates(self):
        docs = load_documents(SEED)
        self.assertEqual(len(docs), 435)
        self.assertEqual(len({i for i, _ in docs}), 435)
        repo = InMemoryRepository()
        first, second = import_documents(repo, docs), import_documents(repo, docs)
        self.assertEqual((first["created"], first["unchanged"]), (435, 0))
        self.assertEqual((second["created"], second["unchanged"], second["conflicts"]), (0, 435, []))
        self.assertEqual(len(repo.list()), 435)
        # 날짜(같은 시점)·값·단위·원본 식별자 보존
        payload = json.loads(SEED.read_text(encoding="utf-8"))
        src = {d["id"]: d["data"] for d in payload["documents"]}
        for doc_id, doc in repo.list():
            s = src[doc_id]
            self.assertEqual(doc["date"], datetime.fromisoformat(s["date"]))
            self.assertIsNotNone(doc["date"].tzinfo)
            for k in ("value", "unit", "run_id", "source_ref", "transform_version", "sim_time_s"):
                self.assertEqual(doc[k], s[k], k)
        # 내용이 다른 같은 ID는 덮어쓰지 않고 conflict
        changed = [(docs[0][0], {**docs[0][1], "value": 99.0})]
        r = import_documents(repo, changed)
        self.assertEqual(r["conflicts"], [docs[0][0]])
        self.assertEqual(repo.get(docs[0][0])["value"], docs[0][1]["value"])

    def test_11b_partial_failure_recorded(self):
        class Flaky(InMemoryRepository):
            def create(self, doc_id, doc):
                if doc_id.endswith("t00100__v1.0.0"):
                    raise RepositoryUnavailable("모의 네트워크 오류")
                super().create(doc_id, doc)
        repo = Flaky()
        r = import_documents(repo, load_documents(SEED))
        self.assertEqual(r["created"], 434)
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("t00100", r["errors"][0]["id"])

    # 12
    def test_12_firestore_unavailable(self):
        # 키 경로 미설정 / 없는 키 파일 → 앱은 뜨고 요청은 503
        for cred in (None, str(ROOT / "no-such-key.json")):
            c = client(data_backend="firestore", firebase_credentials=cred)
            for method, path, body in (("get", "/api/data", None), ("get", "/api/data/summary", None),
                                       ("post", "/api/data", MANUAL)):
                r = c.request(method.upper(), path, json=body)
                self.assertEqual(r.status_code, 503, (cred, path))
                self.assertIn("reason", r.json())
            self.assertEqual(c.get("/health").status_code, 200)

        # 연결 중 장애 (Firestore 예외) → 503
        from google.api_core import exceptions as gexc

        class DownClient:
            def collection(self, name):
                raise gexc.ServiceUnavailable("모의 장애")
        app = create_app(Settings(data_backend="firestore"),
                         repository=FirestoreRepository(Settings(data_backend="firestore"), client=DownClient()))
        self.assertEqual(TestClient(app).get("/api/data").status_code, 503)

    # 13
    def test_13_cors(self):
        c = client(cors_origins=("http://localhost:5500",))
        pre = {"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"}
        ok = c.options("/api/data/x", headers={"Origin": "http://localhost:5500", **pre})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers.get("access-control-allow-origin"), "http://localhost:5500")
        bad = c.options("/api/data/x", headers={"Origin": "http://evil.example", **pre})
        self.assertNotEqual(bad.headers.get("access-control-allow-origin"), "http://evil.example")
        g = c.get("/api/data", headers={"Origin": "http://evil.example"})
        self.assertIsNone(g.headers.get("access-control-allow-origin"))
        # 기본값(CORS_ORIGINS 미설정)은 어떤 출처도 허용하지 않는다
        g = client().get("/api/data", headers={"Origin": "http://localhost:5500"})
        self.assertIsNone(g.headers.get("access-control-allow-origin"))

    # 14
    def test_14_swagger(self):
        spec = self.c.get("/openapi.json").json()
        self.assertEqual(spec["info"]["title"], "ADAIR Mission Intelligence API")
        paths = spec["paths"]
        self.assertEqual(set(paths["/api/data"]), {"get", "post"})
        self.assertEqual(set(paths["/api/data/{doc_id}"]), {"get", "put", "delete"})
        self.assertIn("get", paths["/api/data/summary"])
        docs = self.c.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("swagger-ui", docs.text)


class FakeFirestore:
    """FirestoreRepository 매핑 확인용 최소 가짜 클라이언트 (실제 연결 없음)."""

    def __init__(self):
        self.store = {}

    def collection(self, name):
        self.name = name
        return _Col(self.store)


class _Snap:
    def __init__(self, id_, data):
        self.id, self._d, self.exists = id_, data, data is not None

    def to_dict(self):
        return dict(self._d)


class _Ref:
    def __init__(self, store, id_):
        self.s, self.id = store, id_

    def get(self, **kw):
        return _Snap(self.id, self.s.get(self.id))

    def create(self, data, **kw):
        from google.api_core import exceptions as gexc
        if self.id in self.s:
            raise gexc.AlreadyExists(self.id)
        self.s[self.id] = dict(data)

    def update(self, data, **kw):
        from google.api_core import exceptions as gexc
        if self.id not in self.s:
            raise gexc.NotFound(self.id)
        self.s[self.id].update(data)

    def delete(self, **kw):
        self.s.pop(self.id, None)


class _Col:
    def __init__(self, store, filters=()):
        self.s, self.f = store, filters

    def document(self, id_):
        return _Ref(self.s, id_)

    def where(self, filter):
        return _Col(self.s, (*self.f, (filter.field_path, filter.value)))

    def stream(self, **kw):
        return [_Snap(i, d) for i, d in self.s.items() if all(d.get(k) == v for k, v in self.f)]


class FirestoreMappingTest(unittest.TestCase):
    def test_firestore_repository_mapping(self):
        fake = FakeFirestore()
        repo = FirestoreRepository(Settings(data_backend="firestore"), client=fake)
        docs = load_documents(SEED)
        self.assertEqual(import_documents(repo, docs)["created"], 435)
        self.assertEqual(fake.name, "data")
        self.assertEqual(import_documents(repo, docs)["unchanged"], 435)
        self.assertEqual(len(fake.store), 435)
        stored = fake.store[IMPORTED_ID]["date"]
        # Firestore에는 시간대 있는 datetime 이 가며 Timestamp 로 저장된다 — 같은 시점이어야 한다
        self.assertIsInstance(stored, datetime)
        self.assertEqual(stored, datetime(2026, 10, 10, 9, 45, 32, 747000, tzinfo=timezone.utc))
        self.assertEqual(stored.utcoffset(), timedelta(hours=9))
        with self.assertRaises(DocumentExists):
            repo.create(IMPORTED_ID, docs[0][1])
        app = create_app(Settings(data_backend="firestore"), repository=repo)
        s = TestClient(app).get("/api/data/summary").json()
        self.assertAlmostEqual(s["mean"], BASE["mean"], delta=1e-6)
        self.assertEqual(len(repo.list(run_id=RUN, record_type="imported")), 435)


class FirestoreSafetyTest(unittest.TestCase):
    """실제 연결 전 준비: 필터, 오류 마스킹, 데이터베이스 단위 오류, 설정."""

    def test_summary_filters(self):
        c = client()
        self.assertEqual(c.get("/api/data/summary", params={"run_id": RUN}).json()["count"], 435)
        self.assertEqual(c.get("/api/data/summary", params={"metric": "ugv_speed_planar"}).json()["count"], 435)
        c.post("/api/data", json={**MANUAL, "metric": "battery_pct", "unit": "%", "value": 80})
        self.assertEqual(c.get("/api/data/summary").status_code, 400)  # metric 섞임 → 지정 요구
        s = c.get("/api/data/summary", params={"metric": "battery_pct"}).json()
        self.assertEqual((s["count"], s["unit"], s["recent_comparison"]), (1, "%", None))
        self.assertEqual(c.get("/api/data/summary", params={"run_id": RUN}).json()["count"], 435)
        # 다른 run 이 섞이면 구간 비교 생략
        c.post("/api/data", json={**MANUAL, "run_id": "other_run", "sim_time_s": 5})
        s = c.get("/api/data/summary", params={"metric": "ugv_speed_planar"}).json()
        self.assertIsNone(s["recent_comparison"])
        self.assertEqual(len(s["run_ids"]), 2)

    def test_error_redaction(self):
        from app.repository import safe_error
        pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
        for msg in (f"bad: {pem} tail", '{"private_key_id": "abc123def456", "x": 1}', f'"private_key": "{pem}"'):
            out = safe_error(msg)
            self.assertNotIn("MIIEvQ", out)
            self.assertNotIn("abc123def456", out)
        self.assertLessEqual(len(safe_error("x" * 1000)), 301)

    def test_database_not_found_is_unavailable(self):
        from google.api_core import exceptions as gexc

        class NoDb:
            def collection(self, name):
                return self

            def where(self, filter):
                return self

            def stream(self, timeout=None):
                raise gexc.NotFound("The database (default) does not exist for project x")
        repo = FirestoreRepository(Settings(data_backend="firestore"), client=NoDb())
        r = TestClient(create_app(Settings(data_backend="firestore"), repository=repo)).get("/api/data")
        self.assertEqual(r.status_code, 503)

    def test_key_project_mismatch_and_settings(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            key = Path(d) / "k.json"
            key.write_text(json.dumps({"type": "service_account", "project_id": "proj-a",
                                       "private_key": "-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----"}),
                           encoding="utf-8")
            repo = FirestoreRepository(Settings(data_backend="firestore", firebase_credentials=str(key),
                                                firebase_project_id="proj-b"))
            self.assertIn("proj-a", repo._init_error)
            self.assertNotIn("SECRET", repo._init_error)
            r = TestClient(create_app(Settings(data_backend="firestore"), repository=repo)).get("/api/data")
            self.assertEqual(r.status_code, 503)
            self.assertNotIn("SECRET", r.text)
        keys = ("FIRESTORE_DATABASE_ID", "FIRESTORE_TIMEOUT_S", "CORS_ORIGINS")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.update({"FIRESTORE_DATABASE_ID": "adair-mi", "FIRESTORE_TIMEOUT_S": "5",
                               "CORS_ORIGINS": "http://a.test, http://b.test"})
            st = Settings.from_env(env_file=None)
            self.assertEqual((st.firestore_database_id, st.firestore_timeout_s), ("adair-mi", 5.0))
            self.assertEqual(st.cors_origins, ("http://a.test", "http://b.test"))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class PlanImportTest(unittest.TestCase):
    def test_plan_import(self):
        from app.importer import plan_import
        docs = load_documents(SEED)
        todo, same, conflicts = plan_import({}, docs)
        self.assertEqual((len(todo), same, conflicts), (435, [], []))
        existing = dict(docs[:10])
        existing[docs[0][0]] = {**docs[0][1], "value": 99.0}
        todo, same, conflicts = plan_import(existing, docs)
        self.assertEqual((len(todo), len(same), conflicts), (425, 9, [docs[0][0]]))


class CredentialsEnvTest(unittest.TestCase):
    def test_google_application_credentials(self):
        from app.config import credentials_path
        self.assertEqual(credentials_path(None, "C:/k/a.json"), "C:/k/a.json")
        self.assertEqual(credentials_path("C:/k/a.json", None), "C:/k/a.json")
        self.assertEqual(credentials_path("C:/k/a.json", "C:/k/a.json"), "C:/k/a.json")
        self.assertIsNone(credentials_path("", "  "))
        with self.assertRaises(ValueError):
            credentials_path("C:/k/a.json", "C:/k/b.json")


class RepositoryContractTest(unittest.TestCase):
    def test_memory_is_repository(self):
        self.assertTrue(issubclass(InMemoryRepository, DataRepository))
        self.assertTrue(issubclass(FirestoreRepository, DataRepository))


if __name__ == "__main__":
    unittest.main()
