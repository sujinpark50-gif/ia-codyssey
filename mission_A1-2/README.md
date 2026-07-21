# AI 여행지 추천 프로그램

## 1. 프로그램 개요

사용자가 입력한 여행 날짜를 바탕으로 AI가 적합한 국내 여행지를 추천하는 프로그램입니다.

OpenAI API를 이용하여 추천 여행지, 예상 날씨, 추천 이유, 행사 정보를 생성하고, Kakao Local API를 이용하여 추천 지역의 맛집 정보를 검색합니다.

최종 결과는 Markdown 파일로 생성되어 `result` 폴더에 저장됩니다.

### 주요 기능

- 여행 날짜 입력 및 형식 검증
- AI를 활용한 국내 여행지 추천
- 추천 지역의 예상 날씨 및 행사 정보 제공
- Kakao Local API를 활용한 맛집 검색
- 여행 정보를 Markdown 파일로 저장

---

## 2. 실행 방법

### 1) 필요한 패키지 설치

```bash
pip install requests python-dotenv
```

### 2) 프로그램 실행

여행 날짜를 `YYYY-MM-DD` 형식으로 입력하여 실행합니다.

```bash
python main.py --date 2026-07-20
```

예시

```bash
python main.py --date 2026-08-15
```

날짜 형식이 올바르지 않으면 오류 메시지를 출력하고 프로그램이 종료됩니다.

---

## 3. API 키 설정 방법

프로젝트 최상위 폴더에 `.env` 파일을 생성합니다.

프로젝트 구조 예시

```text
mission_A1-2/
├── main.py
├── .env
├── .gitignore
├── README.md
└── result/
```

`.env` 파일에 다음 내용을 작성합니다.

```env
OPENAI_API_KEY=본인의_OpenAI_API_키
OPENAI_API_URL=https://api.openai.com/v1/chat/completions
OPENAI_MODEL=사용할_OpenAI_모델명

KAKAO_REST_API_KEY=본인의_Kakao_REST_API_키
KAKAO_LOCAL_API_URL=https://dapi.kakao.com/v2/local/search/keyword.json
```

예시

```env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
KAKAO_REST_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
```

Kakao Local API를 사용하려면 Kakao Developers에서 애플리케이션을 생성한 후 Local API 사용을 활성화해야 합니다.

---

## 4. 결과물 확인 방법

프로그램이 정상적으로 실행되면 `result` 폴더에 Markdown 파일이 생성됩니다.

파일명 예시

```text
result/
└── 2026-07-20_강릉.md
```

생성되는 Markdown 파일에는 다음 정보가 포함됩니다.

- 추천 여행지
- 예상 날씨
- 추천 이유
- 행사 정보
- 추천 맛집
- 맛집 주소
- 음식점 분류
- 전화번호
- 카카오맵 URL
- 위도 및 경도

VS Code에서는 생성된 `.md` 파일을 열고 **Ctrl + Shift + V**를 누르면 Markdown 미리보기로 결과를 확인할 수 있습니다.

---

## 5. API 키 보안 주의 사항

API 키는 외부에 공개되지 않도록 반드시 관리해야 합니다.

### `.env` 파일을 GitHub에 업로드하지 않기

`.gitignore` 파일에 다음 내용을 추가합니다.

```gitignore
.env
```

### API 키를 코드에 직접 작성하지 않기

잘못된 예

```python
OPENAI_API_KEY = "실제 API 키"
```

올바른 예

```python
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
```

### 화면 공유 및 캡처 시 주의

다음 정보가 노출되지 않도록 확인합니다.

- `.env` 파일 내용
- OpenAI API Key
- Kakao REST API Key
- API 요청 헤더
- 인증 정보

### API 키가 유출된 경우

API 키가 GitHub 또는 인터넷에 공개되었다면 기존 키를 폐기하고 새로운 키를 발급받아 사용해야 합니다.