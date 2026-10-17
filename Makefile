PYTHON ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3)
VENV ?= .venv
VENV_PYTHON = $(VENV)/bin/python

.PHONY: install test test-unit frontend-check run lint docker-build

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.lock

test: test-unit

test-unit:
	$(VENV_PYTHON) -m unittest discover -s . -p 'test_*.py' -v

frontend-check:
	@command -v node >/dev/null || (echo "node is required for frontend-check" >&2; exit 1)
	@tmp=$$(mktemp /tmp/egg-harvester-XXXXXX.js); python3 -c 'from pathlib import Path; print("\n".join((lambda s: s[s.find("<script>")+8:s.rfind("</script>")])(Path(n).read_text()) for n in ("templates/index.html", "templates/doge.html")))' > $$tmp; \
	node --check $$tmp; rm -f $$tmp

run:
	$(VENV_PYTHON) -m uvicorn app:app --host 127.0.0.1 --port 8890

lint:
	$(VENV_PYTHON) -m py_compile app.py api_extensions.py config.py db.py wxgf_decoder.py

docker-build:
	docker build -t egg-harvester:local .
