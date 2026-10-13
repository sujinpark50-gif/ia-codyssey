"""대화 기록 저장소.

Firestore 구조:
  conversations/{conv_id}                title, created_at, updated_at, run_id, metric, message_count
  conversations/{conv_id}/messages/{seq} role, content, created_at, seq (+ assistant: model, stats_basis, grounding)

메시지 추가(append)는 대화 문서 갱신과 메시지 문서 생성을 한 번의 batch 로 커밋한다 → 전부 저장되거나 하나도 저장되지 않는다.
메시지 문서 ID 는 0을 채운 순번(000000, 000001, …)이라 같은 대화에 동시에 두 요청이 같은 순번을 쓰면
batch 전체가 실패한다(덮어쓰기 없음).
"""

from __future__ import annotations

import copy
import threading
from abc import ABC, abstractmethod
from datetime import timezone

from .repository import (DocumentExists, DocumentNotFound, RepositoryUnavailable,
                         firestore_call, safe_error)


def _updated_key(item):
    return item[1]["updated_at"].astimezone(timezone.utc)


class ConversationRepository(ABC):
    @abstractmethod
    def get(self, conv_id: str) -> dict | None: ...

    @abstractmethod
    def list(self, limit: int = 50) -> list[tuple[str, dict]]:
        """updated_at 내림차순."""

    @abstractmethod
    def messages(self, conv_id: str, last_n: int | None = None) -> list[dict]:
        """seq 오름차순. last_n 이면 마지막 n개."""

    @abstractmethod
    def create(self, conv_id: str, meta: dict) -> None: ...

    @abstractmethod
    def append(self, conv_id: str, *, new_meta: dict | None, messages: list[dict], updated_at) -> None:
        """new_meta 가 있으면 대화를 새로 만들며 메시지를 넣는다. 원자적."""

    @abstractmethod
    def delete(self, conv_id: str) -> None: ...


class InMemoryConversationRepository(ConversationRepository):
    def __init__(self):
        self._convs: dict[str, dict] = {}
        self._msgs: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def get(self, conv_id):
        c = self._convs.get(conv_id)
        return copy.deepcopy(c) if c else None

    def list(self, limit=50):
        return sorted(((i, copy.deepcopy(c)) for i, c in self._convs.items()), key=_updated_key, reverse=True)[:limit]

    def messages(self, conv_id, last_n=None):
        ms = self._msgs.get(conv_id, [])
        return copy.deepcopy(ms[-last_n:] if last_n else ms)

    def create(self, conv_id, meta):
        with self._lock:
            if conv_id in self._convs:
                raise DocumentExists(conv_id)
            self._convs[conv_id] = {**copy.deepcopy(meta), "message_count": 0}
            self._msgs[conv_id] = []

    def append(self, conv_id, *, new_meta, messages, updated_at):
        with self._lock:
            if new_meta is not None:
                if conv_id in self._convs:
                    raise DocumentExists(conv_id)
                conv, start = {**copy.deepcopy(new_meta), "message_count": 0}, 0
            else:
                if conv_id not in self._convs:
                    raise DocumentNotFound(conv_id)
                conv = copy.deepcopy(self._convs[conv_id])
                start = conv["message_count"]
            msgs = [{**copy.deepcopy(m), "seq": start + i} for i, m in enumerate(messages)]
            conv.update(updated_at=updated_at, message_count=start + len(messages))
            self._convs[conv_id] = conv
            self._msgs.setdefault(conv_id, []).extend(msgs)

    def delete(self, conv_id):
        with self._lock:
            if self._convs.pop(conv_id, None) is None:
                raise DocumentNotFound(conv_id)
            self._msgs.pop(conv_id, None)


class FirestoreConversationRepository(ConversationRepository):
    def __init__(self, settings, client=None, init_error: str | None = None):
        self._name = settings.conversations_collection
        self._timeout = settings.firestore_timeout_s
        self._client = client
        self._init_error = init_error

    def _col(self):
        if self._client is None:
            raise RepositoryUnavailable(self._init_error or "Firestore 미연결")
        return firestore_call(lambda: self._client.collection(self._name))

    def get(self, conv_id):
        snap = firestore_call(lambda: self._col().document(conv_id).get(timeout=self._timeout))
        return snap.to_dict() if snap.exists else None

    def list(self, limit=50):
        q = self._col().order_by("updated_at", direction="DESCENDING").limit(limit)
        return firestore_call(lambda: [(s.id, s.to_dict()) for s in q.stream(timeout=self._timeout)])

    def messages(self, conv_id, last_n=None):
        sub = self._col().document(conv_id).collection("messages")
        if last_n:
            q = sub.order_by("seq", direction="DESCENDING").limit(last_n)
            out = firestore_call(lambda: [s.to_dict() for s in q.stream(timeout=self._timeout)])
            return list(reversed(out))
        q = sub.order_by("seq")
        return firestore_call(lambda: [s.to_dict() for s in q.stream(timeout=self._timeout)])

    def create(self, conv_id, meta):
        ref = self._col().document(conv_id)
        firestore_call(lambda: ref.create({**meta, "message_count": 0}, timeout=self._timeout))

    def append(self, conv_id, *, new_meta, messages, updated_at):
        ref = self._col().document(conv_id)
        if new_meta is not None:
            start = 0
        else:
            snap = firestore_call(lambda: ref.get(timeout=self._timeout))
            if not snap.exists:
                raise DocumentNotFound(conv_id)
            start = snap.to_dict().get("message_count", 0)
        batch = self._client.batch()
        if new_meta is not None:
            batch.create(ref, {**new_meta, "updated_at": updated_at, "message_count": len(messages)})
        else:
            batch.update(ref, {"updated_at": updated_at, "message_count": start + len(messages)})
        for i, m in enumerate(messages):
            batch.create(ref.collection("messages").document(f"{start + i:06d}"), {**m, "seq": start + i})
        firestore_call(lambda: batch.commit(timeout=self._timeout))

    def delete(self, conv_id):
        ref = self._col().document(conv_id)
        if not firestore_call(lambda: ref.get(timeout=self._timeout)).exists:
            raise DocumentNotFound(conv_id)
        sub = ref.collection("messages")
        while True:  # 메시지를 400개씩 지운 뒤 대화 문서를 지운다 (batch 한도 500)
            snaps = firestore_call(lambda: list(sub.limit(400).stream(timeout=self._timeout)))
            if not snaps:
                break
            batch = self._client.batch()
            for s in snaps:
                batch.delete(s.reference)
            firestore_call(lambda: batch.commit(timeout=self._timeout))
        firestore_call(lambda: ref.delete(timeout=self._timeout), doc_level=True)


__all__ = ["ConversationRepository", "InMemoryConversationRepository", "FirestoreConversationRepository",
           "safe_error"]
