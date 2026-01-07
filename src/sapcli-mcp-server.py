"""
Export sapcli commands as MCP tools.
"""

import json
from io import StringIO
from typing import (
    Any,
    Callable,
    NamedTuple,
    Union,
)
from types import SimpleNamespace

from sap import (
    adt,
    errors,
)

import sap.cli
import sap.cli.core
import sap.cli.package

from fastmcp import FastMCP
from fastmcp.tools import Tool
from fastmcp.tools.tool import ToolResult

from sapclimcp.argparsertool import ArgParserTool

# Type aliases for SAP connections and commands
SAPConnectionType = Union[adt.Connection]
CommandType = Callable[[SAPConnectionType, SimpleNamespace], None]

# Connection parameters for MCP tools
# Common parameters required for all connection types
COMMON_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'ashost': {'type': 'string'},
    'client': {'type': 'string'},
    'user': {'type': 'string'},
    'password': {'type': 'string'},
}

# ADT connection specific parameters
ADT_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'http_port': {'type': 'integer'},
    'use_ssl': {'type': 'boolean'},
    'verify_ssl': {'type': 'boolean'},
}

# RFC connection specific parameters
RFC_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'sysnr': {'type': 'string'},
}

# OData connection specific parameters
ODATA_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'http_port': {'type': 'integer'},
    'use_ssl': {'type': 'boolean'},
    'verify_ssl': {'type': 'boolean'},
}

# REST/gCTS connection specific parameters
REST_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'http_port': {'type': 'integer'},
    'use_ssl': {'type': 'boolean'},
    'verify_ssl': {'type': 'boolean'},
}


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


def _run_sapcli_command(conn: SAPConnectionType, command: CommandType, args: SimpleNamespace) -> OperationResult:

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


class SapcliCommandToolError(Exception):
    """Error raised by SapcliCommandTool."""


class SapcliCommandTool(Tool):
    """MCP Tool for executing sapcli commands.

    This tool wraps sapcli commands transformed from ArgParserTool
    and executes them via the appropriate connection type.
    Currently only ADT connections are supported.
    """

    cmdfn: CommandType
    conn_factory: Callable

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the sapcli command with the given arguments.

        Args:
            arguments: Dictionary containing connection parameters and command arguments.

        Returns:
            ToolResult with the command output.

        Raises:
            SapcliCommandToolError: If cmdfn is None or connection type is not ADT.
        """
        if self.cmdfn is None:
            raise SapcliCommandToolError(
                f"Tool '{self.name}' has no command function (cmdfn is None)"
            )

        # Only ADT connections are supported
        # pylint: disable-next=comparison-with-callable
        if self.conn_factory != sap.cli.adt_connection_from_args:
            raise SapcliCommandToolError(
                f"Tool '{self.name}' uses unsupported connection type. "
                "Only ADT connections are currently supported."
            )

        # Extract ADT connection configuration from arguments
        adt_conn_conf = ADTConnectionConfig(
            ASHost=arguments['ashost'],
            HTTP_Port=arguments['http_port'],
            Client=arguments['client'],
            User=arguments['user'],
            Password=arguments['password'],
            UseSSL=arguments['use_ssl'],
            VerifySSL=arguments['verify_ssl']
        )

        # Build command arguments (exclude connection parameters)
        connection_params = {
            'ashost', 'http_port', 'client', 'user', 'password',
            'use_ssl', 'verify_ssl'
        }
        cmd_args = {k: v for k, v in arguments.items() if k not in connection_params}

        result = _run_adt_command(adt_conn_conf, self.cmdfn, SimpleNamespace(**cmd_args))

        return ToolResult(
            structured_content={
                'Success': result.Success,
                'LogMessages': result.LogMessages,
                'Contents': result.Contents
            }
        )

    @classmethod
    def from_argparser_tool(
        cls,
        cmd: ArgParserTool,
        conn_factory: Callable,
        description: str | None = None
    ) -> 'SapcliCommandTool':
        """Create a SapcliCommandTool from an ArgParserTool.

        Args:
            cmd: The ArgParserTool containing command definition.
            conn_factory: The connection factory function for this command.
            description: Optional description for the tool.

        Returns:
            A new SapcliCommandTool instance.

        Raises:
            SapcliCommandToolError: If cmd.cmdfn is None.
        """
        if cmd.cmdfn is None:
            raise SapcliCommandToolError(
                f"Cannot create tool from '{cmd.name}': cmdfn is None"
            )

        # Extract the execute function from cmdfn dict
        execute_fn = cmd.cmdfn.get('execute')
        if execute_fn is None:
            raise SapcliCommandToolError(
                f"Cannot create tool from '{cmd.name}': 'execute' not found in cmdfn"
            )

        return cls(
            name=cmd.name,
            description=description or f"Execute sapcli command: {cmd.name}",
            parameters=cmd.to_mcp_input_schema(),
            cmdfn=execute_fn,
            conn_factory=conn_factory,
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


def _transform_sapcli_commands(server: FastMCP):
    args_tools = ArgParserTool("abap", None)

    # Mapping from connection factory functions to their specific parameters
    conn_factory_to_params = {
        sap.cli.adt_connection_from_args: ADT_CONNECTION_PARAMS,
        sap.cli.rfc_connection_from_args: RFC_CONNECTION_PARAMS,
        sap.cli.gcts_connection_from_args: REST_CONNECTION_PARAMS,
        sap.cli.odata_connection_from_args: ODATA_CONNECTION_PARAMS,
    }

    # Install ArgParser and build Tools definitions
    # The list items returned by sap.cli.get_commands() are tuples
    # where:
    # - the index 0 is a connection factory function
    # - the index 1 is a sapcli command specification
    # Hence the variable conn_factory is a reference to one of the following functions:
    # - ADT - sap.cli.adt_connection_from_args
    # - RFC - sap.cli.rfc_connection_from_args
    # - REST - sap.cli.gcts_connection_from_args
    # - OData - sap.cli.odata_connection_from_args
    for conn_factory, cmd in sap.cli.get_commands():
        cmd_tool = args_tools.add_parser(cmd.name)

        # Set connection factory before install_parser so sub-parsers inherit it
        cmd_tool.conn_factory = conn_factory

        # Add connection parameters before install_parser so sub-parsers inherit them
        cmd_tool.add_properties(COMMON_CONNECTION_PARAMS)

        specific_params = conn_factory_to_params.get(conn_factory)
        if specific_params is not None:
            cmd_tool.add_properties(specific_params)

        # Install parser after adding connection properties and factory
        cmd.install_parser(cmd_tool)

    # pylint: disable-next=fixme
    # TODO: add name transformations such as "abap_gcts_delete" to "abap_gcts_repo_delete"

    for tool_name, cmd_tool in args_tools.tools.items():
        # pylint: disable=protected-access
        if not cmd_tool._parameters or tool_name not in ["abap_package_list", "abap_package_stat"]:
            continue

        print(tool_name)
        input_schema = cmd_tool.to_mcp_input_schema()
        print(json.dumps(input_schema))
        server.add_tool(SapcliCommandTool.from_argparser_tool(cmd_tool, cmd_tool.conn_factory))


if __name__ == "__main__":
    print("# Transformed ArgParser command tool properties")
    _transform_sapcli_commands(mcp)

    print("# FastMCP tool properties")
    # pylint: disable=protected-access
    for k, v in mcp._tool_manager._tools.items():
        # if k not in ["abap_adt_package_list_objects", "abap_adt_package_get_details"]:
        #    continue

        print(k)
        print(json.dumps(v.parameters))
        print(json.dumps(v.output_schema))

    mcp.run(transport="http", host="127.0.0.1", port=8000)
