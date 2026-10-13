"""저장소 계층. 서비스는 DataRepository만 알고, 메모리·Firestore 구현은 바꿔 끼운다.

저장 형식(dict):
  date: 시간대 있는 datetime (Firestore에서는 Timestamp로 저장된다)
  value: float, memo: str, run_id: str|None, metric: str, unit: str,
  sim_time_s: int|None, source_ref: str|None, transform_version: str|None,
  record_type: "imported" | "manual"
"""

from __future__ import annotations

import copy
import json
import re
from abc import ABC, abstractmethod
from datetime import timezone
from pathlib import Path


class RepositoryError(Exception):
    pass


class RepositoryUnavailable(RepositoryError):
    """저장소에 연결할 수 없음 (설정 누락, 인증 실패, 네트워크 오류)."""


class DocumentExists(RepositoryError):
    pass


class DocumentNotFound(RepositoryError):
    pass


def _sort_key(item: tuple[str, dict]):
    doc_id, doc = item
    return (doc["date"].astimezone(timezone.utc), doc_id)


class DataRepository(ABC):
    @abstractmethod
    def get(self, doc_id: str) -> dict | None: ...

    @abstractmethod
    def list(self, *, run_id: str | None = None, metric: str | None = None,
             record_type: str | None = None) -> list[tuple[str, dict]]:
        """조건(None은 무시)에 맞는 문서를 (date, id) 오름차순으로."""

    @abstractmethod
    def create(self, doc_id: str, doc: dict) -> None:
        """이미 있으면 DocumentExists."""

    @abstractmethod
    def update(self, doc_id: str, fields: dict) -> None:
        """없으면 DocumentNotFound."""

    @abstractmethod
    def delete(self, doc_id: str) -> None:
        """없으면 DocumentNotFound."""


class InMemoryRepository(DataRepository):
    """테스트·로컬 개발용. 프로세스가 끝나면 사라진다."""

    def __init__(self):
        self._docs: dict[str, dict] = {}

    def get(self, doc_id):
        doc = self._docs.get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    def list(self, *, run_id=None, metric=None, record_type=None):
        out = [(i, copy.deepcopy(d)) for i, d in self._docs.items()
               if (run_id is None or d.get("run_id") == run_id)
               and (metric is None or d.get("metric") == metric)
               and (record_type is None or d.get("record_type") == record_type)]
        return sorted(out, key=_sort_key)

    def create(self, doc_id, doc):
        if doc_id in self._docs:
            raise DocumentExists(doc_id)
        self._docs[doc_id] = copy.deepcopy(doc)

    def update(self, doc_id, fields):
        if doc_id not in self._docs:
            raise DocumentNotFound(doc_id)
        self._docs[doc_id].update(copy.deepcopy(fields))

    def delete(self, doc_id):
        if self._docs.pop(doc_id, None) is None:
            raise DocumentNotFound(doc_id)


class FirestoreRepository(DataRepository):
    """firebase-admin 기반. 연결 실패는 생성 시 기록해 두고 각 요청에서 RepositoryUnavailable로 알린다.

    목록은 동등 조건만 Firestore에서 걸고 정렬은 파이썬에서 한다 (복합 색인 없이 동작하도록).
    현재 규모(수백 건)를 전제로 한 선택이다.
    """

    def __init__(self, settings, client=None):
        self._collection = settings.data_collection
        self._timeout = settings.firestore_timeout_s
        self._client = client
        self._init_error: str | None = None
        if client is None:
            try:
                self._client = _make_client(settings)
            except Exception as e:  # 설정·키·초기화 오류 모두 서비스 불가로 취급
                self._init_error = f"Firestore 초기화 실패: {safe_error(e)}"

    def _col(self):
        if self._client is None:
            raise RepositoryUnavailable(self._init_error or "Firestore 미연결")
        return self._call(lambda: self._client.collection(self._collection))

    def _call(self, fn, *, doc_level: bool = False):
        """Google 예외 → 저장소 예외. NotFound 는 문서 단위 호출(doc_level)에서만 '문서 없음'으로 본다.
        컬렉션·데이터베이스 단위 NotFound(예: 데이터베이스 없음)는 서비스 불가다."""
        from google.api_core import exceptions as gexc
        from google.auth import exceptions as aexc
        try:
            return fn()
        except gexc.AlreadyExists as e:
            raise DocumentExists(safe_error(e))
        except gexc.NotFound as e:
            if doc_level:
                raise DocumentNotFound(safe_error(e))
            raise RepositoryUnavailable(f"Firestore 요청 실패: NotFound: {safe_error(e)}")
        except (gexc.GoogleAPIError, aexc.GoogleAuthError, OSError) as e:
            raise RepositoryUnavailable(f"Firestore 요청 실패: {type(e).__name__}: {safe_error(e)}")

    def get(self, doc_id):
        col = self._col()
        snap = self._call(lambda: col.document(doc_id).get(timeout=self._timeout))
        return snap.to_dict() if snap.exists else None

    def list(self, *, run_id=None, metric=None, record_type=None):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self._col()
        for field, value in (("run_id", run_id), ("metric", metric), ("record_type", record_type)):
            if value is not None:
                q = q.where(filter=FieldFilter(field, "==", value))
        docs = self._call(lambda: [(s.id, s.to_dict()) for s in q.stream(timeout=self._timeout)])
        return sorted(docs, key=_sort_key)

    def create(self, doc_id, doc):
        col = self._col()
        self._call(lambda: col.document(doc_id).create(doc, timeout=self._timeout))

    def update(self, doc_id, fields):
        col = self._col()
        self._call(lambda: col.document(doc_id).update(fields, timeout=self._timeout), doc_level=True)

    def delete(self, doc_id):
        col = self._col()
        ref = col.document(doc_id)
        if not self._call(lambda: ref.get(timeout=self._timeout)).exists:
            raise DocumentNotFound(doc_id)
        self._call(lambda: ref.delete(timeout=self._timeout), doc_level=True)


_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?(-----END [A-Z ]+-----|$)", re.S)
_KEY_FIELD = re.compile(r'("?(private_key|private_key_id|client_secret|refresh_token)"?\s*[:=]\s*)("[^"]*"|\S+)', re.I)


def safe_error(e: BaseException | str, limit: int = 300) -> str:
    """오류 메시지에서 키 내용(PEM 블록, private_key 값)을 지우고 길이를 줄인다."""
    msg = _PEM.sub("[REDACTED KEY]", str(e))
    msg = _KEY_FIELD.sub(lambda m: m.group(1) + "[REDACTED]", msg)
    return msg if len(msg) <= limit else msg[:limit] + "…"


def _make_client(settings):
    if not settings.firebase_credentials:
        raise RepositoryUnavailable("FIREBASE_CREDENTIALS(서비스 계정 키 경로)가 설정되지 않았습니다")
    key = Path(settings.firebase_credentials)
    if not key.is_file():
        raise RepositoryUnavailable(f"서비스 계정 키 파일이 없습니다: {key.name}")
    try:
        key_project = json.loads(key.read_text(encoding="utf-8")).get("project_id")
    except (ValueError, OSError):
        raise RepositoryUnavailable(f"서비스 계정 키 파일을 JSON으로 읽을 수 없습니다: {key.name}")
    if settings.firebase_project_id and key_project != settings.firebase_project_id:
        raise RepositoryUnavailable(
            f"키의 project_id({key_project})와 FIREBASE_PROJECT_ID({settings.firebase_project_id})가 다릅니다")
    import firebase_admin
    from firebase_admin import credentials, firestore

    name = "adair-mission-intelligence"
    try:
        app = firebase_admin.get_app(name)
    except ValueError:
        options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
        app = firebase_admin.initialize_app(credentials.Certificate(str(key)), options, name=name)
    return firestore.client(app, database_id=settings.firestore_database_id)
