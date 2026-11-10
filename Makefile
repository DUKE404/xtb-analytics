PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: run test sample clean

$(VENV)/.ok: requirements.txt
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt
	touch $(VENV)/.ok

run: $(VENV)/.ok
	$(BIN)/streamlit run app/main.py

test: $(VENV)/.ok
	$(BIN)/python -m pytest -q

sample: $(VENV)/.ok
	$(BIN)/python -m data.sample_generator --out data/sample/xtb_sample_export.xlsx

clean:
	rm -rf $(VENV) .cache .pytest_cache
