
# LEPI

기록은 있는데 글쓰기가 어려운 사람을 위한 AI 글쓰기 보조 서비스입니다.

LEPI는 사용자가 작성한 메모나 초안을 입력하면 AI가 원문의 의미와 사실관계를 유지하면서
더 자연스럽고 읽기 좋은 글로 다듬어주는 웹 서비스입니다.

현재 무료 베타 형태로 운영하며, 사용자가 직접 AI 글 다듬기 기능을 체험하고
무료 베타를 신청할 수 있도록 구현했습니다.


## 1. 주요 기능

### AI 글 다듬기

사용자가 작성한 원문을 입력하면 AI가 글을 자연스럽게 다듬어 결과를 제공합니다.

- 사용자 원문 입력
- AI API를 통한 글 변환
- 변환 결과 화면 출력
- 빈 입력 및 API 오류 처리
- 처리 중 버튼 비활성화 및 상태 안내


### 무료 베타 신청

랜딩페이지에서 사용자가 직접 무료 베타를 신청할 수 있습니다.

수집 항목:

- 이름
- 활동 분야
- SNS 주소
- 이메일
- 최근 작성한 글
- 개인정보 수집 동의


### 무료 베타 신청 자동화

무료 베타 신청 데이터는 n8n Webhook과 연결하여 자동으로 처리합니다.

처리 흐름:

사용자 신청
→ n8n Webhook
→ Google Sheets 신청 정보 저장
→ Discord 신규 신청 알림

이를 통해 사용자의 입력부터 데이터 저장과 운영자 알림까지 이어지는
기본적인 서비스 운영 자동화 흐름을 구현했습니다.


### 마이크로 인터랙션

무료 베타 신청 과정에서 사용자가 현재 처리 상태를 알 수 있도록
버튼 상태 변화와 시각적 피드백을 적용했습니다.

신청 전
→ 신청 중
→ 신청 완료

신청 성공 시 성공 상태가 명확하게 보이도록 시각적 가시성을 개선하고,
중복 제출을 방지하기 위해 신청 버튼을 비활성화합니다.


## 2. 페이지 구성

LEPI는 하나의 랜딩페이지 안에서 여러 섹션으로 구성되어 있으며
상단 메뉴를 통해 주요 영역으로 이동할 수 있습니다.

주요 구성:

- Hero / 서비스 소개
- LEPI 소개
- AI 글 다듬기 체험
- 무료 베타 신청
- FAQ

데스크톱과 모바일 환경에 대응하는 반응형 레이아웃을 적용했습니다.


## 3. 기술 스택

### Frontend

- HTML
- CSS
- JavaScript

### Backend

- Python
- Vercel Serverless Functions

### AI

- OpenAI API

### 배포

- GitHub
- Vercel

### 운영 자동화

- n8n
- Google Sheets
- Discord


## 4. 프로젝트 구조

```text
mission_A1-3/
├── api/
│   └── refine.py
├── index.html
├── requirements.txt
├── vercel.json
├── README.md
└── service_plan.md
```
#index.html: 랜딩페이지 UI 및 프론트엔드 동작
#api/refine.py: AI 글 다듬기 API
#requirements.txt: Python 패키지 목록
#vercel.json: Vercel 배포 설정
#README.md: 프로젝트 설명
#service_plan.md: 서비스 기획서


5. AI 기능 처리 흐름
사용자 원문 입력
        ↓
JavaScript fetch 요청
        ↓
Vercel Serverless Function
        ↓
OpenAI API
        ↓
AI 변환 결과 반환
        ↓
웹페이지에 결과 표시

프론트엔드에서 API 키를 직접 사용하지 않고
Vercel Serverless Function을 통해 OpenAI API를 호출하도록 구성했습니다.

6. AI 기능 입력 / 출력 / 실패 처리
입력

사용자가 직접 작성한 메모 또는 글의 초안

출력

원문의 의미와 사실관계를 유지하면서 자연스럽게 다듬어진 글

실패 처리
입력값이 비어 있으면 사용자에게 입력 안내 메시지를 표시합니다.
API 요청 중에는 버튼을 비활성화하여 중복 요청을 방지합니다.
API 호출에 실패하면 사용자에게 오류 메시지를 표시합니다.
처리 완료 후 버튼 상태를 복구합니다.
7. 로컬 실행 방법

저장소를 clone 합니다.

git clone <저장소 URL>

프로젝트 폴더로 이동합니다.

cd ia-codyssey/mission_A1-3

필요한 Python 패키지를 설치합니다.

pip install -r requirements.txt

AI API를 사용하려면 환경 변수에 OpenAI API 키를 설정해야 합니다.

로컬 환경에서는 사용 환경에 맞는 방법으로 환경 변수를 설정한 뒤 실행합니다.

8. 환경 변수

필수 환경 변수:

OPENAI_API_KEY

API 키는 코드에 직접 작성하지 않습니다.

Vercel 배포 환경에서는 프로젝트의 Environment Variables 설정에
OPENAI_API_KEY를 등록합니다.

API 키가 GitHub 저장소, README, 스크린샷 등에 노출되지 않도록 주의해야 합니다.

9. 배포

서비스는 GitHub 저장소와 Vercel을 연결하여 배포했습니다.

GitHub의 main 브랜치에 변경 사항을 push하면
Vercel에서 변경 사항을 반영하여 다시 배포합니다.

배포 URL

<https://ia-codyssey-two.vercel.app/>

10. 반응형 확인

데스크톱 브라우저와 실제 모바일 기기에서 화면을 직접 확인했습니다.

모바일 환경에서 다음 항목을 확인했습니다.

주요 섹션 레이아웃
메뉴 및 텍스트 표시
AI 글 입력 영역
AI 변환 기능
무료 베타 신청 폼
신청 버튼 및 완료 상태

실제 모바일 기기에서 레이아웃이 깨지지 않고 정상적으로 표시되는 것을 확인했습니다.

11. 운영 자동화

무료 베타 신청 기능은 n8n을 이용하여 외부 서비스와 연동했습니다.

LEPI 무료 베타 신청
        ↓
n8n Webhook
        ↓
Google Sheets 저장
        ↓
Discord 알림

웹사이트에서 테스트 신청 후 Google Sheets에 신청 정보가 저장되고
Discord로 신규 신청 알림이 전달되는 것을 확인했습니다.

12. 프로젝트에서 학습한 내용

이 프로젝트를 통해 다음 과정을 직접 구현하고 확인했습니다.

HTML, CSS, JavaScript를 이용한 웹페이지 구성
반응형 웹페이지 확인
JavaScript fetch()를 이용한 프론트엔드와 백엔드 통신
Python 기반 Vercel Serverless Function 구현
OpenAI API 연동
환경 변수를 이용한 API 키 관리
GitHub와 Vercel을 이용한 배포
배포 과정에서 발생한 오류 확인 및 수정
n8n Webhook을 이용한 외부 서비스 연동
Google Sheets 데이터 자동 저장
Discord 운영 알림 자동화
사용자 상태 피드백을 위한 마이크로 인터랙션 적용
13. 관련 문서

서비스의 목적, 타겟 사용자, 페이지 구성 및 AI 기능 설계에 대한 자세한 내용은
service_plan.md에서 확인할 수 있습니다.

