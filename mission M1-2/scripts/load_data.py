"""M1-2 1초 시계열을 저장소에 적재한다.

기본은 메모리 저장소 모의 적재(검증 + 두 번 적재해 중복 없음 확인)이며 아무것도 업로드하지 않는다.
실제 Firestore 적재는 --backend firestore --confirm-upload 를 모두 줘야 실행된다 (별도 승인 후 사용).

  python scripts/load_data.py
  python scripts/load_data.py --backend firestore --confirm-upload
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
from app.repository import FirestoreRepository, InMemoryRepository  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=ROOT / "data/ugv_speed_1s.json")
    ap.add_argument("--backend", choices=("memory", "firestore"), default="memory")
    ap.add_argument("--confirm-upload", action="store_true", help="실제 Firestore 쓰기를 허용")
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
        return 0 if total == len(docs) and not first["errors"] and second["created"] == 0 else 1

    if not a.confirm_upload:
        print("Firestore 적재는 --confirm-upload 가 있어야 실행됩니다. 아무것도 쓰지 않았습니다.", file=sys.stderr)
        return 2
    settings = replace(Settings.from_env(), data_backend="firestore")
    result = import_documents(FirestoreRepository(settings), docs)
    print(json.dumps({k: (v if isinstance(v, int) else len(v)) for k, v in result.items()}, ensure_ascii=False))
    if result["errors"] or result["conflicts"]:
        a.error_log.mkdir(parents=True, exist_ok=True)
        path = a.error_log / f"import_errors_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"실패·충돌 기록: {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
