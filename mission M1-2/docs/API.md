# API 및 Firestore 구조 (M2)

Swagger(`/docs`)가 최신 스키마의 기준입니다. 이 문서는 정책과 구조를 설명합니다.

## 엔드포인트

| 메서드 | 경로 | 설명 | 주요 응답 |
|---|---|---|---|
| GET | `/health` | 상태, 사용 중인 백엔드 | 200 |
| POST | `/api/data` | manual 데이터 추가 | 201, 422 |
| GET | `/api/data` | 목록. `run_id`, `metric`, `record_type`, `limit`(1–1000, 기본 100), `offset` | 200 |
| GET | `/api/data/{id}` | 1건 | 200, 404 |
| PUT | `/api/data/{id}` | manual 수정. 보낸 필드만 바꾼다 | 200, 403, 404, 409, 422 |
| DELETE | `/api/data/{id}` | manual 삭제 | 204, 403, 404 |
| GET | `/api/data/summary` | 현재 데이터 통계. `run_id`, `metric` 필터 | 200, 400, 404 |

저장소에 연결할 수 없으면 모든 데이터 엔드포인트가 503 `{"detail", "reason"}`을 돌려줍니다. 422 응답에는 입력값을 다시 담지 않습니다(NaN 입력이 500으로 번지는 것을 막기 위함).

## 입력 규칙

| 필드 | 규칙 |
|---|---|
| `date` | 필수. 시간대가 포함된 ISO 8601 문자열만 받는다. 시간대 없는 값, 날짜만 있는 값, 숫자 epoch는 422 |
| `value` | 필수. JSON 숫자만 받는다. 문자열 `"3.5"`, 불리언, NaN, Infinity는 422. `metric=ugv_speed_planar`이면 0 이상 |
| `memo` | 문자열, 최대 1000자, 기본 `""` |
| `run_id` | 선택. 영문·숫자·`._-` |
| `metric` | 기본 `ugv_speed_planar` |
| `unit` | 기본 `m/s` |
| `sim_time_s` | 선택. 0 이상 정수. 같은 `run_id`·`metric` 안에서 중복되면 409 |
| 그 밖의 필드 | `record_type`, `source_ref` 등을 보내면 422 (클라이언트가 imported를 만들 수 없음) |

## 수정 정책 (서버에서 강제)

- `record_type = "imported"`: 원본에서 계산해 적재한 값. PUT·DELETE는 403.
- `record_type = "manual"`: API로 만든 값. ID는 `manual-<uuid>`. 추가·수정·삭제 가능.
- PUT은 기존 값에 바꾼 필드를 합친 결과를 다시 검증한다(예: metric을 속도로 바꾸면서 음수 값을 남길 수 없음).

## 응답 날짜

모든 응답의 `date`는 KST(`+09:00`) ISO 8601이다. UTC(`Z`)로 보낸 값도 같은 시점을 KST로 바꿔 돌려준다. 저장은 시점(instant) 그대로 한다(Firestore Timestamp).

## summary

```
count, imported_count, manual_count, run_id, run_ids, metric, unit,
start, end (date 최소·최대, KST), mean, min, max,
recent_comparison { basis: "sim_time_s", window_s: 60,
                    recent {from_sim_s, to_sim_s, count, mean},
                    previous {...}, difference },
cautions [ ... ]
```

- 요청할 때마다 현재 저장소 상태로 다시 계산한다(캐시 없음).
- 최근 비교는 `sim_time_s` 기준이다. `end = max(sim_time_s) + 1`을 기준으로 `[end-60, end)`와 `[end-120, end-60)` 구간 값의 평균을 비교한다. M1-2의 `recent_1s` 정의와 같다. `date`(벽시계)로는 구간을 나누지 않는다.
- `sim_time_s`가 없는 manual 값은 count·평균·최소·최대에는 들어가고 구간 비교에서는 빠진다. 이 경우 cautions에 적는다.
- run_id가 여러 개 섞이면 구간 비교를 하지 않는다. metric이나 단위가 섞이면 400을 돌려준다.
- cautions에는 M1-2 `validation_summary.json`의 원시 표본 기준 규칙 최고속도 초과 수(2,234 / 8,078)를 원시 표본 기준이라고 밝혀서 적는다. 1초 집계값의 초과 비율은 계산하지 않고, 위반·결함·제어 실패로 판정하지 않는다.

초기 435개 기준값: count 435, mean 4.896189, min 0, max 5.808594, 최근 4.551057 / 이전 5.279502 (sim 390–450 / 330–390). `tests/test_api.py` 9번 테스트에서 오차 1e-6 이내로 검산한다.

## Firestore 구조

```
data/{docId}
  date: Timestamp            value: number           memo: string
  run_id: string|null        metric: string          unit: string
  sim_time_s: number|null    source_ref: string|null transform_version: string|null
  record_type: "imported" | "manual"

conversations/{id}           M3에서 정의 (컬렉션 이름만 설정에 예약)
```

- imported 문서 ID: M1-2의 결정적 ID `{run_id}__{metric}__1s__t{sim:05d}__v{transform_version}`를 그대로 쓴다.
- 날짜 변환: `"2026-10-10T18:45:32.747+09:00"` → 시간대 있는 datetime → firebase-admin이 Timestamp로 저장한다. 같은 시점 `2026-10-10T09:45:32.747Z`이다(가짜 클라이언트 테스트로 확인했고, 실제 Firestore에서는 확인하지 않았다).
- 적재기(`scripts/load_data.py`, `app/importer.py`)는 문서마다 `create`를 쓴다. 이미 있고 내용이 같으면 건너뛰고, 내용이 다르면 덮어쓰지 않고 conflict로 기록한다. 문서 하나가 실패해도 나머지는 계속 적재하고 실패는 `logs/import_errors_*.json`에 남긴다.
- 목록과 summary는 Firestore에서 동등 조건만 걸고 정렬·페이지 나누기는 서버에서 한다. 복합 색인 없이 동작하지만, 요청마다 조건에 맞는 문서를 모두 읽는다(현재 435건 규모를 전제로 함).
