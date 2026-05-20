# Example local run configuration for cc-lint.
#
# Copy this file to cc-lint.mk, then edit bucket paths and sizing for your
# AWS account. Alternatively, pass CONFIG=/path/to/another.mk to make.

CRAWL_ID ?= CC-MAIN-2026-12
LOCAL_CRAWL_ID ?= CC-MAIN-2024-18
TOP_N ?= 50000
LOCAL_TOP_N ?= 1000
SAMPLE_TOP_N ?= 10000
RECORD_LIMIT ?= 0

OUTPUT_DIR = s3://YOUR-BUCKET/cc-lint/results/
PATHS_PREFIX = s3://YOUR-BUCKET/cc-lint/paths/
WHEEL_S3_PATH = s3://YOUR-BUCKET/cc-lint/wheels/

MAP_TASKS ?= 1600
REDUCES ?= 20
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
