"""Tests for the offline IP->ASN lookup (issue #4)."""

from pathlib import Path

from cc_lint.ipasn import IpAsnTable, load_ipasn


def _table(path: Path, rows: list[tuple[str, int, int]]) -> IpAsnTable:
    path.write_text("".join(f"{p}\t{plen}\t{asn}\n" for p, plen, asn in rows))
    return load_ipasn(str(path))


def test_longest_prefix_wins(tmp_path: Path) -> None:
    table = _table(
        tmp_path / "ipasn.tsv",
        [("10.0.0.0", 8, 111), ("10.1.0.0", 16, 222), ("10.1.2.0", 24, 333)],
    )
    self_checks = {
        "10.1.2.5": 333,  # /24 is the most specific
        "10.1.9.9": 222,  # falls back to /16
        "10.9.9.9": 111,  # falls back to /8
        "11.0.0.1": None,  # no covering prefix
    }
    for ip, expected in self_checks.items():
        assert table.lookup(ip) == expected


def test_ipv6_lookup(tmp_path: Path) -> None:
    table = _table(tmp_path / "ipasn.tsv", [("2001:200::", 32, 2500)])
    assert table.lookup("2001:200::dead:beef") == 2500
    assert table.lookup("2001:201::1") is None


def test_mixed_v4_v6(tmp_path: Path) -> None:
    table = _table(
        tmp_path / "ipasn.tsv",
        [("1.0.0.0", 24, 13335), ("2606:4700::", 32, 13335)],
    )
    assert table.lookup("1.0.0.1") == 13335
    assert table.lookup("2606:4700::1") == 13335


def test_malformed_ip_returns_none(tmp_path: Path) -> None:
    table = _table(tmp_path / "ipasn.tsv", [("1.0.0.0", 24, 13335)])
    assert table.lookup("not-an-ip") is None
    assert table.lookup("") is None


def test_moas_and_comments_parsed(tmp_path: Path) -> None:
    path = tmp_path / "ipasn.tsv"
    path.write_text(
        "# a comment line\n"
        "8.8.8.0\t24\t15169_36040\n"  # MOAS: keep the first ASN
        "1.1.1.0\t24\t{13335,1234}\n"  # AS-set braces
        "garbage line without tabs\n"
    )
    table = load_ipasn(str(path))
    assert table.lookup("8.8.8.8") == 15169
    assert table.lookup("1.1.1.1") == 13335
