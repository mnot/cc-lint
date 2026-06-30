"""Tests for the non-standard header census (issue #12)."""

from pathlib import Path

import pytest

from cc_lint.header_census import (
    OTHER_FAMILY,
    UNATTRIBUTED,
    build_census,
    default_families,
    derive_prefix,
    load_families,
)

# A small, representative proprietary header population. Counts are the
# per-response "carried it" semantics of unprocessed_counts.
COUNTS = {
    "cf-ray": 900,
    "cf-cache-status": 880,
    "x-amz-cf-id": 700,
    "x-amz-cf-pop": 690,
    "x-amz-request-id": 400,
    "x-vercel-id": 300,
    "x-served-by": 250,
    "x-timer": 240,
    "x-forwarded-for": 230,
    "x-request-id": 220,
    "x-acme-edge": 30,
}
BYTES = {name: count * 30 for name, count in COUNTS.items()}


def test_registered_headers_excluded() -> None:
    # content-type / x-cache have httplint parsers, so they are not
    # proprietary and must never enter the census even if handed in.
    census = build_census(
        {**COUNTS, "content-type": 99999, "x-cache": 5000}, BYTES, truncated=False
    )
    names = {entry.name for entry in census.top_headers}
    assert "content-type" not in names
    assert "x-cache" not in names
    assert census.distinct_names == len(COUNTS)


def test_case_folding() -> None:
    # Case variants of one header fold to a single lowercased entry.
    census = build_census({"X-Acme-Edge": 10, "x-acme-edge": 5}, {}, truncated=False)
    assert census.distinct_names == 1
    entry = census.top_headers[0]
    assert entry.name == "x-acme-edge"
    assert entry.count == 15


def test_vendor_clustering() -> None:
    census = build_census(COUNTS, BYTES, truncated=False)
    by_vendor = {c.key: c for c in census.by_vendor}
    assert by_vendor["cloudflare"].distinct == 2
    assert by_vendor["cloudflare"].count == 900 + 880
    assert by_vendor["cloudfront"].distinct == 2  # x-amz-cf-*
    # General AWS / de-facto headers land in the Unattributed bucket.
    assert "x-amz-request-id" in {
        m.name for m in by_vendor[UNATTRIBUTED].members
    }


def test_well_known_vs_novel() -> None:
    census = build_census(COUNTS, BYTES, truncated=False)
    wk = {e.name for e in census.top_headers if e.well_known}
    assert "x-forwarded-for" in wk
    assert "x-request-id" in wk
    assert "cf-ray" not in wk  # vendor signal, not a de-facto cross-vendor header
    assert census.well_known_names + census.novel_names == census.distinct_names


def test_prefix_axis_groups_amz() -> None:
    census = build_census(COUNTS, BYTES, truncated=False)
    by_prefix = {c.label: c for c in census.by_prefix}
    assert by_prefix["x-amz-*"].distinct == 3  # cf-id, cf-pop, request-id
    assert by_prefix["cf-*"].distinct == 2


def test_deterministic_ordering() -> None:
    # Same input (in any dict order) yields identical, sorted output -- the
    # property the future longitudinal diff relies on.
    census_a = build_census(COUNTS, BYTES, truncated=False)
    shuffled = dict(reversed(list(COUNTS.items())))
    census_b = build_census(shuffled, BYTES, truncated=False)
    assert [e.name for e in census_a.top_headers] == [
        e.name for e in census_b.top_headers
    ]
    assert [c.key for c in census_a.by_vendor] == [c.key for c in census_b.by_vendor]
    # Clusters sort by count desc; cloudflare (1780) precedes cloudfront (1390).
    assert census_a.by_vendor[0].key == "cloudflare"


def test_byte_share_lower_bound_when_bytes_missing() -> None:
    # A name whose bytes fell off the byte-economics head contributes 0 bytes,
    # not an error.
    census = build_census({"x-acme-edge": 30}, {}, truncated=True)
    assert census.total_bytes == 0
    assert census.truncated is True


def test_empty_census() -> None:
    census = build_census({}, {}, truncated=False)
    assert not census.has_data
    assert census.distinct_names == 0
    assert census.by_vendor == []


def test_derive_prefix() -> None:
    assert derive_prefix("x-amz-cf-id") == "x-amz"
    assert derive_prefix("x-forwarded-for") == "x-forwarded"
    assert derive_prefix("x-cache") == "x-cache"
    assert derive_prefix("cf-ray") == "cf"
    assert derive_prefix("surrogate-key") == "surrogate"
    assert derive_prefix("singleword") == "singleword"


def test_family_classification_and_order() -> None:
    fam = default_families()
    assert fam.classify("x-forwarded-for") == "routing"
    assert fam.classify("cf-cache-status") == "caching"
    assert fam.classify("x-request-id") == "observability"
    assert fam.classify("x-acme-totally-novel") == OTHER_FAMILY
    # OTHER is always last in the display order.
    assert fam.family_order[-1] == OTHER_FAMILY


def test_proxy_cache_classifies_as_caching() -> None:
    # Regression: routing must not shadow caching's specific x-proxy-cache.
    fam = default_families()
    assert fam.classify("x-proxy-cache") == "caching"
    assert fam.classify("x-proxy-cache-hit") == "caching"


def test_crawler_headers_excluded() -> None:
    # x-crawler-* are CC-injected; the census must drop them even if a caller
    # hands them in (the renderers claim they are excluded).
    census = build_census(
        {"x-crawler-detected-charset": 999, "x-acme-edge": 5}, {}, truncated=False
    )
    names = {e.name for e in census.top_headers}
    assert "x-crawler-detected-charset" not in names
    assert names == {"x-acme-edge"}


def test_family_well_known() -> None:
    fam = default_families()
    assert fam.is_well_known("x-forwarded-proto") is True  # prefix
    assert fam.is_well_known("x-request-id") is True  # exact name
    assert fam.is_well_known("x-acme-edge") is False


def test_custom_families_table(tmp_path: Path) -> None:
    table = tmp_path / "fam.toml"
    table.write_text("""
[[family]]
id = "demo"
label = "Demo"
prefixes = ["x-demo-"]

[well_known]
names = ["x-demo-known"]
""")
    fam = load_families(str(table))
    assert fam.classify("x-demo-thing") == "demo"
    assert fam.label("demo") == "Demo"
    assert fam.is_well_known("x-demo-known") is True


@pytest.mark.parametrize(
    "body, message",
    [
        ('[well_known]\nnames=["a"]', r"no \[\[family\]\] entries"),
        ('[[family]]\nlabel="L"\nprefixes=["x-"]', "missing 'id'"),
        ('[[family]]\nid="a"\nprefixes=["x-"]', "missing 'label'"),
        ('[[family]]\nid="a"\nlabel="L"\nprefixes=[1]', "must be"),
    ],
)
def test_malformed_families_raise(tmp_path: Path, body: str, message: str) -> None:
    table = tmp_path / "bad.toml"
    table.write_text(body)
    with pytest.raises(ValueError, match=message):
        load_families(str(table))


def test_packaged_families_load() -> None:
    fam = default_families()
    assert "caching" in fam.family_order
    assert "observability" in fam.family_order
