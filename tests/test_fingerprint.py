"""Tests for the infrastructure fingerprint loader and matcher (issue #4)."""

from pathlib import Path

import pytest

from cc_lint.fingerprint import Fingerprinter, load_fingerprinter


def _headers(**pairs: str) -> dict[str, list[str]]:
    """Build a lowercased name -> [values] map from simple kwargs."""
    return {name.replace("_", "-").lower(): [value] for name, value in pairs.items()}


def test_packaged_table_loads() -> None:
    fp = load_fingerprinter()
    # A representative slice of the seed table is present.
    for layer_id in ("cloudflare", "cloudfront", "fastly", "akamai", "nginx", "nextjs"):
        assert layer_id in fp.layer_ids
    assert fp.roles["cloudflare"] == "cdn"
    assert fp.roles["nginx"] == "server"
    assert fp.roles["nextjs"] == "framework"


def test_presence_signal_matches() -> None:
    fp = load_fingerprinter()
    assert fp.match(_headers(cf_ray="7d9f...-LHR")) == {"cloudflare"}


def test_value_contains_ci() -> None:
    fp = load_fingerprinter()
    assert "nginx" in fp.match(_headers(server="nginx/1.25.3"))
    assert "akamai" in fp.match(_headers(server="AkamaiGHost"))


def test_regex_signal() -> None:
    fp = load_fingerprinter()
    assert "akamai" in fp.match({"server-timing": ["cdn-cache; desc=HIT, ak_p; dur=1"]})
    # A server-timing without the ak_p token should not trip Akamai.
    assert "akamai" not in fp.match({"server-timing": ["cache;desc=HIT"]})


def test_layer_stack() -> None:
    fp = load_fingerprinter()
    matched = fp.match(_headers(cf_ray="abc", server="nginx", x_powered_by="Next.js"))
    assert matched == {"cloudflare", "nginx", "nextjs"}


def test_unmatched_returns_empty() -> None:
    fp = load_fingerprinter()
    assert fp.match(_headers(server="CoolServer/9")) == set()


def test_asn_match() -> None:
    fp = load_fingerprinter()
    # No header signal, but the ASN belongs to Cloudflare (13335).
    assert fp.match({}, asn=13335) == {"cloudflare"}
    assert fp.match({}, asn=99999) == set()


def test_custom_table_path(tmp_path: Path) -> None:
    table = tmp_path / "fp.toml"
    table.write_text("""
[[layer]]
id = "demo"
role = "server"
signals = [ { header = "x-demo" }, { header = "server", contains = "demo" } ]
""")
    fp = load_fingerprinter(str(table))
    assert fp.layer_ids == ["demo"]
    assert fp.match(_headers(x_demo="1")) == {"demo"}
    assert fp.match(_headers(server="DemoServer")) == {"demo"}


@pytest.mark.parametrize(
    "body, message",
    [
        ('[[layer]]\nrole = "cdn"\nsignals = [{header="x"}]', "missing 'id'"),
        ('[[layer]]\nid="a"\nrole="bogus"\nsignals=[{header="x"}]', "invalid role"),
        ('[[layer]]\nid="a"\nrole="cdn"\nsignals=[{contains="y"}]', "missing 'header'"),
        (
            '[[layer]]\nid="a"\nrole="cdn"\nsignals=[{header="x",contains="y",regex="z"}]',
            "both 'contains' and 'regex'",
        ),
        ('[[layer]]\nid="a"\nrole="cdn"', "at least one signal or asn"),
        ('title = "no layers"', r"no \[\[layer\]\] entries"),
    ],
)
def test_malformed_table_raises(tmp_path: Path, body: str, message: str) -> None:
    table = tmp_path / "bad.toml"
    table.write_text(body)
    with pytest.raises(ValueError, match=message):
        load_fingerprinter(str(table))


def test_duplicate_ids_raise(tmp_path: Path) -> None:
    table = tmp_path / "dup.toml"
    table.write_text(
        '[[layer]]\nid="x"\nrole="cdn"\nsignals=[{header="a"}]\n'
        '[[layer]]\nid="x"\nrole="server"\nsignals=[{header="b"}]\n'
    )
    with pytest.raises(ValueError, match="duplicate fingerprint layer ids"):
        load_fingerprinter(str(table))


def test_public_type() -> None:
    # The public type is importable for annotations elsewhere.
    assert isinstance(load_fingerprinter(), Fingerprinter)
