"""환경변수 설정. `.env`(커밋 금지)가 있으면 읽고, 이미 설정된 환경변수가 우선한다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKENDS = ("memory", "firestore")


def _bool(text: str | None, default: bool) -> bool:
    if text is None or text.strip() == "":
        return default
    return text.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    data_backend: str = "memory"
    seed_path: Path = ROOT / "data/ugv_speed_1s.json"
    seed_on_startup: bool = True  # memory 백엔드에서만 사용
    quality_path: Path = ROOT / "data/validation_summary.json"
    firebase_credentials: str | None = None  # 서비스 계정 키 파일 경로 (저장소 밖에 둔다)
    firebase_project_id: str | None = None
    data_collection: str = "data"
    conversations_collection: str = "conversations"  # M3에서 사용 예정
    cors_origins: tuple[str, ...] = ()

    def __post_init__(self):
        if self.data_backend not in BACKENDS:
            raise ValueError(f"DATA_BACKEND는 {BACKENDS} 중 하나여야 합니다: {self.data_backend!r}")

    @classmethod
    def from_env(cls, env_file: Path | None = ROOT / ".env") -> "Settings":
        if env_file is not None and env_file.is_file():
            load_dotenv(env_file, override=False)
        get = os.getenv
        origins = tuple(o.strip() for o in (get("CORS_ORIGINS") or "").split(",") if o.strip())
        return cls(
            data_backend=(get("DATA_BACKEND") or "memory").strip().lower(),
            seed_path=Path(get("SEED_DATA_PATH") or cls.seed_path),
            seed_on_startup=_bool(get("SEED_ON_STARTUP"), True),
            quality_path=Path(get("QUALITY_SUMMARY_PATH") or cls.quality_path),
            firebase_credentials=get("FIREBASE_CREDENTIALS") or None,
            firebase_project_id=get("FIREBASE_PROJECT_ID") or None,
            data_collection=get("FIRESTORE_DATA_COLLECTION") or "data",
            conversations_collection=get("FIRESTORE_CONVERSATIONS_COLLECTION") or "conversations",
            cors_origins=origins,
        )
