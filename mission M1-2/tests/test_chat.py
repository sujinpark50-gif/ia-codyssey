"""M3 채팅·대화 기록 테스트. 실제 OpenAI·Firestore 호출 없음 (가짜 LLM, 가짜 전송 계층, 가짜 Firestore).

실행: .venv/Scripts/python -m unittest discover -s tests -v
"""

import json
import logging
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx2  # noqa: E402
import openai  # noqa: E402
from support import TestClient  # noqa: E402

from app.chat import build_system_prompt, check_grounding  # noqa: E402
from app.config import Settings  # noqa: E402
from app.conversations import FirestoreConversationRepository, InMemoryConversationRepository  # noqa: E402
from app.llm import LLMClient, LLMError, LLMResult, LLMTimeout, OpenAIChatClient  # noqa: E402
from support import create_app  # noqa: E402
from app.repository import InMemoryRepository, RepositoryUnavailable  # noqa: E402

RUN = "B1x_slope_low_20261010_184532"
FAKE_KEY = "sk-test-SECRETKEY1234567890"
GOOD_ANSWER = ("분석 대상 B1x_slope_low_20261010_184532 의 ugv_speed_planar 평균은 4.896189 m/s 입니다. "
               "최근 60초(sim 390–450) 평균 4.551057 m/s 로 이전 5.279502 m/s 보다 0.728445 m/s 낮습니다.")


class FakeLLM(LLMClient):
    model = "fake-model"

    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [GOOD_ANSWER])
        self.error = error
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return LLMResult(text=self.replies[min(len(self.calls), len(self.replies)) - 1], model=self.model)


class CountingRepo(InMemoryRepository):
    def __init__(self):
        super().__init__()
        self.list_calls = 0

    def list(self, **kw):
        self.list_calls += 1
        return super().list(**kw)


def make(llm=None, convs=None, repo=None, **settings_kw):
    settings = Settings(**{"summary_cache_ttl_s": 60.0, "chat_rate_per_minute": 1000, "chat_rate_per_hour": 1000,
                           **settings_kw})
    if repo is not None:
        from app.importer import import_documents, load_documents
        import_documents(repo, load_documents(settings.seed_path))
    app = create_app(settings, repository=repo, conversations=convs, llm=llm or FakeLLM())
    return TestClient(app), app


class ChatTest(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM()
        self.c, self.app = make(self.llm)

    def chat(self, **body):
        return self.c.post("/api/chat", json={"message": "최근 속도 변화는?", **body})

    # 정상 채팅 요청 + 새 대화 생성
    def test_chat_creates_conversation(self):
        r = self.chat()
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["created_conversation"])
        self.assertEqual(d["answer"], GOOD_ANSWER)
        self.assertEqual(d["target"], {"run_id": RUN, "metric": "ugv_speed_planar", "unit": "m/s"})
        b = d["stats_basis"]
        self.assertEqual((b["count"], b["mean"], b["min"], b["max"]), (435, 4.896189, 0.0, 5.808594))
        self.assertEqual(b["recent_comparison"]["basis"], "sim_time_s")
        self.assertTrue(d["grounding"]["ok"], d["grounding"])
        self.assertEqual(d["history_messages_used"], 0)
        detail = self.c.get(f"/api/conversations/{d['conversation_id']}").json()
        self.assertEqual([m["role"] for m in detail["messages"]], ["user", "assistant"])
        self.assertEqual(detail["messages"][1]["stats_basis"]["mean"], 4.896189)
        self.assertEqual(detail["title"], "최근 속도 변화는?")
        self.assertEqual(len(self.llm.calls), 1)

    # POST /api/conversations 로 만든 대화에서 이어가기
    def test_create_then_continue(self):
        conv = self.c.post("/api/conversations", json={"title": "테스트 대화"})
        self.assertEqual(conv.status_code, 201)
        cid = conv.json()["id"]
        self.assertEqual(conv.json()["run_id"], RUN)
        self.llm.replies = ["첫 답", "두 번째 답"]
        r1 = self.chat(conversation_id=cid)
        self.assertFalse(r1.json()["created_conversation"])
        r2 = self.c.post("/api/chat", json={"message": "그럼 최대값은?", "conversation_id": cid})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["history_messages_used"], 2)
        sent = self.llm.calls[1]
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "user"])
        self.assertEqual((sent[1]["content"], sent[2]["content"], sent[3]["content"]),
                         ("최근 속도 변화는?", "첫 답", "그럼 최대값은?"))
        detail = self.c.get(f"/api/conversations/{cid}").json()
        self.assertEqual(detail["message_count"], 4)
        self.assertEqual([m["seq"] for m in detail["messages"]], [0, 1, 2, 3])
        self.assertEqual(detail["title"], "테스트 대화")

    def test_list_detail_delete(self):
        ids = [self.chat().json()["conversation_id"] for _ in range(2)]
        items = self.c.get("/api/conversations").json()["items"]
        self.assertEqual([i["id"] for i in items], ids[::-1])  # 최근 수정 순
        self.assertTrue(all(i["created_at"].endswith("+09:00") for i in items))
        self.assertEqual(self.c.delete(f"/api/conversations/{ids[0]}").status_code, 204)
        self.assertEqual(self.c.get(f"/api/conversations/{ids[0]}").status_code, 404)
        self.assertEqual(self.c.delete(f"/api/conversations/{ids[0]}").status_code, 404)
        self.assertEqual(len(self.c.get("/api/conversations").json()["items"]), 1)

    def test_bad_conversation_id(self):
        for bad in ("abc", "conv-123", "conv-" + "g" * 32, "CONV-" + "0" * 32):
            self.assertEqual(self.c.get(f"/api/conversations/{bad}").status_code, 422, bad)
            self.assertEqual(self.chat(conversation_id=bad).status_code, 422, bad)
        missing = "conv-" + "0" * 32
        self.assertEqual(self.c.get(f"/api/conversations/{missing}").status_code, 404)
        self.assertEqual(self.chat(conversation_id=missing).status_code, 404)
        self.assertEqual(self.llm.calls, [])

    def test_empty_or_bad_message(self):
        for body in ({"message": ""}, {"message": "   \n "}, {"message": "x" * 2001}, {}, {"message": 3},
                     {"message": "hi", "extra": 1}):
            self.assertEqual(self.c.post("/api/chat", json=body).status_code, 422, body)
        self.assertEqual(self.llm.calls, [])

    # GPT 타임아웃·오류 → 저장 안 함
    def test_llm_failures_not_saved(self):
        for err, status in ((LLMTimeout("시간 초과"), 504), (LLMError("HTTP 500"), 502)):
            c, _ = make(FakeLLM(error=err))
            r = c.post("/api/chat", json={"message": "평균은?"})
            self.assertEqual(r.status_code, status)
            self.assertFalse(r.json()["saved"])
            self.assertEqual(c.get("/api/conversations").json()["items"], [])
        # 기존 대화에서 실패해도 메시지 수 그대로
        llm = FakeLLM()
        c, _ = make(llm)
        cid = c.post("/api/chat", json={"message": "a"}).json()["conversation_id"]
        llm.error = LLMTimeout("x")
        self.assertEqual(c.post("/api/chat", json={"message": "b", "conversation_id": cid}).status_code, 504)
        self.assertEqual(c.get(f"/api/conversations/{cid}").json()["message_count"], 2)
        # 키·모델 미설정 → 503
        c = TestClient(create_app(Settings()))
        self.assertEqual(c.post("/api/chat", json={"message": "a"}).status_code, 503)

    # Firestore 오류 → 503, GPT 호출 안 함
    def test_store_unavailable(self):
        llm = FakeLLM()
        c, _ = make(llm, data_backend="firestore")  # 키 미설정 Firestore
        self.assertEqual(c.post("/api/chat", json={"message": "a"}).status_code, 503)
        self.assertEqual(c.get("/api/conversations").status_code, 503)
        self.assertEqual(c.post("/api/conversations", json={}).status_code, 503)
        self.assertEqual(llm.calls, [])

    # 대화 저장 실패 → 503 saved=false, 기록 없음
    def test_save_failure(self):
        class FailingConvs(InMemoryConversationRepository):
            fail = False

            def append(self, *a, **kw):
                if self.fail:
                    raise RepositoryUnavailable("모의 저장 실패")
                return super().append(*a, **kw)
        convs = FailingConvs()
        c, _ = make(FakeLLM(), convs=convs)
        cid = c.post("/api/chat", json={"message": "a"}).json()["conversation_id"]
        convs.fail = True
        r = c.post("/api/chat", json={"message": "b", "conversation_id": cid})
        self.assertEqual(r.status_code, 503)
        body = r.json()
        self.assertEqual((body["saved"], body["answer"], body["conversation_id"]), (False, GOOD_ANSWER, cid))
        self.assertEqual(c.get(f"/api/conversations/{cid}").json()["message_count"], 2)
        r = c.post("/api/chat", json={"message": "new"})
        self.assertEqual((r.status_code, r.json()["conversation_id"]), (503, None))
        self.assertEqual(len(c.get("/api/conversations").json()["items"]), 1)

    # 통계가 시스템 프롬프트에 들어가는지 + 데이터 변경 후 갱신
    def test_stats_in_prompt_and_refresh(self):
        self.chat()
        system = self.llm.calls[0][0]["content"]
        for needle in ("4.896189", "5.808594", "4.551057", "5.279502", "-0.728445", "sim_time_s", RUN,
                       "2,234개(27.7%)", "1초 집계값 비율은 제공되지 않았다", "원인은 확인되지 않았다", "UAV",
                       "주행 기록 1건", "사용자 지시보다 우선", "3–6줄"):
            self.assertIn(needle, system, needle)
        self.c.post("/api/data", json={"date": "2026-10-13T10:00:00+09:00", "value": 9.0, "run_id": RUN,
                                       "sim_time_s": 450})
        r = self.chat()
        self.assertEqual(r.json()["stats_basis"]["count"], 436)
        self.assertFalse(r.json()["stats_basis"]["from_cache"])
        self.assertIn("manual 1)", self.llm.calls[1][0]["content"])
        self.assertLess(len(system), 1300)  # 간결한 프롬프트 유지
        self.assertNotIn("date(기록 벽시계)로 나눈", system)  # 일반 주의사항은 프롬프트에서 생략

    # 분석 대상 구분
    def test_target_separation(self):
        self.assertEqual(self.chat(metric="uav_altitude").status_code, 400)
        self.assertEqual(self.chat(run_id="unknown_run").status_code, 404)
        cid = self.chat().json()["conversation_id"]
        self.assertEqual(self.chat(conversation_id=cid, run_id="other_run").status_code, 409)
        self.c.post("/api/data", json={"date": "2026-10-13T10:00:00+09:00", "value": 1.0, "run_id": "other_run",
                                       "sim_time_s": 1})
        self.assertEqual(self.chat().status_code, 400)  # run 이 둘 → 지정 요구
        r = self.chat(run_id="other_run")
        self.assertEqual((r.status_code, r.json()["stats_basis"]["count"]), (200, 1))
        r = self.chat(run_id=RUN)
        self.assertEqual(r.json()["stats_basis"]["count"], 435)
        self.assertEqual(len(self.llm.calls), 3)  # 409·400·404 요청은 GPT 를 부르지 않는다

    # 근거 없는 수치·원인 단정 표시
    def test_grounding_flags(self):
        bad = FakeLLM(["평균은 7.12 m/s 이고 최근 감속은 모터 고장입니다. 초과 비율은 31.5% 입니다."])
        c, _ = make(bad)
        g = c.post("/api/chat", json={"message": "왜 느려졌어?"}).json()["grounding"]
        self.assertFalse(g["ok"])
        self.assertEqual(g["unverified_numbers"], ["7.12", "31.5"])
        self.assertEqual(g["assertive_cause_phrases"], ["고장입니다"])
        ok = check_grounding("고장으로 단정할 수 없습니다. 평균 4.90 m/s, 최대 5.81 m/s, 2,234개 초과(원시 표본).",
                             build_system_prompt(*_summary()))
        self.assertTrue(ok.ok, ok)

    def test_api_key_not_exposed(self):
        settings = Settings(openai_api_key=FAKE_KEY, openai_model="gpt-5.4")
        self.assertNotIn(FAKE_KEY, repr(settings))

        class LeakyClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        raise openai.OpenAIError(f"invalid api key {FAKE_KEY} provided")
        llm = OpenAIChatClient(api_key=FAKE_KEY, model="gpt-5.4", timeout_s=5, max_output_tokens=100,
                               client=LeakyClient())
        c = TestClient(create_app(settings, llm=llm))
        with self.assertLogs("adair_mi", level="WARNING") as logs:
            r = c.post("/api/chat", json={"message": "a"})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn(FAKE_KEY, r.text)
        self.assertNotIn("SECRETKEY", "\n".join(logs.output))
        self.assertIn("[REDACTED]", r.text)

    def test_summary_cache(self):
        repo = CountingRepo()
        c, _ = make(FakeLLM(), repo=repo)
        repo.list_calls = 0
        for _ in range(3):
            self.assertEqual(c.post("/api/chat", json={"message": "a"}).status_code, 200)
        self.assertEqual(repo.list_calls, 1)  # 3번 채팅, data 조회 1번
        c.post("/api/data", json={"date": "2026-10-13T10:00:00+09:00", "value": 1.0})
        n = repo.list_calls
        c.post("/api/chat", json={"message": "a"})
        self.assertEqual(repo.list_calls, n + 1)  # 변경 후 재계산
        # TTL 0 이면 캐시 없음
        repo2 = CountingRepo()
        c2, _ = make(FakeLLM(), repo=repo2, summary_cache_ttl_s=0)
        repo2.list_calls = 0
        c2.post("/api/chat", json={"message": "a"})
        c2.post("/api/chat", json={"message": "a"})
        self.assertEqual(repo2.list_calls, 2)

    def test_history_limits(self):
        llm = FakeLLM(["답" * 50])
        c, _ = make(llm, chat_history_max_messages=4, chat_history_max_chars=250)
        cid = c.post("/api/chat", json={"message": "q0"}).json()["conversation_id"]
        for i in range(1, 5):
            c.post("/api/chat", json={"message": f"q{i}", "conversation_id": cid})
        last = llm.calls[-1]
        hist = last[1:-1]
        self.assertLessEqual(len(hist), 4)
        self.assertLessEqual(sum(len(m["content"]) for m in hist), 250)
        self.assertEqual(hist[0]["role"], "user")

    def test_swagger_lists_chat_endpoints(self):
        paths = self.c.get("/openapi.json").json()["paths"]
        self.assertIn("post", paths["/api/chat"])
        self.assertEqual(set(paths["/api/conversations"]), {"get", "post"})
        self.assertEqual(set(paths["/api/conversations/{conv_id}"]), {"get", "delete"})


def _summary():
    from app.service import DataService
    repo = InMemoryRepository()
    from app.importer import import_documents, load_documents
    import_documents(repo, load_documents(Settings().seed_path))
    from app.service import load_quality
    s, at, _ = DataService(repo, load_quality(Settings().quality_path)).summary_info()
    return s, at


class OpenAICompatTest(unittest.TestCase):
    """실제 SDK가 Codyssey 호환 주소로 보낼 요청을 가짜 전송 계층에서 검사 (네트워크 없음)."""

    def _client(self, handler):
        http = httpx2.Client(transport=httpx2.MockTransport(handler))
        sdk = openai.OpenAI(api_key=FAKE_KEY, base_url="https://copa.codyssey.kr/v1", http_client=http, max_retries=0)
        return OpenAIChatClient(api_key=FAKE_KEY, model="gpt-5.4", timeout_s=5, max_output_tokens=300,
                                base_url="https://copa.codyssey.kr/v1", client=sdk)

    def test_request_shape(self):
        seen = {}

        def handler(req):
            seen.update(url=str(req.url), auth=req.headers.get("authorization"), body=json.loads(req.content))
            return httpx2.Response(200, json={
                "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-5.4",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "응답"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})
        r = self._client(handler).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(seen["url"], "https://copa.codyssey.kr/v1/chat/completions")
        self.assertEqual(seen["auth"], f"Bearer {FAKE_KEY}")
        self.assertEqual(seen["body"]["model"], "gpt-5.4")
        self.assertEqual(seen["body"]["max_completion_tokens"], 300)
        self.assertNotIn("store", seen["body"])
        self.assertEqual((r.text, r.model, r.usage["total_tokens"]), ("응답", "gpt-5.4", 12))

    def test_error_mapping(self):
        def status(code, msg):
            return lambda req: httpx2.Response(code, json={"error": {"message": msg}})
        with self.assertRaises(LLMError) as e:
            self._client(status(404, "model gpt-5.4 not found")).complete([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 404", str(e.exception))
        self.assertIn("gpt-5.4", str(e.exception))  # 모델 미지원은 그대로 보고 (다른 모델로 바꾸지 않음)
        with self.assertRaises(LLMError) as e:
            self._client(status(401, f"bad key {FAKE_KEY}")).complete([{"role": "user", "content": "hi"}])
        self.assertNotIn("SECRETKEY", str(e.exception))

        def timeout(req):
            raise httpx2.ReadTimeout("timed out", request=req)
        with self.assertRaises(LLMTimeout):
            self._client(timeout).complete([{"role": "user", "content": "hi"}])

        def empty(req):
            return httpx2.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-5.4",
                                              "choices": [{"index": 0, "finish_reason": "length",
                                                           "message": {"role": "assistant", "content": ""}}]})
        with self.assertRaises(LLMError) as e:
            self._client(empty).complete([{"role": "user", "content": "hi"}])
        self.assertIn("length", str(e.exception))


# ---------------------------------------------------------------- 가짜 Firestore (대화 저장 구조 확인)
class _Snap:
    def __init__(self, ref, data):
        self.reference, self.id, self._d, self.exists = ref, ref.id, data, data is not None

    def to_dict(self):
        return dict(self._d)


class _Doc:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.split("/")[-1]

    def collection(self, name):
        return _Coll(self.db, f"{self.path}/{name}")

    def get(self, **kw):
        return _Snap(self, self.db.data.get(self.path))

    def create(self, d, **kw):
        from google.api_core import exceptions as gexc
        if self.path in self.db.data:
            raise gexc.AlreadyExists(self.path)
        self.db.data[self.path] = dict(d)

    def delete(self, **kw):
        self.db.data.pop(self.path, None)


class _Coll:
    def __init__(self, db, path, order=None, desc=False, lim=None, filters=()):
        self.db, self.path, self.order, self.desc, self.lim, self.filters = db, path, order, desc, lim, filters

    def document(self, i):
        return _Doc(self.db, f"{self.path}/{i}")

    def where(self, filter):
        return _Coll(self.db, self.path, self.order, self.desc, self.lim,
                     (*self.filters, (filter.field_path, filter.value)))

    def order_by(self, f, direction="ASCENDING"):
        return _Coll(self.db, self.path, f, direction == "DESCENDING", self.lim, self.filters)

    def limit(self, n):
        return _Coll(self.db, self.path, self.order, self.desc, n, self.filters)

    def stream(self, **kw):
        depth = self.path.count("/") + 1
        docs = [(p, d) for p, d in self.db.data.items() if p.startswith(self.path + "/") and p.count("/") == depth
                and all(d.get(k) == v for k, v in self.filters)]
        if self.order:
            docs.sort(key=lambda x: x[1][self.order], reverse=self.desc)
        return [_Snap(_Doc(self.db, p), d) for p, d in docs[: self.lim]]


class _Batch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def create(self, ref, d):
        self.ops.append(("create", ref, d))

    def update(self, ref, d):
        self.ops.append(("update", ref, d))

    def delete(self, ref):
        self.ops.append(("delete", ref, None))

    def commit(self, **kw):
        from google.api_core import exceptions as gexc
        if self.db.fail_commit:
            raise gexc.ServiceUnavailable("모의 커밋 실패")
        staged = dict(self.db.data)
        for op, ref, d in self.ops:  # 하나라도 실패하면 아무것도 반영하지 않는다
            if op == "create":
                if ref.path in staged:
                    raise gexc.AlreadyExists(ref.path)
                staged[ref.path] = dict(d)
            elif op == "update":
                if ref.path not in staged:
                    raise gexc.NotFound(ref.path)
                staged[ref.path] = {**staged[ref.path], **d}
            else:
                staged.pop(ref.path, None)
        self.db.data = staged


class FakeDB:
    def __init__(self):
        self.data, self.fail_commit = {}, False

    def collection(self, name):
        return _Coll(self, name)

    def batch(self):
        return _Batch(self)


class FirestoreConversationTest(unittest.TestCase):
    def test_structure_atomic_delete(self):
        db = FakeDB()
        convs = FirestoreConversationRepository(Settings(), client=db)
        llm = FakeLLM(["답1", "답2"])
        c, _ = make(llm, convs=convs)
        cid = c.post("/api/chat", json={"message": "질문1"}).json()["conversation_id"]
        c.post("/api/chat", json={"message": "질문2", "conversation_id": cid})
        meta = db.data[f"conversations/{cid}"]
        self.assertEqual((meta["message_count"], meta["run_id"], meta["metric"]), (4, RUN, "ugv_speed_planar"))
        self.assertEqual(sorted(p.split("/")[-1] for p in db.data if p.startswith(f"conversations/{cid}/messages/")),
                         ["000000", "000001", "000002", "000003"])
        self.assertIn("stats_basis", db.data[f"conversations/{cid}/messages/000001"])
        self.assertEqual(llm.calls[1][2]["content"], "답1")
        # 커밋 실패 → 아무것도 바뀌지 않음
        before = dict(db.data)
        db.fail_commit = True
        self.assertEqual(c.post("/api/chat", json={"message": "질문3", "conversation_id": cid}).status_code, 503)
        self.assertEqual(db.data, before)
        db.fail_commit = False
        detail = c.get(f"/api/conversations/{cid}").json()
        self.assertEqual([m["content"] for m in detail["messages"]], ["질문1", "답1", "질문2", "답2"])
        self.assertEqual(c.delete(f"/api/conversations/{cid}").status_code, 204)
        self.assertEqual([p for p in db.data if p.startswith("conversations/")], [])


class SettingsTest(unittest.TestCase):
    def test_env(self):
        import os
        keys = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "SUMMARY_CACHE_TTL_S")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.update({"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": "gpt-5.4",
                               "OPENAI_BASE_URL": "https://copa.codyssey.kr/v1", "SUMMARY_CACHE_TTL_S": "15"})
            s = Settings.from_env(env_file=None)
            self.assertEqual((s.openai_model, s.openai_base_url, s.summary_cache_ttl_s),
                             ("gpt-5.4", "https://copa.codyssey.kr/v1", 15.0))
            self.assertNotIn(FAKE_KEY, repr(s))
            self.assertIsNone(replace(s, openai_model=None).openai_model)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


logging.getLogger("adair_mi").setLevel(logging.INFO)

if __name__ == "__main__":
    unittest.main()
