from http.server import BaseHTTPRequestHandler
import json
import os

from openai import OpenAI


client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)


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

            if not os.environ.get("OPENAI_API_KEY"):
                self.send_json(
                    500,
                    {
                        "error": "OpenAI API 키가 설정되지 않았습니다."
                    }
                )
                return

            response = client.responses.create(
                model="gpt-5.4",
                instructions=(
                    "너는 사용자가 작성한 게시글을 "
                    "한가지 주제를 중심으로 자연스럽게 다듬는 글쓰기 도우미 레피다. "
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