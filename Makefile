PY ?= python3

.PHONY: test check web rails
test:          ## run the unit tests (needs pytest: pip install -r requirements-dev.txt)
	$(PY) -m pytest tests -q
check:         ## verify the install without Hermes
	$(PY) . check
rails:         ## render the latest task from the bundled sample ledger
	$(PY) . rails
web:           ## serve the web UI on http://127.0.0.1:8877
	$(PY) . web --open
