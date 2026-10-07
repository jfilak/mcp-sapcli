# MCP for sapcli

[sapcli](https://github.com/jfilak/sapcli) is a command-line tool that enables
access to SAP products from scripts and automation pipelines.

This MCP server is build on top of [FastMCP](https://github.com/jlowin/fastmcp)

## Requirements

Python => 3.10

## Installation

First clone sapcli's repository because it is not yet published as PyPI package:

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

To start HTTP server on localhost:8000 run the following bash command:

```bash
python3 src/sapcli-mcp-server.py
```

You can customize the host and port with command line arguments:

```bash
python3 src/sapcli-mcp-server.py --host 0.0.0.0 --port 9000
```

| Argument | Default     | Description                |
|----------|-------------|----------------------------|
| `--host` | `127.0.0.1` | Host address to bind to   |
| `--port` | `8000`      | Port to listen on         |

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

### Implementation Details
- MCP tool definitions are automatically generated from Python's ArgParser definitions in the module sap.cli
- every sapcli command is supposed to use sap.cli.core.PrintConsole to print out data (no direct output is allowed)
- MCP server replaces the default sap.cli.core.PrintConsole with it is own buffer based implementation and returns the captured output
- the sapcli functions handling commands take the PrintConsole object from the given args under the member console_factory

### Verified tools
- [abap\_package\_list](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#list) - list objects belonging to ABAP package hierarchy
- [abap\_package\_stat](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#stat) - provide ABAP package information (aka libc stat)
- [abap\_package\_create](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#create) - provide ABAP package information (aka libc stat)

- [abap\_program\_create](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#create) - create ABAP Program
- [abap\_program\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#read) - return code of ABAP Program
- [abap\_program\_activate](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#activate) - activate ABAP Program

- [abap\_class\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/class.md#read-1) - return code of ABAP class
- [abap\_ddl\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/ddl.md#read) - return code of CDS view
- [abap\_aunit\_run](https://github.com/jfilak/sapcli/blob/master/doc/commands/aunit.md#run) - run AUnits on package, class, program, program-include, transport
- [abap\_atc\_run](https://github.com/jfilak/sapcli/blob/master/doc/commands/atc.md#run) - run ATC checks for package, class, program
- [abap\_gcts\_repolist](https://github.com/jfilak/sapcli/blob/master/doc/commands/gcts.md#repolist) - lists gCTS repositories

### Experimental tools

The following tools are available when the server is started with `--experimental`.
They have been automatically generated from sapcli commands but have not been
manually verified yet.

- abap\_include\_attributes
- abap\_include\_create
- abap\_include\_read
- abap\_include\_activate
- abap\_interface\_create
- abap\_interface\_read
- abap\_interface\_activate
- abap\_class\_attributes
- abap\_class\_execute
- abap\_class\_create
- abap\_class\_activate
- abap\_ddl\_create
- abap\_ddl\_activate
- abap\_dcl\_create
- abap\_dcl\_read
- abap\_dcl\_write
- abap\_dcl\_activate
- abap\_bdef\_create
- abap\_bdef\_read
- abap\_bdef\_write
- abap\_bdef\_activate
- abap\_functiongroup\_create
- abap\_functiongroup\_read
- abap\_functiongroup\_write
- abap\_functiongroup\_activate
- abap\_functiongroup\_include\_create
- abap\_functiongroup\_include\_read
- abap\_functiongroup\_include\_write
- abap\_functiongroup\_include\_activate
- abap\_functionmodule\_chattr
- abap\_functionmodule\_create
- abap\_functionmodule\_read
- abap\_functionmodule\_write
- abap\_functionmodule\_activate
- abap\_atc\_customizing
- abap\_atc\_profile\_list
- abap\_atc\_profile\_dump
- abap\_datapreview\_osql
- abap\_package\_check
- abap\_cts\_create
- abap\_cts\_release
- abap\_cts\_delete
- abap\_cts\_reassign
- abap\_cts\_list
- abap\_checkout\_class
- abap\_checkout\_program
- abap\_checkout\_interface
- abap\_checkout\_function\_group
- abap\_checkout\_package
- abap\_activation\_inactiveobjects\_list
- abap\_adt\_collections
- abap\_abapgit\_link
- abap\_abapgit\_pull
- abap\_rap\_binding\_publish
- abap\_rap\_definition\_activate
- abap\_table\_create
- abap\_table\_read
- abap\_table\_write
- abap\_table\_activate
- abap\_structure\_create
- abap\_structure\_read
- abap\_structure\_write
- abap\_structure\_activate
- abap\_dataelement\_define
- abap\_dataelement\_create
- abap\_dataelement\_read
- abap\_dataelement\_write
- abap\_dataelement\_activate
- abap\_checkin
- abap\_badi
- abap\_badi\_list
- abap\_badi\_set-active
- abap\_featuretoggle\_state
- abap\_featuretoggle\_on
- abap\_featuretoggle\_off
- abap\_gcts\_clone
- abap\_gcts\_config
- abap\_gcts\_delete
- abap\_gcts\_checkout
- abap\_gcts\_log
- abap\_gcts\_pull
- abap\_gcts\_commit
- abap\_gcts\_user\_get-credentials
- abap\_gcts\_user\_set-credentials
- abap\_gcts\_user\_delete-credentials
- abap\_gcts\_repo\_set-url
- abap\_gcts\_repo\_set-role-target
- abap\_gcts\_repo\_set-role-source
- abap\_gcts\_repo\_activities
- abap\_gcts\_repo\_messages
- abap\_gcts\_repo\_objects
- abap\_gcts\_repo\_property\_get
- abap\_gcts\_repo\_property\_set
- abap\_gcts\_repo\_branch\_create
- abap\_gcts\_repo\_branch\_delete
- abap\_gcts\_repo\_branch\_list
- abap\_gcts\_repo\_branch\_update\_filesystem
- abap\_gcts\_system\_config\_get
- abap\_gcts\_system\_config\_list
- abap\_gcts\_system\_config\_set
- abap\_gcts\_system\_config\_unset
- abap\_gcts\_task\_info
- abap\_gcts\_task\_list
- abap\_gcts\_task\_delete
- abap\_startrfc
- abap\_strust\_list
- abap\_strust\_createpse
- abap\_strust\_createidentity
- abap\_strust\_removepse
- abap\_strust\_getcsr
- abap\_strust\_putpkc
- abap\_strust\_upload
- abap\_strust\_putcertificate
- abap\_strust\_getowncert
- abap\_strust\_listcertificates
- abap\_strust\_dumpcertificates
- abap\_user\_details
- abap\_user\_create
- abap\_user\_change
- abap\_bsp\_upload
- abap\_bsp\_stat
- abap\_bsp\_delete
- abap\_flp\_init
