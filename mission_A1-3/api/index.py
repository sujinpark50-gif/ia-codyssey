from http.server import BaseHTTPRequestHandler
import json
import os
import re
from pathlib import Path

from openai import OpenAI


# 남용 상한. 이 엔드포인트는 페이지를 거치지 않고 직접 호출할 수 있으므로
# 프런트의 입력 제한만으로는 비용이 보호되지 않는다. 체험 한 번이 곧 OpenAI
# 호출 두 번(분석 + 작성)이라, 긴 글 한 번이 짧은 글 수십 번보다 비싸다.
# 레피는 짧은 상품 메모에 스토리를 입히는 도구다. 랜딩 사례의 메모가
# 100~300자 선이라 500자면 정상 사용에 지장이 없다.
MAX_TEXT_LENGTH = 500

# 한글은 UTF-8에서 글자당 3바이트다. 500자 = 1.5KB에 JSON 여유를 더한 값.
# 본문을 읽기 전에 막아야 큰 요청이 메모리에 올라오는 것을 방지할 수 있다.
MAX_BODY_BYTES = 8 * 1024

TOO_LONG_MESSAGE = "메모가 너무 깁니다. 500자 이내로 줄여 주세요."

MODEL = "gpt-5.4"


# ── Story Engine ──
# 프롬프트와 스키마의 원본은 engine/ 폴더다. 코드에 복사해 두지 않고
# 매번 파일에서 읽는다 — 원본이 둘이 되면 어느 쪽을 고쳐야 하는지 흐려진다.
# vercel.json의 @vercel/python 빌더는 프로젝트 폴더 전체를 상대 경로 그대로
# 함수에 담으므로(.git, node_modules, __pycache__ 등만 제외) engine/도 함께 간다.
# 체험판은 응답 시간 때문에 Analyzer → Writer 두 단계만 쓰고 Reviewer는 붙이지 않는다.
ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"


def _read_text(name):
    return (ENGINE_DIR / name).read_text(encoding="utf-8")


ANALYZER_PROMPT = _read_text("analyzer.md")
WRITER_PROMPT = _read_text("writer.md")
ANALYZER_SCHEMA = json.loads(_read_text("analyzer.schema.json"))
WRITER_SCHEMA = json.loads(_read_text("writer.schema.json"))

# 체험판 입력값은 고정이다. 랜딩에서 받는 건 메모 한 칸뿐이라
# 사진, 덧붙인 말, 상품 정보 입력이 없다.
SCENARIO_MODE = "REAL_SELLER"

# 테스트 모드(랜딩 주소 ?test)는 구매자로 쓴 메모를 판매자 글로 바꿔 본다.
# 방문자는 실제 판매자로 보고, 이 값이 올 때만 DEMO_SELLER로 돌린다.
SCENARIO_BY_MODE = {"demo": "DEMO_SELLER"}
OUTPUT_REQUEST = {
    "output_type": "PRODUCT_STORY",
    "target_channel": "landing_demo",
    "target_length": "short"
}


def call_structured(client, instructions, user_input, name, schema):
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=json.dumps(user_input, ensure_ascii=False),
        text={
            "format": {
                "type": "json_schema",
                "name": name,
                "schema": schema,
                "strict": True
            }
        }
    )

    # 모델 출력의 JSON 오류를 요청 JSON 오류(400)와 섞지 않는다.
    # 아래 핸들러는 JSONDecodeError를 사용자의 잘못된 요청으로 안내한다.
    try:
        return json.loads(response.output_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} 출력이 JSON이 아닙니다.") from error


def _normalize(text):
    # 공백과 문장부호를 모두 지운다. 메모가 "어쩔수 없이"처럼 띄어쓰기가
    # 틀려 있거나, Writer가 source에 마침표를 붙여도 같은 구절로 본다.
    return re.sub(r"[\s\W_]+", "", text or "")


def check_sentences(sentences, raw_input, seller_additions, analysis):
    """Writer가 댄 근거(source)를 실제 입력과 대조해 근거 없는 문장을 지운다.

    근거로 인정하는 텍스트는 메모 원문, 덧붙인 말, Analyzer가 정리한
    product.facts다. 권유(invitation) 문장은 근거를 따지지 않되 한 글에
    하나까지만 둔다. 남은 문장을 paragraph 번호로 묶어 문단 배열을 만든다.
    """
    facts = ((analysis or {}).get("product") or {}).get("facts") or []
    allowed = _normalize(
        " ".join([raw_input, *seller_additions, *facts])
    )

    kept = []
    removed = []
    invitation_used = False

    for sentence in sentences or []:
        text = (sentence.get("text") or "").strip()
        if not text:
            continue

        if sentence.get("source_type") == "invitation":
            if invitation_used:
                removed.append({**sentence, "reason": "권유 문장은 한 글에 하나까지"})
                continue
            invitation_used = True
            kept.append(sentence)
            continue

        source = _normalize(sentence.get("source"))
        if not source:
            removed.append({**sentence, "reason": "source가 비어 있음"})
            continue
        if source not in allowed:
            removed.append({**sentence, "reason": "source가 입력에 없음"})
            continue

        kept.append(sentence)

    paragraphs = {}
    for sentence in kept:
        paragraphs.setdefault(sentence.get("paragraph", 0), []).append(
            sentence["text"].strip()
        )

    body = [" ".join(paragraphs[number]) for number in sorted(paragraphs)]
    return body, removed


def to_page_response(story, body):
    """Writer 출력을 화면(index.html)이 기대하는 필드로 옮긴다.

    화면은 product_card.price와 문단 배열 body를 읽는다. Writer 스키마는
    price_display와 문장 배열 sentences를 준다. body는 check_sentences가
    근거를 확인하고 문단으로 묶은 결과다.
    """
    card = story.get("product_card") or {}
    headline = (story.get("headline") or "").strip()

    if not headline or not body:
        raise ValueError("Writer 결과가 비어 있습니다.")

    return {
        "status": "READY",
        "product_card": {
            "name": card.get("name") or "",
            "price": card.get("price_display") or ""
        },
        "headline": headline,
        "body": body
    }


def run_story_engine(
    client,
    raw_input,
    scenario_mode=SCENARIO_MODE,
    seller_additions=()
):
    # 체험판(handler)은 기본값으로만 부른다. 두 인자는 기준 샘플처럼
    # DEMO_SELLER나 덧붙인 말이 있는 입력을 테스트할 때만 바꾼다.
    seller_additions = list(seller_additions)

    analyzer_input = {
        "scenario_mode": scenario_mode,
        "raw_input": raw_input,
        "seller_additions": seller_additions
    }

    analysis = call_structured(
        client,
        ANALYZER_PROMPT,
        analyzer_input,
        "lepi_analyzer",
        ANALYZER_SCHEMA
    )

    # 사진이 없으니 conflicts는 비어 있어야 하지만, engine/README.md의 흐름과
    # 같이 하나라도 있으면 질문으로 돌린다. Writer는 부르지 않는다.
    if (
        analysis.get("story_readiness") == "NEED_MORE_INFO"
        or analysis.get("conflicts")
    ):
        return {
            "status": "NEED_MORE_INFO",
            # 질문은 최대 2개 — 사장님에게 숙제를 늘리지 않는다.
            "questions": (analysis.get("questions") or [])[:2]
        }

    if analysis.get("story_readiness") != "READY":
        return {"status": "NO_STORY_FOUND"}

    story = call_structured(
        client,
        WRITER_PROMPT,
        {
            "raw_input": raw_input,
            "seller_additions": seller_additions,
            "analysis": analysis,
            "output_request": OUTPUT_REQUEST
        },
        "lepi_writer",
        WRITER_SCHEMA
    )

    # 헤드라인 후보는 고르는 과정을 보려는 것이라 화면에는 내려보내지 않고
    # 서버 로그(Vercel 함수 로그)에만 남긴다.
    print(
        "headline_candidates:",
        json.dumps(
            {
                "candidates": story.get("headline_candidates"),
                "chosen": story.get("headline")
            },
            ensure_ascii=False
        )
    )

    body, removed = check_sentences(
        story.get("sentences"),
        raw_input,
        seller_additions,
        analysis
    )

    # 근거가 없어 지운 문장도 화면에는 보이지 않으니 로그로 남긴다.
    # 같은 이유로 자주 지워지는 문장이 프롬프트를 고칠 단서다.
    if removed:
        print(
            "removed_sentences:",
            json.dumps(removed, ensure_ascii=False)
        )

    return to_page_response(story, body)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(
                self.headers.get("Content-Length", 0)
            )

            if content_length > MAX_BODY_BYTES:
                self.send_json(
                    413,
                    {
                        "error": TOO_LONG_MESSAGE
                    }
                )
                return

            request_body = self.rfile.read(content_length)
            request_data = json.loads(request_body)

            original_text = request_data.get("text", "").strip()

            if not original_text:
                self.send_json(
                    400,
                    {
                        "error": "상품 메모를 입력해 주세요."
                    }
                )
                return

            if len(original_text) > MAX_TEXT_LENGTH:
                self.send_json(
                    413,
                    {
                        "error": TOO_LONG_MESSAGE
                    }
                )
                return

            api_key = os.environ.get("OPENAI_API_KEY")

            if not api_key:
                self.send_json(
                    500,
                    {
                        "error": "OpenAI API 키가 설정되지 않았습니다."
                    }
                )
                return

            client = OpenAI(api_key=api_key)

            mode = request_data.get("mode")
            scenario_mode = (
                SCENARIO_BY_MODE.get(mode, SCENARIO_MODE)
                if isinstance(mode, str)
                else SCENARIO_MODE
            )

            self.send_json(
                200,
                run_story_engine(client, original_text, scenario_mode)
            )

        # UTF-8이 아닌 본문(예: 윈도우 터미널의 CP949)은 json.loads에서
        # UnicodeDecodeError가 난다. 이것도 요청 쪽 잘못이라 400으로 돌린다.
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(
                400,
                {
                    "error": "요청 데이터 형식이 올바르지 않습니다."
                }
            )

        except Exception as error:
            print("OpenAI API 오류:", error)

            self.send_json(
                500,
                {
                    "error": "이야기를 입히는 중 오류가 발생했습니다."
                }
            )

    def send_json(self, status_code, data):
        response_body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status_code)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(response_body))
        )

        self.end_headers()
        self.wfile.write(response_body)
