"""Tests for libmyown.pdf_book typst templates."""

from __future__ import annotations

import unittest

from libmyown.pdf_book.typst import make_typst_document_digital, make_typst_document_print


class TypstTemplateTests(unittest.TestCase):
    def test_digital_has_symmetric_margins(self) -> None:
        doc = make_typst_document_digital("body", {"title": "Example"})
        self.assertIn("left: 0.37in", doc)
        self.assertIn("right: 0.37in", doc)
        self.assertNotIn("binding: left", doc)
        self.assertNotIn("inside:", doc)

    def test_digital_page_header_matches_print(self) -> None:
        digital = make_typst_document_digital("body", {"title": "Example"})
        printed = make_typst_document_print("body", {"title": "Example"})
        self.assertIn("emph(running-title), h(1fr), num", digital)
        self.assertNotIn("header: context {{", digital)
        self.assertIn("emph(running-title), h(1fr), num", printed)

    def test_print_has_binding_gutter(self) -> None:
        doc = make_typst_document_print("body", {"title": "Example"})
        self.assertIn("binding: left", doc)
        self.assertIn("inside: 0.43in", doc)
        self.assertIn("outside: 0.31in", doc)


if __name__ == "__main__":
    unittest.main()
