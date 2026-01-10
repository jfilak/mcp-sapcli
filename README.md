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

## Tools

The MCP server automatically converts [sapcli
commands](https://github.com/jfilak/sapcli/blob/master/doc/commands.md) into
MCP tools.  This approach simplifies the MCP server maintenance and makes new
tool exposure super simple. However, by default, only the tools that has been
manually tested are exposed.

The tools uses the following name schema:
  - `abap_<command>_<subcommand>_<?etc ...>`

Note: the prefix abap was not probably the best idea but currently sapcli works
only with SAP ABAP systems.

If you are brave and not scared of possible crashes, start the MCP server with
the command line flag `--experimental`.

```bash
python3 src/sapcli-mcp-server.py --experimental
```

### Verified tools
- [abap\_package\_list](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#list) - list objects belonging to ABAP package hierarchy
- [abap\_package\_stat](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#stat) - provide ABAP package information (aka libc stat)
- [abap\_gcts\_repolist](https://github.com/jfilak/sapcli/blob/master/doc/commands/gcts.md#repolist) - lists gCTS repositories
- [abap\_class\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/class.md#read-1) - return code of ABAP class
- [abap\_aunit\_run](https://github.com/jfilak/sapcli/blob/master/doc/commands/aunit.md#run) - run AUnits on package, class, program, program-include, transport
- [abap\_atc\_run](https://github.com/jfilak/sapcli/blob/master/doc/commands/atc.md#run) - run ATC checks for package, class, program
