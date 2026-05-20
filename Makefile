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
	@echo "  make venv          Create or update the local development environment"
	@echo "  make test          Run fast unit tests"
	@echo "  make tidy          Format Python code"
	@echo "  make typecheck     Run mypy over package and tests"
	@echo "  make lint          Run pylint over package and tests"
	@echo "  make stats.json    Run a local lint against paths.txt"
	@echo "  make report.html   Render report.html from stats.json"
	@echo "  make test-emr      Run an EMR smoke test"
	@echo "  make emr           Run the full EMR analysis"
	@echo "  make report RESULTS_DIR=results/...  Re-render saved EMR reports"
	@echo "  make tranco-cache  Ensure the Tranco top-sites CSV is downloaded"
	@echo "  make wheels        Build EMR dependency wheels"
	@echo "  make upload-wheels Build and upload EMR dependency wheels"
	@echo "  make show-config   Print effective make configuration"
	@echo "  make clean         Remove local generated scratch artifacts and venv"
	@echo ""
	@echo "Configuration:"
	@echo "  cp cc-lint.example.mk cc-lint.mk"
	@echo "  make CONFIG=/path/to/config.mk show-config"

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

.PHONY: stats.json
stats.json:
	PYTHONPATH=$(VENV) $(VENV)/cc-lint lint --limit 100 --cache-dir $(CACHE_DIR) --paths-file paths.txt --top-sites $(LOCAL_TOP_N) > $@

.PHONY: report.html
report.html:
	PYTHONPATH=$(VENV) $(VENV)/cc-lint report --input stats.json --output $@

.PHONY: test
test: test_py

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

MRJOB_COMMON_ARGS = \
	-r emr \
	$(MRJOB_BOOTSTRAP_ARGS) \
	--files "$(TRANCO_CACHE)\#top-1m-sites.csv" \
	--cleanup $(MRJOB_CLEANUP) \
	--no-read-logs --no-cat-output \
	--tranco-path top-1m-sites.csv

RESULTS_DIR ?=

.PHONY: tranco-cache
tranco-cache: venv
	$(VENV)/python -c "from cc_lint.top_sites import get_top_sites_path; get_top_sites_path('$(TRANCO_CACHE_DIR)')"

.PHONY: wheels
wheels:
	mkdir -p wheels
	docker run --rm --platform linux/amd64 -v $(PWD)/wheels:/output amazonlinux:2023 /bin/bash -c "\
		yum install -y gcc gcc-c++ python3.12-devel python3.12-pip zlib-devel brotli-devel && \
		/usr/bin/python3.12 -m pip wheel --wheel-dir=/output mrjob warcio httplint boto3 requests click python-dateutil"

.PHONY: upload-wheels
upload-wheels: check-s3-config wheels
	aws s3 sync wheels/ $(WHEEL_S3_PATH)

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
