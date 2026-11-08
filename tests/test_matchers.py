"""算法注册表与可插拔结构的测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from strinova_star.legacy_hist import HalfbodyMatcher  # noqa: E402
from strinova_star.matchers import (  # noqa: E402
    DEFAULT,
    MATCHERS,
    FeatureMatcher,
    describe,
    make,
    surf_available,
)
from strinova_star.matcher_surf import SurfUnavailable  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_three_feature_algorithms_registered(self):
        self.assertEqual(sorted(MATCHERS), ["orb", "sift", "surf"])

    def test_default_is_orb(self):
        self.assertEqual(DEFAULT, "orb")
        self.assertIsInstance(make(), MATCHERS["orb"])

    def test_all_are_feature_matchers(self):
        for name, cls in MATCHERS.items():
            self.assertTrue(issubclass(cls, FeatureMatcher), name)

    def test_unknown_algorithm_raises(self):
        with self.assertRaises(KeyError):
            make("superpoint")

    def test_describe_covers_every_algorithm(self):
        rows = describe()
        self.assertEqual([r[0] for r in rows], list(MATCHERS))
        for _name, usable, note in rows:
            self.assertIsInstance(usable, bool)
            self.assertTrue(note)


class SurfTest(unittest.TestCase):
    def test_matches_availability(self):
        """SURF 能用就该造得出来，不能用就该抛 SurfUnavailable。"""
        if surf_available():
            self.assertIsInstance(make("surf"), MATCHERS["surf"])
        else:
            with self.assertRaises(SurfUnavailable):
                make("surf")


class HistFallbackTest(unittest.TestCase):
    """直方图那套不在注册表里，但必须能按同样接口用（--method hist）。"""

    @classmethod
    def setUpClass(cls):
        cls.matcher = HalfbodyMatcher()

    def test_has_same_interface(self):
        for attr in ("names", "score_row", "scores", "match"):
            self.assertTrue(hasattr(self.matcher, attr), attr)

    def test_scores_are_between_zero_and_one(self):
        crop = __import__("strinova_star.avatars", fromlist=["imread"]).imread(
            self.matcher.folder / "令.png")
        row = self.matcher.score_row(crop)
        self.assertTrue(float(row.max()) <= 1.0 + 1e-6)
        self.assertGreater(float(row.max()), 0.4)


if __name__ == "__main__":
    unittest.main()
