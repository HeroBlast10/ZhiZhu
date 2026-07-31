import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from scraper import (
    CommentFetchError,
    ContentExtractionError,
    _fetch_comment_page,
    _first_nonempty_html,
    _load_verified_progress,
    _normalize_zhihu_url,
    _validate_delay_range,
    _write_progress,
    download_images,
    sanitize_filename,
    save_content_as_markdown,
)


class _MissingLocator:
    @property
    def first(self):
        return self

    async def count(self):
        return 0


class _MissingPage:
    def locator(self, _selector):
        return _MissingLocator()


class _FailingCommentPage:
    async def evaluate(self, _script, _url):
        return {"ok": False, "status": 503, "error": "HTTP 503"}


class ScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_distinct_answers_never_share_a_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            common = {
                "title": "同一个问题",
                "date": "2026-01-01",
                "html": "<p>正文</p>",
                "type": "answer",
            }
            first = await save_content_as_markdown(
                {
                    **common,
                    "author": "匿名用户",
                    "url": "https://www.zhihu.com/question/1/answer/11",
                },
                root,
                False,
            )
            second = await save_content_as_markdown(
                {
                    **common,
                    "author": "匿名用户",
                    "url": "https://www.zhihu.com/question/1/answer/22",
                },
                root,
                False,
            )
            self.assertNotEqual(first, second)
            self.assertEqual(len(list(root.rglob("*.md"))), 2)

    async def test_long_titles_keep_stable_id_in_image_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            common = {
                "title": "长" * 180,
                "author": "作者",
                "date": "2026-01-01",
                "html": "<p>正文</p>",
                "type": "answer",
            }
            first = await save_content_as_markdown(
                {
                    **common,
                    "url": "https://www.zhihu.com/question/2/answer/31",
                },
                root,
                True,
            )
            second = await save_content_as_markdown(
                {
                    **common,
                    "url": "https://www.zhihu.com/question/2/answer/32",
                },
                root,
                True,
            )
            self.assertNotEqual(first, second)
            self.assertIn("answer_31", str(first))
            self.assertIn("answer_32", str(second))

    async def test_missing_content_never_falls_back_to_body(self):
        with self.assertRaises(ContentExtractionError):
            await _first_nonempty_html(
                _MissingPage(),
                (".expected-content",),
                content_kind="回答",
                url="https://www.zhihu.com/question/1/answer/2",
            )

    async def test_comment_api_failure_is_not_silently_treated_as_end(self):
        with patch("scraper.asyncio.sleep", new=self._no_sleep):
            with self.assertRaises(CommentFetchError):
                await _fetch_comment_page(
                    _FailingCommentPage(),
                    "https://www.zhihu.com/api/v4/comment_v5/answers/2/root_comment",
                )

    @staticmethod
    async def _no_sleep(_seconds):
        return None

    async def test_progress_only_trusts_existing_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid_url = "https://www.zhihu.com/question/1/answer/11"
            valid_path = await save_content_as_markdown(
                {
                    "title": "问题",
                    "author": "作者",
                    "date": "2026-01-01",
                    "html": "<p>正文</p>",
                    "type": "answer",
                    "url": valid_url,
                },
                root,
                False,
            )
            ghost_url = "https://www.zhihu.com/question/1/answer/99"
            progress = root / "progress.json"
            progress.write_text(
                json.dumps({"done": [valid_url, ghost_url]}),
                encoding="utf-8",
            )

            items = _load_verified_progress(root, progress, ("answers",))
            self.assertEqual(items, {valid_url: valid_path})

            _write_progress(progress, root, items)
            payload = json.loads(progress.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["done"], [valid_url])
            self.assertNotIn(ghost_url, payload["items"])

    async def test_image_download_streams_and_normalizes_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://pic.example/a.jpg")
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=b"jpeg-data",
            )

        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient

        def client_factory(**kwargs):
            return original_client(transport=transport, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "images"
            with patch("scraper.httpx.AsyncClient", side_effect=client_factory):
                mapping = await download_images(
                    ["//pic.example/a.jpg"],
                    destination,
                )

            self.assertEqual(
                set(mapping),
                {"https://pic.example/a.jpg"},
            )
            saved = destination / Path(next(iter(mapping.values()))).name
            self.assertEqual(saved.read_bytes(), b"jpeg-data")


class ScraperUtilityTests(unittest.TestCase):
    def test_external_links_are_rejected(self):
        self.assertIsNone(
            _normalize_zhihu_url(
                "https://evil.example/question/1/answer/2",
                "https://www.zhihu.com/question/1",
            )
        )

    def test_reserved_windows_filename_is_safe(self):
        self.assertEqual(sanitize_filename("CON"), "_CON")

    def test_invalid_delay_ranges_are_rejected(self):
        with self.assertRaises(ValueError):
            _validate_delay_range(-1, 2)
        with self.assertRaises(ValueError):
            _validate_delay_range(3, 2)


if __name__ == "__main__":
    unittest.main()
