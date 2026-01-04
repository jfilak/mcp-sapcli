run_flake8:
	flake8 src/sapcli-mcp-server.py

run_pylint:
	pylint src/sapcli-mcp-server.py

run_mypy:
	mypy --config-file=mypy.ini src/sapcli-mcp-server.py

check: run_mypy run_pylint run_flake8
	@echo Checked

test:
	pytest tests/
