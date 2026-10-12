run_flake8:
	flake8 src/sapclimcp/ src/sapcli-mcp-server.py src/sapcli-mcp-client.py start-mcp.py

run_pylint:
	pylint src/sapclimcp/ src/sapcli-mcp-server.py src/sapcli-mcp-client.py start-mcp.py

run_mypy:
	mypy --config-file=mypy.ini src/sapclimcp/ src/sapcli-mcp-server.py src/sapcli-mcp-client.py start-mcp.py

lint: run_mypy run_pylint run_flake8
	@echo Linted

test:
	pytest tests/ --ignore=tests/e2e --cov=sapclimcp --cov-report=term-missing

check: lint test
	@echo Checked
