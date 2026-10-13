"""요청·응답 모델과 입력 검증."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SPEED_METRIC = "ugv_speed_planar"
ID_PATTERN = r"^[A-Za-z0-9._\-]+$"


def parse_aware_datetime(v):
    """ISO 8601 문자열만 받는다. 시간대가 없으면 거부 (숫자 epoch·날짜만 있는 값도 거부)."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise ValueError("date에 시간대가 필요합니다")
        return v
    if not isinstance(v, str):
        raise ValueError("date는 ISO 8601 문자열이어야 합니다 (예: 2026-10-10T18:45:32.747+09:00)")
    try:
        d = datetime.fromisoformat(v.strip())
    except ValueError:
        raise ValueError(f"ISO 8601 날짜로 읽을 수 없습니다: {v!r}")
    if d.tzinfo is None:
        raise ValueError("date에 시간대(+09:00 또는 Z)가 필요합니다")
    return d


def parse_finite_number(v):
    """JSON 숫자만 받는다. 문자열·불리언·NaN·Infinity 거부."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("value는 숫자여야 합니다")
    try:
        f = float(v)
    except OverflowError:
        raise ValueError("value가 너무 큽니다")
    if not math.isfinite(f):
        raise ValueError("value는 유한한 숫자여야 합니다 (NaN·Infinity 불가)")
    return f


class DataCreate(BaseModel):
    """수동(manual) 데이터 생성. imported 데이터는 적재기로만 들어온다."""

    model_config = ConfigDict(extra="forbid")

    date: datetime = Field(description="시간대가 포함된 ISO 8601 (예: 2026-10-10T18:45:32.747+09:00)")
    value: float = Field(description="측정값. ugv_speed_planar는 m/s, 0 이상")
    memo: str = Field("", max_length=1000)
    run_id: str | None = Field(None, max_length=200, pattern=ID_PATTERN)
    metric: str = Field(SPEED_METRIC, max_length=100, pattern=ID_PATTERN)
    unit: str = Field("m/s", min_length=1, max_length=20)
    sim_time_s: int | None = Field(None, ge=0, description="시뮬레이션 1초 구간 시작 (구간 비교 기준)")

    _date = field_validator("date", mode="before")(parse_aware_datetime)
    _value = field_validator("value", mode="before")(parse_finite_number)

    @field_validator("sim_time_s", mode="before")
    @classmethod
    def _sim_int(cls, v):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise ValueError("sim_time_s는 정수여야 합니다")
        return v

    @model_validator(mode="after")
    def _speed_nonnegative(self):
        if self.metric == SPEED_METRIC and self.value < 0:
            raise ValueError("속도(ugv_speed_planar)는 음수일 수 없습니다")
        return self


class DataUpdate(BaseModel):
    """manual 데이터 수정. 보낸 필드만 바꾼다. run_id·sim_time_s는 null로 비울 수 있다."""

    model_config = ConfigDict(extra="forbid")

    date: datetime | None = None
    value: float | None = None
    memo: str | None = Field(None, max_length=1000)
    run_id: str | None = Field(None, max_length=200, pattern=ID_PATTERN)
    metric: str | None = Field(None, max_length=100, pattern=ID_PATTERN)
    unit: str | None = Field(None, min_length=1, max_length=20)
    sim_time_s: int | None = Field(None, ge=0)

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, v):
        return None if v is None else parse_aware_datetime(v)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, v):
        return None if v is None else parse_finite_number(v)

    @field_validator("sim_time_s", mode="before")
    @classmethod
    def _sim_int(cls, v):
        return DataCreate._sim_int(v)

    @model_validator(mode="after")
    def _non_empty(self):
        if not self.model_fields_set:
            raise ValueError("바꿀 필드가 하나 이상 필요합니다")
        for name in ("date", "value", "memo", "metric", "unit"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name}은(는) null로 바꿀 수 없습니다")
        return self


RecordType = Literal["imported", "manual"]


class DataOut(BaseModel):
    id: str
    date: str = Field(description="KST(+09:00) ISO 8601")
    value: float
    memo: str
    run_id: str | None
    metric: str
    unit: str
    sim_time_s: int | None
    source_ref: str | None
    transform_version: str | None
    record_type: RecordType


class DataList(BaseModel):
    items: list[DataOut]
    total: int
    limit: int
    offset: int


class WindowStat(BaseModel):
    from_sim_s: int = Field(description="포함")
    to_sim_s: int = Field(description="제외")
    count: int
    mean: float | None


class RecentComparison(BaseModel):
    basis: str
    definition: str
    window_s: int
    recent: WindowStat
    previous: WindowStat
    difference: float | None = Field(description="recent.mean - previous.mean")


class Summary(BaseModel):
    run_id: str | None
    run_ids: list[str | None]
    metric: str
    unit: str
    count: int
    imported_count: int
    manual_count: int
    start: str
    end: str
    mean: float
    min: float
    max: float
    recent_comparison: RecentComparison | None
    cautions: list[str]
