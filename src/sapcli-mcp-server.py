"""
Export sapcli commands as MCP tools.
"""

from io import StringIO
from typing import (
    NamedTuple,
)
from types import SimpleNamespace

from sap import (
    adt,
    errors,
)

import sap.cli.core
import sap.cli.package

from fastmcp import FastMCP

from sapclimcp.argparsertool import (
    ConnectionType,
    CommandType,
    ArgParserTool,
)


mcp = FastMCP(
    name="sapcli",
    instructions="""
        This server connects to various SAP products and allows you to read and
        write their contents.
        For ABAP functions you can use features that requires HTTP or RFC.
        Both HTTP and RFC requires:
        - ASHOST   : Application Server host name
        - CLIENT   : ABAP Client (3 upper case letters+digits)
        - USER     : user name (case insensitive)
        - PASSWORD : password (case sensitive)
        For HTTP features, you must provide:
        - HTTPPORT   : the HTTP port
        - USE_SSL    : true for HTTPS; false for naked HTTP
        - VERIFY_SSL : true to check ABAP server cert validity; otherwise false
        For RFC features, you must have the PyRFC library with NWRFC SDK
        installed on your machine and then you must provide:
        - SYSNR : 2 digits from 00 to 99 which will be translated to port
    """
)


class OutputBuffer(sap.cli.core.PrintConsole):
    """Capture output of sapcli commands in memory buffer.
    """

    def __init__(self):
        self.std_output = StringIO()
        self.err_output = StringIO()

        super().__init__(out_file=self.std_output, err_file=self.err_output)

    @property
    def capout(self) -> str:
        """Captured standard output
        """

        return self.std_output.getvalue()

    @property
    def caperr(self) -> str:
        """Captured error output
        """

        return self.err_output.getvalue()

    def reset(self) -> None:
        """Reset captured contents
        """

        self.std_output.truncate(0)
        self.std_output.seek(0)
        self.err_output.truncate(0)
        self.std_output.seek(0)


class OperationResult(NamedTuple):
    """MCP tool results
    """

    Success: bool
    LogMessages: list[str]
    Contents: str


class ADTConnectionConfig(NamedTuple):
    """Crate for ADT connection configuration
    """

    ASHost: str
    HTTP_Port: int
    Client: str
    User: str
    Password: str
    UseSSL: bool
    VerifySSL: bool


def _new_adt_connection(adt_conn_conf: ADTConnectionConfig) -> adt.Connection:
    return adt.Connection(
        adt_conn_conf.ASHost,
        adt_conn_conf.Client,
        adt_conn_conf.User,
        adt_conn_conf.Password,
        port=adt_conn_conf.HTTP_Port,
        ssl=adt_conn_conf.UseSSL,
        verify=adt_conn_conf.VerifySSL
    )


def _run_adt_command(adt_conn_conf: ADTConnectionConfig, command: CommandType, args: SimpleNamespace):
    try:
        adt_conn = _new_adt_connection(adt_conn_conf)
    except errors.SAPCliError as ex:
        return OperationResult(
                Success=False,
                LogMessages=['Could not connect to ADT Server', str(ex)],
                Contents=""
            )

    return _run_sapcli_command(adt_conn, command, args)


def _run_sapcli_command(conn: ConnectionType, command: CommandType, args: SimpleNamespace) -> OperationResult:

    output_buffer = OutputBuffer()

    sap.cli.core.set_console(output_buffer)

    try:
        command(conn, args)
    except errors.SAPCliError as ex:
        return OperationResult(
                Success=False,
                LogMessages=[str(ex), output_buffer.caperr],
                Contents=output_buffer.capout
            )

    return OperationResult(
            Success=True,
            LogMessages=[output_buffer.caperr],
            Contents=output_buffer.capout
        )


def _adt_connection_test(conn, _):
    console = sap.cli.core.get_console()
    conn.collection_types.items()
    console.printout("ADT connection works!")


@mcp.tool
def abap_adt_connection_test(
        ashost: str,
        http_port: int,
        client: str,
        user: str,
        password: str,
        use_ssl: bool,
        verify_ssl: bool) -> OperationResult:
    """Test given ADT connection configuration by fetching ADT configuration
       from the target ABAP sytem.
    """

    adt_conn_conf = ADTConnectionConfig(
        ashost,
        http_port,
        client,
        user,
        password,
        use_ssl,
        verify_ssl
    )

    return _run_adt_command(
        adt_conn_conf,
        _adt_connection_test,
        SimpleNamespace()
    )


@mcp.tool
def abap_adt_package_get_details(
        ashost: str,
        http_port: int,
        client: str,
        user: str,
        password: str,
        use_ssl: bool,
        verify_ssl: bool,
        name: str) -> OperationResult:
    """Return ABAP package details such as compoentn and activation status.
    """

    adt_conn_conf = ADTConnectionConfig(
        ashost,
        http_port,
        client,
        user,
        password,
        use_ssl,
        verify_ssl
    )

    return _run_adt_command(
        adt_conn_conf,
        sap.cli.package.stat,
        SimpleNamespace(
            name=name
        )
    )


@mcp.tool
def abap_adt_package_list_objects(
        ashost: str,
        http_port: int,
        client: str,
        user: str,
        password: str,
        use_ssl: bool,
        verify_ssl: bool,
        name: str,
        recursive: bool = False) -> OperationResult:
    """List ABAP objects belonging the give ABAP development package hierarchy.
    """

    adt_conn_conf = ADTConnectionConfig(
        ashost,
        http_port,
        client,
        user,
        password,
        use_ssl,
        verify_ssl
    )

    return _run_adt_command(
        adt_conn_conf,
        sap.cli.package.list_package,
        SimpleNamespace(
            name=name,
            recursive=recursive
        )
    )


if __name__ == "__main__":
    args_tools = ArgParserTool("abap", None)
    # Install ArgParser and build Tools definitions
    for conn, cmd in sap.cli.get_commands():
        cmd_tool = args_tools.add_parser(cmd.name)
        cmd.install_parser(cmd_tool)

    # TODO: add name transformations such as "abap_gcts_delete" to "abap_gcts_repo_delete"

    for name, cmd in args_tools.tools.items():
        if not cmd._parameters:
            continue

        print(name)
        print('  parameters:')
        for k, v in cmd._parameters.items():
            print('   - ', k, v)

        input_schema = cmd.to_mcp_input_schema()
        print(json.dumps(input_schema))

    for k, v in mcp._tool_manager._tools.items():
        print(k, str(v))

    mcp.run(transport="http", host="127.0.0.1", port=8000)
