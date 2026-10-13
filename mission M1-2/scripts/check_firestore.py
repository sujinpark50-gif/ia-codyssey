"""실제 Firestore 연결 확인 (데이터 적재 전).

`.env`의 Firestore 설정으로 접속해 다음을 확인한다. 데이터는 적재하지 않는다.
  1. 설정·키 파일 확인 (키 내용은 출력하지 않는다)
  2. 인증 및 `data` 컬렉션 접근 (현재 문서 수 집계)
  3. `connection_tests` 컬렉션에 임시 문서 1개 생성 → 조회 (Timestamp·KST 변환 확인) → 삭제 → 삭제 확인

임시 문서는 `data`가 아닌 별도 컬렉션에 만들어 기존 데이터에 닿지 않는다.
문서 ID는 `_conncheck-<uuid>`. 중간에 실패해도 삭제를 시도한다.

  python scripts/check_firestore.py
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.repository import FirestoreRepository, RepositoryError, safe_error  # noqa: E402
from app.service import KST, iso_kst  # noqa: E402

SAMPLE_ISO = "2026-10-10T18:45:32.747+09:00"
TEST_COLLECTION = "connection_tests"


class Checker:
    def __init__(self):
        self.results: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool | None, note: str = "") -> bool:
        status = "PASS" if ok else ("UNVERIFIED" if ok is None else "FAIL")
        self.results.append((name, status, note))
        print(f"[{status}] {name}" + (f" — {note}" if note else ""))
        return bool(ok)


def main() -> int:
    s = replace(Settings.from_env(), data_backend="firestore")
    c = Checker()

    key = Path(s.firebase_credentials) if s.firebase_credentials else None
    key_project = None
    if key and key.is_file():
        try:
            key_json = json.loads(key.read_text(encoding="utf-8"))
            key_project = key_json.get("project_id")
            inside_repo = ROOT.parent in key.resolve().parents or ROOT == key.resolve().parent
            c.record("키 파일이 저장소 밖에 있음", not inside_repo, key.name)
            c.record("키 종류 service_account", key_json.get("type") == "service_account")
        except (ValueError, OSError) as e:
            c.record("키 파일 읽기", False, safe_error(e))
            return 1
    else:
        c.record("FIREBASE_CREDENTIALS 키 파일", False, "설정되지 않았거나 파일이 없음")
        return 1
    print(f"  project_id={s.firebase_project_id or key_project}  database={s.firestore_database_id}  "
          f"collection={s.data_collection}")

    repo = FirestoreRepository(s)
    if not c.record("Firebase Admin 초기화", repo._init_error is None, repo._init_error or ""):
        return 1

    try:
        col = repo._col()
        n = repo._call(lambda: col.count().get(timeout=s.firestore_timeout_s))[0][0].value
        c.record("인증 및 data 컬렉션 접근", True, f"현재 문서 {int(n)}건")
    except RepositoryError as e:
        c.record("인증 및 data 컬렉션 접근", False, safe_error(e))
        return 1

    # 임시 문서는 별도 컬렉션에서 (같은 Firebase 앱·클라이언트 재사용)
    repo = FirestoreRepository(replace(s, data_collection=TEST_COLLECTION), client=repo._client)
    print(f"  임시 문서 컬렉션={TEST_COLLECTION}")

    doc_id = f"_conncheck-{uuid.uuid4().hex}"
    sample = datetime.fromisoformat(SAMPLE_ISO)
    created = False
    try:
        repo.create(doc_id, {"date": sample, "value": 1.25, "memo": "연결 확인용 임시 문서", "run_id": None,
                             "metric": "_connection_check", "unit": "m/s", "sim_time_s": None,
                             "source_ref": None, "transform_version": None, "record_type": "manual"})
        created = True
        c.record("임시 문서 생성", True, doc_id)
        got = repo.get(doc_id)
        c.record("임시 문서 조회", got is not None)
        if got:
            d = got["date"]
            c.record("date가 Timestamp(시간대 있는 datetime)로 조회됨",
                     isinstance(d, datetime) and d.tzinfo is not None, type(d).__name__)
            c.record("같은 시점 보존", d == sample,
                     d.astimezone(timezone.utc).isoformat(timespec="milliseconds"))
            c.record("KST 변환", iso_kst(d) == SAMPLE_ISO, iso_kst(d))
            c.record("value 보존", got["value"] == 1.25)
    except RepositoryError as e:
        c.record("임시 문서 생성·조회", False, safe_error(e))
    finally:
        if created:
            try:
                repo.delete(doc_id)
                c.record("임시 문서 삭제", repo.get(doc_id) is None)
            except RepositoryError as e:
                c.record("임시 문서 삭제", False, f"{doc_id} 를 직접 지워야 합니다: {safe_error(e)}")

    fails = [r for r in c.results if r[1] == "FAIL"]
    print(f"\n결과: PASS {sum(r[1] == 'PASS' for r in c.results)} / FAIL {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
