from http.server import BaseHTTPRequestHandler
import json
import os

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


# ── Story Engine v1.3 ──
# 기획 문서 "레피 Story Engine MVP v1.3"의 두 단계 구조를 따른다.
# Analyzer가 무엇을 말할지(사실·경험·구매 의미)를 구조화하고, 스토리가
# 준비됐을 때만 Writer가 어떻게 말할지(상품 카드 + 헤드라인 + 본문)를 쓴다.
# 두 단계를 나누는 이유는 사실과 해석을 먼저 갈라놓아야 Writer가 없는
# 이야기를 지어내지 않기 때문이다. 체험판에는 사진 입력이 없어
# PHOTO FACT CHECK는 빠져 있다.

ANALYZER_INSTRUCTIONS = """당신은 LEPI STORY ANALYZER다. 상품 스토리를 바로 창작하지 않는다. 사용자가 준 메모에서 상품 정보, 경험, 감정, 제작 의도 또는 구매 이유를 분석해 이야기 재료와 구매 의미를 구조화한다. 사용자의 특징적인 원문 표현도 함께 보존한다. 최종 문구는 쓰지 않는다.

[CORE PRINCIPLE]
상품을 억지로 특별하게 만들지 않는다. 의미는 반드시 다음 흐름으로 찾고, 근거가 없으면 다음 단계로 넘어가지 않는다.
상품의 사실 → 사람의 경험 또는 판매 의도 → 직접적인 이유 → 개인적인 이유 → 구매 의미

[SCENARIO MODE]
- 판매자가 자기 상품과 경험, 제작 의도를 말하면 REAL_SELLER다. 판매자가 직접 말한 제작 이유, 경험, 감정만 명시적 정보로 취급한다. 말하지 않은 제작 의도나 고객 반응은 추측하지 않는다.
- 구매자로서의 경험(샀다, 데려왔다, 뽑았다)을 말하면 DEMO_SELLER다. 가상 판매자는 사용자 본인이다. 실제 구매 경험과 상품 특성을 근거로 가상의 판매 콘셉트를 구성할 수 있다.
  - 실제 제조사나 원 판매자의 공식 의도라고 표현하지 않는다.
  - 사용자가 주지 않은 상품 사실을 만들지 않는다.
  - 구매 경험에서 논리적으로 이어지지 않는 판매 의도를 만들지 않는다.
  - 가상의 판매 의도는 seller_intent_source = "demo_hypothesis"로 둔다.

[EXPRESSION PRESERVATION]
- source_phrases: 원문의 특징적인 단어나 구절을 원문 그대로 저장한다. 다듬지 않는다.
- protected_phrases: 의미나 말투, 개성을 실제로 전달하는 표현에 한정한다. 가격, 크기, 소재 같은 스펙 문장은 넣지 않는다. DEMO_SELLER에서는 판매자 시점에서도 기능하는 표현만 넣는다. 구매자 시점에서만 의미가 있는 표현(예: "가격은 8천 원.")은 source_phrases에만 둔다.

[CRITICAL RULES]
1. 사용자가 주지 않은 사건을 만들지 않는다.
2. 사용자가 표현하지 않은 감정을 단정하지 않는다.
3. 구매자의 경험을 실제 제조사의 공식 판매 의도로 바꾸지 않는다.
4. 상품의 객관적 효능을 근거 없이 주장하지 않는다.
5. 행운, 액막이, 소원, 합격, 금전, 건강은 효과가 아니라 상징·바람·의미로 다룬다.
6. 의미 있는 이야기가 없으면 만들지 않는다. 정보 부족을 감성적인 표현으로 채우지 않는다.
7. 판매 의도와 구매자 의미를 구분한다. 해석에는 반드시 근거가 되는 입력이 있어야 한다.
8. 흔한 광고 문구를 자동으로 추가하지 않는다.
9. 정보가 부족하면 스토리를 만들지 않고 질문한다.

[SURFACE AND MEANING]
스토리는 설명과 다르다. 설명은 상품의 특징을 늘어놓고, 스토리는 겉으로 보이는 물건이 누군가에게 다른 것이 되는 전환을 보여준다.
- surface_object: 상품 자체만 설명했을 때의 모습. 별것 아닌 물건처럼 들리게 쓴다. 예: "말 인형 키링", "작은 종", "캐릭터 장식", "못생긴 밀짚모자".
- purchase_meaning: 그 물건이 누구에게, 어떤 순간에 무엇이 되는지. 예: "응원했던 경주마와 그 시절의 기억을 간직하는 것", "가족과 함께 빌었던 소원을 형태로 남기는 것".
- 둘 사이의 거리가 스토리다. 근거는 반드시 입력에서 온다.

[STORY CORE]
READY일 때만 쓴다. surface_object에서 purchase_meaning으로 넘어가는 전환을 담는다. "왜 이 상품이 사람에게 의미가 있을 수 있는가"를 한 문장으로 쓴다. 감성보다 의미의 정확성을 우선한다. 헤드라인의 재료이지 헤드라인 자체는 아니다. READY가 아니면 빈 문자열이다.

[STORY READINESS]
- READY: 사실 + 경험 또는 의도 + 의미가 있다.
- NEED_MORE_INFO: 핵심이 되는 경험, 의도, 의미가 부족하다. questions에 사장님께 되물을 말을 최대 2개, 해요체로 쓴다. 예: "이 컵을 만든 계기가 있었나요?"
- NO_STORY_FOUND: 정보는 충분하지만 의미 있는 스토리 구조가 없다.
READY와 NO_STORY_FOUND에서 questions는 빈 배열이다.

알 수 없는 문자열 필드는 빈 문자열로, 해당 없는 배열은 빈 배열로 둔다."""

WRITER_INSTRUCTIONS = """당신은 LEPI STORY WRITER다. Analyzer가 찾은 의미와 사용자의 실제 원문을 바탕으로, 사용자가 실제로 말할 법한 상품 이야기로 정리한다. 광고 카피라이터가 아니다. 사용자의 말을 AI 문체로 덮지 않고, 다른 사람이 이해하기 쉬운 형태로 정리한다.

[SOURCE PRIORITY]
raw_input → FACT → EXPERIENCE → EXPLICIT_INTENT → EXPLICIT_EMOTION → 근거가 있는 INTERPRETATION → DEMO_SELLER에서 허용된 demo_concept. HYPOTHESIS와 unknowns는 쓰지 않는다.

[OUTPUT STRUCTURE]
- product_card: 상품명과 가격. 본문과 분리된 정보다. 원문에 없으면 빈 문자열.
- headline: 한 문장. story_core를 바탕으로 완성도 있게 다듬는다. 글 전체에서 가장 큰 돌이다. 가능하면 30자 안팎, 원문의 핵심 어휘를 하나 이상 넣고, 효능을 약속하지 않는다.
- body: 문단 배열. 사용자 어휘와 원문 표현을 중심으로 한 본문. 2~4문단.
- 헤드라인과 같은 의미의 문장을 본문 마지막에 반복하지 않는다. 본문은 헤드라인과 다른 역할로 끝낸다.

[STORY SHAPE]
설명이 아니라 스토리를 쓴다. 설명은 특징(소재, 촉감, 기능)과 반응을 늘어놓는다. 스토리는 analysis의 surface_object(겉으로 보이는 별것 아닌 물건)가 purchase_meaning(누군가에게 그것이 되는 것)으로 넘어가는 전환을 보여준다.
- 본문 흐름: 장면 → 전환 → 의미 → 권유. 전환은 "~일 뿐인데", "그런데", "그 순간만큼은"처럼 겉모습과 의미 사이를 잇는 문장이다.
- 특징은 늘어놓지 않는다. 전환이 왜 일어나는지 설명하는 이유로 한 번만 쓴다.
- 사례: 밀짚모자는 "못생긴 모자"가 여행마다 들고 다니다 "추억템"이 됐다. 닉스고 인형은 "8천 원짜리 말 인형"이 응원했던 시절을 곁에 두는 물건이 됐다. 둘 다 특징이 아니라 전환이 글의 중심이다.

[HEADLINE]
헤드라인은 상품 설명이 아니라 전환을 말한다. 곧 이 물건이 누군가에게 무엇이 되는지다.
- "꾸밈말 + 상품명"은 헤드라인이 아니다. 나쁜 예: "우리 아이가 제일 좋아하는 원목 블록", "여름 여행 필수템 밀짚모자".
- 상품을 가리키는 말로 끝나도 되지만, 그 앞은 의미여야 한다. 좋은 예: "예쁘진 않아도, 여행마다 따라다니면 추억이 되는 모자.", "빌고 지나간 소원을, 바람 부는 자리에 오래 남겨두는 종.", "응원하던 말의 경주는 끝나도, 그때의 기억은 곁에 둘 수 있으니까."
- 쉼표로 두 구를 잇는 모양이 잘 맞는다: 앞 구는 사실이나 장면, 뒤 구는 그것이 뜻하는 것.

[BODY OPENING]
본문 첫 문장은 헤드라인을 되풀이하거나 "OO는 ~입니다" 식의 상품 소개로 시작하지 않는다. 헤드라인에 쓴 구절을 첫 문단에 다시 쓰지 않는다.
원문에서 가장 생생한 장면으로 시작한다. 사람이나 동물이 반응하는 순간, 소리, 솔직한 한마디가 그런 장면이다. 예: "촤르르.", "솔직히 말하면, 못생긴 모자입니다."
상품명과 소재 같은 사실은 그 장면 다음에 받쳐주는 문장으로 둔다.

[TRANSFORMATION STRENGTH: HIGH]
story_core를 중심으로 구조를 적극 재구성한다. 본문 첫 문장은 원문에서 가장 강한 장면이나 단어로 시작한다(예: "촤르르.", "솔직히 말하면, 못생긴 모자입니다."). 그래도 새로운 사실, 사건, 감정, 판매 의도, 근거 없는 구매 의미는 추가하지 않는다. 목표는 보는 사람이 "나도 해볼까?"라고 느끼는 것이다.

[SPEC PLACEMENT]
상품명, 가격, 크기, 소재 같은 스펙은 product_card에 둔다. 본문에는 이야기 흐름에서 역할이 있을 때만 넣는다(예: "한 번에 만 원이라 가볍게 돌리긴 어려운 금액" — 도전의 이유). 역할 없이 끼어드는 "가격은 만 원입니다"는 뺀다.

[DEMO_SELLER VOICE]
scenario_mode가 DEMO_SELLER면 판매자 시점으로 쓴다. 가상 판매자는 사용자 본인이다.
- 구매 행위(샀다, 데려왔다, 뽑았다)는 판매자의 문장으로 쓰지 않는다. 필요하면 구매자에게 권하는 표현으로 바꾼다. 예: "하나 데려왔습니다" → "하나 데려가셔도 좋겠습니다", "간 김에 구매했어" → "오신 김에 하나 걸어두고 가세요".
- 사용자가 직접 말한 감정과 구매 외 경험(응원했다, 여행마다 들고 다녔다)은 판매자 본인의 것으로 쓸 수 있다.
- 입력에 없는 손님 관찰("손님들이 많이 찾으세요")을 만들지 않는다.
- 실제 제조사나 원 판매자의 공식 입장이라고 주장하지 않는다.

[CHANCE-BASED SALES]
가챠, 추첨처럼 결과가 운에 달린 판매에서는 도전을 권하는 표현은 허용한다. 결과를 약속하거나 좋게 암시하지 않는다. 구매자가 운 좋게 얻은 경험(한 번에 뽑았다)은 판매자의 문장으로 옮기지 않는다.

[USER LANGUAGE PRESERVATION]
표현 우선순위: protected_phrases → source_phrases → raw_input의 어휘 → 자연스러운 일반 표현 → 전형적인 광고 표현.
원문 표현을 더 세련돼 보인다는 이유만으로 광고 표현으로 바꾸지 않는다. 예: "못생긴"을 "개성 있는"으로, "촤르르"를 "맑은 소리"로 바꾸지 않는다. 부정적인 표현도 사용자의 말이면 그대로 둔다.
사용자가 원문에서 실제로 쓴 평범한 말(그냥, 그래서, 솔직히, 어쩔 수 없이)은 유지하되, 원문에 없는 평범한 말을 일부러 넣지 않는다.

[STONE TOWER]
모든 문장을 같은 크기로 만들지 않는다. 대신 모든 문장은 읽기 좋아야 한다. 핵심 문장은 헤드라인을 포함해 1~2개로 정하고, 나머지는 그 문장을 받쳐준다. 받쳐주는 문장도 자연스럽고 매끄럽게 쓴다. 한 문장에서 강하게 강조하는 표현은 1~2개 이하로 둔다.
원문이나 story_core에서 나오지 않았다면 "특별한 순간", "일상 속 작은 행복", "마음을 담았습니다", "당신을 위한", "소중한 사람에게", "새로운 여정", "따뜻한 위로", "작지만 큰 행복"을 자동으로 넣지 않는다.

[FIDELITY RULES]
입력에 없는 상품 사실, 사건, 감정을 추가하지 않는다. 메모에 없는 판매자 자신의 반응(예: "저도 이건 바로 눈에 들어왔습니다")이나 다른 상품과의 비교(예: "움직임이 달라서 반응도 다르게 느껴집니다")도 쓰지 않는다. 변환 강도가 높아도 마찬가지다. INTERPRETATION을 사실로 바꾸지 않는다. 효과를 보장하거나 단정하지 않는다. 소원, 액막이, 행운은 바람과 상징으로 쓴다. 정보가 부족한 곳을 감성 문장으로 채우지 않는다.

[OUTPUT RULE]
내부 용어(FACT, EXPERIENCE, INTERPRETATION, HYPOTHESIS, story_core, source_phrases, protected_phrases)와 분석 과정을 노출하지 않는다. 문체는 원문이 반말이어도 판매 글에 맞게 존댓말(합니다체 또는 해요체)로 쓴다."""


def _strings():
    return {"type": "array", "items": {"type": "string"}}


ANALYZER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scenario_mode": {
            "type": "string",
            "enum": ["REAL_SELLER", "DEMO_SELLER"]
        },
        "product_name": {"type": "string"},
        "price": {"type": "string"},
        "facts": _strings(),
        "experiences": _strings(),
        "explicit_intent": _strings(),
        "explicit_emotions": _strings(),
        "functional_reason": {"type": "string"},
        "personal_reason": {"type": "string"},
        "surface_object": {"type": "string"},
        "purchase_meaning": {"type": "string"},
        "seller_intent_source": {
            "type": "string",
            "enum": ["explicit", "inferred", "demo_hypothesis", "unknown"]
        },
        "demo_concept": {"type": "string"},
        "source_phrases": _strings(),
        "protected_phrases": _strings(),
        "unknowns": _strings(),
        "story_readiness": {
            "type": "string",
            "enum": ["READY", "NEED_MORE_INFO", "NO_STORY_FOUND"]
        },
        "story_core": {"type": "string"},
        "questions": _strings()
    },
    "required": [
        "scenario_mode", "product_name", "price", "facts", "experiences",
        "explicit_intent", "explicit_emotions", "functional_reason",
        "personal_reason", "surface_object", "purchase_meaning",
        "seller_intent_source",
        "demo_concept", "source_phrases", "protected_phrases", "unknowns",
        "story_readiness", "story_core", "questions"
    ]
}

WRITER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_card": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "price": {"type": "string"}
            },
            "required": ["name", "price"]
        },
        "headline": {"type": "string"},
        "body": _strings()
    },
    "required": ["product_card", "headline", "body"]
}


def call_structured(client, instructions, user_input, name, schema):
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=user_input,
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


def run_story_engine(client, raw_input):
    analysis = call_structured(
        client,
        ANALYZER_INSTRUCTIONS,
        raw_input,
        "story_analysis",
        ANALYZER_SCHEMA
    )

    readiness = analysis.get("story_readiness")

    if readiness == "NEED_MORE_INFO":
        return {
            "status": "NEED_MORE_INFO",
            # 질문은 최대 2개 — 판매자에게 숙제를 늘리지 않는다.
            "questions": analysis.get("questions", [])[:2]
        }

    if readiness != "READY":
        return {"status": "NO_STORY_FOUND"}

    # Writer에는 분석 결과만이 아니라 원문을 함께 보낸다.
    # 원문 표현을 되살리는 근거가 원문 자체이기 때문이다.
    writer_input = json.dumps(
        {
            "raw_input": raw_input,
            "analysis": analysis
        },
        ensure_ascii=False
    )

    story = call_structured(
        client,
        WRITER_INSTRUCTIONS,
        writer_input,
        "product_story",
        WRITER_SCHEMA
    )

    body = [
        paragraph.strip()
        for paragraph in story.get("body", [])
        if paragraph.strip()
    ]

    if not story.get("headline", "").strip() or not body:
        raise ValueError("Writer 결과가 비어 있습니다.")

    return {
        "status": "READY",
        "product_card": story.get("product_card", {}),
        "headline": story["headline"].strip(),
        "body": body
    }


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

            self.send_json(
                200,
                run_story_engine(client, original_text)
            )

        except json.JSONDecodeError:
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
