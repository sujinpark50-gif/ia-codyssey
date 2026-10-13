"""실제 GPT + 실제 Firestore 통합 확인 (GPT 호출 정확히 1회).

흐름: 대화 생성 → POST /api/chat → Firestore conversations 직접 확인 → 목록·상세 조회 → 삭제 → 삭제 확인.
data 컬렉션은 읽기만 하며 시작·끝에서 문서 수와 통계가 같은지 확인한다.
답변 전문은 logs/e2e_chat_<UTC시각>.json 에 저장한다 (logs/ 는 커밋하지 않는다). 키는 출력하지 않는다.

  python scripts/e2e_chat.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import TokenVerifier  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import LLMClient, OpenAIChatClient, redact  # noqa: E402
from app.main import build_repository, create_app  # noqa: E402
from app.service import DataService, load_quality  # noqa: E402

QUESTION = ("이 UGV 주행 기록의 평균·최소·최대 속도와 최근 60초 변화를 알려주세요. "
            "최근 속도가 낮아진 것이 차량 고장 때문인지, 규칙 최고속도 초과는 어떻게 봐야 하는지도 설명해 주세요.")


class LocalUser(TokenVerifier):
    """이 스크립트 프로세스 안에서만 쓰는 고정 사용자 (네트워크로 노출되지 않음).
    실제 Firebase 로그인 검증은 브라우저 확인과 자동 테스트에서 따로 한다."""

    def verify(self, token):
        if token != "e2e-local":
            raise ValueError("unknown")
        return {"sub": "e2e-local", "user_id": "e2e-local", "email": "e2e@local.test", "email_verified": True}


class OneShot(LLMClient):
    """실제 클라이언트를 감싸 두 번째 호출을 막는다."""

    def __init__(self, inner: LLMClient):
        self.inner, self.model, self.calls = inner, inner.model, 0

    def complete(self, messages):
        if self.calls >= 1:
            raise RuntimeError("승인된 GPT 호출 1회를 초과하려 했습니다")
        self.calls += 1
        return self.inner.complete(messages)


def main() -> int:
    s = Settings.from_env()
    secrets = (s.openai_api_key,)
    results: list[tuple[str, str, str]] = []

    def rec(name, ok, note=""):
        st = "PASS" if ok else "FAIL"
        results.append((name, st, note))
        print(f"[{st}] {name}" + (f" — {note}" if note else ""))
        return ok

    if s.data_backend != "firestore":
        print("DATA_BACKEND=firestore 가 아닙니다")
        return 1
    repo, _ = build_repository(s)
    fresh = DataService(repo, load_quality(s.quality_path))  # 캐시 없는 기준값
    client = repo._client
    data_col = client.collection(s.data_collection)

    def data_state():
        n = int(data_col.count().get(timeout=10)[0][0].value)
        sm = fresh.summary(metric="ugv_speed_planar")
        return n, (sm.count, sm.mean, sm.min, sm.max)

    before = data_state()
    rec("시작 시 data", before[0] == 435, f"{before[0]}건, (count, mean, min, max)={before[1]}")

    llm = OneShot(OpenAIChatClient(api_key=s.openai_api_key, model=s.openai_model, timeout_s=s.openai_timeout_s,
                                   max_output_tokens=s.openai_max_output_tokens, base_url=s.openai_base_url,
                                   max_retries=0))
    from dataclasses import replace
    s = replace(s, allowed_emails=("e2e@local.test",))
    c = TestClient(create_app(s, repository=repo, llm=llm, verifier=LocalUser()),
                   headers={"Authorization": "Bearer e2e-local"})
    conv_col = client.collection(s.conversations_collection)
    conv_id = None
    record = {"question": QUESTION}
    try:
        r = c.post("/api/conversations", json={"title": "[E2E] 통합 테스트 — 삭제 예정"})
        rec("1. 테스트 대화 생성", r.status_code == 201, f"HTTP {r.status_code}")
        conv_id = r.json()["id"]

        r = c.post("/api/chat", json={"message": QUESTION, "conversation_id": conv_id})
        ok = rec("2. POST /api/chat", r.status_code == 200, f"HTTP {r.status_code}")
        body = r.json()
        if not ok:
            print(redact(json.dumps(body, ensure_ascii=False), secrets))
            return 1
        answer = body["answer"]
        record.update(response=body)
        rec("3. 실제 모델", body["model"] == s.openai_model, f"응답 model={body['model']}")
        b = body["stats_basis"]
        rec("3. 답변 통계 = Firestore 통계", (b["count"], b["mean"], b["min"], b["max"]) == before[1],
            f"{(b['count'], b['mean'], b['min'], b['max'])} from_cache={b['from_cache']}")

        snap = conv_col.document(conv_id).get(timeout=10)
        msgs = [m.to_dict() for m in conv_col.document(conv_id).collection("messages").order_by("seq").stream(timeout=10)]
        meta = snap.to_dict() if snap.exists else {}
        rec("4. Firestore 대화 문서", snap.exists and meta.get("message_count") == 2,
            f"message_count={meta.get('message_count')} run_id={meta.get('run_id')}")
        rec("4. Firestore 메시지 2건 (user, assistant)", [m["role"] for m in msgs] == ["user", "assistant"])
        stored = msgs[1]["content"] if len(msgs) == 2 else ""
        rec("4. 저장된 답변 = 응답 답변 (잘림 없음)", stored == answer, f"{len(stored)}자 / {len(answer)}자")
        rec("4. 저장된 created_at 이 Timestamp", type(msgs[1]["created_at"]).__name__ == "DatetimeWithNanoseconds")

        items = c.get("/api/conversations").json()["items"]
        rec("5. GET /api/conversations 목록에 있음", any(i["id"] == conv_id for i in items), f"목록 {len(items)}건")
        d = c.get(f"/api/conversations/{conv_id}").json()
        ok6 = (len(d["messages"]) == 2 and d["messages"][1]["content"] == answer
               and d["messages"][1]["stats_basis"]["mean"] == before[1][1])
        rec("6. GET /api/conversations/{id} 메시지·통계 근거", ok6,
            f"messages={len(d['messages'])} stats_basis.mean={d['messages'][1]['stats_basis']['mean']}")

        r = c.delete(f"/api/conversations/{conv_id}")
        rec("7. 테스트 대화 삭제", r.status_code == 204, f"HTTP {r.status_code}")
        deleted = conv_id
        conv_id = None
        r = c.get(f"/api/conversations/{deleted}")
        left = list(conv_col.document(deleted).collection("messages").stream(timeout=10))
        rec("8. 삭제 후 조회 불가", r.status_code == 404 and not conv_col.document(deleted).get(timeout=10).exists
            and not left, f"API {r.status_code}, 대화 문서 없음, 남은 메시지 {len(left)}")
    finally:
        if conv_id:  # 중간 실패 시 정리
            try:
                c.delete(f"/api/conversations/{conv_id}")
                print(f"(정리) 테스트 대화 {conv_id} 삭제")
            except Exception as e:  # noqa: BLE001
                print(f"(정리 실패) {conv_id} 를 직접 지워야 합니다: {redact(e, secrets)}")
        after = data_state()
        rec("끝난 뒤 data 변경 없음", after == before, f"{after[0]}건, {after[1]}")
        rec("GPT 호출 횟수 1회", llm.calls == 1, f"{llm.calls}회")
        out = ROOT / "logs" / f"e2e_chat_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        out.parent.mkdir(exist_ok=True)
        record["checks"] = results
        out.write_text(redact(json.dumps(record, ensure_ascii=False, indent=2), secrets, limit=None), encoding="utf-8")
        print(f"\n기록: {out.relative_to(ROOT)}")

    fails = [x for x in results if x[1] == "FAIL"]
    print(f"결과: PASS {len(results) - len(fails)} / FAIL {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
