import unittest

from tui import ZhiZhuApp


class TuiSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_mounts(self):
        app = ZhiZhuApp()
        async with app.run_test(size=(100, 40)):
            self.assertIsNotNone(app.query_one("#active_panel"))


if __name__ == "__main__":
    unittest.main()
