"""OpenAI 호환 API(Codyssey) 실제 연결 확인 — 정확히 1회 요청 (SDK 재시도 0).

Firestore data 의 실제 summary 로 만든 시스템 프롬프트에 질문 1개를 보내고,
연결·모델·답변·근거 검사 결과를 출력한다. 대화 기록에는 저장하지 않는다. 키는 출력하지 않는다.

  python scripts/check_llm.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat import build_system_prompt, check_grounding  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import LLMError, OpenAIChatClient, redact  # noqa: E402
from app.main import build_repository  # noqa: E402
from app.service import DataService, load_quality  # noqa: E402

QUESTION = "이 주행 기록에서 최근 60초 평균 속도가 이전보다 낮아진 이유가 차량 고장인가요? 규칙 최고속도 초과 비율도 알려주세요."


def main() -> int:
    s = Settings.from_env()
    print(f"model={s.openai_model}  base_url={s.openai_base_url}  key={'set' if s.openai_api_key else 'missing'}  "
          f"data_backend={s.data_backend}")
    if not (s.openai_api_key and s.openai_model and s.openai_base_url):
        print("[FAIL] OPENAI_API_KEY·OPENAI_MODEL·OPENAI_BASE_URL 중 빠진 값이 있습니다")
        return 1
    repo, _ = build_repository(s)
    summary, computed_at, _ = DataService(repo, load_quality(s.quality_path)).summary_info(metric="ugv_speed_planar")
    print(f"[PASS] 통계 조회 ({s.data_backend}) count={summary.count} mean={summary.mean}")
    system = build_system_prompt(summary, computed_at)
    llm = OpenAIChatClient(api_key=s.openai_api_key, model=s.openai_model, timeout_s=s.openai_timeout_s,
                           max_output_tokens=s.openai_max_output_tokens, base_url=s.openai_base_url, max_retries=0)
    t0 = time.monotonic()
    try:
        r = llm.complete([{"role": "system", "content": system}, {"role": "user", "content": QUESTION}])
    except LLMError as e:
        print(f"[FAIL] API 호출 ({time.monotonic() - t0:.1f}s): {redact(e, (s.openai_api_key,))}")
        return 1
    print(f"[PASS] API 호출 1회 ({time.monotonic() - t0:.1f}s)  응답 model={r.model}  usage={r.usage}")
    g = check_grounding(r.text, system + "\n" + QUESTION)
    print(f"[{'PASS' if g.ok else 'CHECK'}] 근거 검사 unverified_numbers={g.unverified_numbers} "
          f"assertive_cause_phrases={g.assertive_cause_phrases}")
    print("\n--- 질문 ---\n" + QUESTION + "\n--- 답변 ---\n" + redact(r.text, (s.openai_api_key,), limit=None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
