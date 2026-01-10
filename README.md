# MCP for sapcli

[sapcli](https://github.com/jfilak/sapcli) is a command-line tool that enables
access to SAP products from scripts and automation pipelines.

This MCP server is build on top of [FastMCP](https://github.com/jlowin/fastmcp)

## Requirements

Python => 3.10

## Installation

First clone sapcli's repository because it has been published as PyPI package
yet:

```bash
git clone https://github.com/jfilak/sapcli
```

Then make update PYTHONPATH to allow Python find the module `sap`:
```bash
export PYTHONPATH=$(pwd)/sapcli
```

Finally clone this MCP server repository, create virtual environment,
and install already packaged dependencies:

```bash
git clone https://github.com/jfilak/mcp-sapcli
cd mcp-sapcli
python3 -m venv ve
source ./ve/bin/activate
pip install fastmcp pydantic pyodata
```

## Usage

To start HTTP server localhost:8000 run the following bash command:

```bash
python3 src/sapcli-mcp-server.py
```
