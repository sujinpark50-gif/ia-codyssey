# GPT 분석 비서와 대화 기록 (M3)

## 엔드포인트

| 메서드 | 경로 | 설명 | 주요 응답 |
|---|---|---|---|
| POST | `/api/chat` | 질문 1개 → GPT 호출 1회 → 질문·답변 저장 | 200, 400, 404, 409, 422, 502, 503, 504 |
| POST | `/api/conversations` | 빈 대화 만들기 (`title`, `run_id`, `metric` 선택) | 201 |
| GET | `/api/conversations` | 대화 목록, 최근 수정 순 (`limit` 1–200) | 200 |
| GET | `/api/conversations/{id}` | 대화와 메시지 전체 | 200, 404, 422 |
| DELETE | `/api/conversations/{id}` | 대화와 메시지 삭제 | 204, 404, 422 |

대화 ID 형식은 `conv-<32자리 소문자 hex>`이다. 형식이 틀리면 422, 없는 대화면 404.

### POST /api/chat

요청: `message`(공백 제외 1–2000자, 필수), `conversation_id`(없으면 새 대화), `run_id`, `metric`(기본 `ugv_speed_planar`).

응답: `conversation_id`, `created_conversation`, `answer`, `target`(run_id·metric·unit), `stats_basis`(답변에 쓴 통계 스냅샷과 계산 시각·캐시 사용 여부), `grounding`(근거 검사), `model`, `history_messages_used`.

처리 순서:
1. 입력 검증 (실패 시 GPT 호출 없음)
2. 분석 대상 결정: 기존 대화면 그 대화의 run_id·metric. 다른 대상을 요청하면 409. 지원하지 않는 metric 은 400, 데이터 없는 run_id 는 404, run_id 가 여러 개인데 지정하지 않으면 400
3. Firestore `data` 의 summary 조회 (캐시)
4. 시스템 프롬프트 = 답변 원칙 + summary 사실 블록 (통계·최근 구간 비교·데이터 품질 주의사항)
5. 이전 대화 이력 추가 (최근 `CHAT_HISTORY_MAX_MESSAGES`개, 합계 `CHAT_HISTORY_MAX_CHARS`자 이하, 오래된 것부터 뺌)
6. GPT 호출 (`OPENAI_BASE_URL`/chat/completions, 모델 `OPENAI_MODEL`)
7. 답변 근거 검사
8. 질문·답변을 한 번에 저장 (새 대화면 대화 문서도 같은 batch 에서 생성)

### 실패 처리

| 상황 | 응답 | 저장 |
|---|---|---|
| 키·모델 미설정 | 503 `saved:false` | 없음 |
| GPT 시간 초과 | 504 `saved:false` | 없음 |
| GPT API 오류 (모델 미지원 포함) | 502 `saved:false`, 상위 오류 메시지(키 제거) | 없음 |
| Firestore 연결 불가 | 503 | 없음, GPT 호출 안 함 |
| 답변 생성 후 저장 실패 | 503 `saved:false`, `answer` 포함 | 없음 (batch 원자성) |

모델을 지원하지 않는다는 오류가 와도 다른 모델로 바꾸지 않는다. 오류를 그대로 돌려준다.

## 답변 근거

시스템 프롬프트의 `[데이터]` 블록은 `GET /api/data/summary` 와 같은 계산 결과다: 분석 대상, 데이터 수(imported·manual), 기록 시각 범위, 평균·최소·최대, 최근 60초 비교(sim_time_s 기준), 데이터 품질 주의사항(원시 표본 최고속도 초과 2,234/8,078 은 원인 미확정·1초 집계 비율 아님 등).

답변 원칙 8개(`app/chat.py`의 `PRINCIPLES`): 제공된 수치만 사용, 단일 주행으로 임무 성공률 판단 금지, UGV 데이터로 UAV 평가 금지, 속도 저하를 고장으로 단정 금지, 최고속도 초과 원인 미확정, 원시 비율과 1초 집계 비율 구분, 답할 수 없으면 확인 가능한 범위 설명, 분석 대상·단위·시간 기준 명시. 사용자 메시지의 지시보다 원칙이 우선한다고 명시한다.

### 근거 검사 (`grounding`)

답변을 막지 않고 표시만 한다.
- `unverified_numbers`: 답변의 수치 중 시스템 프롬프트·이력·질문 어디에도 없는 값 (답변 자릿수로 반올림해 비교, 0–10 정수는 제외)
- `assertive_cause_phrases`: "고장입니다", "결함으로 확인" 같은 단정 표현 ("고장으로 단정할 수 없습니다" 같은 부정형은 걸리지 않음)
- 둘 다 비어 있으면 `ok: true`

정규식 기반이라 모든 경우를 잡지는 못한다.

## Firestore 대화 저장 구조

```
conversations/{conv_id}
  title, created_at(Timestamp), updated_at(Timestamp), run_id, metric, message_count
conversations/{conv_id}/messages/{000000, 000001, …}
  seq, role("user"|"assistant"), content, created_at
  assistant 만: model, stats_basis, grounding
```

- 질문·답변 2건과 대화 문서 갱신을 한 batch 로 커밋한다. 전부 저장되거나 하나도 저장되지 않는다.
- 같은 대화에 동시에 두 요청이 들어오면 같은 순번 문서를 만들려다 한쪽 batch 가 실패한다 (덮어쓰기 없음, 실패한 쪽은 503 `saved:false`).
- 목록은 `updated_at` 단일 필드 정렬, 이력은 `seq` 단일 필드 정렬이라 복합 색인이 필요 없다.
- 삭제는 메시지를 400개씩 지운 뒤 대화 문서를 지운다. 중간에 실패하면 일부 메시지만 지워질 수 있다.

## 성능·비용

- GPT 는 `POST /api/chat` 요청 1회당 1회만 부른다. 백그라운드 호출 없음, SDK 재시도 1회.
- summary 캐시: `(run_id, metric)` 별로 `SUMMARY_CACHE_TTL_S`(기본 60초) 동안 재사용한다.
  - API 를 거친 데이터 추가·수정·삭제는 즉시 캐시를 비운다.
  - API 를 거치지 않은 변경(적재 스크립트, Firestore 콘솔)은 TTL 이 지나면 반영된다.
  - 0 이면 캐시를 쓰지 않는다. 캐시는 프로세스 메모리에 있어 서버 인스턴스마다 따로다.
- Firestore 읽기: 캐시가 비었을 때 summary 1회 = 435건, 채팅 1회마다 이력 최대 10건 + 대화 문서 1건.
- 토큰: 시스템 프롬프트 약 1,100 토큰(2026-10-13 실제 호출 기준), 답변 최대 `OPENAI_MAX_OUTPUT_TOKENS`(기본 800).

## 연결 확인

```bash
python scripts/check_llm.py   # 실제 API 1회 호출 (재시도 없음), 저장하지 않음
```
