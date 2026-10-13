"""M1-2 산출물(data/ugv_speed_1s.json) → 저장소 적재.

- 적재 전 전체 검증 (개수, ID 중복, 날짜 시간대, 값 유한성, 단위, 원본 식별자)
- 같은 ID가 이미 있고 내용이 같으면 건너뜀 → 재적재해도 문서 수가 늘지 않는다
- 같은 ID인데 내용이 다르면 덮어쓰지 않고 conflict로 기록
- 문서별 실패는 errors에 모으고 나머지는 계속 진행
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .models import parse_aware_datetime
from .repository import DataRepository, DocumentExists, RepositoryUnavailable, safe_error

FIELDS = ("date", "value", "memo", "run_id", "metric", "unit", "sim_time_s", "source_ref", "transform_version")


class ImportValidationError(Exception):
    pass


def load_documents(path: Path) -> list[tuple[str, dict]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    docs = payload.get("documents")
    if not isinstance(docs, list):
        raise ImportValidationError("documents 배열이 없습니다")
    problems: list[str] = []
    if payload.get("count") != len(docs):
        problems.append(f"count({payload.get('count')}) != documents({len(docs)})")
    ids = [d.get("id") for d in docs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        problems.append(f"중복 ID {len(dup)}개: {dup[:5]}")
    source_file = (payload.get("source") or {}).get("file", "")
    out = []
    for d in docs:
        doc_id, data = d.get("id"), d.get("data") or {}
        missing = [f for f in FIELDS if f not in data]
        if not doc_id or "/" in doc_id or missing:
            problems.append(f"{doc_id}: ID 또는 필드 누락 {missing}")
            continue
        try:
            date = parse_aware_datetime(data["date"])
        except ValueError as e:
            problems.append(f"{doc_id}: {e}")
            continue
        v = data["value"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            problems.append(f"{doc_id}: value {v!r}")
            continue
        if data["run_id"] != payload.get("run_id") or data["unit"] != payload.get("unit") \
                or data["metric"] != payload.get("metric") \
                or data["transform_version"] != payload.get("transform_version"):
            problems.append(f"{doc_id}: run_id/metric/unit/transform_version 이 상위 값과 다름")
            continue
        if not str(data["source_ref"]).startswith(source_file + "@sha256:"):
            problems.append(f"{doc_id}: source_ref 가 원본 파일({source_file})을 가리키지 않음")
            continue
        out.append((doc_id, {**{f: data[f] for f in FIELDS}, "date": date, "value": float(v),
                             "record_type": "imported"}))
    if problems:
        raise ImportValidationError(f"적재 전 검증 실패 {len(problems)}건: " + "; ".join(problems[:10]))
    return out


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in (*FIELDS, "record_type"))


def import_documents(repo: DataRepository, docs: list[tuple[str, dict]]) -> dict:
    result = {"total": len(docs), "created": 0, "unchanged": 0, "conflicts": [], "errors": []}
    for doc_id, doc in docs:
        try:
            try:
                repo.create(doc_id, doc)
                result["created"] += 1
            except DocumentExists:
                existing = repo.get(doc_id)
                if existing is not None and _same(existing, doc):
                    result["unchanged"] += 1
                else:
                    result["conflicts"].append(doc_id)
        except RepositoryUnavailable as e:
            result["errors"].append({"id": doc_id, "error": str(e)})
        except Exception as e:  # 문서 하나의 실패가 전체를 멈추지 않게
            result["errors"].append({"id": doc_id, "error": f"{type(e).__name__}: {safe_error(e)}"})
    return result
