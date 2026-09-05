"""推荐引擎纯逻辑单元测试：坐标解析/换算、距离、打分、半径吸附。"""
import asyncio
import unittest

from trip_planner import recommend as R


class HaversineTest(unittest.TestCase):
    def test_beijing_shanghai_approx_1070km(self):
        d = R._haversine_km(39.9042, 116.4074, 31.2304, 121.4737)
        self.assertGreater(d, 1000)
        self.assertLess(d, 1150)

    def test_same_point_zero(self):
        self.assertEqual(R._haversine_km(39.9, 116.4, 39.9, 116.4), 0.0)


class CoordParseTest(unittest.TestCase):
    def test_valid_user_location(self):
        self.assertEqual(R.parse_user_location("39.9,116.4"), (39.9, 116.4))

    def test_invalid_user_location(self):
        for bad in ("", "39.9", "91,0", "-91,0", "181,0", "abc,def", "39.9,116.4,1"):
            self.assertIsNone(R.parse_user_location(bad), bad)

    def test_amap_location_lng_lat_order(self):
        self.assertEqual(R._parse_amap_location("116.4,39.9"), (39.9, 116.4))
        self.assertIsNone(R._parse_amap_location("bad"))


class CoordTransformTest(unittest.TestCase):
    def test_out_of_china_identity(self):
        # 纽约不在中国，应原样返回
        self.assertEqual(R.wgs84_to_gcj02(40.7128, -74.0060), (40.7128, -74.0060))

    def test_in_china_shifted(self):
        lat, lng = R.wgs84_to_gcj02(39.916527, 116.397128)
        self.assertNotAlmostEqual(lat, 39.916527)
        self.assertNotAlmostEqual(lng, 116.397128)
        # 偏移应在一个合理范围内（数百米量级，远小于 1 度）
        self.assertLess(abs(lat - 39.916527), 0.01)
        self.assertLess(abs(lng - 116.397128), 0.01)


class SnapRadiusTest(unittest.TestCase):
    def test_default_and_snapping(self):
        self.assertEqual(R.snap_radius(None), R.DEFAULT_RADIUS_M)
        self.assertEqual(R.snap_radius(5200), 5000)
        self.assertEqual(R.snap_radius(8000), 10000)
        self.assertEqual(R.snap_radius(1), 1000)


class ScorePoiTest(unittest.TestCase):
    def _poi(self, **kw):
        base = {
            "name": "故宫博物院",
            "type": "风景名胜;博物馆",
            "address": "北京市东城区",
            "location": "116.397128,39.916527",
            "biz_ext": {"rating": "4.8", "cost": "60"},
            "photos": [{"url": "http://x/1.jpg"}],
        }
        base.update(kw)
        return base

    def test_score_with_distance_and_reason(self):
        poi = self._poi()
        score, reason, dist_m, rating = R._score_poi(poi, "博物馆", (39.9, 116.4))
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertIsInstance(dist_m, int)
        self.assertIn("评分", reason)
        self.assertIn("博物馆", reason)

    def test_score_without_location(self):
        poi = self._poi(location="")
        score, reason, dist_m, rating = R._score_poi(poi, "博物馆", (39.9, 116.4))
        self.assertIsNone(dist_m)


class RecommendEmptyCityTest(unittest.TestCase):
    def test_empty_city_returns_empty(self):
        out = asyncio.run(R.recommend("", [], None))
        self.assertEqual(out["items"], [])
        self.assertEqual(out["meta"]["mode"], "city")


if __name__ == "__main__":
    unittest.main()
