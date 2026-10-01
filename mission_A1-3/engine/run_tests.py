"""레피 Story Engine 테스트 메모 아홉 개를 돌린다.

기준 샘플 네 개(writer.md에 답이 있음)와 새 메모 다섯 개다.
api/index.py의 run_story_engine을 그대로 부르므로 화면에 나가는 결과와 같다.
결과마다 걸린 시간, 헤드라인 후보, 서버가 근거 없음으로 지운 문장을 함께 보여준다.

    python engine/run_tests.py

OPENAI_API_KEY는 환경 변수나 mission_A1-3/.env에서 읽는다.
결과 전체는 engine/test_results.json에도 저장한다. 다른 파일에 저장하려면
경로를 인자로 준다: python engine/run_tests.py engine/test_results_2.json
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

import index  # noqa: E402
from openai import OpenAI  # noqa: E402


TESTS = [
    # 기준 샘플 — 프롬프트 안에 답이 있어 최소 조건 확인용
    {
        "name": "쥐꼬리 낚시대",
        "scenario_mode": "REAL_SELLER",
        "seller_additions": [],
        "memo": "오늘은 우리집 고양이가 제일 좋아하는 쥐꼬리 낚시대를 홍보해볼까해. 실리콘이라서 엄청 부드러워서 진짜 쥐꼬리 같은지 흔들면 우리집냥이가 흥분하면서 좋아하는게 보이는데 우리 고객님들의 고양이들도 한번 보면 엄청 좋아할꺼야. 이걸 보면 고양이가 진짜 사냥한다는게 무슨 의미인지 알껄"
    },
    {
        "name": "밀짚모자",
        "scenario_mode": "DEMO_SELLER",
        "seller_additions": [],
        "memo": "못생긴 밀짚모자인데 철원 주상절리입장료내면 한사람당 5천원 상품권주는데 그걸 이용하면 주변상점에서 살수 있거든 너무 햇볕이 뜨거워서 어쩔수 없이 샀는데 국내 여행갈때마다 필요할때 들고갔더니 추억템이 됐어"
    },
    {
        "name": "닉스고",
        "scenario_mode": "DEMO_SELLER",
        "seller_additions": [],
        "memo": "저는 현역 시절 닉스고를 응원했습니다. 세계적인 무대에서 좋은 성적을 거두던 한국마사회 소유의 말이라는 점도 특별하게 느껴졌고요. 지금은 경주에서 은퇴해 씨수말로 지내고 있지만, 마사회 공식 굿즈에서 실제 닉스고의 외형을 본떠 만든 인형을 발견했습니다. 여러 마사회 굿즈 중에서도 가장 실제 말처럼 생겼고, 무엇보다 귀여웠습니다. 가격은 8천 원. 그래서 하나 데려왔습니다."
    },
    {
        "name": "잉어킹",
        "scenario_mode": "DEMO_SELLER",
        "seller_additions": ["별일 없이 잘 지나가자는 마음을 걸어두는 작은 액막이 이런 글귀도 있으면 좋지"],
        "memo": "남편이랑 포켓몬팝업이 있다고 해서 놀러갔더니 잉어킹액막이가 있더라고 그런데 가챠로밖에 안팔아서 만원이라는 큰 금액이어도 한번 도전해봤더니 성공해서 한번에 뽑았어 몇달뒤에 이사 예정이어서 가지고 싶었는데 좋은 추억이었어"
    },
    # 새 메모 — 헤드라인과 일반화 평가용
    {
        "name": "딸기 케이크",
        "scenario_mode": "REAL_SELLER",
        "seller_additions": [],
        "memo": "우리 가게 딸기 케이크는 제철 딸기만 써요. 겨울에만 나와서 그런지 단골분들이 겨울 되면 먼저 물어보세요."
    },
    {
        "name": "단풍 네일",
        "scenario_mode": "REAL_SELLER",
        "seller_additions": [],
        "memo": "이번 달 시즌 네일은 단풍 그라데이션이에요. 손톱 끝만 살짝 물들인 느낌이라 회사 다니시는 분들도 부담 없이 많이 하세요. 저도 지금 하고 있는데 손 볼 때마다 기분 좋아요"
    },
    {
        "name": "작약",
        "scenario_mode": "REAL_SELLER",
        "seller_additions": [],
        "memo": "인스타에 올릴 거니까 짧게 써줘. 작약 한 단 3만5천원. 봉오리로 와서 집에서 하루이틀이면 확 펴요. 피는 거 보는 재미가 있어서 저는 일부러 봉오리로 들여요"
    },
    {
        "name": "쌀뜨물 비누",
        "scenario_mode": "REAL_SELLER",
        "seller_additions": [],
        "memo": "쌀뜨물 비누. 엄마가 예전에 쌀뜨물로 세수하던 게 생각나서 만들어봤어요. 거품이 크진 않은데 뽀득하게 씻겨요"
    },
    {
        # 재료가 거의 없어 NEED_MORE_INFO가 나와야 맞다.
        "name": "아메리카노",
        "scenario_mode": "REAL_SELLER",
        "seller_additions": [],
        "memo": "아메리카노 4500원 맛있어요"
    },
]


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def run_one(client, test):
    """run_story_engine을 부르면서 Writer 출력과 지운 문장을 함께 잡아 둔다."""
    captured = {}
    original_call = index.call_structured
    original_check = index.check_sentences

    def spy_call(client_, instructions, user_input, name, schema):
        result = original_call(client_, instructions, user_input, name, schema)
        captured[name] = result
        return result

    def spy_check(*args):
        body, removed = original_check(*args)
        captured["removed"] = removed
        return body, removed

    index.call_structured = spy_call
    index.check_sentences = spy_check
    started = time.time()
    try:
        result = index.run_story_engine(
            client,
            test["memo"],
            scenario_mode=test["scenario_mode"],
            seller_additions=test["seller_additions"]
        )
    except Exception as error:
        result = {"error": repr(error)}
    finally:
        index.call_structured = original_call
        index.check_sentences = original_check

    writer = captured.get("lepi_writer") or {}
    return {
        "name": test["name"],
        "scenario_mode": test["scenario_mode"],
        "seconds": round(time.time() - started, 1),
        "result": result,
        "headline_candidates": writer.get("headline_candidates"),
        "removed_sentences": captured.get("removed") or [],
        # 문장마다 Writer가 댄 근거. 화면에는 안 나가지만 검사가 무엇을 통과시켰는지 볼 수 있다.
        "sentences": writer.get("sentences")
    }


def print_report(report):
    print(f"\n### {report['name']} · {report['scenario_mode']} · {report['seconds']}초")
    print(json.dumps(report["result"], ensure_ascii=False, indent=1))
    for candidate in report["headline_candidates"] or []:
        print(f"  후보 {candidate['type']}: {candidate['text']}")
    for sentence in report["removed_sentences"]:
        print(
            f"  지운 문장 ({sentence['reason']}): {sentence['text']}"
            f"  ← source: {sentence.get('source')!r}"
        )


def main():
    # Windows 터미널에서도 한글이 깨지지 않게 한다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY가 없습니다.")

    client = OpenAI(api_key=api_key)
    reports = []
    for test in TESTS:
        report = run_one(client, test)
        print_report(report)
        reports.append(report)

    out_path = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent / "test_results.json"
    )
    out_path.write_text(
        json.dumps(reports, ensure_ascii=False, indent=1),
        encoding="utf-8"
    )
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
