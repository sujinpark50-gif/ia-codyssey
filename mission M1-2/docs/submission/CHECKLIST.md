# 과제 제출 체크리스트 — AI Agent 개발: 나만의 AI 비서 구축

기준: 과제 원문(사용자 제공, 2026-10-13). 상태: PASS / FAIL / UNVERIFIED / NOT APPLICABLE.
마지막 갱신: 2026-10-13, M6 운영 배포·검증 후 (Render `6688209`, Vercel 운영 `API_MODE=remote`).

## 필수 요건

| 구분 | 요건 | 상태 | 근거 |
|---|---|---|---|
| 개발 환경 | Python 3.10+, venv | PASS | Python 3.12, `.venv` |
| 개발 환경 | fastapi, uvicorn, firebase-admin, openai, python-dotenv | PASS | `requirements.txt` |
| 데이터 | 시계열 100개 이상 | PASS | UGV 1초 속도 435개 (`docs/DATA_VALIDATION.md`) |
| 데이터 | 요약: 기간·개수·기본 통계 | PASS | `/api/data/summary` start/end, count 435, 평균 4.896189·최소 0·최대 5.808594 (운영 확인) |
| 데이터 | 요약: 최근 추세(증가/감소/유지) | PASS | 운영 `trend: "감소"`, `change_pct: -13.8` (최근 60초 4.551057 vs 이전 60초 5.279502, sim_time_s 기준) |
| FastAPI | 앱 초기화 + CORS 설정 | PASS | 운영 `ALLOWED_ORIGINS` = Vercel 운영 도메인. 그 도메인 preflight 200, 다른 출처 400 |
| FastAPI | 로컬 실행, `/docs` | PASS | 로컬 `/docs` 200 |
| Firestore | 서비스 계정 키를 환경 변수로 | PASS | `GOOGLE_APPLICATION_CREDENTIALS`(경로), Render Secret File, 최소 권한 계정 |
| Firestore | `data`, `conversations` 컬렉션 | PASS | `docs/API.md`, `docs/CHAT.md` |
| 데이터 API | POST/GET/PUT/DELETE `/api/data`, GET `/api/data/summary` | PASS | 운영 점검: 생성 201 → 조회 200 → 수정 200 → 삭제 204 → 조회 404, 원본 435건 유지 |
| 대화 API | POST/GET `/api/conversations`, DELETE `/{id}`, (A) GET `/{id}` | PASS | POST는 대화(제목·대상) 저장, 메시지는 `/api/chat`이 자동 저장 |
| 채팅 API | summary → 시스템 프롬프트 → GPT → conversations 자동 저장 | PASS | 운영 실호출 2회 (`gpt-5.4`), 새로고침 후 대화 복원 |
| 백엔드 배포 | Render Web Service | PASS | https://adair-mission-intelligence.onrender.com |
| 백엔드 배포 | 배포 URL에서 Swagger `/docs` | PASS | `/docs` 200. 스키마 비밀값 검사 0건, 보호 API 12개 토큰 없음·위조 토큰 모두 401 |
| 백엔드 배포 | 콜드 스타트 안내 | PASS (구현) / UNVERIFIED (운영 잠든 상태 재현) | 요청이 4초 넘게 걸리면 "서버를 깨우는 중" 배너, 실패 시 "잠들어 있었다면 1분쯤 뒤 새로고침" 안내(네트워크 오류·5xx만). 운영에서 15분 대기 후 확인은 미실시 |
| 프론트엔드 | HTML/CSS/JS, 프레임워크 없음 | PASS | `web/` |
| 프론트엔드 | 채팅: 입력·표시·로딩 | PASS | 스크린샷 1 |
| 프론트엔드 | 데이터 관리: 추가·목록·수정/삭제 | PASS | 스크린샷 2 |
| 프론트엔드 | 대화 기록: 목록·불러오기 | PASS | 스크린샷 3 |
| 프론트엔드 | 데이터 요약 표시(기간·개수·트렌드) | PASS | 대시보드 "최근 추세 · 감소 −0.728 m/s (−13.8%)" |
| 프론트 배포 | Vercel 배포 | PASS | https://adair-mission-intelligenceweb.vercel.app (Render API 직접 호출) |
| 프론트 배포 | 환경 변수로 API 서버 URL 설정 | PASS | Vercel `API_BASE_URL` → `build.mjs` → `dist/env-config.js` |
| README | 서비스 소개, 기술 스택, 로컬 실행 | PASS | `README.md` |
| README | 배포 URL(프론트·백엔드·Swagger) | PASS | `README.md` "배포 주소" |
| README | 환경 변수 목록(최소 세트) | PASS | `README.md` "환경 변수" |
| README | 스크린샷 3종 (채팅·데이터 관리·대화 기록) | PASS | `docs/submission/01-chat.png`, `02-crud.png`, `03-history.png` (이메일 미노출) |
| 제약 | 키는 환경 변수, 코드 노출 없음 | PASS | 정적 파일·OpenAPI·Render 로그(최근 1시간) 비밀값 검사 0건 |
| 제약 | 입력 검증·예외 처리 | PASS | Pydantic, 오류 처리 테스트 |
| 제약 | 백엔드 Render / 프론트 Vercel | PASS | |
| 비용 | 토큰 제한(max_tokens 등) | PASS | `OPENAI_MAX_OUTPUT_TOKENS=800`, 호출 제한 |

## 보너스 (선택)

| 요건 | 상태 |
|---|---|
| Function Calling + MCP/GPT Actions | NOT APPLICABLE (미구현, 선택) |
| 추가 지표 1개 이상 | PASS — 최근/이전 60초 비교, 변화율, 추세 |
| 그래프 1개 | PASS — 435점 시계열 |
| CSV/JSON 내보내기 | NOT APPLICABLE (미구현, 선택) |
| 다크 모드 토글 | NOT APPLICABLE — 시스템 설정 자동 다크 모드만 있고 토글 없음 |

## 미확인·참고

- 제출 방법(어디에·어떤 형식으로 제출하는지)은 원문에 없어 UNVERIFIED. README에 스크린샷을 넣는 것만 명시됨.
- 대화 제목은 모두 "새 대화"로 저장된다(첫 질문으로 제목을 만드는 기능 없음). 목록은 생성 시각으로 구분한다.
- Preview 첫 삭제 요청에서 브라우저 도구에만 503이 보인 기록은 원인 미확정. 운영 점검 삭제는 첫 시도 204, 로그 5xx 없음.
