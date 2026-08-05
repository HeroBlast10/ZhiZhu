import argparse
import unittest

from main import _nonnegative_float, _positive_int


class CliValidationTests(unittest.TestCase):
    def test_positive_integer(self):
        self.assertEqual(_positive_int("3"), 3)
        for value in ("0", "-1", "x"):
            with self.assertRaises(argparse.ArgumentTypeError):
                _positive_int(value)

    def test_nonnegative_float(self):
        self.assertEqual(_nonnegative_float("0"), 0.0)
        with self.assertRaises(argparse.ArgumentTypeError):
            _nonnegative_float("-0.1")


if __name__ == "__main__":
    unittest.main()
