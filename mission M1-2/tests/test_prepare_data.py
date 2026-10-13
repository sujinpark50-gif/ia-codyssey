"""prepare_data.py 산출물 자동 검증. 실행: python -m unittest discover -s tests -v"""

import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_data as pd_  # noqa: E402

DATA = ROOT / "data"
EXPECTED_CSV_SHA = "4d18f3d45101cbd68ff85d39632e6355c7f6603caf9b2963d7bd5efb7583d4c8"
EXPECTED_JSON_SHA = "4fa14bd41bfb4b74bd25a293c0cd1ccf219b88f559674cb0d8ea978f2a4e22bf"
OUTPUTS = ("ugv_speed_1s.csv", "ugv_speed_1s.json", "source_manifest.json", "validation_summary.json")


class PrepareDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header, cls.raw = pd_.load_rows(pd_.DEFAULT_CSV)
        cls.rows = pd_.parse_required(cls.raw)
        cls.iv, cls.excluded = pd_.speed_intervals(cls.rows)
        with (DATA / "ugv_speed_1s.csv").open(encoding="utf-8", newline="") as f:
            cls.out_csv = list(csv.DictReader(f))
        cls.out_json = json.loads((DATA / "ugv_speed_1s.json").read_text(encoding="utf-8"))

    # 1
    def test_01_required_columns(self):
        for c in pd_.REQUIRED:
            self.assertIn(c, self.header)

    # 2
    def test_02_time_values_valid(self):
        self.assertTrue(all(r["valid"] for r in self.rows))
        sims = [r["sim_ms"] for r in self.rows]
        walls = [r["wall_ms"] for r in self.rows]
        self.assertTrue(all(b > a for a, b in zip(sims, sims[1:])), "sim_t 중복/역전")
        self.assertTrue(all(b > a for a, b in zip(walls, walls[1:])), "wall_t 중복/역전")

    # 3
    def test_03_position_values_valid(self):
        for r in self.rows:
            self.assertTrue(math.isfinite(r["x"]) and math.isfinite(r["y"]), r["row"])

    # 4
    def test_04_speeds_finite(self):
        self.assertEqual(len(self.iv), len(self.raw) - 1)
        self.assertEqual(self.excluded, [])
        self.assertTrue(all(math.isfinite(s["speed"]) for s in self.iv))
        self.assertTrue(all(math.isfinite(float(r["value"])) for r in self.out_csv))

    # 5
    def test_05_speeds_nonnegative(self):
        self.assertTrue(all(s["speed"] >= 0 for s in self.iv))
        self.assertTrue(all(float(r["value"]) >= 0 for r in self.out_csv))

    # 6
    def test_06_bins_unique_and_contiguous(self):
        starts = [int(r["sim_time_s"]) for r in self.out_csv]
        self.assertEqual(len(starts), len(set(starts)))
        self.assertTrue(all(b - a == 1 for a, b in zip(starts, starts[1:])))
        ids = [d["id"] for d in self.out_json["documents"]]
        self.assertEqual(len(ids), len(set(ids)))

    # 7
    def test_07_at_least_100_points(self):
        self.assertGreaterEqual(len(self.out_csv), 100)

    # 8
    def test_08_csv_json_counts_match(self):
        docs = self.out_json["documents"]
        self.assertEqual(len(self.out_csv), len(docs))
        self.assertEqual(self.out_json["count"], len(docs))
        for r, d in zip(self.out_csv, docs):
            self.assertEqual(r["doc_id"], d["id"])
            self.assertEqual(r["date"], d["data"]["date"])
            self.assertAlmostEqual(float(r["value"]), d["data"]["value"], places=6)

    # 9
    def test_09_reproducible(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pd_.build(pd_.DEFAULT_CSV, pd_.DEFAULT_JSON, Path(a))
            pd_.build(pd_.DEFAULT_CSV, pd_.DEFAULT_JSON, Path(b))
            for name in OUTPUTS:
                fresh = (Path(a) / name).read_bytes()
                self.assertEqual(fresh, (Path(b) / name).read_bytes(), name)
                self.assertEqual(fresh, (DATA / name).read_bytes(), f"저장된 {name} 이 재생성 결과와 다름")

    # 10
    def test_10_source_hash_unchanged(self):
        self.assertEqual(pd_.sha256(pd_.DEFAULT_CSV), EXPECTED_CSV_SHA)
        self.assertEqual(pd_.sha256(pd_.DEFAULT_JSON), EXPECTED_JSON_SHA)
        manifest = json.loads((DATA / "source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([f["sha256"] for f in manifest["files"]], [EXPECTED_CSV_SHA, EXPECTED_JSON_SHA])

    # 추가: 집계 결과가 원시 시간 범위 안
    def test_11_bins_within_source_range(self):
        first, last = self.rows[0]["sim_ms"], self.rows[-1]["sim_ms"]
        for r in self.out_csv:
            self.assertGreaterEqual(int(r["sim_time_s"]) * 1000, first)
            self.assertLessEqual(int(r["bin_end_sim_s"]) * 1000, last)
            self.assertEqual(int(r["covered_ms"]), 1000)
            self.assertGreaterEqual(int(r["source_row_first"]), 1)
            self.assertLessEqual(int(r["source_row_last"]), len(self.raw))

    # 추가: 시간가중 평균 = 구간 이동거리 / 1초 (경계 분할 검산)
    def test_12_time_weighted_equals_distance(self):
        def pos(t_ms):
            for a, b in zip(self.rows, self.rows[1:]):
                if a["sim_ms"] <= t_ms <= b["sim_ms"]:
                    f = (t_ms - a["sim_ms"]) / (b["sim_ms"] - a["sim_ms"])
                    return a, b, f
        for r in self.out_csv[::37]:
            lo, hi = int(r["sim_time_s"]) * 1000, int(r["bin_end_sim_s"]) * 1000
            dist = 0.0
            pts = [lo] + [x["sim_ms"] for x in self.rows if lo < x["sim_ms"] < hi] + [hi]
            for t0, t1 in zip(pts, pts[1:]):
                a, b, _ = pos((t0 + t1) / 2)
                seg = math.hypot(b["x"] - a["x"], b["y"] - a["y"]) / (b["sim_ms"] - a["sim_ms"])
                dist += seg * (t1 - t0)
            # seg 는 m/ms, (t1-t0) 는 ms → dist 는 m. 구간 길이가 1초이므로 dist 가 곧 m/s
            self.assertAlmostEqual(dist, float(r["value"]), places=5)


if __name__ == "__main__":
    unittest.main()
