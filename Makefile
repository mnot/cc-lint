PROJECT = cc_lint
CONFIG ?= cc-lint.mk
include cc-lint.defaults.mk
-include $(CONFIG)

PYTHON_TARGETS = cc_lint $(wildcard tests/*.py)
CACHE_DIR = warc

# Use := to ensure RUN_ID is fixed for the entire make execution
RUN_ID := $(shell date +%Y%m%d-%H%M%S)
FULL_RUN_NAME = $(CRAWL_ID)-$(RUN_ID)
TEST_RUN_NAME = test-$(RUN_ID)

.PHONY: help
help:
	@echo "Common targets:"
	@echo "  make setup         Interactively create cc-lint.mk + mrjob configs"
	@echo "  make venv          Create or update the local development environment"
	@echo "  make test          Run fast unit tests"
	@echo "  make tidy          Format Python code"
	@echo "  make typecheck     Run mypy over package and tests"
	@echo "  make lint          Run pylint over package and tests"
	@echo "  make report.html   Run a local lint and render report.html + report.md"
	@echo "  make test-emr      Run an EMR smoke test"
	@echo "  make emr           Run the full EMR analysis"
	@echo "  make report RESULTS_DIR=results/...  Re-render saved EMR reports"
	@echo "  make tranco-cache  Ensure the Tranco top-sites CSV is downloaded"
	@echo "  make ipasn-cache   Build the IP->ASN table for ASN fingerprinting"
	@echo "  make wheels        Build EMR dependency wheels"
	@echo "  make upload-wheels Build and upload EMR dependency wheels"
	@echo "  make emr-timing EMR_LOG_CLUSTER_ID=j-...  Summarize preserved EMR timing logs"
	@echo "  make show-config   Print effective make configuration"
	@echo "  make clean         Remove local generated scratch artifacts and venv"
	@echo ""
	@echo "Configuration:"
	@echo "  make setup   (or by hand: cp cc-lint.example.mk cc-lint.mk)"
	@echo "  make CONFIG=/path/to/config.mk show-config"

# Generated, operator-specific config files (gitignored). `make setup` creates
# them from the tracked *.example templates by substituting the bucket name.
SETUP_FILES = cc-lint.mk mrjob.conf mrjob-test.conf

.PHONY: setup
setup:
	@for f in $(SETUP_FILES); do \
		if [ -e "$$f" ]; then \
			echo "Refusing to overwrite existing $$f -- remove it first to regenerate."; \
			exit 1; \
		fi; \
	done
	@printf 'S3 bucket name (no s3:// prefix, e.g. my-cc-lint): '; \
	read bucket; \
	bucket=$$(printf '%s' "$$bucket" | sed -E 's#^s3://##; s#/+$$##'); \
	if [ -z "$$bucket" ]; then echo "No bucket given; aborting."; exit 1; fi; \
	sed "s/YOUR-BUCKET/$$bucket/g" cc-lint.example.mk      > cc-lint.mk; \
	sed "s/YOUR-BUCKET/$$bucket/g" mrjob.conf.example      > mrjob.conf; \
	sed "s/YOUR-BUCKET/$$bucket/g" mrjob-test.conf.example > mrjob-test.conf; \
	echo "Wrote $(SETUP_FILES) for bucket '$$bucket'."; \
	echo "Review them (esp. cc-lint.mk overrides), then: make show-config"

.PHONY: show-config
show-config:
	@echo "CONFIG=$(CONFIG)"
	@echo "CRAWL_ID=$(CRAWL_ID)"
	@echo "LOCAL_CRAWL_ID=$(LOCAL_CRAWL_ID)"
	@echo "TOP_N=$(TOP_N)"
	@echo "LOCAL_TOP_N=$(LOCAL_TOP_N)"
	@echo "RECORD_LIMIT=$(RECORD_LIMIT)"
	@echo "OUTPUT_DIR=$(OUTPUT_DIR)"
	@echo "PATHS_PREFIX=$(PATHS_PREFIX)"
	@echo "WHEEL_S3_PATH=$(WHEEL_S3_PATH)"
	@echo "MAP_TASKS=$(MAP_TASKS)"
	@echo "REDUCES=$(REDUCES)"
	@echo "TEST_MAP_TASKS=$(TEST_MAP_TASKS)"
	@echo "TEST_REDUCES=$(TEST_REDUCES)"
	@echo "MRJOB_CONFIG=$(MRJOB_CONFIG)"
	@echo "MRJOB_TEST_CONFIG=$(MRJOB_TEST_CONFIG)"
	@echo "MRJOB_CLEANUP=$(MRJOB_CLEANUP)"
	@echo "EMR_LOG_DIR=$(EMR_LOG_DIR)"
	@echo "TRANCO_CACHE=$(TRANCO_CACHE)"

.PHONY: check-s3-config
check-s3-config:
	@test -n "$(OUTPUT_DIR)" || (echo "Set OUTPUT_DIR in $(CONFIG) or pass CONFIG=/path/to/config.mk"; exit 1)
	@test -n "$(PATHS_PREFIX)" || (echo "Set PATHS_PREFIX in $(CONFIG) or pass CONFIG=/path/to/config.mk"; exit 1)
	@test -n "$(WHEEL_S3_PATH)" || (echo "Set WHEEL_S3_PATH in $(CONFIG) or pass CONFIG=/path/to/config.mk"; exit 1)
	@case "$(OUTPUT_DIR) $(PATHS_PREFIX) $(WHEEL_S3_PATH)" in *YOUR-BUCKET*) echo "Replace YOUR-BUCKET in $(CONFIG) before running EMR targets"; exit 1;; esac

paths.txt:
	curl -sSf https://data.commoncrawl.org/crawl-data/$(LOCAL_CRAWL_ID)/warc.paths.gz | gunzip > $@

# Pass the IP->ASN table through when it has been built (make ipasn-cache);
# otherwise local fingerprinting is header-only.
LOCAL_IPASN_ARG = $(if $(wildcard $(IPASN_CACHE)),--ipasn-path $(IPASN_CACHE),)

.PHONY: report.html
report.html: venv paths.txt
	PYTHONPATH=$(VENV) $(VENV)/cc-lint lint --limit 100 --cache-dir $(CACHE_DIR) --paths-file paths.txt --top-sites $(LOCAL_TOP_N) $(LOCAL_IPASN_ARG) --output $@

.PHONY: test
test: test_py

# Pytest target -- the upstream Makefile.pyproject template no longer ships
# a test_py rule, so define it here.
.PHONY: test_py
test_py: venv
	PYTHONPATH=$(VENV) $(VENV)/python -m pytest

.PHONY: clean
clean: clean_py clean-local

.PHONY: clean-local
clean-local:
	rm -rf .pytest_cache .coverage htmlcov

.PHONY: lint
lint: lint_py

.PHONY: typecheck
typecheck: typecheck_py

.PHONY: tidy
tidy: tidy_py

# --- EMR pipeline -----------------------------------------------------------

MRJOB_BOOTSTRAP_ARGS = \
	--bootstrap "$(MRJOB_BOOTSTRAP_INSTALL)" \
	--bootstrap "aws s3 sync $(WHEEL_S3_PATH) /tmp/wheels/" \
	--bootstrap "$(MRJOB_BOOTSTRAP_PIP_INSTALL)"

# ASN args wire in automatically once $(IPASN_CACHE) exists (build it with
# `make ipasn-cache`); otherwise this is empty and fingerprinting stays
# header-only, so existing runs are unaffected.
IPASN_ARGS = $(if $(wildcard $(IPASN_CACHE)),--files "$(IPASN_CACHE)\#ipasn.tsv" --ipasn-path ipasn.tsv,)

MRJOB_COMMON_ARGS = \
	-r emr \
	$(MRJOB_BOOTSTRAP_ARGS) \
	--files "$(TRANCO_CACHE)\#top-1m-sites.csv" \
	--files "cc_lint/fingerprints.toml\#fingerprints.toml" \
	--files "cc_lint/cooccur_alphabet.toml\#cooccur_alphabet.toml" \
	$(IPASN_ARGS) \
	--cleanup $(MRJOB_CLEANUP) \
	--no-read-logs --no-cat-output \
	--tranco-path top-1m-sites.csv

RESULTS_DIR ?=

.PHONY: tranco-cache
tranco-cache: venv
	$(VENV)/python -c "from cc_lint.top_sites import get_top_sites_path; get_top_sites_path('$(TRANCO_CACHE_DIR)')"

# Build the IP->ASN table from CAIDA pfx2as snapshots. Requires IPASN_V4_URL
# (and optionally IPASN_V6_URL) to be set in your config; see
# cc-lint.defaults.mk for where to find the snapshot URLs. Concatenates the
# IPv4 and IPv6 tables into one TSV that the EMR job and local lint consume.
.PHONY: ipasn-cache
ipasn-cache:
	@test -n "$(IPASN_V4_URL)" || (echo "Set IPASN_V4_URL (and optionally IPASN_V6_URL) to a CAIDA pfx2as snapshot URL; see cc-lint.defaults.mk" && exit 1)
	mkdir -p $(IPASN_CACHE_DIR)
	curl -fsSL "$(IPASN_V4_URL)" | gunzip > $(IPASN_CACHE).tmp
	@if [ -n "$(IPASN_V6_URL)" ]; then curl -fsSL "$(IPASN_V6_URL)" | gunzip >> $(IPASN_CACHE).tmp; fi
	mv $(IPASN_CACHE).tmp $(IPASN_CACHE)
	@echo "Wrote $(IPASN_CACHE)"

.PHONY: wheels
wheels:
	mkdir -p wheels
	docker run --rm --platform linux/amd64 -v $(PWD)/wheels:/output amazonlinux:2023 /bin/bash -c "\
		yum install -y gcc gcc-c++ python3.12-devel python3.12-pip zlib-devel brotli-devel && \
		/usr/bin/python3.12 -m pip wheel --wheel-dir=/output mrjob warcio httplint boto3 requests click python-dateutil"

.PHONY: upload-wheels
upload-wheels: check-s3-config wheels
	aws s3 sync wheels/ $(WHEEL_S3_PATH)

.PHONY: emr-timing
emr-timing: venv
	@test -n "$(EMR_LOG_CLUSTER_ID)" || (echo "Usage: make emr-timing EMR_LOG_CLUSTER_ID=j-..." && exit 1)
	$(VENV)/python -m cc_lint.emr.timing --cluster-id $(EMR_LOG_CLUSTER_ID) --log-dir $(EMR_LOG_DIR)

.PHONY: emr
emr: check-s3-config venv tranco-cache
	$(VENV)/python -m cc_lint.emr.split_paths \
		s3://commoncrawl/crawl-data/$(CRAWL_ID)/warc.paths.gz \
		$(PATHS_PREFIX)$(FULL_RUN_NAME)/ \
		$(MAP_TASKS)
	$(VENV)/python -m cc_lint.emr.job -c $(MRJOB_CONFIG) \
		$(MRJOB_COMMON_ARGS) \
		--jobconf mapreduce.job.reduces=$(REDUCES) \
		--top-sites $(TOP_N) \
		--sample-top-sites $(SAMPLE_TOP_N) \
		--crawl-id $(CRAWL_ID) \
		--record-limit $(RECORD_LIMIT) \
		--output-dir $(OUTPUT_DIR)$(FULL_RUN_NAME)/ \
		$(PATHS_PREFIX)$(FULL_RUN_NAME)/
	mkdir -p results/$(FULL_RUN_NAME)
	aws s3 sync $(OUTPUT_DIR)$(FULL_RUN_NAME)/ results/$(FULL_RUN_NAME)/
	$(VENV)/python -m cc_lint.emr.finalize results/$(FULL_RUN_NAME)/ results/$(FULL_RUN_NAME)/report.html
	@echo "Report generated at results/$(FULL_RUN_NAME)/report.html"

LIMIT ?= 1

.PHONY: test-emr
test-emr: check-s3-config venv tranco-cache
	$(VENV)/python -m cc_lint.emr.split_paths \
		tests/fixtures/warc.paths.txt \
		$(PATHS_PREFIX)$(TEST_RUN_NAME)/ \
		$(TEST_MAP_TASKS) \
		$(LIMIT)
	$(VENV)/python -m cc_lint.emr.job -c $(MRJOB_TEST_CONFIG) \
		$(MRJOB_COMMON_ARGS) \
		--jobconf mapreduce.job.reduces=$(TEST_REDUCES) \
		--top-sites $(LOCAL_TOP_N) \
		--sample-top-sites $(LOCAL_TOP_N) \
		--crawl-id $(CRAWL_ID) \
		--record-limit $(RECORD_LIMIT) \
		--limit $(LIMIT) \
		--output-dir $(OUTPUT_DIR)$(TEST_RUN_NAME)/ \
		$(PATHS_PREFIX)$(TEST_RUN_NAME)/
	mkdir -p results/$(TEST_RUN_NAME)
	aws s3 sync $(OUTPUT_DIR)$(TEST_RUN_NAME)/ results/$(TEST_RUN_NAME)/
	$(VENV)/python -m cc_lint.emr.finalize results/$(TEST_RUN_NAME)/ results/$(TEST_RUN_NAME)/report.html
	@echo "Report generated at results/$(TEST_RUN_NAME)/report.html"

# Re-render a specific results dir: make results/test-xxx/report.html
.PHONY: results/%/report.html
results/%/report.html: venv
	$(VENV)/python -m cc_lint.emr.finalize results/$*/ $@

.PHONY: report
report: venv
	@test -n "$(RESULTS_DIR)" || (echo "Usage: make report RESULTS_DIR=results/test-YYYYMMDD-HHMMSS" && exit 1)
	$(VENV)/python -m cc_lint.emr.finalize $(RESULTS_DIR) $(RESULTS_DIR)/report.html
	@echo "Report generated at $(RESULTS_DIR)/report.html"

include Makefile.pyproject
