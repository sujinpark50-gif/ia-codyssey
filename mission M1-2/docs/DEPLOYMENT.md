# M5 배포 준비

상태: **준비 문서, 미배포.** 서비스 계정 생성·키 폐기·배포는 사용자 승인 후 진행한다.

## 1. 권장 방식: Render Web Service 1개 (FastAPI + 정적 웹)

| 비교 | Render 1개 서비스 (권장) | Vercel(화면) + Render(API) |
|---|---|---|
| 출처 | 화면과 API가 같은 도메인 | 서로 다른 도메인 |
| CORS | 필요 없음 (`CORS_ORIGINS` 비움) | Vercel 주소 허용 필요 |
| Firebase 승인 도메인 | 1개 | 2개 |
| 비밀값 위치 | Render 하나 | Render (Vercel에는 비밀 없음) |
| 구성 | `render.yaml` 1개 | 프로젝트 2개, 배포 2번 |

이유: 지금 코드는 FastAPI가 `web/`을 함께 서빙하도록 되어 있다(`app.mount("/")`). 같은 출처라 CORS 설정, 쿠키, 도메인 문제가 없고, 비밀값도 한 곳에서만 관리한다. 과제 기술 스택의 Vercel은 나중에 정적 화면만 옮길 때 쓸 수 있다(`CORS_ORIGINS`·승인 도메인 추가만 하면 됨).

### Render 설정 (`render.yaml` 초안)

| 항목 | 값 |
|---|---|
| Runtime | Python, `PYTHON_VERSION=3.12.14` |
| Root Directory | `mission M1-2` — 경로에 공백이 있다. Render가 처리하지 못하면 폴더 이름 변경이 필요하다 (미확인) |
| Build | `pip install -r requirements.txt` |
| Start | `python -X utf8 -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT` |
| Health check | `/health` → `{"status":"ok"}` (내부 정보 없음) |
| HTTPS | Render가 `*.onrender.com` 인증서를 자동 제공 |
| Region | singapore (한국에서 가장 가까운 Render 지역) |
| Auto deploy | 꺼 둠 (`autoDeploy: false`) — 승인한 커밋만 수동 배포 |

`render.yaml`은 저장소 루트가 아니라 `mission M1-2/`에 있다. Blueprint로 쓰려면 Render에서 파일 경로를 지정하거나, 대시보드에서 같은 값으로 직접 만든다.

## 2. 환경변수

| 변수 | 값 | 비밀 | 비고 |
|---|---|---|---|
| `DATA_BACKEND` | `firestore` | | |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/secrets/adair-mi-sa.json` | 경로만 | 키 JSON은 Render **Secret File**로 업로드 |
| `FIREBASE_PROJECT_ID` | `adair-mission-intelligence` | | 키의 project_id와 다르면 연결 거부 |
| `FIRESTORE_DATABASE_ID` | `(default)` | | |
| `FIREBASE_WEB_API_KEY` | 콘솔 값 | 공개 식별자 | 브라우저로 전달됨. 대시보드 입력 |
| `FIREBASE_AUTH_DOMAIN` | `adair-mission-intelligence.firebaseapp.com` | | |
| `FIREBASE_WEB_APP_ID` | 콘솔 값 | 공개 식별자 | |
| `ALLOWED_EMAILS` | 허용 계정 | 개인정보 | 대시보드 입력 |
| `OPENAI_API_KEY` | Codyssey virtual key | **비밀** | 대시보드에만 |
| `OPENAI_MODEL` | `gpt-5.4` | | |
| `OPENAI_BASE_URL` | `https://copa.codyssey.kr/v1` | | |
| `OPENAI_MAX_RETRIES` | `1` | | 0이면 질문 1회 = HTTP 1회 |
| `DOCS_ENABLED` | `false` | | `/docs`, `/openapi.json` 끔 |
| `CHAT_RATE_PER_MINUTE` / `_PER_HOUR` / `CHAT_DAILY_LIMIT` | 5 / 30 / 200 | | |
| `SUMMARY_CACHE_TTL_S` | `60` | | data 스냅샷·통계 캐시 |
| `CORS_ORIGINS` | (비움) | | 같은 출처라 불필요 |

## 3. Firebase 설정

1. Authentication → 설정 → **승인된 도메인**에 Render 주소(`<서비스명>.onrender.com`)를 추가한다. 없으면 로그인 팝업이 `auth/unauthorized-domain`으로 실패한다.
2. Authentication → 로그인 방법: Google 사용 (완료, 2026-10-13 브라우저 확인).
3. Firestore → 규칙: `firestore.rules`(모두 거부)와 같은지 확인한다. 브라우저는 Firestore에 직접 접근하지 않고 API만 쓴다. Admin SDK(서버)는 규칙과 상관없이 동작한다. **현재 콘솔 규칙은 확인하지 않았다.**
4. (선택) Google Cloud → API 키 제한: `FIREBASE_WEB_API_KEY`를 HTTP 리퍼러(`localhost`, Render 도메인)로 제한한다.

## 4. 최소 권한 서비스 계정 전환

### 필요한 권한

| 서버 동작 | 사용 API | 필요한 권한 |
|---|---|---|
| data·conversations 조회, 목록, count 집계 | get / runQuery / runAggregationQuery | `datastore.entities.get`, `datastore.entities.list` |
| manual 데이터·대화 생성·수정·삭제, batch | commit | `datastore.entities.create/update/delete` |
| usage 일일 카운터 | beginTransaction / commit | 위와 같음 |
| Firebase ID 토큰 검증 | Google 공개 인증서 (인증 없음) | 없음 |

`roles/datastore.user`(Cloud Datastore 사용자)가 위 권한을 모두 포함한다. 색인 관리, 내보내기, 규칙 변경, Firebase Auth 사용자 관리 권한은 포함하지 않는다. 지금 쓰는 firebase-adminsdk 계정은 Firebase 전체 관리 권한이라 범위가 훨씬 넓다.

### 절차 (각 단계 승인 후)

1. **[승인 필요] 계정 생성과 역할 부여**
   ```
   gcloud iam service-accounts create adair-mi-api --project adair-mission-intelligence --display-name "ADAIR MI API"
   gcloud projects add-iam-policy-binding adair-mission-intelligence \
     --member serviceAccount:adair-mi-api@adair-mission-intelligence.iam.gserviceaccount.com --role roles/datastore.user
   ```
2. **[승인 필요] 키 발급** — 저장소 밖(`C:\Users\…\firebase-keys\`)에 저장
   ```
   gcloud iam service-accounts keys create <저장소 밖 경로>\adair-mi-api.json \
     --iam-account adair-mi-api@adair-mission-intelligence.iam.gserviceaccount.com
   ```
3. 로컬 `.env`의 `GOOGLE_APPLICATION_CREDENTIALS`를 새 키로 바꾸고 확인한다(데이터 변경 없음):
   - `python scripts/check_firestore.py` — 인증, data count, 임시 문서 생성·조회·삭제, 트랜잭션 (관리자 키 기준 12/12 PASS)
   - `python scripts/load_data.py --backend firestore --verify-only` — 435건·통계
   - 브라우저 로그인 → 대시보드·대화 생성·삭제
4. 모두 통과하면 **[승인 필요]** 관리자 키 폐기: 콘솔 → 서비스 계정 → firebase-adminsdk → 키 → 해당 키 삭제. 로컬 파일 삭제만으로는 키가 계속 유효하다.
5. Render Secret File에는 새 키만 올린다.

IAM 역할 반영에는 몇 분이 걸릴 수 있다. 3단계에서 `PermissionDenied`가 나오면 잠시 뒤 다시 확인한다.

## 5. 보안 점검 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| GPT 키 서버 보관 | PASS | 환경변수에만 있음. 설정 repr·로그·오류 응답에서 마스킹 (테스트) |
| Firebase 키 서버 보관 | PASS | 경로만 환경변수, 파일은 저장소 밖/Secret File. 커밋 이력에 없음 |
| 브라우저 비밀값 노출 | PASS | `/`, `app.js`, `/api/config` 검사 (테스트 + 실제 서버) |
| 미인증 API 차단 | PASS | `/api/config`, `/health`, 정적 파일 외 401 (테스트 + 실제 브라우저) |
| 다른 사용자 대화 | PASS | 조회·삭제·이어가기 모두 404 (테스트) |
| 요청 본문 크기 | PASS | 16KB 초과 413 |
| 보안 응답 헤더 | PASS | nosniff, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy. CSP는 Firebase 로그인 호환성 때문에 넣지 않음 |
| `/docs` 운영 비활성 | 설정으로 PASS | `DOCS_ENABLED=false` |
| API 오류의 민감 정보 | 부분 | 키·PEM은 지움. 503 응답의 `reason`에 Firestore 오류 요약이 담김 — 허용된 로그인 사용자만 볼 수 있음 |
| 호출 제한 — 재시작 | 부분 | 일일 상한은 Firestore에 있어 유지. 분·시간·동시 제한은 메모리라 재시작 시 초기화 |
| 호출 제한 — 복수 인스턴스 | 부분 | 일일 상한은 공유. 분·시간·동시 제한은 인스턴스마다 따로라 N개면 최대 N배. Render 무료는 1개라 현재 영향 없음 |
| data 스냅샷 캐시 — 복수 인스턴스 | 부분 | 다른 인스턴스의 쓰기는 TTL(60초) 뒤 반영 |
| Firestore 규칙 vs IAM | 문서화 | 규칙은 클라이언트 SDK만 막고, 서버 권한은 IAM이 정함. 현재 콘솔 규칙 미확인 |

## 6. Firestore 읽기 최적화

### 분석 (문서 435건 기준)

| 요청 | 변경 전 | 변경 후 (TTL 60초 안) |
|---|---|---|
| `/api/me` (usage 문서) | 1 | 1 |
| `/api/data/summary` | 435 | 435 (스냅샷 생성) |
| `/api/data?limit=1000` (그래프) | 435 | 0 |
| `/api/data?limit=50` (표) | 435 | 0 |
| 표 페이지 이동 1회 | 435 | 0 |
| 채팅 1회 (통계) | 0–435 | 0–435 (TTL마다 1회) |
| **첫 화면 합계** | **약 1,306** | **약 436** |

목록 API는 조건에 맞는 문서를 모두 읽고 서버에서 페이지를 나눈다(복합 색인 없이 쓰려는 선택). 그래서 표 한 페이지를 봐도 435건을 읽었다.

### 변경 (`CachedRepository`)

- data 전체 스냅샷을 TTL(`SUMMARY_CACHE_TTL_S`, 기본 60초) 동안 공유하고 summary·목록·단건 조회가 모두 이것을 쓴다.
- API를 거친 추가·수정·삭제는 스냅샷과 summary 캐시를 즉시 비운다. 바로 다음 요청부터 정확한 값이 나온다 (테스트: 추가→수정→삭제 후 통계가 원래 값과 같음).
- API를 거치지 않은 변경(적재 스크립트, 콘솔, 다른 인스턴스)은 TTL이 지나면 반영된다.
- 실제 Firestore 데이터는 바꾸지 않는다.

남은 개선 후보 (미구현): 데이터가 수천 건 이상으로 늘면 스냅샷 대신 `sim_time_s` 정렬 + 커서 페이지네이션(단일 필드 색인)과 summary 사전 계산 문서로 바꾼다.

## 7. 비용·무료 플랜 제약 (2026-10-13 기준 알려진 값, 배포 전 각 콘솔에서 확인 필요)

| 서비스 | 제약 | 영향 |
|---|---|---|
| Render Free Web Service | 15분 동안 요청이 없으면 잠듦, 다시 깨우는 데 수십 초. 월 사용 시간 한도 있음 | 첫 접속이 느림. 메모리 캐시·분당 제한이 재시작 시 초기화 |
| Firestore Spark | 하루 읽기 50,000, 쓰기 20,000, 삭제 20,000, 저장 1GiB | 첫 화면 약 436 읽기 → 하루 100회 이상 열어도 여유 |
| Firebase Auth | Google 로그인 무료 범위 | 단일 사용자 영향 없음 |
| Codyssey gpt-5.4 | 질문 1회 ≈ 입력 660 + 출력 400 토큰 (실측 2026-10-13) | 일일 상한 200회로 제한 |

## 8. 배포 전 사용자 승인 필요 항목

1. 최소 권한 서비스 계정 생성·역할 부여·키 발급
2. 새 키 검증 후 관리자 키 폐기
3. Render 계정·서비스 생성, 저장소 연결 (공개 저장소 `sujinpark50-gif/ia-codyssey`)
4. Render 대시보드에 비밀값 입력 (`OPENAI_API_KEY`, Secret File, `ALLOWED_EMAILS`, 웹 설정값)
5. Firebase 승인된 도메인에 Render 주소 추가
6. Firestore 규칙 확인·적용
7. 실제 배포와 배포 후 확인 (GPT 1회 호출 포함 여부)
