"""ADAIR UGV 주행 로그 → 1초 속도 시계열 검증·변환 (표준 라이브러리만 사용).

입력: Gazebo 위치 로그 CSV(sim_t, x, y, ..., wall_t)와 같은 시험의 요약 JSON.
출력(--out 디렉터리):
  ugv_speed_1s.csv          검증된 1초 속도 시계열 (대표값 + 비교용 단순평균)
  ugv_speed_1s.json         Firestore 적재 준비 문서 목록 (업로드하지 않음)
  source_manifest.json      원본 경로·해시·출처
  validation_summary.json   원본 검증·재계산·보고값 대조 결과

재현: python scripts/prepare_data.py
같은 원본·같은 TRANSFORM_VERSION이면 출력 바이트가 동일하다 (생성 시각 등 가변값을 쓰지 않는다).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

TRANSFORM_VERSION = "1.0.0"
METRIC = "ugv_speed_planar"
UNIT = "m/s"
BIN_MS = 1000
KST = timezone(timedelta(hours=9), "KST")
REQUIRED = ("wall_t", "sim_t", "x", "y")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data/raw/B1x_slope_low_20261010_184532.csv"
DEFAULT_JSON = ROOT / "data/raw/B1x_slope_low_20261010_184532.json"
# 원본이 있던 ADAIR 저장소 기준 경로 (출처 기록용, 읽지 않아도 동작한다)
ORIGIN_REL = "ADAIR/logs/ugv"

# 기존 조사 보고서(Codex 조사) 기준값 — 맞추지 않고 대조만 한다
REPORTED = {
    "position_observations": 8079,
    "derived_speeds": 8078,
    "sim_duration_s": 436.212,
    "speed_mean_mps": 4.8826,
    "speed_min_mps": 0.0,
    "speed_max_mps": 5.8649,
    "nonempty_1s_bins": 437,
    "complete_1s_bins": 435,
}


class DataError(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_ms(text: str, field: str, row: int) -> int:
    """'14.69' → 14690. 소수 3자리를 넘거나 숫자가 아니면 오류."""
    try:
        d = Decimal(text) * 1000
    except InvalidOperation:
        raise DataError(f"row {row}: {field}={text!r} 숫자 아님")
    if not d.is_finite() or d != d.to_integral_value():
        raise DataError(f"row {row}: {field}={text!r} 유한한 ms 정수로 표현 불가")
    return int(d)


# ---------------------------------------------------------------- 원본 검증
def load_rows(csv_path: Path) -> tuple[list[str], list[dict]]:
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        raw = list(reader)
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise DataError(f"필수 컬럼 없음: {missing}")
    return header, raw


def column_profile(header: list[str], raw: list[dict]) -> dict:
    prof = {}
    for c in header:
        empty = unparsable = nonfinite = 0
        nonfinite_rows = []
        for i, r in enumerate(raw, start=1):
            v = (r.get(c) or "").strip()
            if v == "":
                empty += 1
                continue
            try:
                fv = float(v)
            except ValueError:
                unparsable += 1
                continue
            if not math.isfinite(fv):
                nonfinite += 1
                nonfinite_rows.append(i)
        prof[c] = {
            "type": "float" if unparsable == 0 else "mixed",
            "empty": empty,
            "unparsable": unparsable,
            "nonfinite": nonfinite,
            "nonfinite_rows": nonfinite_rows[:20],
        }
    return prof


def parse_required(raw: list[dict]) -> list[dict]:
    """필수 필드만 파싱. 비유한·결측 행은 valid=False로 표시하고 이유를 남긴다."""
    rows = []
    for i, r in enumerate(raw, start=1):  # i = 데이터 행 번호 (CSV 줄 번호 = i + 1)
        reason = None
        try:
            sim_ms = to_ms(r["sim_t"].strip(), "sim_t", i)
            wall_ms = to_ms(r["wall_t"].strip(), "wall_t", i)
            x = float(r["x"])
            y = float(r["y"])
            if not (math.isfinite(x) and math.isfinite(y)):
                reason = "x/y 비유한"
        except (DataError, ValueError, AttributeError) as e:
            reason = str(e)
            sim_ms = wall_ms = None
            x = y = float("nan")
        rows.append({"row": i, "sim_ms": sim_ms, "wall_ms": wall_ms, "x": x, "y": y,
                     "valid": reason is None, "reason": reason})
    return rows


def time_checks(rows: list[dict]) -> dict:
    v = [r for r in rows if r["valid"]]
    sims = [r["sim_ms"] for r in v]
    walls = [r["wall_ms"] for r in v]
    dsim = [b - a for a, b in zip(sims, sims[1:])]
    dwall = [b - a for a, b in zip(walls, walls[1:])]
    hist: dict[int, int] = {}
    for d in dsim:
        hist[d] = hist.get(d, 0) + 1
    return {
        "sim_duplicates": len(sims) - len(set(sims)),
        "sim_reversals": sum(d < 0 for d in dsim),
        "sim_zero_steps": sum(d == 0 for d in dsim),
        "sim_step_ms_histogram": {str(k): hist[k] for k in sorted(hist)},
        "wall_duplicates": len(walls) - len(set(walls)),
        "wall_reversals": sum(d < 0 for d in dwall),
        "wall_step_ms_min": min(dwall),
        "wall_step_ms_max": max(dwall),
        "wall_steps_over_200ms": sum(d > 200 for d in dwall),
        "sim_first_s": sims[0] / 1000,
        "sim_last_s": sims[-1] / 1000,
        "sim_duration_s": (sims[-1] - sims[0]) / 1000,
        "wall_duration_s": (walls[-1] - walls[0]) / 1000,
        "rtf_sim_over_wall": round((sims[-1] - sims[0]) / (walls[-1] - walls[0]), 6),
    }


# ---------------------------------------------------------------- 속도
def speed_intervals(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """연속한 두 관측 (i, i+1) → 평면 속도 1개. 제외 쌍은 이유와 함께 반환."""
    used, excluded = [], []
    for a, b in zip(rows, rows[1:]):
        if not (a["valid"] and b["valid"]):
            excluded.append({"rows": [a["row"], b["row"]],
                             "reason": a["reason"] or b["reason"]})
            continue
        dt_ms = b["sim_ms"] - a["sim_ms"]
        if dt_ms <= 0:
            excluded.append({"rows": [a["row"], b["row"]],
                             "reason": f"sim_t 간격 {dt_ms} ms (중복/역전)"})
            continue
        speed = math.hypot(b["x"] - a["x"], b["y"] - a["y"]) / (dt_ms / 1000)
        if not math.isfinite(speed):
            excluded.append({"rows": [a["row"], b["row"]], "reason": "속도 비유한"})
            continue
        used.append({"row_a": a["row"], "row_b": b["row"],
                     "t0": a["sim_ms"], "t1": b["sim_ms"], "speed": speed})
    return used, excluded


def time_weighted_mean(iv: list[dict], start_ms: int, end_ms: int) -> tuple[float, int]:
    """[start, end) 안에서 각 속도가 [t0, t1) 동안 유지된다고 보고 겹친 시간으로 가중."""
    num = 0.0
    dur = 0
    for s in iv:
        lo, hi = max(s["t0"], start_ms), min(s["t1"], end_ms)
        if hi > lo:
            num += s["speed"] * (hi - lo)
            dur += hi - lo
    return (num / dur if dur else float("nan")), dur


def sample_mean(iv: list[dict], start_ms: int, end_ms: int) -> tuple[float, int]:
    """중점(t0+t1)/2 이 [start, end) 에 드는 표본의 단순 평균."""
    vals = [s["speed"] for s in iv if start_ms * 2 <= s["t0"] + s["t1"] < end_ms * 2]
    return (sum(vals) / len(vals) if vals else float("nan")), len(vals)


def recent_windows(iv: list[dict], end_ms: int, width_ms: int = 60000) -> dict:
    last = time_weighted_mean(iv, end_ms - width_ms, end_ms)
    prev = time_weighted_mean(iv, end_ms - 2 * width_ms, end_ms - width_ms)
    last_s = sample_mean(iv, end_ms - width_ms, end_ms)
    prev_s = sample_mean(iv, end_ms - 2 * width_ms, end_ms - width_ms)
    return {
        "definition": "마지막 관측 sim_t를 끝으로 하는 [end-60s, end) 와 직전 [end-120s, end-60s)",
        "end_sim_s": end_ms / 1000,
        "last60_time_weighted_mps": round(last[0], 6),
        "prev60_time_weighted_mps": round(prev[0], 6),
        "covered_ms": [last[1], prev[1]],
        "last60_sample_mean_mps": round(last_s[0], 6),
        "prev60_sample_mean_mps": round(prev_s[0], 6),
        "sample_counts": [last_s[1], prev_s[1]],
        "delta_time_weighted_mps": round(last[0] - prev[0], 6),
        "delta_pct": round((last[0] - prev[0]) / prev[0] * 100, 2),
    }


# ---------------------------------------------------------------- 1초 집계
def wall_at(rows: list[dict], sim_ms: int) -> int:
    """sim 시각에 해당하는 wall_t(ms)를 앞뒤 원본 행으로 선형보간 (정확한 유리수 계산 후 반올림)."""
    v = [r for r in rows if r["valid"]]
    for a, b in zip(v, v[1:]):
        if a["sim_ms"] <= sim_ms <= b["sim_ms"]:
            if sim_ms == a["sim_ms"]:
                return a["wall_ms"]
            frac = Fraction(sim_ms - a["sim_ms"], b["sim_ms"] - a["sim_ms"])
            return round(a["wall_ms"] + (b["wall_ms"] - a["wall_ms"]) * frac)
    raise DataError(f"sim {sim_ms} ms 가 원본 시간 범위 밖")


def iso_kst(wall_ms: int) -> str:
    t = datetime.fromtimestamp(wall_ms // 1000, tz=timezone.utc) + timedelta(milliseconds=wall_ms % 1000)
    return t.astimezone(KST).isoformat(timespec="milliseconds")


def aggregate(rows: list[dict], iv: list[dict]) -> tuple[list[dict], list[dict]]:
    """구간 k = [k s, k+1 s) (시작 포함·끝 제외). 시간가중 평균이 대표값.
    관측이 구간 전체(1000 ms)를 덮지 못하는 양 끝 구간은 대표 시계열에서 제외."""
    first, last = iv[0]["t0"], iv[-1]["t1"]
    bins, dropped = [], []
    for k in range(first // BIN_MS, (last - 1) // BIN_MS + 1):
        lo, hi = k * BIN_MS, (k + 1) * BIN_MS
        tw, covered = time_weighted_mean(iv, lo, hi)
        sm, n_mid = sample_mean(iv, lo, hi)
        overlap = [s for s in iv if s["t0"] < hi and s["t1"] > lo]
        rec = {
            "bin_start_ms": lo,
            "covered_ms": covered,
            "n_intervals": len(overlap),
            "n_midpoint_samples": n_mid,
            "row_first": overlap[0]["row_a"] if overlap else None,
            "row_last": overlap[-1]["row_b"] if overlap else None,
            "tw": tw,
            "simple": sm,
        }
        if covered == 0:
            dropped.append({**rec, "reason": "빈 구간"})
        elif covered < BIN_MS:
            dropped.append({**rec, "reason": f"불완전 구간 (관측 {covered} ms / 1000 ms)"})
        else:
            rec["wall_ms"] = wall_at(rows, lo)
            bins.append(rec)
    return bins, dropped


def doc_id(run_id: str, bin_start_s: int) -> str:
    return f"{run_id}__{METRIC}__1s__t{bin_start_s:05d}__v{TRANSFORM_VERSION}"


def stats(vals: list[float]) -> dict:
    return {"count": len(vals), "mean": round(sum(vals) / len(vals), 6),
            "min": round(min(vals), 6), "max": round(max(vals), 6)}


# ---------------------------------------------------------------- 실행
def build(csv_path: Path, json_path: Path, out: Path) -> dict:
    for p in (csv_path, json_path):
        if not p.is_file():
            raise DataError(f"원본 없음: {p}")
    csv_hash_before = sha256(csv_path)
    json_hash_before = sha256(json_path)

    meta = json.loads(json_path.read_text(encoding="utf-8"))
    run_id = f"{meta['tag']}_{meta['stamp']}"
    if csv_path.stem != run_id:
        raise DataError(f"CSV 이름({csv_path.stem})과 JSON 시험 식별자({run_id}) 불일치")

    header, raw = load_rows(csv_path)
    profile = column_profile(header, raw)
    rows = parse_required(raw)
    invalid_rows = [{"row": r["row"], "reason": r["reason"]} for r in rows if not r["valid"]]
    tchk = time_checks(rows)
    iv, excluded_pairs = speed_intervals(rows)
    speeds = [s["speed"] for s in iv]
    raw_stats = stats(speeds)
    raw_tw, raw_cov = time_weighted_mean(iv, iv[0]["t0"], iv[-1]["t1"])
    bins, dropped = aggregate(rows, iv)
    tw_vals = [b["tw"] for b in bins]
    simple_vals = [b["simple"] for b in bins]
    # 1초 대표 시계열 기준 최근 비교: 마지막 60개 완전 구간 vs 직전 60개
    recent_1s = {
        "definition": "대표 시계열의 마지막 60개 1초 구간 평균 vs 직전 60개 (구간 값의 단순 평균 = 시간가중)",
        "last60_bins_s": [bins[-60]["bin_start_ms"] // 1000, bins[-1]["bin_start_ms"] // 1000],
        "prev60_bins_s": [bins[-120]["bin_start_ms"] // 1000, bins[-61]["bin_start_ms"] // 1000],
        "last60_mean_mps": round(sum(tw_vals[-60:]) / 60, 6),
        "prev60_mean_mps": round(sum(tw_vals[-120:-60]) / 60, 6),
    }
    recent_1s["delta_mps"] = round(recent_1s["last60_mean_mps"] - recent_1s["prev60_mean_mps"], 6)

    csv_name = csv_path.name
    short_hash = csv_hash_before[:12]
    out.mkdir(parents=True, exist_ok=True)

    # --- ugv_speed_1s.csv
    cols = ["doc_id", "date", "sim_time_s", "bin_end_sim_s", "value", "speed_simple_mean_mps",
            "n_intervals", "n_midpoint_samples", "covered_ms", "source_row_first", "source_row_last",
            "run_id", "metric", "unit", "transform_version"]
    with (out / "ugv_speed_1s.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for b in bins:
            k = b["bin_start_ms"] // 1000
            w.writerow([doc_id(run_id, k), iso_kst(b["wall_ms"]), k, k + 1, f"{b['tw']:.6f}",
                        f"{b['simple']:.6f}", b["n_intervals"], b["n_midpoint_samples"], b["covered_ms"],
                        b["row_first"], b["row_last"], run_id, METRIC, UNIT, TRANSFORM_VERSION])

    # --- ugv_speed_1s.json (Firestore 준비)
    docs = []
    for b in bins:
        k = b["bin_start_ms"] // 1000
        docs.append({
            "id": doc_id(run_id, k),
            "data": {
                "date": iso_kst(b["wall_ms"]),
                "value": round(b["tw"], 6),
                "memo": (f"sim {k}–{k + 1}s 시간가중 평균, 원본 행 {b['row_first']}~{b['row_last']} "
                         f"(속도 구간 {b['n_intervals']}개)"),
                "run_id": run_id,
                "metric": METRIC,
                "unit": UNIT,
                "sim_time_s": k,
                "source_ref": f"{csv_name}@sha256:{short_hash}#rows={b['row_first']}-{b['row_last']}",
                "transform_version": TRANSFORM_VERSION,
            },
        })
    payload = {
        "transform_version": TRANSFORM_VERSION,
        "run_id": run_id,
        "metric": METRIC,
        "unit": UNIT,
        "source": {"file": csv_name, "sha256": csv_hash_before},
        "count": len(docs),
        "documents": docs,
    }
    (out / "ugv_speed_1s.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    # --- 원본 해시 불변 확인 (출력 작성 후)
    csv_hash_after, json_hash_after = sha256(csv_path), sha256(json_path)
    if (csv_hash_after, json_hash_after) != (csv_hash_before, json_hash_before):
        raise DataError("처리 중 원본 해시가 바뀌었다")

    # --- source_manifest.json
    manifest = {
        "run_id": run_id,
        "provenance": {
            "project": "ADAIR 산불재난대응 시뮬레이션",
            "environment": "Gazebo + PX4 (UGV 주행 시험 기록기 출력)",
            "origin_paths": [f"{ORIGIN_REL}/{csv_name}", f"{ORIGIN_REL}/{json_path.name}"],
            "local_copies": [csv_path.relative_to(ROOT).as_posix(), json_path.relative_to(ROOT).as_posix()],
            "note": "원본은 ADAIR 저장소에서 .gitignore(logs/) 대상이며, 로컬 사본은 해시가 동일함을 확인했다.",
        },
        "files": [
            {"role": "position_log_csv", "name": csv_name, "sha256": csv_hash_before,
             "bytes": csv_path.stat().st_size, "data_rows": len(raw), "columns": header},
            {"role": "run_summary_json", "name": json_path.name, "sha256": json_hash_before,
             "bytes": json_path.stat().st_size},
        ],
        "run_metadata": {
            "tag": meta.get("tag"), "stamp": meta.get("stamp"), "world": meta.get("world"),
            "model": meta.get("model"), "resource_id": meta.get("rid"),
            "start_local": meta.get("start_local"), "start_wall_epoch_s": meta.get("start_wall"),
            "task_ids": [r.get("key") for r in meta.get("runs", [])],
            "profile_max_speed_kmh": (meta.get("state") or {}).get("profile", {}).get("max_speed_kmh"),
            "rules_max_speed_mps": (meta.get("health") or {}).get("rules", {}).get("max_speed_mps"),
        },
        "time_bases": {
            "sim_t": "Gazebo 시뮬레이션 시각 (초). 속도·집계 기준",
            "wall_t": "기록기 벽시계, Unix epoch 초 (UTC). date 표기에만 사용",
        },
    }
    (out / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    # --- validation_summary.json
    recomputed = {
        "position_observations": len(raw),
        "derived_speeds": len(iv),
        "sim_duration_s": tchk["sim_duration_s"],
        "speed_mean_mps": raw_stats["mean"],
        "speed_min_mps": raw_stats["min"],
        "speed_max_mps": raw_stats["max"],
        "nonempty_1s_bins": len(bins) + sum(d["covered_ms"] > 0 for d in dropped),
        "complete_1s_bins": len(bins),
    }
    tol = {"speed_mean_mps": 5e-5, "speed_max_mps": 5e-5, "sim_duration_s": 5e-4}
    comparison = {k: {"reported": REPORTED[k], "recomputed": recomputed[k],
                      "match": abs(recomputed[k] - REPORTED[k]) <= tol.get(k, 0)} for k in REPORTED}
    rules_max = manifest["run_metadata"]["rules_max_speed_mps"]
    summary = {
        "run_id": run_id,
        "transform_version": TRANSFORM_VERSION,
        "source_sha256": {"csv": csv_hash_before, "json": json_hash_before},
        "source_unchanged_after_run": True,
        "column_profile": profile,
        "invalid_required_rows": invalid_rows,
        "time_checks": tchk,
        "speed_formula": "hypot(x[i+1]-x[i], y[i+1]-y[i]) / (sim_t[i+1]-sim_t[i])",
        "excluded_pairs": excluded_pairs,
        "raw_speed": {**raw_stats, "time_weighted_mean": round(raw_tw, 6), "covered_ms": raw_cov,
                      "zero_speed_count": sum(s == 0 for s in speeds),
                      "over_rules_max_count": sum(s > rules_max for s in speeds) if rules_max else None,
                      "rules_max_speed_mps": rules_max},
        "recent_raw": recent_windows(iv, iv[-1]["t1"]),
        "aggregation": {
            "method": "시간가중 평균, 구간 [k, k+1) sim 초, 경계 가로지르는 속도 구간은 분할",
            "kept_bins": len(bins),
            "first_bin_s": bins[0]["bin_start_ms"] // 1000,
            "last_bin_s": bins[-1]["bin_start_ms"] // 1000,
            "dropped_bins": [{"bin_start_s": d["bin_start_ms"] // 1000, "covered_ms": d["covered_ms"],
                              "rows": [d["row_first"], d["row_last"]], "reason": d["reason"]}
                             for d in dropped],
            "time_weighted": stats(tw_vals),
            "simple_mean": stats(simple_vals),
            "max_abs_diff_tw_vs_simple": round(max(abs(a - b) for a, b in zip(tw_vals, simple_vals)), 6),
            "n_intervals_histogram": _hist(b["n_intervals"] for b in bins),
            "n_midpoint_samples_histogram": _hist(b["n_midpoint_samples"] for b in bins),
            "date_first": iso_kst(bins[0]["wall_ms"]),
            "date_last": iso_kst(bins[-1]["wall_ms"]),
        },
        "recent_1s": recent_1s,
        "reported_comparison": comparison,
    }
    (out / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return summary


def _hist(values) -> dict:
    h: dict[int, int] = {}
    for v in values:
        h[v] = h.get(v, 0) + 1
    return {str(k): h[k] for k in sorted(h)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    a = ap.parse_args(argv)
    try:
        s = build(a.csv.resolve(), a.json.resolve(), a.out.resolve())
    except DataError as e:
        print(f"검증 실패: {e}", file=sys.stderr)
        return 1
    agg, rs = s["aggregation"], s["raw_speed"]
    print(f"run_id={s['run_id']} transform_version={s['transform_version']}")
    print(f"원시 속도 n={rs['count']} mean={rs['mean']} min={rs['min']} max={rs['max']}")
    print(f"1초 시계열 {agg['kept_bins']}개 (sim {agg['first_bin_s']}–{agg['last_bin_s'] + 1}s), "
          f"mean={agg['time_weighted']['mean']}")
    for k, v in s["reported_comparison"].items():
        print(f"  {k}: 보고 {v['reported']} / 재계산 {v['recomputed']} -> {'일치' if v['match'] else '불일치'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
