# MCP for sapcli

[sapcli](https://github.com/jfilak/sapcli) is a command-line tool that enables
access to SAP products from scripts and automation pipelines.

This MCP server is built on top of [FastMCP](https://github.com/jlowin/fastmcp)

## Requirements

Python >= 3.12

## Installation

```bash
git clone https://github.com/jfilak/mcp-sapcli
cd mcp-sapcli
pip install -e .
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install -e .
```

This installs the `sapcli-mcp` command and all dependencies (including sapcli from git).

### Optional: OS keyring support

To store credentials in the OS keyring (Windows Credential Manager, macOS
Keychain, Linux Secret Service), install the optional `[keyring]` extra:

```bash
pip install -e .[keyring]
# or:
uv pip install -e .[keyring]
```

Without this extra, only `$ENV_VAR` and literal credential modes work; the
`keyring:` prefix and the `sapcli-mcp credential` CLI subcommands raise a
clear install hint.

## Usage

### With server-side connection management (recommended)

Create a config file (`sapcli-mcp.json`) with your SAP system definitions:

```json
{
  "systems": {
    "DEV": {
      "ashost": "dev.sap.example.com",
      "port": 443,
      "client": "001",
      "ssl": true,
      "verify": false,
      "auth": "cookie",
      "cookie": "keyring:DEV"
    }
  },
  "default_system": "DEV"
}
```

Credential fields (`user`, `password`, `cookie`) support three resolution modes,
all resolved lazily at connection time (not at startup):

| Syntax | Resolves via | Example |
|--------|-------------|---------|
| `keyring:<key>` | OS keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service) — requires the `[keyring]` install extra | `"keyring:DEV"` |
| `$ENV_VAR` | Environment variable | `"$SAP_COOKIE_DEV"` |
| literal | Used as-is | `"Welcome1!"` |

Manage keyring credentials with the built-in CLI (requires the
`[keyring]` extra — see [Optional: OS keyring support](#optional-os-keyring-support)):

```bash
# A session cookie is bearer-equivalent — treat it like a password. Provide the
# value via stdin (read from a file, or typed at the prompt) so it stays out of
# BOTH the process list (`ps`) and your shell history:
sapcli-mcp credential set DEV < cookie.txt   # value read from a file
sapcli-mcp credential set DEV                 # or omit it and type/paste at the prompt
sapcli-mcp credential get DEV
sapcli-mcp credential delete DEV

# Avoid passing the value as a command-line argument — it lands in BOTH `ps`
# and your shell history:
#   sapcli-mcp credential set DEV "SAP_SESSIONID=..."
```

Auth types:
- `"basic"` (default) — uses `user` and `password` fields
- `"cookie"` — uses `cookie` field (for SSO environments)

A basic-auth system — both `user` and `password` reference fields are required at
config load; their *resolved* values are validated at first connection (a
`$ENV_VAR` or `keyring:` ref that resolves to empty fails there, not at load):

```json
{
  "systems": {
    "PRD": {
      "ashost": "prd.sap.example.com",
      "client": "100",
      "auth": "basic",
      "user": "$SAP_USER_PRD",
      "password": "keyring:PRD"
    }
  },
  "default_system": "PRD"
}
```

Start the server in **stdio** mode (for Claude Code / MCP clients):

```bash
sapcli-mcp --stdio --config sapcli-mcp.json
```

With this setup, **credentials are never visible to the LLM**. Tools only
expose business parameters (e.g., class name, program name) and an optional
`system` selector when multiple systems are configured.

### Without config (legacy mode)

To start HTTP server on localhost:8000 without server-side connection management:

```bash
sapcli-mcp
```

In this mode, every tool call requires connection parameters (ashost, client,
user, password, etc.) to be provided by the caller.

You can customize the host and port with command line arguments (HTTP mode):

```bash
sapcli-mcp --host 0.0.0.0 --port 9000
```

| Argument         | Default     | Description                                        |
|------------------|-------------|----------------------------------------------------|
| `--config`       | (none)      | Path to JSON config file (env: `SAPCLI_MCP_CONFIG`)|
| `--stdio`        | (off)       | Run in stdio transport mode                        |
| `--experimental` | (off)       | Expose all sapcli commands, not just verified ones  |
| `--host`         | `127.0.0.1` | Host address to bind to (HTTP mode only)           |
| `--port`         | `8000`      | Port to listen on (HTTP mode only)                 |
| `--log-level`    | (none)      | Logging level to stderr (env: `SAPCLI_MCP_LOG_LEVEL`) |

> **Note on keyring scanner output.** When the server starts with a config
> that references `keyring:<key>` credentials but the `[keyring]` extra is
> not installed, a count-only WARNING fires (`Config references N keyring
> credential(s)…`). The list of affected `<system>.<field>` paths is logged
> at DEBUG level — pass `--log-level=DEBUG` (or `SAPCLI_MCP_LOG_LEVEL=DEBUG`)
> to see the full detail. The split is intentional: the WARNING surfaces
> reliably (Python's `lastResort` handler) while the per-field detail stays
> opt-in to avoid leaking the credential layout to MCP clients in stdio mode.

### Legacy invocation

The old invocation still works for backwards compatibility (requires the
package to be installed or `PYTHONPATH` set to `src/`):

```bash
python src/sapcli-mcp-server.py --stdio --experimental --config sapcli-mcp.json
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
sapcli-mcp --experimental
```

### Implementation Details
- MCP tool definitions are automatically generated from Python's ArgParser definitions in the module sap.cli
- every sapcli command is supposed to use sap.cli.core.PrintConsole to print out data (no direct output is allowed)
- MCP server replaces the default sap.cli.core.PrintConsole with it is own buffer based implementation and returns the captured output
- the sapcli functions handling commands take the PrintConsole object from the given args under the member console_factory

### Verified tools
- [abap\_package\_list](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#list) - list objects belonging to ABAP package hierarchy
- [abap\_package\_stat](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#stat) - provide ABAP package information (aka libc stat)
- [abap\_package\_create](https://github.com/jfilak/sapcli/blob/master/doc/commands/package.md#create) - create ABAP package

- [abap\_program\_create](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#create) - create ABAP Program
- [abap\_program\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#read) - read source code of ABAP Program
- [abap\_program\_write](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#write) - write source code of ABAP Program
- [abap\_program\_activate](https://github.com/jfilak/sapcli/blob/master/doc/commands/program.md#activate) - activate ABAP Program

- [abap\_class\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/class.md#read-1) - read source code of ABAP class
- [abap\_class\_write](https://github.com/jfilak/sapcli/blob/master/doc/commands/class.md#write) - write source code of ABAP class
- [abap\_include\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/include.md#read) - read source code of ABAP include
- [abap\_include\_write](https://github.com/jfilak/sapcli/blob/master/doc/commands/include.md#write) - write source code of ABAP include
- [abap\_interface\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/interface.md#read) - read source code of ABAP interface
- [abap\_interface\_write](https://github.com/jfilak/sapcli/blob/master/doc/commands/interface.md#write) - write source code of ABAP interface
- [abap\_ddl\_read](https://github.com/jfilak/sapcli/blob/master/doc/commands/ddl.md#read) - read source code of CDS view
- [abap\_ddl\_write](https://github.com/jfilak/sapcli/blob/master/doc/commands/ddl.md#write) - write source code of CDS view
- [abap\_aunit\_run](https://github.com/jfilak/sapcli/blob/master/doc/commands/aunit.md#run) - run AUnits on package, class, program, program-include, transport
- [abap\_atc\_run](https://github.com/jfilak/sapcli/blob/master/doc/commands/atc.md#run) - run ATC checks for package, class, program
- [abap\_gcts\_repolist](https://github.com/jfilak/sapcli/blob/master/doc/commands/gcts.md#repolist) - lists gCTS repositories

### Experimental tools

The following tools are available when the server is started with `--experimental`.
They have been automatically generated from sapcli commands. Tools marked with
"sandbox-tested" have been verified by an automated agent on a sandbox system —
they are functional but not yet fully production-tested.

#### General

- abap\_abap\_systeminfo — sandbox-tested
- abap\_abap\_find — sandbox-tested
- abap\_abap\_run — sandbox-tested (works for short code; complex code may timeout)
- abap\_datapreview\_osql — bug (writes results to stdout instead of returning them, so the tool yields empty output; raises UnicodeEncodeError on non-ASCII data and is unusable over stdio transport; sapcli upstream)
- abap\_activation\_inactiveobjects\_list — sandbox-tested
- abap\_adt\_collections — sandbox-tested

#### Programs & Includes

- abap\_program\_delete — sandbox-tested
- abap\_program\_whereused — sandbox-tested
- abap\_include\_attributes — sandbox-tested
- abap\_include\_create — sandbox-tested
- abap\_include\_activate — sandbox-tested
- abap\_include\_delete — sandbox-tested
- abap\_include\_whereused — sandbox-tested

#### Classes & Interfaces

- abap\_class\_attributes — sandbox-tested
- abap\_class\_create — sandbox-tested
- abap\_class\_activate — sandbox-tested
- abap\_class\_execute — sandbox-tested (output not captured)
- abap\_class\_delete — sandbox-tested
- abap\_class\_whereused — sandbox-tested
- abap\_interface\_create — sandbox-tested
- abap\_interface\_activate — sandbox-tested
- abap\_interface\_delete — sandbox-tested
- abap\_interface\_whereused — sandbox-tested

#### CDS / DDL / DCL / BDEF

- abap\_ddl\_create — sandbox-tested
- abap\_ddl\_activate — sandbox-tested
- abap\_ddl\_delete — sandbox-tested
- abap\_ddl\_whereused — sandbox-tested
- abap\_ddl\_apistate\_list — sandbox-tested
- abap\_ddl\_apistate\_set
- abap\_dcl\_create — sandbox-tested
- abap\_dcl\_read — sandbox-tested
- abap\_dcl\_write — sandbox-tested
- abap\_dcl\_activate — sandbox-tested
- abap\_dcl\_delete — sandbox-tested
- abap\_dcl\_whereused — sandbox-tested
- abap\_bdef\_create — sandbox-tested
- abap\_bdef\_read — sandbox-tested
- abap\_bdef\_write — sandbox-tested
- abap\_bdef\_activate — sandbox-tested
- abap\_bdef\_delete — sandbox-tested
- abap\_bdef\_whereused — sandbox-tested

#### Function Groups & Modules

- abap\_functiongroup\_create — sandbox-tested
- abap\_functiongroup\_read — sandbox-tested
- abap\_functiongroup\_write
- abap\_functiongroup\_activate
- abap\_functiongroup\_delete — sandbox-tested
- abap\_functiongroup\_whereused — sandbox-tested
- abap\_functiongroup\_include\_create
- abap\_functiongroup\_include\_read
- abap\_functiongroup\_include\_write
- abap\_functiongroup\_include\_activate
- abap\_functiongroup\_include\_delete
- abap\_functiongroup\_include\_whereused — sandbox-tested
- abap\_functionmodule\_chattr
- abap\_functionmodule\_create — sandbox-tested
- abap\_functionmodule\_read — sandbox-tested
- abap\_functionmodule\_write — sandbox-tested
- abap\_functionmodule\_activate — sandbox-tested
- abap\_functionmodule\_delete — sandbox-tested
- abap\_functionmodule\_whereused — sandbox-tested

#### Dictionary Objects

- abap\_table\_create — sandbox-tested
- abap\_table\_read — sandbox-tested
- abap\_table\_write — sandbox-tested
- abap\_table\_activate — sandbox-tested
- abap\_table\_delete — sandbox-tested
- abap\_table\_whereused — sandbox-tested
- abap\_structure\_create — sandbox-tested
- abap\_structure\_read — sandbox-tested
- abap\_structure\_write — sandbox-tested
- abap\_structure\_activate — sandbox-tested
- abap\_structure\_delete — sandbox-tested
- abap\_structure\_whereused — sandbox-tested
- abap\_dataelement\_define
- abap\_dataelement\_create — sandbox-tested
- abap\_dataelement\_read — sandbox-tested
- abap\_dataelement\_write
- abap\_dataelement\_activate
- abap\_dataelement\_delete — sandbox-tested
- abap\_dataelement\_whereused — sandbox-tested
- abap\_domain\_create — not supported on this system version
- abap\_domain\_read — sandbox-tested
- abap\_domain\_write
- abap\_domain\_activate
- abap\_domain\_delete — untested (create not supported on sandbox)
- abap\_domain\_whereused — sandbox-tested

#### Transactions & Authorization Fields

- abap\_transaction\_create — sandbox-tested
- abap\_transaction\_read — sandbox-tested
- abap\_transaction\_write
- abap\_transaction\_activate
- abap\_transaction\_delete — sandbox-tested
- abap\_transaction\_whereused — sandbox-tested
- abap\_authorizationfield\_create — placeholder (raises "not implemented yet")
- abap\_authorizationfield\_read — sandbox-tested
- abap\_authorizationfield\_write — placeholder (raises "not implemented yet")
- abap\_authorizationfield\_activate — sandbox-tested
- abap\_authorizationfield\_delete — placeholder (raises "not implemented yet")
- abap\_authorizationfield\_whereused — sandbox-tested

#### Packages

- abap\_package\_check — sandbox-tested
- abap\_package\_activate — sandbox-tested
- abap\_package\_delete

#### ATC

- abap\_atc\_customizing — sandbox-tested
- abap\_atc\_profile\_list — sandbox-tested
- abap\_atc\_profile\_dump

#### CTS (Change & Transport System)

- abap\_cts\_create
- abap\_cts\_release
- abap\_cts\_delete
- abap\_cts\_reassign
- abap\_cts\_list — sandbox-tested

#### BAdI & Feature Toggles

- abap\_badi — sandbox-tested
- abap\_badi\_list — sandbox-tested
- abap\_badi\_set-active
- abap\_featuretoggle\_state — sandbox-tested
- abap\_featuretoggle\_on — sandbox-tested (requires transport system)
- abap\_featuretoggle\_off — sandbox-tested (requires transport system)

#### RAP (RESTful ABAP Programming)

- abap\_rap\_binding\_publish
- abap\_rap\_definition\_activate

#### Checkout & Checkin

- abap\_checkout\_class
- abap\_checkout\_program
- abap\_checkout\_interface
- abap\_checkout\_function\_group
- abap\_checkout\_package
- abap\_checkin\_package

#### abapGit

- abap\_abapgit\_link
- abap\_abapgit\_pull

#### gCTS

- abap\_gcts\_clone
- abap\_gcts\_config — sandbox-tested
- abap\_gcts\_delete
- abap\_gcts\_checkout
- abap\_gcts\_log — sandbox-tested
- abap\_gcts\_pull
- abap\_gcts\_push
- abap\_gcts\_commit
- abap\_gcts\_user\_get-credentials
- abap\_gcts\_user\_set-credentials
- abap\_gcts\_user\_delete-credentials
- abap\_gcts\_repo\_set-url
- abap\_gcts\_repo\_set-role-target
- abap\_gcts\_repo\_set-role-source
- abap\_gcts\_repo\_activities — sandbox-tested
- abap\_gcts\_repo\_messages — sandbox-tested
- abap\_gcts\_repo\_objects — sandbox-tested
- abap\_gcts\_repo\_tasks — bug (crashes on empty task list, sapcli upstream)
- abap\_gcts\_repo\_property\_get — sandbox-tested
- abap\_gcts\_repo\_property\_set
- abap\_gcts\_repo\_branch\_create
- abap\_gcts\_repo\_branch\_delete
- abap\_gcts\_repo\_branch\_list — sandbox-tested
- abap\_gcts\_repo\_branch\_update\_filesystem
- abap\_gcts\_system\_config\_get — bug (KeyError 'value' in sapcli)
- abap\_gcts\_system\_config\_list
- abap\_gcts\_system\_config\_set
- abap\_gcts\_system\_config\_unset
- abap\_gcts\_task\_info
- abap\_gcts\_task\_list
- abap\_gcts\_task\_delete

#### RFC Tools (require PyRFC + NWRFC SDK)

- abap\_startrfc
- abap\_user\_details
- abap\_user\_create
- abap\_user\_change
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

#### BSP & FLP

- abap\_bsp\_upload
- abap\_bsp\_stat
- abap\_bsp\_delete
- abap\_flp\_init

#### Server Configuration

> **Note:** These tools manage the local sapcli config file and do not require a
> SAP connection. Currently broken — the server incorrectly requires a connection
> type for these commands.

- abap\_config\_view
- abap\_config\_current-context
- abap\_config\_merge
- abap\_config\_set-connection
- abap\_config\_set-context
- abap\_config\_set-user
- abap\_config\_get-connections
- abap\_config\_get-contexts
- abap\_config\_get-users
- abap\_config\_use-context
- abap\_config\_delete-connection
- abap\_config\_delete-context
- abap\_config\_delete-user
