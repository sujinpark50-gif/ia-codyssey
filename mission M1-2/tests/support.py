"""테스트 공용: 가짜 토큰 검증기와 로그인된 TestClient.

서버에는 인증을 건너뛰는 경로가 없다. 테스트는 create_app(verifier=...) 로 검증기만 바꿔 끼운다.
"""

from dataclasses import replace

from fastapi.testclient import TestClient as _TestClient

from app.auth import TokenVerifier
from app.config import Settings
from app.main import create_app as _create_app

ALLOWED = ("a@test.dev", "b@test.dev")
USERS = {
    "tok-a": {"sub": "uid-a", "user_id": "uid-a", "email": "a@test.dev", "email_verified": True},
    "tok-b": {"sub": "uid-b", "user_id": "uid-b", "email": "b@test.dev", "email_verified": True},
    "tok-outsider": {"sub": "uid-x", "user_id": "uid-x", "email": "x@other.dev", "email_verified": True},
    "tok-unverified": {"sub": "uid-u", "user_id": "uid-u", "email": "a@test.dev", "email_verified": False},
}


class FakeVerifier(TokenVerifier):
    def verify(self, token):
        if token not in USERS:
            raise ValueError("unknown token")
        return dict(USERS[token])


def create_app(settings=None, **kw):
    settings = settings or Settings()
    if not settings.allowed_emails:
        settings = replace(settings, allowed_emails=ALLOWED)
    kw.setdefault("verifier", FakeVerifier())
    return _create_app(settings, **kw)


def TestClient(app, token="tok-a", **kw):  # noqa: N802 — fastapi TestClient 자리를 그대로 대신한다
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return _TestClient(app, headers=headers, **kw)
