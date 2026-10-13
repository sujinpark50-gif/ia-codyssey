# M4 보안 설계안 — 단일 사용자 MVP

상태 (2026-10-13, `feat/m4-web-ui`):

| 항목 | 상태 |
|---|---|
| 1. Firebase 로그인 + 허용 이메일 (`app/auth.py`) | 구현·테스트 (실제 RSA 서명 토큰으로 검증 로직 시험) |
| 2. 호출 제한 (`app/ratelimit.py`) | 구현·테스트. Firestore 일일 카운터는 실제 Firestore 미확인 |
| 3. 메시지·이력·본문 크기·대화 수 제한 | 구현·테스트 |
| 4. 대화 `owner_uid` 소유권 | 구현·테스트 |
| 5. 최소 권한 키 교체 | 절차만 문서화 (사용자 승인 후 진행) |
| 6. `/docs` 끄기 (`DOCS_ENABLED=false`) | 구현, 운영 설정에서 적용 예정 |

설계와 달라진 점:
- 대화 목록은 `owner_uid` 동등 조건만 Firestore에서 걸고 정렬은 서버에서 한다. 복합 색인이 필요 없다.
- 공개 인증서는 Cache-Control max-age 동안 서버 메모리에 캐시한다. 인증서를 받지 못하면 401이 아니라 503으로 응답한다.
- 인증 설정(`FIREBASE_PROJECT_ID`)이 없으면 모든 API가 503이다. 인증을 건너뛰는 모드는 없다.

## 현재 위험 (M3 기준)

- 모든 API에 인증이 없다. 배포 주소를 아는 누구나 다음을 할 수 있다:
  - `POST /api/chat`: 유료 GPT 호출
  - `/api/conversations`: 대화 조회·삭제
  - `/api/data`: manual 데이터 추가·수정·삭제
- GPT 호출 횟수에 상한이 없다.
- 서버가 Firebase 관리자 권한 키를 쓰고 있다.
- `/docs`(Swagger)가 공개되어 있다.

## 원칙

- 비밀값(OpenAI 키, Firebase 서비스 계정 키)은 Render 서버 환경에만 둔다. 브라우저 코드·Vercel 환경변수·Git에는 두지 않는다.
- 권한 판단은 서버에서 한다. 화면에서 버튼을 숨기는 것으로 대신하지 않는다.
- 과제 범위(사용자 1명)에 맞게 최소한으로 만든다.

## 1. 인증 — Firebase Authentication + 허용 이메일 1개 (권장)

```
브라우저 (Vercel)                      API 서버 (Render)
 Firebase JS SDK 로 Google 로그인  ──▶  Authorization: Bearer <Firebase ID 토큰>
 (웹 설정값은 공개용 식별자, 비밀 아님)      firebase_admin.auth.verify_id_token()
                                         → uid·email 확인, ALLOWED_EMAILS 에 없으면 403
```

- 이미 쓰고 있는 Firebase 프로젝트에서 Authentication → Google 로그인만 켠다. 무료다.
- 서버는 `verify_id_token`으로 서명·만료·프로젝트를 검증한다. 공개 인증서로 검증하므로 서비스 계정에 별도 IAM 역할이 필요 없다.
- 브라우저에는 공개 식별자인 Firebase 웹 설정값과 1시간짜리 ID 토큰만 있다. 비밀키는 없다.
- 헤더 방식이라 쿠키·CSRF 문제가 없다. Vercel과 Render가 서로 다른 도메인이어도 CORS 허용 출처만 맞추면 된다.
- 구현 범위: FastAPI 의존성 `require_user` 하나(약 30줄)를 `/api/*` 전체에 적용한다. 예외는 `/health`.

대안(더 단순하지만 약함): `APP_PASSCODE` 하나를 두고 `POST /api/auth/login`으로 서명 쿠키를 발급한다. 이 경우 Vercel과 Render가 다른 도메인이라 브라우저의 서드파티 쿠키 차단에 걸릴 수 있다. 그래서 Vercel rewrites로 `/api/*`를 Render에 프록시해 같은 출처로 만들어야 한다. 비밀번호 관리 부담도 있어 권장하지 않는다.

## 2. 채팅 호출 제한

| 한도 | 값(제안) | 위치 |
|---|---|---|
| 사용자별 | 분당 5회, 시간당 30회 | 서버 메모리 (토큰 버킷) |
| 전체 일일 상한 | 하루 200회 (`CHAT_DAILY_LIMIT`) | Firestore `usage/{YYYY-MM-DD}` 문서, 트랜잭션으로 증가 |
| 동시 요청 | 사용자당 1개 | 서버 메모리 |

- 한도를 넘으면 GPT를 부르지 않고 429와 `Retry-After`를 돌려준다.
- 일일 상한을 Firestore에 두는 이유: Render 무료 인스턴스는 재시작이 잦아 메모리 카운터가 초기화된다. 비용 상한은 재시작해도 유지돼야 한다. 채팅 1회당 Firestore 쓰기 1회가 추가된다.
- 같은 대화에 동시에 요청하면 지금은 저장 충돌이 난다. 사용자당 동시 요청 1개 제한으로 이것도 막는다.

## 3. 메시지·이력 제한

| 항목 | 현재 | M4 |
|---|---|---|
| 질문 길이 | 2,000자 | 유지 |
| GPT에 보내는 이력 | 최근 10개, 8,000자 | 유지 |
| 답변 길이 | 최대 800 토큰 | 유지 |
| 대화당 메시지 수 | 무제한 | 200개 (넘으면 409, 새 대화 안내) |
| 사용자당 대화 수 | 무제한 | 100개 |
| 요청 본문 크기 | 제한 없음 | 16 KB (미들웨어) |

## 4. 대화 소유권

- 대화 문서에 `owner_uid`를 저장한다.
- 목록은 `owner_uid == 내 uid`로만 조회한다. Firestore 복합 색인 1개(`owner_uid` + `updated_at` 내림차순)가 필요하다.
- 상세·삭제·채팅 이어가기에서 소유자가 다르면 **404**를 돌려준다(403이 아니라 404여서 대화 존재 여부도 드러나지 않는다).
- 단일 사용자라도 이 규칙을 넣어 두면 나중에 사용자를 늘릴 때 그대로 쓸 수 있다.
- `/api/data`의 쓰기(POST·PUT·DELETE)도 인증 사용자만 가능하다. imported 데이터는 지금처럼 읽기 전용이다.

## 5. Firebase 최소 권한

서버가 쓰는 Firestore 작업은 문서 읽기·쓰기·삭제, 쿼리, count 집계, 트랜잭션(`usage`)이다. 모두 `roles/datastore.user`에 들어 있다.
토큰 검증은 공개 인증서만 쓰므로 IAM 권한이 필요 없다. Firebase Authentication 관리 권한도 필요 없다(서버는 사용자를 만들거나 바꾸지 않는다).

1. Google Cloud IAM에서 새 서비스 계정(예: `adair-mi-api`)을 만들고 **Cloud Datastore 사용자(`roles/datastore.user`)** 역할 하나만 준다.
   gcloud 예시 (실행 전 승인 필요):
   ```
   gcloud iam service-accounts create adair-mi-api --project adair-mission-intelligence
   gcloud projects add-iam-policy-binding adair-mission-intelligence      --member serviceAccount:adair-mi-api@adair-mission-intelligence.iam.gserviceaccount.com --role roles/datastore.user
   gcloud iam service-accounts keys create <저장소 밖 경로>.json      --iam-account adair-mi-api@adair-mission-intelligence.iam.gserviceaccount.com
   ```
2. 새 키로 연결 확인(`scripts/check_firestore.py`)을 한 뒤, 지금의 firebase-adminsdk 키는 콘솔에서 **삭제(폐기)**한다.
3. Render에는 키 JSON을 Secret File로 올리고 `GOOGLE_APPLICATION_CREDENTIALS`로 경로를 지정한다.
4. Firestore 보안 규칙은 "모두 거부"를 유지한다. 브라우저는 Firestore에 직접 접근하지 않고 API만 거친다. Admin SDK는 규칙과 상관없이 접근한다.
5. `verify_id_token`은 추가 권한 없이 동작한다.

## 6. 그 밖

- CORS: Vercel 배포 주소 하나만 허용한다(`CORS_ORIGINS`). `allow_headers`에 `Authorization`을 추가한다.
- `/docs`, `/openapi.json`: 운영에서는 끄거나(`docs_url=None`) 인증 뒤에 둔다.
- 로그: 질문·답변 본문은 로그에 남기지 않는다(지금도 남기지 않음). 키 마스킹은 유지한다.
- OpenAI 키: Render 환경변수에만 둔다. 사용량 한도는 Codyssey 쪽에서 확인한다(현재 미확인).

## 구현 순서 (제안)

1. `require_user`, `ALLOWED_EMAILS`, CORS `Authorization` 허용
2. 대화 `owner_uid`와 소유권 검사, 색인
3. 호출 제한 (메모리 + Firestore 일일 상한), 본문 크기 제한
4. 최소 권한 키로 교체, 관리자 키 폐기
5. `/docs` 비활성화, 배포

테스트: 토큰 없음 401, 허용 외 이메일 403, 다른 사용자 대화 404, 한도 초과 429(GPT 미호출), 일일 상한이 재시작 후에도 유지.
