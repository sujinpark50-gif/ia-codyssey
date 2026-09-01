import argparse
import os
import requests
import json
import sys

from datetime import datetime
from dotenv import load_dotenv


# =========================
# 환경변수 불러오기
# =========================

load_dotenv()

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

API_KEY = os.getenv("OPENAI_API_KEY")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_API_URL = os.getenv("KAKAO_LOCAL_API_URL")


# =========================
# API 키 확인
# =========================

if not API_KEY:
    print("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    print(".env 파일에 OPENAI_API_KEY를 설정해 주세요.")
    sys.exit()

if not KAKAO_REST_API_KEY:
    print("KAKAO_REST_API_KEY가 설정되어 있지 않습니다.")
    print(".env 파일에 KAKAO_REST_API_KEY를 설정해 주세요.")
    sys.exit()

if not KAKAO_API_URL:
    print("KAKAO_LOCAL_API_URL이 설정되어 있지 않습니다.")
    print(".env 파일에 KAKAO_LOCAL_API_URL을 설정해 주세요.")
    sys.exit()


# =========================
# API 요청 헤더
# =========================

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

kakao_headers = {
    "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"
}


# 오류 기록
errors = []


# =========================
# CLI 날짜 입력
# =========================

parser = argparse.ArgumentParser()

parser.add_argument(
    "-date",
    "--date",
    dest="date",
    required=True
)

args = parser.parse_args()


# 날짜 형식 확인
try:
    datetime.strptime(args.date, "%Y-%m-%d")
    print("여행 날짜:", args.date)

except ValueError:
    print("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.")
    parser.print_usage()
    sys.exit()


# =========================
# 캐시 파일 확인
# =========================

cache_file = os.path.join(
    "results",
    f"{args.date}_travel_plan_raw.json"
)

markdown_cache_file = os.path.join(
    "results",
    f"{args.date}_travel_plan.md"
)

def load_cache(cache_file):
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        print("기존 캐시 파일을 사용합니다.")

        return data

    except (OSError, json.JSONDecodeError) as e:
        print(f"캐시 파일을 읽지 못했습니다: {e}")
        return None

cache_data = load_cache(cache_file)

# =========================
# 1차 LLM 요청
# 복수 여행 지역 추천
# =========================
if cache_data:
    result = cache_data["recommendation"]
    restaurants_by_city = cache_data["restaurants"]
    errors = cache_data.get("errors", [])

    print("저장된 추천 결과와 맛집 정보를 불러왔습니다.")

else:
    prompt = f"""
당신은 국내 여행지를 추천하는 여행 도우미입니다.

사용자가 입력한 여행 날짜는 {args.date}입니다.

해당 시기의 일반적인 날씨와 예상 가능한 행사 또는 축제를 고려하여
대한민국 여행 도시 2~3곳을 추천하세요.

반드시 아래 조건을 지켜서 응답하세요.

- 설명이나 마크다운을 추가하지 마세요.
- JSON 객체만 출력하세요.
- recommended_cities는 2~3개의 객체를 담은 배열이어야 합니다.
- 각 도시 객체에는 city, weather, events, reason 키가 있어야 합니다.
- events는 문자열 1~3개를 담은 배열이어야 합니다.
- reason은 2~4문장으로 작성하세요.

출력 형식:

{{
  "recommended_cities": [
    {{
      "city": "도시 이름",
      "weather": "해당 시기의 일반적인 날씨 요약",
      "events": ["행사 또는 축제 후보"],
      "reason": "추천 근거"
    }}
  ]
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



# =========================
# 1차 LLM API 호출
# =========================

    try:
        response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=request_data,
            timeout=30
        )

        response.raise_for_status()

        response_data = response.json()

        answer = response_data["choices"][0]["message"]["content"]

    except (requests.RequestException, KeyError, IndexError) as e:
        errors.append(f"1차 LLM API 호출 실패: {e}")

        result = {
        "recommended_cities": []
        }

    else:

    # =========================
    # JSON 파싱
    # =========================

        try:
            result = json.loads(answer)

        except json.JSONDecodeError:

            print("JSON 변환에 실패했습니다. 한 번 더 요청합니다.")

            retry_request_data = request_data.copy()

            retry_request_data["messages"] = (
                request_data["messages"].copy()
            )

            retry_request_data["messages"].append({
                "role": "user",
                "content": """
    이전 답변을 올바른 JSON 형식으로 다시 출력하세요.

    반드시 아래 구조를 따르세요.

    {
        "recommended_cities": [
        {
        "city": "도시 이름",
        "weather": "날씨 요약",
        "events": ["행사 또는 축제"],
        "reason": "추천 근거"
        }
        ]
    }

    recommended_cities에는 2~3개의 도시 객체를 포함하세요.

    설명, 마크다운 코드 블록, 추가 문장은 작성하지 말고
    파싱 가능한 JSON 객체만 출력하세요.
    """
            })

            try:
                retry_response = requests.post(
                    OPENAI_API_URL,
                    headers=headers,
                    json=retry_request_data,
                    timeout=30
                )

                retry_response.raise_for_status()

                retry_content = (
                    retry_response
                    .json()["choices"][0]["message"]["content"]
                )

                result = json.loads(retry_content)

                errors.append(
                    "LLM 응답의 JSON 파싱 실패 후 재요청하여 복구함"
                )

            except (
                requests.RequestException,
                KeyError,
                IndexError,
                json.JSONDecodeError
            ) as e:

                errors.append(f"LLM JSON 재시도 실패: {e}")

                result = {
                    "recommended_cities": []
                }


# =========================
# 추천 도시 출력
# =========================

    cities = result.get("recommended_cities", [])

    if cities:
        print("\n추천 도시")

        for city_info in cities:
            print("-", city_info["city"])

    else:
        print("추천 도시를 생성하지 못했습니다.")


# =========================
# Kakao Local 맛집 검색
# =========================

    restaurants_by_city = {}

    if cities:

        for city_info in cities:

            city = city_info["city"]

            kakao_params = {
                "query": city + " 맛집",
                "category_group_code": "FD6",
                "size": 5,
                "sort": "accuracy"
            }

            city_restaurants = []

            try:
                place_response = requests.get(
                    KAKAO_API_URL,
                    headers=kakao_headers,
                    params=kakao_params,
                    timeout=30
                )

                place_response.raise_for_status()

                place_data = place_response.json()

                for place in place_data.get("documents", []):

                    restaurant = {
                        "name": place["place_name"],
                        "address": (
                            place["road_address_name"]
                            or place["address_name"]
                        ),
                        "category": place.get(
                            "category_name",
                            ""
                        ),
                        "phone": place.get(
                            "phone",
                            ""
                        ),
                        "url": place.get(
                            "place_url",
                            ""
                        ),
                        "lng": float(place["x"]),
                        "lat": float(place["y"])
                    }

                    city_restaurants.append(
                        restaurant
                    )

                restaurants_by_city[city] = (
                    city_restaurants
                )

                print(
                    f"{city} 추천 맛집이 "
                    f"{len(city_restaurants)}개 검색되었습니다."
                )

            except (
                requests.RequestException,
                KeyError,
                ValueError
            ) as e:

                restaurants_by_city[city] = []

                errors.append(
                    f"{city} 맛집 API 오류: {e}"
                )

                print(
                    f"{city} 맛집 정보를 가져오지 못했습니다."
                )

    else:
        errors.append(
        "추천 도시를 생성하지 못해 맛집 검색을 진행하지 않음"
        )


# =========================
# 최종 Markdown 리포트 생성
# =========================

def generate_final_report(
    result,
    restaurants_by_city,
    errors
):

    report_prompt = f"""
당신은 여행 리포트를 작성하는 도우미입니다.

아래 여행 추천 정보와
도시별 맛집 정보를 바탕으로
최종 여행 리포트를 Markdown 형식으로 작성하세요.

여행 추천 정보:

{json.dumps(
    result,
    ensure_ascii=False,
    indent=2
)}

도시별 맛집 정보:

{json.dumps(
    restaurants_by_city,
    ensure_ascii=False,
    indent=2
)}

오류 정보:

{json.dumps(
    errors,
    ensure_ascii=False,
    indent=2
)}

반드시 각 추천 도시별로 아래 내용을 정리하세요.

# 여행 추천 리포트

## 도시 이름

### 추천 이유

### 날씨

### 행사/축제

### 추천 맛집

맛집 검색 결과가 없으면
"데이터 없음"이라고 작성하세요.

### 1일 일정

- 오전
- 오후
- 저녁

마지막에는 아래 항목을 작성하세요.

## Errors

오류가 있으면 목록으로 작성하고,
오류가 없으면 "없음"이라고 작성하세요.

Markdown 텍스트만 출력하세요.
"""

    report_request_data = {
        "model": "gpt-5.4-mini",
        "messages": [
            {
                "role": "user",
                "content": report_prompt
            }
        ]
    }

    try:
        response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=report_request_data,
            timeout=30
        )

        response.raise_for_status()

        response_data = response.json()

        report = (
            response_data["choices"][0]
            ["message"]["content"]
        )

        return report

    except (
        requests.RequestException,
        KeyError,
        IndexError
    ) as e:

        errors.append(
            f"최종 리포트 생성 실패: {e}"
        )

        return (
            "# 여행 추천 리포트\n\n"
            "최종 리포트를 생성하지 못했습니다."
        )


# =========================
# 원본 JSON 저장
# =========================

def save_json(
    result,
    restaurants_by_city,
    errors
):

    os.makedirs(
        "results",
        exist_ok=True
    )

    filename = os.path.join(
        "results",
        f"{args.date}_travel_plan_raw.json"
    )

    data = {
        "recommendation": result,
        "restaurants": restaurants_by_city,
        "errors": errors
    }

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4
        )

    print(
        f"'{filename}' 파일이 저장되었습니다."
    )


# =========================
# Markdown 저장
# =========================

def save_markdown(
    report
):

    os.makedirs(
        "results",
        exist_ok=True
    )

    filename = os.path.join(
        "results",
        f"{args.date}_travel_plan.md"
    )

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(report)

    print(
        f"'{filename}' 파일이 저장되었습니다."
    )


# =========================
# 최종 실행
# =========================

final_report = generate_final_report(
    result,
    restaurants_by_city,
    errors
)


save_json(
    result,
    restaurants_by_city,
    errors
)


print(
    "\n===== 최종 여행 리포트 =====\n"
)

print(final_report)


save_markdown(
    final_report
)