# 데이터 규격 — 1초 속도 시계열과 Firestore 변환

변환 버전 `1.0.0`. 생성 코드 `scripts/prepare_data.py`.

## 1. 원본 CSV (읽기 전용)

| 컬럼 | 단위 | 사용 |
|---|---|---|
| `sim_t` | s, Gazebo 시뮬레이션 시각 | 속도·집계 시간축 |
| `x`, `y` | m, 월드 평면 좌표 | 속도 계산 |
| `wall_t` | s, Unix epoch (UTC) 기록기 벽시계 | `date` 표기 |
| `z`, `yaw`, `roll`, `pitch`, `steer_rad`, `wheel_rad_s` | — | 사용하지 않음 (품질 프로파일만) |

행 번호 규칙: 헤더 제외 1부터 센다. CSV 파일의 줄 번호 = 행 번호 + 1.

## 2. `data/ugv_speed_1s.csv`

한 행 = sim 1초 구간 하나. 435행.

| 컬럼 | 형식 | 설명 |
|---|---|---|
| `doc_id` | 문자열 | Firestore 문서 ID (4절) |
| `date` | ISO 8601, `+09:00` | 구간 시작 sim 시각의 `wall_t` 선형보간, ms 반올림 |
| `sim_time_s` | 정수 | 구간 시작 k (구간 = [k, k+1)) |
| `bin_end_sim_s` | 정수 | k + 1 (미포함) |
| `value` | 실수, 소수 6자리 | 시간가중 평균 평면 속도 m/s (대표값) |
| `speed_simple_mean_mps` | 실수 | 비교용 단순 표본 평균 (중점 기준) |
| `n_intervals` | 정수 | 구간과 겹친 속도 표본 수 |
| `n_midpoint_samples` | 정수 | 중점이 구간 안에 있는 표본 수 |
| `covered_ms` | 정수 | 관측이 덮은 시간. 대표 시계열은 모두 1000 |
| `source_row_first`, `source_row_last` | 정수 | 이 구간 계산에 쓴 원본 행 범위 (양 끝 포함) |
| `run_id`, `metric`, `unit`, `transform_version` | 문자열 | 4절과 같음 |

경계를 가로지르는 표본 때문에 이웃 구간의 원본 행 범위는 1행씩 겹친다(예: 행 25–26 표본은 sim 15와 16 구간에 나눠 들어감).

## 3. `data/ugv_speed_1s.json` — Firestore 적재 준비

```json
{
  "transform_version": "1.0.0",
  "run_id": "B1x_slope_low_20261010_184532",
  "metric": "ugv_speed_planar",
  "unit": "m/s",
  "source": {"file": "B1x_slope_low_20261010_184532.csv", "sha256": "4d18f3d4…"},
  "count": 435,
  "documents": [
    {
      "id": "B1x_slope_low_20261010_184532__ugv_speed_planar__1s__t00015__v1.0.0",
      "data": {
        "date": "2026-10-10T18:45:32.747+09:00",
        "value": 0.0,
        "memo": "sim 15–16s 시간가중 평균, 원본 행 6~26 (속도 구간 20개)",
        "run_id": "B1x_slope_low_20261010_184532",
        "metric": "ugv_speed_planar",
        "unit": "m/s",
        "sim_time_s": 15,
        "source_ref": "B1x_slope_low_20261010_184532.csv@sha256:4d18f3d45101#rows=6-26",
        "transform_version": "1.0.0"
      }
    }
  ]
}
```

| 필드 | 타입 | 규칙 |
|---|---|---|
| `date` | string (ISO 8601, 시간대 포함) | 업로드 단계에서 Firestore Timestamp로 바꿀지 결정 필요 (6절) |
| `value` | number | m/s, 소수 6자리 반올림, 0 이상 |
| `memo` | string | 집계 근거를 기계적으로 적은 문장. 해석은 넣지 않는다 |
| `run_id` | string | 원본 JSON `tag + "_" + stamp` = CSV 파일명 |
| `metric` | string | `ugv_speed_planar` (평면 x, y 속도) |
| `unit` | string | `m/s` |
| `sim_time_s` | integer | 구간 시작 sim 초 |
| `source_ref` | string | `<CSV 파일명>@sha256:<앞 12자리>#rows=<첫 행>-<끝 행>` |
| `transform_version` | string | 변환 규칙이 바뀌면 올린다 |

## 4. 문서 ID

```
{run_id}__{metric}__1s__t{sim_time_s:05d}__v{transform_version}
```

- 같은 시험·같은 구간·같은 변환 버전이면 항상 같은 ID → 다시 올려도 덮어쓰기만 되고 중복이 생기지 않는다.
- 변환 버전이 바뀌면 ID가 달라져 이전 버전과 공존한다.
- Firestore ID 제약(`/` 없음, 1,500바이트 이하, `.`·`..` 단독 아님)을 만족한다.
- `t00015`처럼 0을 채워 사전순 정렬 = 시간순.

## 5. 원본 연결

`source_ref`의 파일명·해시 앞자리·행 범위 → `data/source_manifest.json`의 전체 해시·원본 경로 → `ADAIR/logs/ugv/` 원본. 행 범위로 원본 CSV에서 해당 관측을 그대로 찾을 수 있다.

## 6. 정하지 않은 것

- Firestore 컬렉션 이름
- `date`를 문자열로 둘지 Timestamp로 바꿀지
- 일괄 쓰기 방식(배치 크기 등)

이 셋은 Firestore 연동 단계에서 정한다.
