# Reproduces the project from a clean checkout. Stages are ordered by dependency:
# nothing downstream runs before the data it reads exists.

PYTHON ?= python
export PYTHONPATH := .

.PHONY: all data test results figures robustness clean help

help:
	@echo "make data     - fetch filings and market data, extract tables, bucket the books"
	@echo "make test     - run the test suite"
	@echo "make results  - out-of-sample test, EVE scenarios, run risk"
	@echo "make figures  - build the charts in reports/figures"
	@echo "make robustness - re-run the out-of-sample test across assumption variants"
	@echo "make all      - everything, in order"

all: data test results figures

# The filings are committed under references/, so this re-extracts rather than
# re-downloads unless the market data files are missing.
data:
	$(PYTHON) -m src.data.extract_filings
	$(PYTHON) -m src.data.market_data
	$(PYTHON) -m src.data.buckets

test:
	$(PYTHON) -m pytest tests -q

# The EVE stage prices four balance sheets under eleven curves and is the slow one.
results:
	$(PYTHON) -m src.analysis.validation
	$(PYTHON) -m src.analysis.eve
	$(PYTHON) -m src.analysis.run_risk

figures:
	$(PYTHON) -m src.plots

# Not part of `make all`: it re-runs the out-of-sample test once per variant and the
# report quotes its output rather than depending on it.
robustness:
	$(PYTHON) -m src.analysis.robustness

clean:
	rm -rf data/processed/*.csv reports/figures/*.png
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
