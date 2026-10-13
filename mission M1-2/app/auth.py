"""Firebase ID 토큰 인증.

브라우저는 Firebase JS SDK 로 Google 로그인 후 받은 ID 토큰을 `Authorization: Bearer <토큰>` 으로 보낸다.
서버는 Google 공개 인증서로 서명·만료·대상 프로젝트를 검증하고, 이메일이 ALLOWED_EMAILS 에 있어야 통과시킨다.
서비스 계정 권한은 필요 없다. 인증을 건너뛰는 경로는 없다 (설정이 없으면 503).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import Request

log = logging.getLogger("adair_mi.auth")


@dataclass(frozen=True)
class User:
    uid: str
    email: str


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class TokenVerifier(ABC):
    @abstractmethod
    def verify(self, token: str) -> dict:
        """검증된 토큰 클레임. 실패하면 ValueError."""


CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"


class FirebaseTokenVerifier(TokenVerifier):
    """Firebase ID 토큰 검증 (google-auth). 공개 인증서는 Cache-Control max-age 동안 재사용한다."""

    def __init__(self, project_id: str, clock_skew_s: int = 10, fetch=None, clock=time.time):
        self.project_id = project_id
        self.clock_skew_s = clock_skew_s
        self._fetch = fetch or self._fetch_certs
        self._clock = clock
        self._certs: dict | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _fetch_certs() -> tuple[dict, float]:
        import requests
        r = requests.get(CERTS_URL, timeout=10)
        r.raise_for_status()
        m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
        return r.json(), float(m.group(1)) if m else 3600.0

    def _get_certs(self) -> dict:
        with self._lock:
            if self._certs is None or self._clock() >= self._expires:
                certs, max_age = self._fetch()
                self._certs, self._expires = certs, self._clock() + max_age
            return self._certs

    def verify(self, token):
        from google.auth import jwt
        try:
            certs = self._get_certs()
        except Exception as e:  # 인증서를 못 받으면 로그인 판단 불가 → 서비스 불가
            raise AuthError(503, f"로그인 확인 서버에 연결할 수 없습니다 ({type(e).__name__})") from e
        try:
            claims = jwt.decode(token, certs=certs, audience=self.project_id,
                                clock_skew_in_seconds=self.clock_skew_s)
        except Exception as e:  # 서명·만료·형식·대상 오류
            raise ValueError(type(e).__name__) from e
        if claims.get("iss") != f"https://securetoken.google.com/{self.project_id}":
            raise ValueError("발급자 불일치")
        if not claims.get("sub"):
            raise ValueError("sub 없음")
        if claims.get("auth_time", 0) > self._clock() + self.clock_skew_s:
            raise ValueError("auth_time 이 미래")
        return claims


class UnavailableVerifier(TokenVerifier):
    def __init__(self, reason: str):
        self.reason = reason

    def verify(self, token):
        raise AuthError(503, self.reason)


def build_verifier(settings) -> TokenVerifier:
    if not settings.firebase_project_id:
        return UnavailableVerifier("인증 설정이 없습니다 (FIREBASE_PROJECT_ID)")
    return FirebaseTokenVerifier(settings.firebase_project_id)


class Authenticator:
    def __init__(self, verifier: TokenVerifier, allowed_emails: tuple[str, ...]):
        self.verifier = verifier
        self.allowed = {e.strip().lower() for e in allowed_emails if e.strip()}

    def __call__(self, request: Request) -> User:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError(401, "로그인이 필요합니다")
        try:
            claims = self.verifier.verify(token.strip())
        except ValueError as e:
            log.info("토큰 거부: %s", e)
            raise AuthError(401, "로그인 정보가 유효하지 않습니다. 다시 로그인하세요")
        email = (claims.get("email") or "").lower()
        if not claims.get("email_verified"):
            raise AuthError(403, "이메일 인증이 확인되지 않은 계정입니다")
        if not self.allowed:
            raise AuthError(403, "허용된 사용자가 설정되지 않았습니다 (ALLOWED_EMAILS)")
        if email not in self.allowed:
            raise AuthError(403, "이 서비스를 사용할 수 없는 계정입니다")
        uid = claims.get("user_id") or claims.get("sub")
        if not uid:
            raise AuthError(401, "로그인 정보가 유효하지 않습니다")
        user = User(uid=uid, email=email)
        request.state.user = user
        return user
