"""환경변수 설정. `.env`(커밋 금지)가 있으면 읽고, 이미 설정된 환경변수가 우선한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKENDS = ("memory", "firestore")


def _bool(text: str | None, default: bool) -> bool:
    if text is None or text.strip() == "":
        return default
    return text.strip().lower() in ("1", "true", "yes", "on")


def credentials_path(firebase: str | None, google: str | None) -> str | None:
    """서비스 계정 키 경로. GOOGLE_APPLICATION_CREDENTIALS(표준) 또는 FIREBASE_CREDENTIALS(기존) 중 하나.
    둘 다 있고 서로 다른 파일을 가리키면 어느 키를 쓸지 모호하므로 거부한다."""
    firebase, google = (firebase or "").strip() or None, (google or "").strip() or None
    if firebase and google and Path(firebase).resolve() != Path(google).resolve():
        raise ValueError("FIREBASE_CREDENTIALS 와 GOOGLE_APPLICATION_CREDENTIALS 가 서로 다른 파일을 가리킵니다 — 하나만 설정하세요")
    return firebase or google


def cors_origins(allowed: str | None, legacy: str | None) -> tuple[str, ...]:
    """CORS 허용 Origin. ALLOWED_ORIGINS(과제 원문 이름) 또는 CORS_ORIGINS(기존 이름), 쉼표 구분.
    둘 다 있고 내용이 다르면 어느 쪽인지 모호하므로 거부한다. '*' 는 받지 않는다."""
    def parse(v):
        return tuple(o.strip().rstrip("/") for o in (v or "").split(",") if o.strip())
    a, b = parse(allowed), parse(legacy)
    if a and b and set(a) != set(b):
        raise ValueError("ALLOWED_ORIGINS 와 CORS_ORIGINS 가 서로 다릅니다 — 하나만 설정하세요")
    out = a or b
    if "*" in out:
        raise ValueError("CORS 허용 Origin 에 '*' 는 쓸 수 없습니다 — 운영 도메인을 명시하세요")
    return out


@dataclass(frozen=True)
class Settings:
    data_backend: str = "memory"
    seed_path: Path = ROOT / "data/ugv_speed_1s.json"
    seed_on_startup: bool = True  # memory 백엔드에서만 사용
    quality_path: Path = ROOT / "data/validation_summary.json"
    firebase_credentials: str | None = None  # 서비스 계정 키 파일 경로 (저장소 밖에 둔다)
    firebase_project_id: str | None = None
    firestore_database_id: str = "(default)"
    firestore_timeout_s: float = 10.0
    data_collection: str = "data"
    conversations_collection: str = "conversations"  # M3에서 사용 예정
    cors_origins: tuple[str, ...] = ()
    # M3 GPT 채팅 — 키는 repr·로그에 나오지 않게 repr=False
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str | None = None  # 기본값 없음: 미설정이면 /api/chat 이 503
    openai_base_url: str | None = None  # OpenAI 호환 API (예: Codyssey). 없으면 OpenAI 기본 주소
    openai_timeout_s: float = 30.0
    openai_max_retries: int = 1  # SDK 자동 재시도 횟수 (0 이면 요청 1회 = HTTP 1회)
    openai_max_output_tokens: int = 800
    chat_history_max_messages: int = 10
    chat_history_max_chars: int = 8000
    summary_cache_ttl_s: float = 60.0
    # M4 인증·제한
    allowed_emails: tuple[str, ...] = ()  # 비어 있으면 아무도 통과하지 못한다
    firebase_web_api_key: str | None = None  # Firebase 웹 설정값 (공개 식별자, 비밀 아님)
    firebase_auth_domain: str | None = None
    firebase_web_app_id: str | None = None
    chat_rate_per_minute: int = 5
    chat_rate_per_hour: int = 30
    chat_max_concurrent: int = 1
    chat_daily_limit: int = 200
    usage_collection: str = "usage"
    max_request_bytes: int = 16_384
    max_conversations_per_user: int = 100
    max_messages_per_conversation: int = 200
    docs_enabled: bool = True  # Swagger /docs 공개 (운영도 true, 보호 API 는 토큰 검증 유지)

    def __post_init__(self):
        if self.data_backend not in BACKENDS:
            raise ValueError(f"DATA_BACKEND는 {BACKENDS} 중 하나여야 합니다: {self.data_backend!r}")

    @classmethod
    def from_env(cls, env_file: Path | None = ROOT / ".env") -> "Settings":
        if env_file is not None and env_file.is_file():
            load_dotenv(env_file, override=False)
        get = os.getenv
        origins = cors_origins(get("ALLOWED_ORIGINS"), get("CORS_ORIGINS"))
        return cls(
            data_backend=(get("DATA_BACKEND") or "memory").strip().lower(),
            seed_path=Path(get("SEED_DATA_PATH") or cls.seed_path),
            seed_on_startup=_bool(get("SEED_ON_STARTUP"), True),
            quality_path=Path(get("QUALITY_SUMMARY_PATH") or cls.quality_path),
            firebase_credentials=credentials_path(get("FIREBASE_CREDENTIALS"), get("GOOGLE_APPLICATION_CREDENTIALS")),
            firebase_project_id=get("FIREBASE_PROJECT_ID") or None,
            firestore_database_id=get("FIRESTORE_DATABASE_ID") or "(default)",
            firestore_timeout_s=float(get("FIRESTORE_TIMEOUT_S") or 10.0),
            data_collection=get("FIRESTORE_DATA_COLLECTION") or "data",
            conversations_collection=get("FIRESTORE_CONVERSATIONS_COLLECTION") or "conversations",
            cors_origins=origins,
            openai_api_key=(get("OPENAI_API_KEY") or "").strip() or None,
            openai_model=(get("OPENAI_MODEL") or "").strip() or None,
            openai_base_url=(get("OPENAI_BASE_URL") or "").strip() or None,
            openai_timeout_s=float(get("OPENAI_TIMEOUT_S") or 30.0),
            openai_max_retries=int(get("OPENAI_MAX_RETRIES") or 1),
            openai_max_output_tokens=int(get("OPENAI_MAX_OUTPUT_TOKENS") or 800),
            chat_history_max_messages=int(get("CHAT_HISTORY_MAX_MESSAGES") or 10),
            chat_history_max_chars=int(get("CHAT_HISTORY_MAX_CHARS") or 8000),
            summary_cache_ttl_s=float(get("SUMMARY_CACHE_TTL_S") or 60.0),
            allowed_emails=tuple(e.strip().lower() for e in (get("ALLOWED_EMAILS") or "").split(",") if e.strip()),
            firebase_web_api_key=(get("FIREBASE_WEB_API_KEY") or "").strip() or None,
            firebase_auth_domain=(get("FIREBASE_AUTH_DOMAIN") or "").strip() or None,
            firebase_web_app_id=(get("FIREBASE_WEB_APP_ID") or "").strip() or None,
            chat_rate_per_minute=int(get("CHAT_RATE_PER_MINUTE") or 5),
            chat_rate_per_hour=int(get("CHAT_RATE_PER_HOUR") or 30),
            chat_max_concurrent=int(get("CHAT_MAX_CONCURRENT") or 1),
            chat_daily_limit=int(get("CHAT_DAILY_LIMIT") or 200),
            usage_collection=get("FIRESTORE_USAGE_COLLECTION") or "usage",
            max_request_bytes=int(get("MAX_REQUEST_BYTES") or 16_384),
            max_conversations_per_user=int(get("MAX_CONVERSATIONS_PER_USER") or 100),
            max_messages_per_conversation=int(get("MAX_MESSAGES_PER_CONVERSATION") or 200),
            docs_enabled=_bool(get("DOCS_ENABLED"), True),
        )
