"""Tests for crosspost links."""

from __future__ import annotations

import unittest

from libmyown.content import parse_work_field_keys, resolve_crosspost_url
from libmyown.site_config import Crosspost, SiteConfig


class CrosspostTests(unittest.TestCase):
    def test_ao3_work_id(self) -> None:
        self.assertEqual(
            resolve_crosspost_url("AO3", "12345678"),
            "https://archiveofourown.org/works/12345678",
        )

    def test_ao3_full_url(self) -> None:
        url = "https://archiveofourown.org/works/99"
        self.assertEqual(resolve_crosspost_url("AO3", url), url)

    def test_other_site_needs_url(self) -> None:
        url = "https://example.com/story"
        self.assertEqual(resolve_crosspost_url("Tumblr", url), url)
        self.assertIsNone(resolve_crosspost_url("Tumblr", "12345"))

    def test_ao3_label_variants(self) -> None:
        self.assertEqual(
            resolve_crosspost_url("On AO3", "12345678"),
            "https://archiveofourown.org/works/12345678",
        )

    def test_site_lookup(self) -> None:
        site = SiteConfig(
            crossposts={
                "Series/Sample One.md": [
                    Crosspost(label="AO3", url="12345678"),
                    Crosspost(label="Blog", url="https://example.com/x"),
                ]
            },
        )
        self.assertEqual(
            site.crossposts_for("Series/Sample One.md"),
            [
                ("AO3", "https://archiveofourown.org/works/12345678"),
                ("Blog", "https://example.com/x"),
            ],
        )

    def test_legacy_ao3_links(self) -> None:
        site = SiteConfig.from_dict(
            {"ao3_links": {"Series/Old.md": "999"}}
        )
        self.assertEqual(
            site.crossposts_for("Series/Old.md"),
            [("AO3", "https://archiveofourown.org/works/999")],
        )


    def test_parse_work_field_keys(self) -> None:
        text = "---\ntitle: Test\nrating: Teen\n---\n\nBody here.\n"
        self.assertEqual(parse_work_field_keys(text), {"title", "rating"})


if __name__ == "__main__":
    unittest.main()
