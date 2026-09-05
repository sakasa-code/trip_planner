"""数据模型单元测试：用户 / 企业产品 / 推广 / 统计。使用临时 SQLite 库隔离。"""
import os
import shutil
import unittest
import uuid

from trip_planner import models

# 沙箱只允许在工作区内写文件；且 tempfile.mkdtemp 创建的目录会被沙箱拒绝写入，
# 因此用 os.makedirs 在工作区根目录下创建唯一临时目录。
_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ModelsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = os.path.join(_WORKSPACE_ROOT, f".tmpdb_{uuid.uuid4().hex[:10]}")
        os.makedirs(self._tmpdir)
        self._orig_db = models.DB_PATH
        models.DB_PATH = os.path.join(self._tmpdir, "test.db")
        models.init_db()

    def tearDown(self):
        models.DB_PATH = self._orig_db
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_merchant(self, username="shop"):
        user = models.create_user(username, "hash", "merchant")
        models.create_enterprise(user["id"], username)
        return user


class UserCrudTest(ModelsTestBase):
    def test_create_and_get(self):
        user = models.create_user("alice", "hash", "personal")
        self.assertEqual(user["role"], "personal")
        self.assertIsNotNone(models.get_user_by_username("alice"))
        self.assertIsNotNone(models.get_user_by_id(user["id"]))
        self.assertIsNone(models.get_user_by_id(999999))

    def test_duplicate_username_raises(self):
        models.create_user("bob", "hash")
        with self.assertRaises(ValueError):
            models.create_user("bob", "hash")


class ProductStatsTest(ModelsTestBase):
    def test_increment_reset_on_cross_day(self):
        user = self._make_merchant()
        product = models.create_product(user["id"], "测试景点", lng="116.4", lat="39.9")

        # 将 stat_date 置为过去，模拟跨日
        conn = models.get_conn()
        try:
            conn.execute("UPDATE product_stats SET stat_date = '2000-01-01' WHERE product_id = ?",
                         (product["id"],))
            conn.commit()
        finally:
            conn.close()

        models.increment_product_stats(product["id"], "recommend")

        stats = models.get_product_stats(user["id"])[0]
        self.assertEqual(stats["recommend_count"], 1)
        # 关键回归：跨日重置后当日计数应保留本次 +1，而不是被清零
        self.assertEqual(stats["today_recommend"], 1)

    def test_increment_invalid_field_is_noop(self):
        user = self._make_merchant()
        product = models.create_product(user["id"], "x")
        models.increment_product_stats(product["id"], "bogus")
        stats = models.get_product_stats(user["id"])[0]
        self.assertEqual(stats["recommend_count"], 0)

    def test_favorite_does_not_touch_today(self):
        user = self._make_merchant()
        product = models.create_product(user["id"], "x")
        models.increment_product_stats(product["id"], "favorite")
        stats = models.get_product_stats(user["id"])[0]
        self.assertEqual(stats["favorite_count"], 1)
        self.assertEqual(stats["today_recommend"], 0)


class ProductCrudTest(ModelsTestBase):
    def test_create_update_toggle_list(self):
        user = self._make_merchant()
        p = models.create_product(user["id"], "故宫", lng="116.4", lat="39.9",
                                  category="风景名胜", tags=["历史", "文化"])
        self.assertEqual(p["name"], "故宫")
        self.assertEqual(p["tags"], ["历史", "文化"])

        updated = models.update_product(p["id"], user["id"], name="故宫博物院", rating=4.8)
        self.assertEqual(updated["name"], "故宫博物院")

        toggled = models.toggle_product_status(p["id"], user["id"])
        self.assertEqual(toggled["status"], 0)  # 上架 → 下架

        items = models.list_products(user["id"], status=0)
        self.assertEqual(len(items), 1)

    def test_update_ignores_unknown_fields(self):
        user = self._make_merchant()
        p = models.create_product(user["id"], "x")
        # 未知字段被忽略，不报错
        models.update_product(p["id"], user["id"], hacker_field="should_be_ignored")


class PromotionTest(ModelsTestBase):
    def test_recharge_all_and_single(self):
        user = self._make_merchant()
        models.create_promotion(user["id"], bid_per_1k=100, balance=10)
        total = models.recharge_promotion(user["id"], None, 50)
        self.assertEqual(total, 60.0)

    def test_enterprise_overview_shape(self):
        user = self._make_merchant()
        models.create_product(user["id"], "x")
        ov = models.get_enterprise_overview(user["id"])
        self.assertIn("today", ov)
        self.assertIn("total", ov)
        self.assertIn("products", ov)


if __name__ == "__main__":
    unittest.main()
