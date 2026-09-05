"""行程规划智能体纯逻辑测试：城市一致性校验。"""
import unittest

from trip_planner.trip_planner_agent import _CityMismatch, _normalize_city


class NormalizeCityTest(unittest.TestCase):
    def test_strips_whitespace_and_suffix(self):
        self.assertEqual(_normalize_city(" 广州 "), "广州")
        self.assertEqual(_normalize_city("北京市"), "北京")
        self.assertEqual(_normalize_city("北京"), "北京")

    def test_distinct_cities_do_not_match(self):
        self.assertNotEqual(_normalize_city("北京"), _normalize_city("广州"))

    def test_empty(self):
        self.assertEqual(_normalize_city(""), "")
        self.assertEqual(_normalize_city(None), "")


class CityMismatchTest(unittest.TestCase):
    def test_exception_carries_both_values(self):
        e = _CityMismatch("北京", "广州")
        self.assertEqual(e.output, "北京")
        self.assertEqual(e.requested, "广州")
        self.assertIn("北京", str(e))
        self.assertIn("广州", str(e))


if __name__ == "__main__":
    unittest.main()
