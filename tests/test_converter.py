import unittest

from converter import ZhihuConverter, normalize_image_url


class ConverterTests(unittest.TestCase):
    def test_protocol_relative_image_uses_local_mapping(self):
        html = '<img src="//pic.example/a.jpg" alt="示例">'
        normalized = "https://pic.example/a.jpg"

        self.assertEqual(normalize_image_url("//pic.example/a.jpg"), normalized)
        self.assertEqual(
            ZhihuConverter.extract_image_urls(html),
            [normalized],
        )
        markdown = ZhihuConverter(
            {normalized: "images/a.jpg"}
        ).convert(html)
        self.assertIn("![示例](images/a.jpg)", markdown)

    def test_math_placeholder_is_restored(self):
        html = '<p>A <span class="ztext-math" data-tex="x^2"></span></p>'
        self.assertEqual(ZhihuConverter().convert(html), "A $x^2$\n")


if __name__ == "__main__":
    unittest.main()
