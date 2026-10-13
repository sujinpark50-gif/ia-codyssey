# ADAIR Mission Intelligence

### AI 기반 산불재난대응 통합 임무 분석 비서

## 프로젝트 소개

ADAIR 산불재난대응 프로젝트에서 생성된 실제 시뮬레이션 및 테스트 데이터를 활용하여 UAV, UGV, 오케스트레이터의 운영 정보를 분석하는 AI 비서 웹 애플리케이션입니다.

코디세이 「AI Agent 개발: 나만의 AI 비서 구축」 과제로 개발하며, 향후 다중 무인체계의 임무 수행 결과와 운영 상태를 통합 분석하는 서비스로 확장하는 것을 목표로 합니다.

## 주요 기능

**1차 개발 — 코디세이 과제**

- UGV 주행 속도 시계열 분석
- 평균·최대·최소 및 구간별 변화 분석
- GPT 기반 데이터 질의응답
- Firestore 데이터 관리 및 대화 기록 저장
- 웹 기반 AI 비서 인터페이스

**향후 확장**

- UAV 비행 및 정찰 데이터 분석
- UGV 출동·복귀 및 주행 결과 분석
- 오케스트레이터의 임무 단계별 이벤트 분석
- 통합 임무 성공·실패 이력 비교
- 반복 오류 및 운영 병목 분석

## 데이터

ADAIR 프로젝트의 Gazebo·PX4 시뮬레이션 및 테스트 로그를 활용합니다.

1차 데이터는 실제 UGV 주행 기록에서 계산한 속도 시계열이며, 원본 검증과 정규화를 마쳤습니다(M1-2).

향후 UAV, UGV, 오케스트레이터 로그를 검증하여 통합 데이터셋으로 확장할 계획입니다.

## 기술 스택

- **Backend:** Python, FastAPI
- **Database:** Firebase Firestore
- **AI:** OpenAI GPT API
- **Frontend:** HTML, CSS, JavaScript
- **Deployment:** Render, Vercel

## 개발 단계

1. UGV 시계열 데이터 검증 및 정규화
2. 데이터베이스 및 API 구현
3. GPT 기반 분석 비서 구현
4. 웹 인터페이스 및 배포
5. UAV·UGV·오케스트레이터 통합 분석 확장

## 프로젝트 목표

단일 차량의 성능 분석을 넘어, 다중 무인체계의 임무 수행 과정에서 발생하는 데이터를 통합하고 AI를 통해 운영 결과를 이해할 수 있는 분석 기반을 구축합니다.

현재는 코디세이 과제의 필수 기능을 우선 구현하는 단계입니다.

## 현재 구현 상태

| 단계 | 내용 | 상태 |
|---|---|---|
| M1-2 | 원본 검증, 속도 재계산, 1초 시계열 435개 생성 | 완료 — `docs/DATA_VALIDATION.md` |
| M2 | FastAPI 데이터 API, 저장소 계층(메모리·Firestore), 적재기, 통계 요약 | 완료 — 실제 Firestore `data` 컬렉션에 435건 적재·검증 (2026-10-13) |
| M3 | GPT 분석 비서 `POST /api/chat`, 대화 기록 API, 통계 캐시 | 구현·모의 테스트 완료, 실제 API 연결 1회 확인 — `docs/CHAT.md` |
| M4 | 웹 화면(대시보드·AI 비서·대화 기록), Firebase 로그인, 소유권, 호출 제한 | 구현·자동 테스트 완료, 실제 로그인 확인 대기 — `docs/SECURITY_M4.md` |
| M5 | 배포 준비 (Render 설정 초안, 최소 권한 절차, 보안 점검, 읽기 최적화) | 준비 완료·미배포 — `docs/DEPLOYMENT.md` |

## 폴더 구조

```
mission M1-2/
├─ app/                 FastAPI 앱 (config, models, repository, service, importer, llm, chat, conversations, api/)
├─ scripts/
│  ├─ prepare_data.py   원본 → 1초 시계열 (표준 라이브러리만)
│  ├─ load_data.py      1초 시계열 → 저장소 적재 (기본: 메모리 모의 적재)
│  ├─ check_firestore.py Firestore 연결 확인
│  └─ check_llm.py      GPT API 연결 확인 (실제 호출 1회)
├─ data/
│  ├─ raw/              원본 CSV·JSON (커밋하지 않음, 직접 준비)
│  ├─ ugv_speed_1s.csv / .json, source_manifest.json, validation_summary.json
├─ web/                 웹 화면 (index.html, styles.css, app.js — 비밀값 없음)
├─ docs/                DATA_VALIDATION.md, DATA_SCHEMA.md, API.md, CHAT.md, SECURITY_M4.md, DEPLOYMENT.md
├─ render.yaml          Render 배포 설정 초안 (비밀값 없음)
├─ firestore.rules      Firestore 보안 규칙 (클라이언트 접근 모두 거부)
└─ tests/               test_prepare_data.py (M1-2), test_api.py (M2), test_chat.py (M3), test_auth_ui.py (M4), test_deploy.py (M5)
```

명령은 모두 `mission M1-2` 폴더에서 실행합니다.

## 1. 원본 파일 준비

원본은 ADAIR 저장소의 `logs/ugv/`에 있습니다. 두 파일을 `data/raw/`에 복사하고 해시를 확인합니다.

```bash
mkdir -p data/raw
cp <ADAIR 경로>/logs/ugv/B1x_slope_low_20261010_184532.csv  data/raw/
cp <ADAIR 경로>/logs/ugv/B1x_slope_low_20261010_184532.json data/raw/
sha256sum data/raw/*      # PowerShell: Get-FileHash data/raw/*
```

| 파일 | SHA-256 |
|---|---|
| `B1x_slope_low_20261010_184532.csv` | `4d18f3d45101cbd68ff85d39632e6355c7f6603caf9b2963d7bd5efb7583d4c8` |
| `B1x_slope_low_20261010_184532.json` | `4fa14bd41bfb4b74bd25a293c0cd1ccf219b88f559674cb0d8ea978f2a4e22bf` |

해시가 다르면 다른 파일입니다. 데이터 생성과 M1-2 테스트가 실패합니다.

## 2. 환경 준비

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
cp .env.example .env                                         # 필요할 때만
```

## 3. 데이터 생성 (M1-2)

```bash
python scripts/prepare_data.py
```

`data/ugv_speed_1s.csv`, `ugv_speed_1s.json`, `source_manifest.json`, `validation_summary.json`을 다시 만듭니다. 같은 원본이면 바이트까지 같은 결과가 나옵니다. 원본 파일은 읽기만 합니다.

## 4. 테스트

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

- `test_prepare_data.py` 12개: 원본 품질, 속도 계산, 집계, 재현성, 원본 해시. **`data/raw/`에 원본이 있어야 합니다.**
- `test_api.py` 23개: API 동작, 수정 정책, 통계 검산, 중복 적재 방지, Firestore 장애 처리, CORS, Swagger. Firestore 연결이나 원본 없이 돌아갑니다.
- `test_chat.py` 19개: 채팅, 대화 기록, GPT 오류·저장 실패 처리, 통계 프롬프트, 근거 검사, 키 노출 방지, Codyssey 호환 요청 형식. 가짜 GPT·가짜 전송 계층을 쓰며 실제 API를 부르지 않습니다.

## 5. API 실행

```bash
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload
```

- Swagger: http://127.0.0.1:8000/docs
- 기본(`DATA_BACKEND=memory`)은 시작할 때 435개를 메모리에 적재하고, 종료하면 변경 내용이 사라집니다.
- 엔드포인트와 응답 형식은 `docs/API.md`에 있습니다.

## 6. Firestore 연결과 적재

`.env`에 `DATA_BACKEND=firestore`, `GOOGLE_APPLICATION_CREDENTIALS` 또는 `FIREBASE_CREDENTIALS`(저장소 밖 키 경로, 둘 중 하나), `FIREBASE_PROJECT_ID`, `FIRESTORE_DATABASE_ID`를 넣은 뒤 순서대로 실행합니다.

```bash
python scripts/load_data.py                                       # 1. 모의 적재 (메모리, 쓰기 없음)
python scripts/check_firestore.py                                 # 2. 연결 확인: 인증, data 접근, 임시 문서 생성·조회·삭제
python scripts/load_data.py --backend firestore --confirm-upload  # 3. 실제 적재 + 검증 (승인 후에만)
python scripts/load_data.py --backend firestore --confirm-upload  # 4. 재적재: created=0, unchanged=435 이어야 함
python scripts/load_data.py --backend firestore --verify-only     # 쓰기 없이 검증만
```

적재 후 검증 항목: imported 문서 수 435, 문서 ID 집합 일치, 날짜·값·단위·원본 식별자 보존, 평균 4.896189·최소 0·최대 5.808594 (오차 1e-6).

키 파일 내용과 private key는 출력·로그·API 오류 응답에 나오지 않도록 지웁니다(`app/repository.py`의 `safe_error`).

## 원본 데이터 정책

- 원본 CSV·JSON은 **읽기 전용**입니다. 스크립트는 원본을 읽기만 하고, 실행 전후 해시가 같은지 확인합니다.
- 원본은 이 저장소에 **커밋하지 않습니다** (`.gitignore`의 `data/raw/`).
- Firestore의 `imported` 데이터(원본에서 계산한 435개)는 API로 수정·삭제할 수 없습니다. 서버가 403으로 거부합니다. 추가·수정·삭제는 `manual` 데이터만 가능합니다.
- 이 저장소는 공개 저장소입니다. 원본에서 계산한 파생 산출물(1초 속도값, 원본 행 번호, 파일 해시, 시험 식별자·월드·차량 이름)은 저장소에 포함되어 있습니다. 소유자가 공개해도 된다고 판단했습니다(2026-10-13).
- API 키, 서비스 계정 키, `.env`는 커밋하지 않습니다.

## 7. GPT 분석 비서 (M3)

`.env`에 `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5.4`, `OPENAI_BASE_URL=https://copa.codyssey.kr/v1`을 넣습니다. 서버를 실행한 뒤 `POST /api/chat`으로 질문합니다. 요청 1회가 GPT 호출 1회(유료)입니다. 자세한 내용은 `docs/CHAT.md`.

## 8. 웹 화면과 로그인 (M4)

```bash
.venv/Scripts/python -X utf8 -m uvicorn app.main:create_app --factory --port 8000   # -X utf8: Windows 에서 로그 한글 깨짐 방지
# 브라우저에서 http://localhost:8000
```

- `/api/config`와 정적 파일 외의 모든 API는 Firebase Google 로그인과 `ALLOWED_EMAILS`가 필요합니다. 로그인하지 않으면 401, 허용되지 않은 계정이면 403입니다.
- `.env` 필요 값: `FIREBASE_PROJECT_ID`, `FIREBASE_WEB_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_WEB_APP_ID`, `ALLOWED_EMAILS`. Firebase 콘솔 → 프로젝트 설정 → 내 앱(웹)에서 확인하고, Authentication에서 Google 로그인을 켭니다.
- 질문 제한은 기본 분당 5회, 시간당 30회, 동시 1개, 하루 200회이며 환경변수로 바꿀 수 있습니다(`.env.example`).
