# 과제 제출 체크리스트 — AI Agent 개발: 나만의 AI 비서 구축

기준: 과제 원문(사용자 제공, 2026-10-13). 상태: PASS / FAIL / UNVERIFIED / NOT APPLICABLE / ACTION REQUIRED.
마지막 갱신: M6 로컬 구현 완료, 운영 배포 전.

## 필수 요건

| 구분 | 요건 | 상태 | 근거 |
|---|---|---|---|
| 개발 환경 | Python 3.10+, venv | PASS | Python 3.12, `.venv` |
| 개발 환경 | fastapi, uvicorn, firebase-admin, openai, python-dotenv | PASS | `requirements.txt` |
| 데이터 | 시계열 100개 이상 | PASS | UGV 1초 속도 435개 (`docs/DATA_VALIDATION.md`) |
| 데이터 | 요약: 기간·개수·기본 통계 | PASS | `/api/data/summary` start/end, count, mean/min/max |
| 데이터 | 요약: 최근 추세(증가/감소/유지) | ACTION REQUIRED | M6에서 `trend` 추가, 운영 배포 필요 |
| FastAPI | 앱 초기화 + CORS 설정 | PASS (코드) / ACTION REQUIRED (운영 CORS 값) | `ALLOWED_ORIGINS` |
| FastAPI | 로컬 실행, `/docs` | PASS | 로컬 `/docs` 200 |
| Firestore | 서비스 계정 키를 환경 변수로 | PASS | `GOOGLE_APPLICATION_CREDENTIALS`(경로), Render Secret File |
| Firestore | `data`, `conversations` 컬렉션 | PASS | `docs/API.md`, `docs/CHAT.md` |
| 데이터 API | POST/GET/PUT/DELETE `/api/data`, GET `/api/data/summary` | PASS | 자동 테스트, 운영 확인 |
| 대화 API | POST/GET `/api/conversations`, DELETE `/{id}`, (A) GET `/{id}` | PASS | POST는 대화(제목·대상) 저장, 메시지는 `/api/chat`이 자동 저장 |
| 채팅 API | summary → 시스템 프롬프트 → GPT → conversations 자동 저장 | PASS | 운영 실호출 (`docs/DEPLOYMENT.md`) |
| 백엔드 배포 | Render Web Service | PASS | https://adair-mission-intelligence.onrender.com |
| 백엔드 배포 | 배포 URL에서 Swagger `/docs` | ACTION REQUIRED | 현재 `DOCS_ENABLED=false` (404) — 승인 후 공개 |
| 백엔드 배포 | 콜드 스타트 안내 | ACTION REQUIRED | M6 화면 안내 구현, 운영 배포 필요 |
| 프론트엔드 | HTML/CSS/JS, 프레임워크 없음 | PASS | `web/` |
| 프론트엔드 | 채팅: 입력·표시·로딩 | PASS | |
| 프론트엔드 | 데이터 관리: 추가·목록·수정/삭제 | PASS | |
| 프론트엔드 | 대화 기록: 목록·불러오기 | PASS | |
| 프론트엔드 | 데이터 요약 표시(기간·개수·트렌드) | ACTION REQUIRED | 기간·개수 표시 중, 트렌드 라벨은 M6 배포 필요 |
| 프론트 배포 | Vercel 배포 | PASS (프록시 방식) / ACTION REQUIRED (B안 전환) | https://adair-mission-intelligenceweb.vercel.app |
| 프론트 배포 | 환경 변수로 API 서버 URL 설정 | ACTION REQUIRED | `API_BASE_URL` + `build.mjs`, Vercel 설정 필요 |
| README | 서비스 소개, 기술 스택, 로컬 실행 | PASS (일부 갱신 필요) | |
| README | 배포 URL(프론트·백엔드·Swagger) | ACTION REQUIRED | 운영 검증 후 기입 |
| README | 환경 변수 목록(최소 세트) | ACTION REQUIRED | 이름·용도 표로 정리 예정 |
| README | 스크린샷 3종 (채팅·데이터 관리·대화 기록) | ACTION REQUIRED | 운영에서 촬영 예정 |
| 제약 | 키는 환경 변수, 코드 노출 없음 | PASS | 커밋·정적 파일·로그 검사 |
| 제약 | 입력 검증·예외 처리 | PASS | Pydantic, 오류 처리 테스트 |
| 제약 | 백엔드 Render / 프론트 Vercel | PASS (배포됨) | |
| 비용 | 토큰 제한(max_tokens 등) | PASS | `OPENAI_MAX_OUTPUT_TOKENS=800`, 호출 제한 |

## 보너스 (선택)

| 요건 | 상태 |
|---|---|
| Function Calling + MCP/GPT Actions | NOT APPLICABLE (미구현, 선택) |
| 추가 지표 1개 이상 | PASS — 최근/이전 60초 비교, 변화율, 추세 |
| 그래프 1개 | PASS — 435점 시계열 |
| CSV/JSON 내보내기 | NOT APPLICABLE (미구현, 선택) |
| 다크 모드 토글 | NOT APPLICABLE — 시스템 설정 자동 다크 모드만 있고 토글 없음 |

## 미확인

- 제출 방법(어디에·어떤 형식으로 제출하는지)은 원문에 없어 UNVERIFIED. README에 스크린샷을 넣는 것만 명시됨.
