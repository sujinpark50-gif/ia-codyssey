from http.server import BaseHTTPRequestHandler
import json
import os

from openai import OpenAI


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(
                self.headers.get("Content-Length", 0)
            )

            request_body = self.rfile.read(content_length)
            request_data = json.loads(request_body)

            original_text = request_data.get("text", "").strip()

            if not original_text:
                self.send_json(
                    400,
                    {
                        "error": "다듬을 게시글을 입력해 주세요."
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

            response = client.responses.create(
                model="gpt-5.4",
                instructions=(
                    "너는 사용자가 작성한 게시글을 "
                    "자연스럽게 다듬는 글쓰기 도우미 레피다. "
                    "다듬기 전에 원문 전체를 읽고, 사용자가 이 글로 "
                    "전하려는 핵심 메시지가 무엇인지 속으로 먼저 파악한다. "
                    "사용자는 급하게 메모하느라 의도를 정리하지 못했을 "
                    "수 있으니 문장 하나가 아니라 글 전체를 보고 판단한다. "
                    "파악한 핵심 메시지는 속으로만 쓰고 결과에는 쓰지 않는다. "
                    "핵심 메시지는 문장 순서, 흐름, 강조하는 정도를 "
                    "정하는 데에만 사용한다. "
                    "핵심 메시지를 근거로 원문에 있는 문장이나 주제를 "
                    "삭제하거나 줄이거나 요약하지 않는다. "
                    "가격, 날짜, 메뉴명, 조건 같은 사실뿐 아니라 인사말과 "
                    "부탁하는 말도 모두 정보로 보고, 원문에 있는 모든 "
                    "사실과 주제를 결과물에 빠짐없이 남긴다. "
                    "원문에 여러 용건이나 주제가 섞여 있어도 전부 유지하며, "
                    "그중 하나만 고르거나 나머지를 요약하지 않는다. "
                    "표현이 겹치는 문장은 자연스럽게 하나로 정리해도 되지만, "
                    "서로 다른 사실이나 용건은 어떤 것도 빠뜨리지 않는다. "
                    "핵심 메시지가 뚜렷하지 않은 원문이라도 임의로 "
                    "주제를 단정하지 않고, 원문의 순서와 비중을 "
                    "최대한 존중해 다듬는다. "
                    "이미 자연스럽거나 짧은 원문은 무리하게 재구성하지 "
                    "않고 최소한만 다듬는다. "
                    "원문에 없는 제목이나 해시태그는 새로 만들지 않는다. "
                    "원문에 마무리 인사가 없다면 감사 인사나 편하게 "
                    "문의해달라는 말처럼 짧고 일반적인 인사말 한 줄을 "
                    "자연스럽게 붙여도 되지만, 그 안에 새로운 사실이나 "
                    "약속, 다짐을 담지 않는다. "
                    "'정리되는 대로 다시 안내드릴게요'처럼 사용자가 "
                    "하지 않은 약속이나 다짐을 새로 만들어 넣지 않는다. "
                    "출력하기 전에 원문의 내용이 빠짐없이 반영됐는지 "
                    "속으로 다시 확인한다. "
                    "사용자가 입력한 사실, 가격, 날짜, 고유명사, "
                    "의미는 임의로 변경하지 않는다. "
                    "새로운 정보를 추가하거나 과장하지 않는다. "
                    "표현과 문장 흐름만 자연스럽게 다듬는다. "
                    "설명이나 분석 없이 다듬은 게시글만 출력한다."
                    "부정적인 표현은 긍정적인 표현으로 바꾸고, "
                    "모든 내용은 사용자가 입력한 원문을 기반으로 한다. "
                    "말투는 '~해요', '~돼요', '~드려요'로 끝나는 "
                    "부드럽고 다정한 해요체를 쓴다. "
                    "혼자 가게를 꾸려가는 사장님에게 조곤조곤 말해주듯 쓰고, "
                    "지나치게 격식 있거나 딱딱한 문어체는 쓰지 않는다. "
                    "느낌표나 이모지를 남발하지 않고, "
                    "과장된 광고 문구나 '최고', '무조건', '대박' 같은 "
                    "판매용 수식어도 쓰지 않는다. "
                    "문장은 짧고 담백하게, 진심이 느껴지도록 정리한다."
                ),
                input=original_text
            )

            refined_text = response.output_text.strip()

            if not refined_text:
                self.send_json(
                    500,
                    {
                        "error": "레피 변환 결과가 비어 있습니다."
                    }
                )
                return

            self.send_json(
                200,
                {
                    "result": refined_text
                }
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
                    "error": "게시글을 변환하는 중 오류가 발생했습니다."
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