# Backend Makefile.
#
# `make smoke` is the one-command sanity check: it boots an in-process
# FastAPI TestClient for each smoke under app/devtools/ and prints a
# pass/fail table. Exit code is 0 only if every smoke passes.
#
# `make test` is currently aliased to `make smoke` until pytest gets wired up.

.PHONY: smoke test help

help:
	@echo "Targets:"
	@echo "  make smoke   - run every smoke_*.py under app/devtools/"
	@echo "  make test    - alias for 'make smoke'"

smoke:
	python run_smoke.py

test: smoke
