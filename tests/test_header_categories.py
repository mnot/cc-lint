"""Tests for header byte-economics classification (issue #10)."""

import unittest

from cc_lint.header_categories import (
    DEPRECATED,
    PROPRIETARY,
    STANDARD,
    categorize_header_bytes,
    classify_header,
)


class TestClassifyHeader(unittest.TestCase):
    def test_registered_field_is_standard(self) -> None:
        self.assertEqual(classify_header("content-type"), STANDARD)
        self.assertEqual(classify_header("cache-control"), STANDARD)

    def test_deprecated_field_is_deprecated_not_standard(self) -> None:
        # Pragma is registered *and* deprecated; deprecation wins so it doesn't
        # land in the standard bucket.
        self.assertEqual(classify_header("pragma"), DEPRECATED)

    def test_unregistered_field_is_proprietary(self) -> None:
        self.assertEqual(classify_header("x-amz-cf-id"), PROPRIETARY)
        self.assertEqual(classify_header("cf-ray"), PROPRIETARY)


class TestCategorizeHeaderBytes(unittest.TestCase):
    def test_sums_into_categories_in_order(self) -> None:
        result = categorize_header_bytes(
            {
                "content-type": 100,
                "cache-control": 50,
                "pragma": 30,
                "cf-ray": 200,
                "x-amz-cf-id": 70,
            }
        )
        self.assertEqual(
            result,
            [(STANDARD, 150), (DEPRECATED, 30), (PROPRIETARY, 270)],
        )

    def test_empty_input_yields_zeroed_categories(self) -> None:
        self.assertEqual(
            categorize_header_bytes({}),
            [(STANDARD, 0), (DEPRECATED, 0), (PROPRIETARY, 0)],
        )


if __name__ == "__main__":
    unittest.main()
