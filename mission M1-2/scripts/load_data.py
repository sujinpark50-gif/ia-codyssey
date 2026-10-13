"""M1-2 1초 시계열을 저장소에 적재한다.

기본은 메모리 저장소 모의 적재(검증 + 두 번 적재해 중복 없음 확인)이며 아무것도 업로드하지 않는다.
실제 Firestore 적재는 --backend firestore --confirm-upload 를 모두 줘야 실행된다 (별도 승인 후 사용).

  python scripts/load_data.py
  python scripts/load_data.py --backend firestore --confirm-upload
  python scripts/load_data.py --backend firestore --verify-only   # 쓰기 없이 적재 결과만 검증
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.importer import ImportValidationError, import_documents, load_documents  # noqa: E402
from app.repository import FirestoreRepository, InMemoryRepository, RepositoryError, safe_error  # noqa: E402
from app.service import DataService  # noqa: E402

TOL = 1e-6


def verify(repo, docs: list[tuple[str, dict]], expected_path: Path) -> bool:
    """적재 결과 검증: 문서 수·ID 집합·원본 식별자, summary 통계가 M1-2 값과 일치."""
    run_id, metric = docs[0][1]["run_id"], docs[0][1]["metric"]
    ok = True

    def check(name, cond, note=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {note}" if note else ""))

    stored = dict(repo.list(run_id=run_id, metric=metric, record_type="imported"))
    expected = dict(docs)
    check("imported 문서 수", len(stored) == len(expected), f"{len(stored)} / {len(expected)}")
    check("문서 ID 집합 일치", set(stored) == set(expected))
    diff = [i for i in expected if i in stored and any(
        stored[i].get(k) != expected[i][k] for k in ("date", "value", "unit", "run_id", "source_ref", "sim_time_s"))]
    check("날짜·값·단위·원본 식별자 보존", not diff, f"불일치 {len(diff)}건 {diff[:3]}")

    agg = json.loads(expected_path.read_text(encoding="utf-8"))["aggregation"]["time_weighted"]
    s = DataService(repo).summary(run_id=run_id, metric=metric)
    if s.manual_count:
        print(f"  (참고) 같은 run_id 의 manual 문서 {s.manual_count}건이 summary 에 포함됨 — imported 만 따로 계산")
        vals = [d["value"] for d in stored.values()]
        mean, lo, hi = sum(vals) / len(vals), min(vals), max(vals)
    else:
        mean, lo, hi = s.mean, s.min, s.max
    for name, got, want in (("평균", mean, agg["mean"]), ("최소", lo, agg["min"]), ("최대", hi, agg["max"])):
        check(f"{name} {want}", abs(got - want) <= TOL, f"{got:.6f}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=ROOT / "data/ugv_speed_1s.json")
    ap.add_argument("--backend", choices=("memory", "firestore"), default="memory")
    ap.add_argument("--confirm-upload", action="store_true", help="실제 Firestore 쓰기를 허용")
    ap.add_argument("--verify-only", action="store_true", help="쓰지 않고 Firestore 적재 결과만 검증")
    ap.add_argument("--expected", type=Path, default=ROOT / "data/validation_summary.json")
    ap.add_argument("--error-log", type=Path, default=ROOT / "logs")
    a = ap.parse_args(argv)

    try:
        docs = load_documents(a.input)
    except ImportValidationError as e:
        print(f"적재 전 검증 실패: {e}", file=sys.stderr)
        return 1
    print(f"검증 통과: {len(docs)}건 ({a.input.name})")

    if a.backend == "memory":
        repo = InMemoryRepository()
        first = import_documents(repo, docs)
        second = import_documents(repo, docs)
        total = len(repo.list())
        print(f"1차 적재 created={first['created']} unchanged={first['unchanged']} "
              f"conflicts={len(first['conflicts'])} errors={len(first['errors'])}")
        print(f"2차 적재 created={second['created']} unchanged={second['unchanged']} "
              f"conflicts={len(second['conflicts'])} errors={len(second['errors'])}")
        print(f"저장소 문서 수: {total}")
        return 0 if (total == len(docs) and not first["errors"] and second["created"] == 0
                     and verify(repo, docs, a.expected)) else 1

    settings = replace(Settings.from_env(), data_backend="firestore")
    repo = FirestoreRepository(settings)
    try:
        if a.verify_only:
            return 0 if verify(repo, docs, a.expected) else 1
        if not a.confirm_upload:
            print("Firestore 적재는 --confirm-upload 가 있어야 실행됩니다. 아무것도 쓰지 않았습니다.", file=sys.stderr)
            return 2
        result = import_documents(repo, docs)
        print(f"적재 created={result['created']} unchanged={result['unchanged']} "
              f"conflicts={len(result['conflicts'])} errors={len(result['errors'])}")
        if result["errors"] or result["conflicts"]:
            a.error_log.mkdir(parents=True, exist_ok=True)
            path = a.error_log / f"import_errors_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"실패·충돌 기록: {path}", file=sys.stderr)
            return 1
        return 0 if verify(repo, docs, a.expected) else 1
    except RepositoryError as e:
        print(f"Firestore 오류: {safe_error(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
