import os
import tempfile
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from stealth import STEALTH_JS


class StealthIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_browser_apis_remain_consistent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = dict(os.environ)
            env["CHROME_LOG_FILE"] = str(Path(temp_dir) / "chromium.log")
            try:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(
                        headless=True,
                        env=env,
                    )
                    context = await browser.new_context()
                    await context.add_init_script(STEALTH_JS)
                    page = await context.new_page()
                    await page.goto("data:text/html,<html></html>")
                    result = await page.evaluate(
                        """async () => {
                            let permission;
                            try {
                                permission = (
                                    await navigator.permissions.query({name: 'geolocation'})
                                ).state;
                            } catch (error) {
                                permission = `ERROR: ${error.message}`;
                            }
                            return {
                                webdriver: navigator.webdriver,
                                hardware: Array.from(
                                    {length: 5},
                                    () => navigator.hardwareConcurrency
                                ),
                                pluginsType: Object.prototype.toString.call(
                                    navigator.plugins
                                ),
                                permission
                            };
                        }"""
                    )
                    await browser.close()
            except Exception as exc:
                self.skipTest(f"Playwright Chromium 不可用: {exc}")

        self.assertIsNone(result["webdriver"])
        self.assertEqual(len(set(result["hardware"])), 1)
        self.assertEqual(result["pluginsType"], "[object PluginArray]")
        self.assertFalse(result["permission"].startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
