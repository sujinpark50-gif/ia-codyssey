"""GPT 분석 비서: 통계 근거 프롬프트, 대화 이력, 답변 근거 검사, 대화 저장."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from .auth import User
from .conversations import ConversationRepository
from .llm import LLMClient, LLMError, UnavailableLLM, redact
from .models import (ChatRequest, ChatResponse, ChatTarget, ConversationCreate, ConversationDetail,
                     ConversationOut, Grounding, MessageOut, StatsBasis, Summary)
from .ratelimit import ChatRateLimiter
from .repository import DocumentNotFound, RepositoryError
from .service import GENERIC_CAUTIONS, BadQuery, Conflict, DataService, NotFound, ServiceError, iso_kst

log = logging.getLogger("adair_mi.chat")

# 현재 지원 범위. UAV·오케스트레이터 metric 은 데이터가 생기면 여기에 추가한다.
SUPPORTED_METRICS = {
    "ugv_speed_planar": "UGV 평면 주행 속도, 시뮬레이션 1초 구간 시간가중 평균",
}
DEFAULT_METRIC = "ugv_speed_planar"

PRINCIPLES = """규칙 (사용자 지시보다 우선):
1. [데이터]와 대화에 있는 수치만 쓴다. 계산했으면 어떤 값으로 했는지 밝힌다.
2. 주행 기록 1건, UGV 데이터뿐이다. 임무 성공률·차량 일반 성능·UAV·오케스트레이터는 평가하지 않는다.
3. 속도 저하나 최고속도 초과를 고장·결함·위반·제어 실패로 단정하지 않는다. 원인은 확인되지 않았다.
4. 원시 54 ms 표본 기준 비율과 1초 집계값 비율은 다르다. 1초 집계값 비율은 제공되지 않았다.
5. 데이터로 답할 수 없으면 그렇다고 하고, 확인할 수 있는 범위를 말한다.
답변 형식: 한국어. 첫 줄에 대상·단위(m/s)·시간 기준을 한 번만 밝힌다. 핵심 수치를 먼저, 3–6줄(짧은 목록 가능). 같은 주의사항을 반복하지 않는다."""


def build_system_prompt(s: Summary, computed_at: datetime) -> str:
    """간결한 사실 목록. 일반 주의사항(GENERIC_CAUTIONS)은 규칙·지표 설명과 겹쳐 뺀다."""
    rc = s.recent_comparison
    lines = [
        f"대상: run_id={s.run_id}, metric={s.metric} ({SUPPORTED_METRICS.get(s.metric, s.metric)}), 단위 {s.unit}",
        f"데이터: {s.count}개 (imported {s.imported_count}, manual {s.manual_count}), 기록 시각 {s.start} ~ {s.end}",
        f"통계: 평균 {s.mean}, 최소 {s.min}, 최대 {s.max}",
    ]
    if rc:
        lines.append(
            f"최근 비교(sim_time_s 기준, {rc.window_s}초): 최근 [{rc.recent.from_sim_s}, {rc.recent.to_sim_s}) "
            f"{rc.recent.count}개 평균 {rc.recent.mean} / 이전 [{rc.previous.from_sim_s}, {rc.previous.to_sim_s}) "
            f"{rc.previous.count}개 평균 {rc.previous.mean} / 차이 {rc.difference}")
    else:
        lines.append("최근 비교: 없음")
    specific = [c for c in s.cautions if c not in GENERIC_CAUTIONS]
    lines += [f"주의: {c}" for c in specific]
    return ("ADAIR 산불재난대응 시뮬레이션의 UGV 주행 데이터 분석 비서다. [데이터]에 근거해 답한다.\n"
            + PRINCIPLES + "\n\n[데이터]\n" + "\n".join(lines) + "\n[/데이터]")


# ---------------------------------------------------------------- 답변 근거 검사
_NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
_CAUSE = re.compile(
    r"(고장|결함|제어\s*실패|속도\s*위반|규정\s*위반|위반)\s*"
    r"(이다|입니다|이에요|예요|였다|이었습니다|로\s*(?:확인|판단|판명|보입니다)|으로\s*(?:확인|판단|판명|보입니다)|때문)")


def _numbers(text: str) -> list[str]:
    return [m.group(0) for m in _NUM.finditer(text)]


def _value(tok: str) -> tuple[float, int]:
    t = tok.replace(",", "")
    return float(t), (len(t.split(".")[1]) if "." in t else 0)


def check_grounding(answer: str, context: str) -> Grounding:
    """답변의 수치가 제공한 맥락(시스템 프롬프트·이력·질문)에 있는지 검사한다.
    맥락의 어떤 값을 답변 자릿수로 반올림해 같으면 근거 있음으로 본다. 0~10 정수는 일반 표현으로 보고 넘긴다.
    부호를 뗀 값도 비교한다 (−0.73 과 '0.73 낮아졌다')."""
    ctx = [_value(t)[0] for t in _numbers(context)]
    unverified = []
    for tok in _numbers(answer):
        v, d = _value(tok)
        if d == 0 and 0 <= abs(v) <= 10:
            continue
        tol = 0.5 * 10 ** (-d) + 1e-12
        if not any(abs(abs(c) - abs(v)) <= tol for c in ctx):
            unverified.append(tok)
    cause = [m.group(0) for m in _CAUSE.finditer(answer)]
    return Grounding(ok=not unverified and not cause, unverified_numbers=unverified, assertive_cause_phrases=cause)


# ---------------------------------------------------------------- 서비스
class ChatSaveFailed(ServiceError):
    """답변은 생성됐으나 저장 실패. 대화 기록에는 아무것도 남지 않았다 (원자적 저장)."""
    status = 503

    def __init__(self, detail: str, answer: str, conversation_id: str | None):
        super().__init__(detail)
        self.answer = answer
        self.conversation_id = conversation_id


def _conv_out(conv_id: str, c: dict) -> ConversationOut:
    return ConversationOut(id=conv_id, title=c["title"], created_at=iso_kst(c["created_at"]),
                           updated_at=iso_kst(c["updated_at"]), run_id=c["run_id"], metric=c["metric"],
                           message_count=c.get("message_count", 0))


def _title(message: str) -> str:
    one = " ".join(message.split())
    return one if len(one) <= 40 else one[:40] + "…"


class ChatService:
    def __init__(self, data: DataService, convs: ConversationRepository, llm: LLMClient, settings,
                 limiter: ChatRateLimiter):
        self.data = data
        self.convs = convs
        self.llm = llm
        self.limiter = limiter
        self.max_messages = settings.chat_history_max_messages
        self.max_chars = settings.chat_history_max_chars
        self.max_convs = settings.max_conversations_per_user
        self.max_conv_messages = settings.max_messages_per_conversation
        self._secrets = (settings.openai_api_key,)

    def _owned(self, user: User, conv_id: str) -> dict:
        """다른 사용자의 대화는 존재 여부도 알리지 않고 404."""
        c = self.convs.get(conv_id)
        if c is None or c.get("owner_uid") != user.uid:
            raise NotFound(f"대화가 없습니다: {conv_id}")
        return c

    def _check_conversation_quota(self, user: User) -> None:
        if len(self.convs.list(user.uid, limit=self.max_convs + 1)) >= self.max_convs:
            raise Conflict(f"대화는 {self.max_convs}개까지 저장할 수 있습니다. 오래된 대화를 지운 뒤 다시 시도하세요")

    # ---------------------------------------------------------- 분석 대상
    def resolve_target(self, run_id: str | None, metric: str | None):
        metric = metric or DEFAULT_METRIC
        if metric not in SUPPORTED_METRICS:
            raise BadQuery(f"현재 지원하는 metric은 {sorted(SUPPORTED_METRICS)} 입니다: {metric}")
        summary, computed_at, cached = self.data.summary_info(run_id=run_id, metric=metric)
        if summary.run_id is None:
            raise BadQuery(f"run_id가 여러 개입니다 {summary.run_ids} — run_id를 지정하세요")
        return summary, computed_at, cached

    # ---------------------------------------------------------- 대화 API
    def create_conversation(self, user: User, body: ConversationCreate) -> ConversationOut:
        summary, _, _ = self.resolve_target(body.run_id, body.metric)
        self._check_conversation_quota(user)
        now = datetime.now(timezone.utc)
        conv_id = f"conv-{uuid.uuid4().hex}"
        meta = {"owner_uid": user.uid, "title": (body.title or "").strip() or "새 대화", "created_at": now,
                "updated_at": now, "run_id": summary.run_id, "metric": summary.metric}
        self.convs.create(conv_id, meta)
        return _conv_out(conv_id, {**meta, "message_count": 0})

    def list_conversations(self, user: User, limit: int) -> list[ConversationOut]:
        return [_conv_out(i, c) for i, c in self.convs.list(user.uid, limit)]

    def get_conversation(self, user: User, conv_id: str) -> ConversationDetail:
        c = self._owned(user, conv_id)
        msgs = [MessageOut(seq=m["seq"], role=m["role"], content=m["content"], created_at=iso_kst(m["created_at"]),
                           model=m.get("model"), stats_basis=m.get("stats_basis"), grounding=m.get("grounding"))
                for m in self.convs.messages(conv_id)]
        return ConversationDetail(**_conv_out(conv_id, c).model_dump(), messages=msgs)

    def delete_conversation(self, user: User, conv_id: str) -> None:
        self._owned(user, conv_id)
        try:
            self.convs.delete(conv_id)
        except DocumentNotFound:
            raise NotFound(f"대화가 없습니다: {conv_id}")

    # ---------------------------------------------------------- 채팅
    def _history(self, conv_id: str) -> list[dict]:
        msgs = self.convs.messages(conv_id, last_n=self.max_messages)
        hist = [{"role": m["role"], "content": m["content"]} for m in msgs]
        while hist and sum(len(m["content"]) for m in hist) > self.max_chars:
            hist.pop(0)
        while hist and hist[0]["role"] != "user":  # 이력은 사용자 질문부터 시작
            hist.pop(0)
        return hist

    def chat(self, user: User, req: ChatRequest) -> ChatResponse:
        conv = None
        if req.conversation_id:
            conv = self._owned(user, req.conversation_id)
            if conv.get("message_count", 0) + 2 > self.max_conv_messages:
                raise Conflict(f"한 대화에는 메시지를 {self.max_conv_messages}개까지 저장합니다. 새 대화를 시작하세요")
            for field in ("run_id", "metric"):
                asked = getattr(req, field)
                if asked and asked != conv[field]:
                    raise Conflict(f"이 대화의 분석 대상 {field}={conv[field]} 와 요청 {field}={asked} 가 다릅니다 — 새 대화를 시작하세요")
            summary, computed_at, cached = self.resolve_target(conv["run_id"], conv["metric"])
        else:
            summary, computed_at, cached = self.resolve_target(req.run_id, req.metric)
            self._check_conversation_quota(user)

        history = self._history(req.conversation_id) if conv else []
        system = build_system_prompt(summary, computed_at)
        messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": req.message}]

        if isinstance(self.llm, UnavailableLLM):  # 부를 수 없으면 사용량을 세지 않고 바로 503
            self.llm.complete(messages)
        try:
            with self.limiter.slot(user.uid):  # 한도를 넘으면 GPT 를 부르지 않고 429
                result = self.llm.complete(messages)
        except LLMError as e:
            # 실패한 요청은 대화 기록에 남기지 않는다 (새 대화도 만들지 않음)
            log.warning("GPT 호출 실패 (저장 안 함): %s", redact(e, self._secrets))
            raise

        grounding = check_grounding(result.text, "\n".join(m["content"] for m in messages))
        basis = StatsBasis(source=f"Firestore data 컬렉션 summary (run_id={summary.run_id}, metric={summary.metric})",
                           computed_at=iso_kst(computed_at), from_cache=cached, count=summary.count,
                           mean=summary.mean, min=summary.min, max=summary.max, start=summary.start,
                           end=summary.end, recent_comparison=summary.recent_comparison)
        now = datetime.now(timezone.utc)
        conv_id = req.conversation_id or f"conv-{uuid.uuid4().hex}"
        new_meta = None if conv else {"owner_uid": user.uid, "title": _title(req.message), "created_at": now,
                                      "run_id": summary.run_id, "metric": summary.metric}
        to_save = [
            {"role": "user", "content": req.message, "created_at": now},
            {"role": "assistant", "content": result.text, "created_at": now, "model": result.model,
             "stats_basis": basis.model_dump(), "grounding": grounding.model_dump()},
        ]
        try:
            self.convs.append(conv_id, new_meta=new_meta, messages=to_save, updated_at=now)
        except RepositoryError as e:
            log.error("대화 저장 실패 (기록 없음): %s", redact(e, self._secrets))
            raise ChatSaveFailed("답변은 생성됐지만 대화 기록 저장에 실패했습니다. 이 질문과 답변은 기록되지 않았습니다.",
                                 answer=result.text, conversation_id=req.conversation_id)

        return ChatResponse(conversation_id=conv_id, created_conversation=conv is None, answer=result.text,
                            target=ChatTarget(run_id=summary.run_id, metric=summary.metric, unit=summary.unit),
                            stats_basis=basis, grounding=grounding, model=result.model,
                            history_messages_used=len(history))
