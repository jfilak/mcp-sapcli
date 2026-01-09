run_flake8:
	flake8 src/sapcli-mcp-server.py

run_pylint:
	pylint src/sapcli-mcp-server.py

run_mypy:
	mypy --config-file=mypy.ini src/sapcli-mcp-server.py

lint: run_mypy run_pylint run_flake8
	@echo Linted

test:
	PYTHONPATH=$$(pwd)/src:$$PYTHONPATH pytest tests/

check: lint test
	@echo Checked
