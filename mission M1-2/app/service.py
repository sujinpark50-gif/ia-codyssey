"""수정 정책과 통계 요약. 정책은 여기서 강제한다 (화면 제한에 의존하지 않는다)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import DataCreate, DataOut, DataUpdate, RecentComparison, Summary, WindowStat
from .repository import DataRepository, DocumentNotFound

KST = timezone(timedelta(hours=9))
WINDOW_S = 60


class ServiceError(Exception):
    status = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(ServiceError):
    status = 404


class Forbidden(ServiceError):
    status = 403


class Conflict(ServiceError):
    status = 409


class BadQuery(ServiceError):
    status = 400


def iso_kst(d: datetime) -> str:
    k = d.astimezone(KST)
    return k.isoformat(timespec="milliseconds" if k.microsecond % 1000 == 0 else "microseconds")


def to_out(doc_id: str, doc: dict) -> DataOut:
    return DataOut(id=doc_id, date=iso_kst(doc["date"]), value=doc["value"], memo=doc.get("memo", ""),
                   run_id=doc.get("run_id"), metric=doc["metric"], unit=doc["unit"],
                   sim_time_s=doc.get("sim_time_s"), source_ref=doc.get("source_ref"),
                   transform_version=doc.get("transform_version"), record_type=doc["record_type"])


def load_quality(path: Path) -> dict[str, dict]:
    """M1-2 validation_summary.json → run_id별 원시 데이터 품질 정보. 없으면 빈 dict."""
    try:
        s = json.loads(Path(path).read_text(encoding="utf-8"))
        rs = s["raw_speed"]
        return {s["run_id"]: {"raw_count": rs["count"], "over_count": rs["over_rules_max_count"],
                              "rules_max": rs["rules_max_speed_mps"]}}
    except (OSError, KeyError, ValueError, TypeError):
        return {}


class DataService:
    def __init__(self, repo: DataRepository, quality: dict[str, dict] | None = None):
        self.repo = repo
        self.quality = quality or {}

    # ---------------------------------------------------------- 조회
    def list(self, *, run_id=None, metric=None, record_type=None, limit=100, offset=0):
        docs = self.repo.list(run_id=run_id, metric=metric, record_type=record_type)
        page = docs[offset:offset + limit]
        return [to_out(i, d) for i, d in page], len(docs)

    def get(self, doc_id: str) -> DataOut:
        return to_out(doc_id, self._existing(doc_id))

    def _existing(self, doc_id: str) -> dict:
        doc = self.repo.get(doc_id)
        if doc is None:
            raise NotFound(f"문서가 없습니다: {doc_id}")
        return doc

    # ---------------------------------------------------------- 변경 (manual만)
    def create(self, body: DataCreate) -> DataOut:
        doc = {**body.model_dump(), "source_ref": None, "transform_version": None, "record_type": "manual"}
        self._check_sim_slot(doc, exclude_id=None)
        doc_id = f"manual-{uuid.uuid4().hex}"
        self.repo.create(doc_id, doc)
        return to_out(doc_id, doc)

    def update(self, doc_id: str, body: DataUpdate) -> DataOut:
        current = self._existing(doc_id)
        self._require_manual(doc_id, current)
        changes = body.model_dump(include=body.model_fields_set)
        merged_input = {k: current.get(k) for k in DataCreate.model_fields} | changes
        merged = DataCreate.model_validate(merged_input).model_dump()  # 합친 결과로 다시 검증
        self._check_sim_slot({**current, **merged}, exclude_id=doc_id)
        try:
            self.repo.update(doc_id, merged)
        except DocumentNotFound:
            raise NotFound(f"문서가 없습니다: {doc_id}")
        return to_out(doc_id, {**current, **merged})

    def delete(self, doc_id: str) -> None:
        self._require_manual(doc_id, self._existing(doc_id))
        try:
            self.repo.delete(doc_id)
        except DocumentNotFound:
            raise NotFound(f"문서가 없습니다: {doc_id}")

    @staticmethod
    def _require_manual(doc_id: str, doc: dict) -> None:
        if doc.get("record_type") != "manual":
            raise Forbidden(f"imported 데이터는 읽기 전용입니다: {doc_id}")

    def _check_sim_slot(self, doc: dict, exclude_id: str | None) -> None:
        """같은 run_id·metric 안에서 sim_time_s 구간은 하나만 (구간 비교가 겹치지 않도록)."""
        if doc.get("sim_time_s") is None:
            return
        for other_id, other in self.repo.list(metric=doc["metric"]):
            if other_id != exclude_id and other.get("run_id") == doc.get("run_id") \
                    and other.get("sim_time_s") == doc["sim_time_s"]:
                raise Conflict(f"run_id={doc.get('run_id')} sim_time_s={doc['sim_time_s']} 구간이 이미 있습니다: {other_id}")

    # ---------------------------------------------------------- 요약
    def summary(self, *, run_id: str | None = None, metric: str | None = None) -> Summary:
        docs = [d for _, d in self.repo.list(run_id=run_id, metric=metric)]
        if not docs:
            raise NotFound("조건에 맞는 데이터가 없습니다")
        metrics = sorted({d["metric"] for d in docs})
        if len(metrics) > 1:
            raise BadQuery(f"metric이 여러 개입니다 {metrics} — metric을 지정하세요")
        units = sorted({d["unit"] for d in docs})
        if len(units) > 1:
            raise BadQuery(f"단위가 섞여 있습니다 {units} — 요약할 수 없습니다")
        run_ids = sorted({d.get("run_id") for d in docs}, key=lambda r: (r is None, r or ""))
        values = [d["value"] for d in docs]
        dates = [d["date"] for d in docs]
        manual = sum(d["record_type"] == "manual" for d in docs)

        cautions = [
            "구간 비교는 시뮬레이션 시간(sim_time_s) 기준이다. date(기록 벽시계)로 나눈 60초와 다르다.",
            "각 값은 1초 구간의 시간가중 평균 속도이며 원시 54 ms 표본이 아니다.",
        ]
        recent = None
        if len(run_ids) == 1:
            recent = self._recent(docs, cautions)
        else:
            cautions.append(f"run_id가 {len(run_ids)}개 섞여 있어 최근 구간 비교를 하지 않았다. run_id를 지정하세요.")
        if manual:
            cautions.append(f"수동 입력(manual) {manual}건이 포함되어 있다. 원본 로그에서 계산한 값이 아니다.")
        for rid in run_ids:
            q = self.quality.get(rid) if rid else None
            if q and q.get("over_count") is not None:
                cautions.append(
                    f"[{rid}] 원시 표본 {q['raw_count']:,}개 중 {q['over_count']:,}개"
                    f"({q['over_count'] / q['raw_count'] * 100:.1f}%)가 규칙 최고속도 {q['rules_max']} m/s를 넘는다. "
                    "원인은 확인되지 않았으며 속도 위반·차량 결함·제어 실패로 판정하지 않는다. "
                    "이 비율은 원시 표본 기준이며 1초 집계값의 비율이 아니다.")

        return Summary(
            run_id=run_ids[0] if len(run_ids) == 1 else None, run_ids=run_ids,
            metric=metrics[0], unit=units[0], count=len(docs),
            imported_count=len(docs) - manual, manual_count=manual,
            start=iso_kst(min(dates)), end=iso_kst(max(dates)),
            mean=round(sum(values) / len(values), 6), min=round(min(values), 6), max=round(max(values), 6),
            recent_comparison=recent, cautions=cautions,
        )

    @staticmethod
    def _recent(docs: list[dict], cautions: list[str]) -> RecentComparison | None:
        timed = [d for d in docs if d.get("sim_time_s") is not None]
        if len(timed) < len(docs):
            cautions.append(f"sim_time_s가 없는 {len(docs) - len(timed)}건은 최근 구간 비교에서 빠졌다.")
        if not timed:
            return None
        end = max(d["sim_time_s"] for d in timed) + 1

        def window(lo: int, hi: int) -> WindowStat:
            vals = [d["value"] for d in timed if lo <= d["sim_time_s"] < hi]
            return WindowStat(from_sim_s=lo, to_sim_s=hi, count=len(vals),
                              mean=round(sum(vals) / len(vals), 6) if vals else None)

        rec, prev = window(end - WINDOW_S, end), window(end - 2 * WINDOW_S, end - WINDOW_S)
        for name, w in (("최근", rec), ("이전", prev)):
            if w.count < WINDOW_S:
                cautions.append(f"{name} 구간에 1초 값이 {w.count}개뿐이다 (60개 미만).")
        zeros = sum(1 for d in timed if end - WINDOW_S <= d["sim_time_s"] < end and d["value"] == 0)
        if zeros:
            cautions.append(f"최근 구간에 값이 0인(정지) 1초 구간 {zeros}개가 포함되어 있다. "
                            "구간 차이에 주행 단계 변화(감속·정지)가 섞일 수 있다.")
        diff = round(rec.mean - prev.mean, 6) if rec.mean is not None and prev.mean is not None else None
        return RecentComparison(
            basis="sim_time_s",
            definition="마지막 1초 구간의 끝(max sim_time_s + 1)에서 거슬러 [end-60, end) 와 [end-120, end-60) 의 1초 값 평균",
            window_s=WINDOW_S, recent=rec, previous=prev, difference=diff)
