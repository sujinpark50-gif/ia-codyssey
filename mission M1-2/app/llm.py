"""GPT 호출 계층. 서비스는 LLMClient만 알고, 테스트는 가짜 클라이언트를 끼운다.

실제 OpenAI 호출은 OpenAIChatClient 하나뿐이며 POST /api/chat 요청이 있을 때만 일어난다
(백그라운드 호출·재시도 루프 없음, SDK 재시도는 1회로 제한).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .repository import safe_error

_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def redact(text: object, secrets: tuple[str | None, ...] = (), limit: int | None = 500) -> str:
    """오류 메시지에서 키(PEM·private_key·sk-…·주어진 비밀 문자열)를 지운다. limit=None 이면 자르지 않는다."""
    out = safe_error(text if isinstance(text, (str, BaseException)) else str(text), limit=limit or 10**9)
    for s in secrets:
        if s:
            out = out.replace(s, "[REDACTED]")
    return _OPENAI_KEY.sub("sk-[REDACTED]", out)


class LLMError(Exception):
    """GPT 호출 실패 (상위 API 오류)."""
    status = 502


class LLMTimeout(LLMError):
    status = 504


class LLMNotConfigured(LLMError):
    status = 503


@dataclass
class LLMResult:
    text: str
    model: str
    usage: dict | None = None


class LLMClient(ABC):
    model: str | None = None

    @abstractmethod
    def complete(self, messages: list[dict]) -> LLMResult:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]"""


class UnavailableLLM(LLMClient):
    """키·모델 미설정 등으로 쓸 수 없는 상태. 호출하면 503."""

    def __init__(self, reason: str):
        self.reason = reason

    def complete(self, messages):
        raise LLMNotConfigured(self.reason)


class OpenAIChatClient(LLMClient):
    def __init__(self, *, api_key: str | None, model: str | None, timeout_s: float,
                 max_output_tokens: int, base_url: str | None = None, max_retries: int = 1, client=None):
        if not api_key:
            raise LLMNotConfigured("OPENAI_API_KEY 가 설정되지 않았습니다")
        if not model:
            raise LLMNotConfigured("OPENAI_MODEL 이 설정되지 않았습니다")
        self.model = model
        self._timeout = timeout_s
        self._max_tokens = max_output_tokens
        self._secret = api_key
        if client is None:
            from openai import OpenAI
            # base_url 이 있으면 OpenAI 호환 API ({base_url}/chat/completions, Bearer 인증)
            client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout_s, max_retries=max_retries)
        self._client = client

    def complete(self, messages):
        import openai
        try:
            r = self._client.chat.completions.create(
                model=self.model, messages=messages, max_completion_tokens=self._max_tokens,
                timeout=self._timeout)
        except openai.APITimeoutError:  # APIConnectionError 의 하위 클래스라 먼저 잡는다
            raise LLMTimeout(f"OpenAI 응답 시간 초과 ({self._timeout:g}s)")
        except openai.APIStatusError as e:
            raise LLMError(f"OpenAI 오류 HTTP {e.status_code}: {redact(e, (self._secret,))}")
        except openai.APIConnectionError as e:
            raise LLMError(f"OpenAI 연결 실패: {redact(e, (self._secret,))}")
        except openai.OpenAIError as e:
            raise LLMError(f"OpenAI 오류: {redact(e, (self._secret,))}")
        choice = r.choices[0] if getattr(r, "choices", None) else None
        text = ((choice.message.content if choice and choice.message else None) or "").strip()
        if not text:
            reason = getattr(choice, "finish_reason", None) if choice else None
            raise LLMError(f"OpenAI 가 빈 답변을 돌려줬습니다 (finish_reason={reason})")
        usage = getattr(r, "usage", None)
        usage_d = None
        if usage is not None:
            usage_d = {k: getattr(usage, k, None) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        return LLMResult(text=text, model=getattr(r, "model", None) or self.model, usage=usage_d)


def build_llm(settings) -> LLMClient:
    try:
        return OpenAIChatClient(api_key=settings.openai_api_key, model=settings.openai_model,
                                timeout_s=settings.openai_timeout_s,
                                max_output_tokens=settings.openai_max_output_tokens,
                                base_url=settings.openai_base_url)
    except LLMNotConfigured as e:
        return UnavailableLLM(str(e))
