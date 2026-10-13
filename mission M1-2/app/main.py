"""ADAIR Mission Intelligence API + 웹 화면.

실행: uvicorn app.main:create_app --factory --reload
  /            웹 화면 (web/, 공개 정적 파일 — 데이터·비밀값 없음)
  /api/config  Firebase 웹 로그인 설정 (공개 식별자만)
  /api/*       그 밖의 API 는 모두 Firebase 로그인 + ALLOWED_EMAILS 필요
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import chat as chat_api
from .api import data as data_api
from .auth import Authenticator, AuthError, TokenVerifier, build_verifier
from .chat import DEFAULT_METRIC, SUPPORTED_METRICS, ChatSaveFailed, ChatService
from .config import ROOT, Settings
from .conversations import (ConversationRepository, FirestoreConversationRepository,
                            InMemoryConversationRepository)
from .importer import import_documents, load_documents
from .llm import LLMClient, LLMError, build_llm, redact
from .models import MAX_MESSAGE_CHARS
from .ratelimit import ChatRateLimiter, DailyCounter, FirestoreDailyCounter, MemoryDailyCounter, RateLimited
from .repository import DataRepository, FirestoreRepository, InMemoryRepository, RepositoryUnavailable
from .service import DataService, ServiceError, load_quality

log = logging.getLogger("adair_mi")
WEB_DIR = ROOT / "web"


def build_repository(settings: Settings) -> tuple[DataRepository, dict | None]:
    if settings.data_backend == "firestore":
        return FirestoreRepository(settings), None
    repo = InMemoryRepository()
    seed = None
    if settings.seed_on_startup:
        if settings.seed_path.is_file():
            seed = import_documents(repo, load_documents(settings.seed_path))
        else:
            log.warning("시드 파일이 없어 빈 메모리 저장소로 시작합니다: %s", settings.seed_path)
    return repo, seed


def build_conversations(settings: Settings, data_repo: DataRepository) -> ConversationRepository:
    if isinstance(data_repo, FirestoreRepository):  # 같은 Firebase 클라이언트를 함께 쓴다
        return FirestoreConversationRepository(settings, client=data_repo._client, init_error=data_repo._init_error)
    return InMemoryConversationRepository()


def build_daily_counter(settings: Settings, data_repo: DataRepository) -> DailyCounter:
    if isinstance(data_repo, FirestoreRepository):  # 재시작해도 유지되는 일일 상한
        return FirestoreDailyCounter(data_repo._client, settings.usage_collection, settings.firestore_timeout_s,
                                     init_error=data_repo._init_error)
    return MemoryDailyCounter()


class BodySizeLimit:
    """요청 본문이 max_bytes 를 넘으면 413. Content-Length 가 없어도 실제 읽은 크기로 막는다."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers", []):
            if k == b"content-length" and v.isdigit() and int(v) > self.max_bytes:
                return await self._reject(send)
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    raise _TooLarge()
            return msg
        try:
            await self.app(scope, limited, send)
        except _TooLarge:
            await self._reject(send)

    async def _reject(self, send):
        body = '{"detail":"요청이 너무 큽니다"}'.encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


def create_app(settings: Settings | None = None, repository: DataRepository | None = None,
               conversations: ConversationRepository | None = None, llm: LLMClient | None = None,
               verifier: TokenVerifier | None = None, daily_counter: DailyCounter | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    docs = settings.docs_enabled
    app = FastAPI(
        title="ADAIR Mission Intelligence API",
        version="0.4.0",
        description="UGV 주행 속도 데이터와 GPT 분석 비서. /api/config 외 모든 /api 는 Firebase 로그인이 필요하다.",
        docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None,
    )
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST", "PUT", "DELETE"],
                           allow_headers=["Content-Type", "Authorization"])
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_request_bytes)

    if repository is None:
        repository, seed = build_repository(settings)
    else:
        seed = None
    limiter = ChatRateLimiter(per_minute=settings.chat_rate_per_minute, per_hour=settings.chat_rate_per_hour,
                              max_concurrent=settings.chat_max_concurrent, daily_limit=settings.chat_daily_limit,
                              daily=daily_counter or build_daily_counter(settings, repository))
    app.state.settings = settings
    app.state.seed_result = seed
    app.state.service = DataService(repository, load_quality(settings.quality_path),
                                    cache_ttl_s=settings.summary_cache_ttl_s)
    app.state.chat = ChatService(app.state.service, conversations or build_conversations(settings, repository),
                                 llm or build_llm(settings), settings, limiter)
    app.state.authenticate = Authenticator(verifier or build_verifier(settings), settings.allowed_emails)
    secrets = (settings.openai_api_key,)

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, e: AuthError):
        headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
        return JSONResponse(status_code=e.status, content={"detail": e.detail}, headers=headers)

    @app.exception_handler(RateLimited)
    async def _rate_limited(_: Request, e: RateLimited):
        return JSONResponse(status_code=429, content={"detail": e.detail, "retry_after_s": e.retry_after_s,
                                                      "saved": False},
                            headers={"Retry-After": str(e.retry_after_s)})

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, e: ServiceError):
        if isinstance(e, ChatSaveFailed):
            return JSONResponse(status_code=e.status, content={
                "detail": e.detail, "saved": False, "answer": e.answer, "conversation_id": e.conversation_id})
        return JSONResponse(status_code=e.status, content={"detail": e.detail})

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, e: LLMError):
        return JSONResponse(status_code=e.status, content={"detail": redact(e, secrets), "saved": False})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        # 기본 응답은 입력값을 되돌려 주는데, NaN 같은 값은 JSON으로 못 써서 500이 된다 → 입력값은 빼고 돌려준다
        detail = [{"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
                  for err in e.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(RepositoryUnavailable)
    async def _repo_unavailable(_: Request, e: RepositoryUnavailable):
        log.error("저장소 사용 불가: %s", redact(e, secrets))
        return JSONResponse(status_code=503, content={"detail": "데이터 저장소에 연결할 수 없습니다",
                                                      "reason": redact(e, secrets)})

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    @app.get("/api/config", tags=["meta"], summary="웹 화면 설정 (공개 식별자만, 비밀값 없음)")
    def web_config():
        fb = {"apiKey": settings.firebase_web_api_key, "authDomain": settings.firebase_auth_domain,
              "projectId": settings.firebase_project_id, "appId": settings.firebase_web_app_id}
        return {
            "firebase": fb if all(fb.values()) else None,
            "metrics": {k: {"label": v, "unit": "m/s"} for k, v in SUPPORTED_METRICS.items()},
            "default_metric": DEFAULT_METRIC,
            "max_message_chars": MAX_MESSAGE_CHARS,
        }

    auth = [Depends(chat_api.current_user)]
    app.include_router(data_api.router, prefix="/api", dependencies=auth)
    app.include_router(chat_api.router, prefix="/api", dependencies=auth)
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
