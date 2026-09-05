"""请求 / 响应模型校验测试。"""
import unittest

from pydantic import ValidationError

from trip_planner.schemas import TripRequest, WeatherInfo


def _base_request(**overrides):
    payload = {
        "city": "北京",
        "start_date": "2025-06-01",
        "end_date": "2025-06-03",
        "travel_days": 3,
        "transportation": "公共公交",
        "accommodation": "经济型酒店",
    }
    payload.update(overrides)
    return payload


class TripRequestTest(unittest.TestCase):
    def test_valid_request(self):
        req = TripRequest(**_base_request())
        self.assertEqual(req.preferences, [])

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_base_request(start_date="2025-06-03", end_date="2025-06-01"))

    def test_bad_date_format_rejected(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_base_request(start_date="2025/06/01"))

    def test_travel_days_bounds(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_base_request(travel_days=0))
        with self.assertRaises(ValidationError):
            TripRequest(**_base_request(travel_days=31))

    def test_preferences_default_is_isolated(self):
        a = TripRequest(**_base_request())
        b = TripRequest(**_base_request())
        a.preferences.append("历史")
        self.assertEqual(b.preferences, [])

    def test_city_invalid_rejected(self):
        # 编码损坏产生的「??」、纯符号或空串都应被拦截，避免误导后续规划
        for bad in ("", "   ", "??", "？？", "---"):
            with self.assertRaises(ValidationError):
                TripRequest(**_base_request(city=bad))

    def test_city_valid_and_stripped(self):
        self.assertEqual(TripRequest(**_base_request(city="广州")).city, "广州")
        self.assertEqual(TripRequest(**_base_request(city=" 广州 ")).city, "广州")
        self.assertEqual(TripRequest(**_base_request(city="Xi'an")).city, "Xi'an")


class WeatherInfoTest(unittest.TestCase):
    def test_parses_celsius_suffix(self):
        w = WeatherInfo(date="2025-06-01", day_temp="25°C", night_temp="15℃")
        self.assertEqual(w.day_temp, 25)
        self.assertEqual(w.night_temp, 15)

    def test_invalid_temp_falls_back_to_zero(self):
        w = WeatherInfo(date="2025-06-01", day_temp="N/A")
        self.assertEqual(w.day_temp, 0)


if __name__ == "__main__":
    unittest.main()
