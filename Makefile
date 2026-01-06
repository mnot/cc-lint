PROJECT = cc-lint
CACHE_DIR = warc

.PHONY: stats.json
stats.json:
	PYTHONPATH=$(VENV) $(VENV)/cc-lint lint --limit 100 --cache-dir $(CACHE_DIR) --paths-file paths.txt > $@

.PHONY: report.html
report.html:
	PYTHONPATH=$(VENV) $(VENV)/cc-lint report --input stats.json --output $@

.PHONY: test
test: test_py

.PHONY: clean
clean: clean_py

.PHONY: lint
lint: lint_py

.PHONY: typecheck
typecheck: typecheck_py

.PHONY: tidy
tidy: tidy_py


include Makefile.pyproject
