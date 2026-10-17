PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: install lint test db init seed pull report serve ui mcp

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check src tests

test: lint
	$(PYTHON) -m pytest

db:
	mkdir -p data
	$(PYTHON) -c "import sqlite3; sqlite3.connect('data/book.sqlite').close()"

init:
	$(PYTHON) -m book_move init

seed: init
	$(PYTHON) -m book_move seed

pull: seed
	$(PYTHON) -m book_move pull --start $(START) --end $(END)

report:
	$(PYTHON) -m book_move report --as-of $(AS_OF)

serve:
	$(PYTHON) -m book_move serve

mcp:
	$(PYTHON) -m book_move mcp

ui:
	cd ui && npm run dev
