import argparse
import os
import requests
import json

api_key = os.getenv("OPENAI_API_KEY")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}



from datetime import datetime


parser = argparse.ArgumentParser()

parser.add_argument("--date", required=True)

args = parser.parse_args()

try:
    datetime.strptime(args.date, "%Y-%m-%d")
    print("여행 날짜:", args.date)

except ValueError:
    print("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.")


prompt = f"""
당신은 국내 여행지를 추천하는 여행 도우미입니다.

사용자가 입력한 여행 날짜는 {args.date}입니다.
해당 시기의 일반적인 날씨와 예상 가능한 행사 또는 축제를 고려하여
대한민국 여행 도시 한 곳을 추천하세요.

반드시 아래 조건을 지켜서 응답하세요.

- 설명이나 마크다운을 추가하지 마세요.
- JSON 객체만 출력하세요.
- 모든 필수 키를 포함하세요.
- events는 문자열 1~3개를 담은 배열이어야 합니다.
- reason은 2~4문장으로 작성하세요.

출력 형식:
{{
  "recommended_city": "도시 이름",
  "weather": "해당 시기의 일반적인 날씨 요약",
  "events": ["행사 또는 축제 후보"],
  "reason": "추천 근거"
}}
"""

request_data = {
    "model": "gpt-5",
    "messages": [
        {
            "role": "user",
            "content": prompt
        }
    ]
}

response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=request_data)
response_data = response.json()

answer = response_data["choices"][0]["message"]["content"]
result = json.loads(answer)

response.raise_for_status()

print(result["recommended_city"])
print(result["weather"])
print(result["events"])
print(result["reason"])
