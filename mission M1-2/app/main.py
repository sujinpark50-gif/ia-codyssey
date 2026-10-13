"""ADAIR Mission Intelligence API.

실행: uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import data as data_api
from .config import Settings
from .importer import import_documents, load_documents
from .repository import DataRepository, FirestoreRepository, InMemoryRepository, RepositoryUnavailable
from .service import DataService, ServiceError, load_quality

log = logging.getLogger("adair_mi")


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


def create_app(settings: Settings | None = None, repository: DataRepository | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="ADAIR Mission Intelligence API",
        version="0.2.0",
        description="ADAIR UGV 주행 속도 1초 시계열 데이터 API (M2). "
                    "imported 데이터는 읽기 전용, manual 데이터만 추가·수정·삭제할 수 있다.",
    )
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])

    if repository is None:
        repository, seed = build_repository(settings)
    else:
        seed = None
    app.state.settings = settings
    app.state.seed_result = seed
    app.state.service = DataService(repository, load_quality(settings.quality_path))

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, e: ServiceError):
        return JSONResponse(status_code=e.status, content={"detail": e.detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        # 기본 응답은 입력값을 되돌려 주는데, NaN 같은 값은 JSON으로 못 써서 500이 된다 → 입력값은 빼고 돌려준다
        detail = [{"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
                  for err in e.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(RepositoryUnavailable)
    async def _repo_unavailable(_: Request, e: RepositoryUnavailable):
        log.error("저장소 사용 불가: %s", e)
        return JSONResponse(status_code=503, content={"detail": "데이터 저장소에 연결할 수 없습니다", "reason": str(e)})

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "backend": settings.data_backend}

    # M3: conversations 라우터를 같은 /api 아래에 추가할 예정
    app.include_router(data_api.router, prefix="/api")
    return app
