from http.server import BaseHTTPRequestHandler
import json


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            request_body = self.rfile.read(content_length)
            request_data = json.loads(request_body)

            original_text = request_data.get("text", "").strip()

            if not original_text:
                self.send_json(
                    400,
                    {"error": "변환할 글을 입력해 주세요."}
                )
                return

            result = {
                "result": f"[테스트 변환 결과]\n{original_text}"
            }

            self.send_json(200, result)

        except json.JSONDecodeError:
            self.send_json(
                400,
                {"error": "요청 데이터 형식이 올바르지 않습니다."}
            )

        except Exception:
            self.send_json(
                500,
                {"error": "서버에서 오류가 발생했습니다."}
            )

    def send_json(self, status_code, data):
        response_body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)