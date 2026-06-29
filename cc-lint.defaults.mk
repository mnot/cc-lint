# Default run configuration for cc-lint.
#
# Keep local credentials, buckets, and account-specific settings in
# cc-lint.mk or another CONFIG=... file. See cc-lint.example.mk.

CRAWL_ID ?= CC-MAIN-2026-12
LOCAL_CRAWL_ID ?= CC-MAIN-2024-18
TOP_N ?= 100000
LOCAL_TOP_N ?= 100000
SAMPLE_TOP_N ?= 10000
RECORD_LIMIT ?= 0

MAP_TASKS ?= 1600
# ~100-200 active note keys distribute across reducers; with the sharded
# reducer keys 10 reducers gives 10-20 keys each, well-balanced. The
# previous default of 20 was conservative; the per-key payload is small
# enough that 10 saves shuffle overhead without serialising the long pole.
REDUCES ?= 10
TEST_MAP_TASKS ?= 20
TEST_REDUCES ?= 1

MRJOB_CONFIG ?= mrjob.conf
MRJOB_TEST_CONFIG ?= mrjob-test.conf
MRJOB_BOOTSTRAP_INSTALL ?= sudo dnf install -y python3.12 python3.12-pip zlib brotli
MRJOB_BOOTSTRAP_PIP_INSTALL ?= sudo /usr/bin/python3.12 -m pip install --no-index --find-links=/tmp/wheels/ mrjob warcio httplint boto3 requests click python-dateutil
MRJOB_CLEANUP ?= TMP
EMR_LOG_DIR ?= /tmp/cc-lint-emr-logs
EMR_LOG_CLUSTER_ID ?=

TRANCO_CACHE_DIR ?= $(HOME)/.cache/cc-lint
TRANCO_CACHE_BASENAME ?= top-1m.csv
TRANCO_CACHE ?= $(TRANCO_CACHE_DIR)/$(TRANCO_CACHE_BASENAME)

# IP-to-ASN (CAIDA pfx2as) for ASN-based infrastructure fingerprinting (#4).
# Optional: when IPASN_CACHE exists, the EMR job ships it to mappers and the
# local lint passes it through, enabling ASN matching. Build it with
# `make ipasn-cache` after setting the snapshot URLs below; otherwise
# fingerprinting stays header-only.
IPASN_CACHE_DIR ?= $(HOME)/.cache/cc-lint
IPASN_CACHE ?= $(IPASN_CACHE_DIR)/ipasn.tsv
# CAIDA RouteViews pfx2as snapshot URLs (IPv4 + optional IPv6), gzipped. Pin to
# a date near the crawl month -- ASN ownership drifts, and pinning keeps runs
# reproducible. Find the timestamped files under:
#   https://publicdata.caida.org/datasets/routing/routeviews-prefix2as/
#   https://publicdata.caida.org/datasets/routing/routeviews6-prefix2as/
IPASN_V4_URL ?=
IPASN_V6_URL ?=
