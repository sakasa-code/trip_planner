"""认证模块单元测试：密码哈希 / JWT / 鉴权解析的边界行为。"""
import unittest

import jwt as pyjwt

from trip_planner import auth


class PasswordHashTest(unittest.TestCase):
    def test_hash_and_verify_roundtrip(self):
        stored = auth.hash_password("secret123")
        self.assertIn("$", stored)
        self.assertTrue(auth.verify_password("secret123", stored))
        self.assertFalse(auth.verify_password("wrong", stored))

    def test_verify_malformed_stored(self):
        self.assertFalse(auth.verify_password("x", "not-a-valid-format"))
        self.assertFalse(auth.verify_password("x", "zz$nothex"))
        self.assertFalse(auth.verify_password("x", None))

    def test_hash_is_salted(self):
        # 相同明文两次哈希应得到不同盐、不同结果
        self.assertNotEqual(auth.hash_password("same"), auth.hash_password("same"))


class TokenTest(unittest.TestCase):
    def test_roundtrip(self):
        token = auth.create_token(42, "alice", "merchant")
        payload = auth.decode_token(token)
        self.assertEqual(payload["sub"], "42")
        self.assertEqual(payload["username"], "alice")
        self.assertEqual(payload["role"], "merchant")

    def test_decode_garbage(self):
        self.assertIsNone(auth.decode_token("garbage.token.here"))


class GetCurrentUserTest(unittest.TestCase):
    def test_missing_or_empty_header(self):
        self.assertIsNone(auth.get_current_user(None))
        self.assertIsNone(auth.get_current_user(""))
        self.assertIsNone(auth.get_current_user("Bearer "))
        self.assertIsNone(auth.get_current_user("Basic abc"))

    def test_non_numeric_sub_returns_none(self):
        # 此前 int(payload["sub"]) 会抛 ValueError 导致 500，现应安全返回 None
        payload = {"sub": "not-a-number", "exp": 9999999999}
        token = pyjwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALG)
        self.assertIsNone(auth.get_current_user(f"Bearer {token}"))

    def test_unknown_user_returns_none(self):
        token = auth.create_token(999999999, "ghost", "personal")
        self.assertIsNone(auth.get_current_user(f"Bearer {token}"))


if __name__ == "__main__":
    unittest.main()
