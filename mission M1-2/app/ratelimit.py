"""GPT 채팅 호출 제한.

- 사용자별 분당·시간당 횟수: 서버 메모리 슬라이딩 윈도 (재시작하면 초기화)
- 사용자별 동시 요청 수: 서버 메모리
- 전체 일일 상한: DailyCounter — Firestore `usage/{YYYY-MM-DD}`(KST 날짜) 트랜잭션 증가라 재시작해도 유지

GPT 호출 직전에 일일 카운터를 올린다. GPT 가 실패해도 되돌리지 않는다 (상위 API 비용이 이미 났을 수 있음).
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .repository import RepositoryUnavailable, firestore_call

KST = timezone(timedelta(hours=9))


class RateLimited(Exception):
    def __init__(self, detail: str, retry_after_s: int):
        super().__init__(detail)
        self.detail, self.retry_after_s = detail, max(1, int(retry_after_s))


def kst_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y-%m-%d")


def seconds_to_kst_midnight(now: datetime | None = None) -> int:
    n = (now or datetime.now(timezone.utc)).astimezone(KST)
    nxt = (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((nxt - n).total_seconds()) + 1


class DailyCounter(ABC):
    @abstractmethod
    def try_increment(self, day: str, limit: int) -> int:
        """day 의 사용량을 1 올리고 새 값을 돌려준다. 이미 limit 이면 올리지 않고 -1."""

    @abstractmethod
    def get(self, day: str) -> int: ...


class MemoryDailyCounter(DailyCounter):
    def __init__(self):
        self._n: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def try_increment(self, day, limit):
        with self._lock:
            if self._n[day] >= limit:
                return -1
            self._n[day] += 1
            return self._n[day]

    def get(self, day):
        return self._n.get(day, 0)


class FirestoreDailyCounter(DailyCounter):
    def __init__(self, client, collection: str = "usage", timeout_s: float = 10.0, init_error: str | None = None):
        self._client, self._name, self._timeout, self._init_error = client, collection, timeout_s, init_error

    def _ref(self, day):
        if self._client is None:
            raise RepositoryUnavailable(self._init_error or "Firestore 미연결")
        return self._client.collection(self._name).document(day)

    def try_increment(self, day, limit):
        from google.cloud import firestore
        ref = self._ref(day)

        @firestore.transactional
        def txn(transaction):
            snap = ref.get(transaction=transaction)
            n = (snap.to_dict() or {}).get("chat_calls", 0) if snap.exists else 0
            if n >= limit:
                return -1
            transaction.set(ref, {"chat_calls": n + 1, "updated_at": datetime.now(timezone.utc)}, merge=True)
            return n + 1

        return firestore_call(lambda: txn(self._client.transaction()))

    def get(self, day):
        snap = firestore_call(lambda: self._ref(day).get(timeout=self._timeout))
        return (snap.to_dict() or {}).get("chat_calls", 0) if snap.exists else 0


class ChatRateLimiter:
    def __init__(self, *, per_minute: int, per_hour: int, max_concurrent: int, daily_limit: int,
                 daily: DailyCounter, clock=time.monotonic):
        self.per_minute, self.per_hour = per_minute, per_hour
        self.max_concurrent, self.daily_limit = max_concurrent, daily_limit
        self.daily = daily
        self._clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._active: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    @contextmanager
    def slot(self, uid: str):
        """GPT 호출 1회분 허가. 한도를 넘으면 RateLimited (GPT 를 부르지 않는다)."""
        now = self._clock()
        with self._lock:
            hits = self._hits[uid]
            while hits and now - hits[0] >= 3600:
                hits.popleft()
            in_minute = [t for t in hits if now - t < 60]
            if self._active[uid] >= self.max_concurrent:
                raise RateLimited("이전 질문을 처리하고 있습니다. 답변을 받은 뒤 다시 보내세요", 2)
            if len(in_minute) >= self.per_minute:
                raise RateLimited(f"질문은 1분에 {self.per_minute}회까지입니다", 60 - (now - in_minute[0]))
            if len(hits) >= self.per_hour:
                raise RateLimited(f"질문은 1시간에 {self.per_hour}회까지입니다", 3600 - (now - hits[0]))
            self._active[uid] += 1
        try:
            if self.daily.try_increment(kst_day(), self.daily_limit) < 0:
                raise RateLimited(f"오늘 전체 질문 한도({self.daily_limit}회)를 모두 썼습니다", seconds_to_kst_midnight())
            with self._lock:
                self._hits[uid].append(now)
            yield
        finally:
            with self._lock:
                self._active[uid] -= 1

    def status(self, uid: str) -> dict:
        now = self._clock()
        with self._lock:
            hits = [t for t in self._hits.get(uid, ()) if now - t < 3600]
        try:
            used_today = self.daily.get(kst_day())
        except RepositoryUnavailable:
            used_today = None
        return {"per_minute": self.per_minute, "per_hour": self.per_hour, "daily_limit": self.daily_limit,
                "used_last_minute": sum(1 for t in hits if now - t < 60), "used_last_hour": len(hits),
                "used_today": used_today}
