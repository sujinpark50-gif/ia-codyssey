import argparse
import os
import requests
import json

from dotenv import load_dotenv
load_dotenv() 

API_URL = "https://api.openai.com/v1/chat/completions"
API_KEY = os.getenv("OPENAI_API_KEY")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_API_URL = os.getenv("KAKAO_LOCAL_API_URL")

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

kakao_headers = {
    "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"
}

errors = [] 

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
    "model": "gpt-5.4-mini",
    "messages": [
        {
            "role": "user",
            "content": prompt
        }
    ]
}

response = requests.post(API_URL, 
           headers=headers, 
           json=request_data)

response.raise_for_status()

response_data = response.json()

answer = response_data["choices"][0]["message"]["content"]
try:
    # 첫 번째 JSON 변환 시도
    result = json.loads(content)

except json.JSONDecodeError:
    print("JSON 변환에 실패했습니다. 한 번 더 요청합니다.")

    retry_request_data = request_data.copy()
    retry_request_data["messages"] = request_data["messages"].copy()

    retry_request_data["messages"].append({
        "role": "user",
        "content": """
            이전 답변을 올바른 JSON 형식으로 다시 출력하세요.

            반드시 다음 키만 포함하세요.
            - recommended_city
            - weather
            - reason
            - events

            설명, 마크다운 코드 블록, 추가 문장은 작성하지 말고
            파싱 가능한 JSON 객체만 출력하세요.
            """
    })

    try:
        retry_response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=retry_request_data
        )

        retry_response.raise_for_status()

        retry_content = (
            retry_response.json()["choices"][0]["message"]["content"]
        )

        result = json.loads(retry_content)

        errors.append("LLM 응답의 JSON 파싱 실패 후 재요청하여 복구함")

    except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
        errors.append(f"LLM JSON 재시도 실패: {e}")

        result = {
            "recommended_city": "데이터 없음",
            "weather": "데이터 없음",
            "reason": "데이터 없음",
            "events": []
        }




print("추천도시 : " + result["recommended_city"])



city = result["recommended_city"]

kakao_params = {
    "query": city + " 맛집",
    "category_group_code": "FD6",
    "size": 5,
    "sort": "accuracy"
}

place_response = requests.get(
    KAKAO_API_URL,
    headers=kakao_headers,
    params=kakao_params
)


try:
    place_response.raise_for_status()

    place_data = place_response.json()
    restaurants = []

    for place in place_data["documents"]:
        restaurant = {
            "name": place["place_name"],
            "address": place["road_address_name"] or place["address_name"],
            "category": place.get("category_name", ""),
            "phone": place.get("phone", ""),
            "url": place["place_url"],
            "lng": float(place["x"]),
            "lat": float(place["y"])
        }

        restaurants.append(restaurant)

    print("추천 맛집이 {}개 검색되었습니다.".format(len(restaurants)))
except Exception as e:
    restaurants = []
    errors.append(f"맛집 API 오류: {e}")
    print("맛집 정보를 가져오지 못했습니다.")


def save_markdown(result, restaurants, errors):
    os.makedirs("results",exist_ok=True)
    filename = os.path.join(
        "results",
        f"{args.date}_{result['recommended_city']}_travel_plan.md"
    )

    with open(filename, "w", encoding="utf-8") as file:
        file.write("# 여행 추천 결과\n\n")

        file.write("## 추천 여행지\n")
        file.write(f"{result['recommended_city']}\n\n")

        file.write("## 날씨\n")
        file.write(f"{result['weather']}\n\n")

        file.write("## 추천 이유\n")
        file.write(f"{result['reason']}\n\n")

        file.write("## 행사\n")
        for event in result["events"]:
            file.write(f"- {event}\n")
        file.write("\n")

        if restaurants:
            file.write("## 추천 맛집\n\n")

            for index, restaurant in enumerate(restaurants, start=1):
                file.write(f"### {index}. {restaurant['name']}\n")
                file.write(f"- 주소: {restaurant['address']}\n")
                file.write(f"- 분류: {restaurant['category']}\n")
                file.write(f"- 전화: {restaurant['phone']}\n")
                file.write(f"- URL: {restaurant['url']}\n")
                file.write(f"- 위도: {restaurant['lat']}\n")
                file.write(f"- 경도: {restaurant['lng']}\n\n")
        else:
            file.write("## 추천 맛집\n")
            file.write("데이터 없음\n\n")
    print(f"'{filename}' 파일이 저장되었습니다.") 

save_markdown(result, restaurants, errors)



