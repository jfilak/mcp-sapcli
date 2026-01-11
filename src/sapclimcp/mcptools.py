"""
MCP tool classes and helpers for sapcli commands.
"""

import logging
from io import StringIO
from typing import (
    Any,
    Callable,
    ClassVar,
    FrozenSet,
    Generic,
    NamedTuple,
    Union,
)
from dataclasses import dataclass
from types import SimpleNamespace
from typing_extensions import TypeVar

from pydantic import TypeAdapter

from sap import (
    adt,
    errors,
)

import sap.cli
import sap.cli.core

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from fastmcp.tools.tool import ToolResult

from sapclimcp import argparsertool
from sapclimcp.argparsertool import ArgParserTool

_LOGGER = logging.getLogger(__name__)

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
    'port': {'type': 'integer'},
    'ssl': {'type': 'boolean'},
    'verify': {'type': 'boolean'},
}

# RFC connection specific parameters
RFC_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'sysnr': {'type': 'string'},
}

# OData connection specific parameters
ODATA_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'port': {'type': 'integer'},
    'ssl': {'type': 'boolean'},
    'verify': {'type': 'boolean'},
}

# REST/gCTS connection specific parameters
GCTS_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    'port': {'type': 'integer'},
    'ssl': {'type': 'boolean'},
    'verify': {'type': 'boolean'},
}


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
        self.err_output.seek(0)


class OperationResult(NamedTuple):
    """MCP tool results
    """

    Success: bool
    LogMessages: list[str]
    Contents: str


def _run_adt_command(args: SimpleNamespace, command: CommandType):
    try:
        adt_conn = sap.cli.adt_connection_from_args(args)
    except errors.SAPCliError as ex:
        return OperationResult(
                Success=False,
                LogMessages=['Could not connect to ADT Server', str(ex)],
                Contents=""
            )

    return _run_sapcli_command(command, adt_conn, args)


def _run_gcts_command(
        args: SimpleNamespace,
        command: CommandType,
) -> OperationResult:
    try:
        gcts_conn = sap.cli.gcts_connection_from_args(args)
    except errors.SAPCliError as ex:
        return OperationResult(
                Success=False,
                LogMessages=['Could not connect to ABAP HTTP Server', str(ex)],
                Contents=""
            )

    return _run_sapcli_command(command, gcts_conn, args)


def _run_sapcli_command(command: CommandType, conn: SAPConnectionType, args: SimpleNamespace) -> OperationResult:

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


T = TypeVar("T", default=Any)


@dataclass
class _WrappedResult(Generic[T]):
    """Generic wrapper for non-object return types."""

    result: T


class SapcliCommandToolError(ToolError):
    """Error raised by SapcliCommandTool."""


class SapcliCommandTool(Tool):
    """MCP Tool for executing sapcli commands.

    This tool wraps sapcli commands transformed from ArgParserTool
    and executes them via the appropriate connection type.
    Supported connection types: ADT, gCTS.
    """

    # WTF is this?
    model_config = {'arbitrary_types_allowed': True}

    arg_tool: ArgParserTool

    # HTTP connection parameter names used by ADT and gCTS
    HTTP_CONNECTION_PARAMS: ClassVar[FrozenSet[str]] = frozenset({
        'ashost', 'port', 'client', 'user', 'password',
        'ssl', 'verify'
    })

    def _run_adt(
            self,
            cmd_args: SimpleNamespace
    ) -> OperationResult:
        """Execute an ADT command.

        Args:
            conn_conf: HTTP connection configuration.
            cmd_args: Command-specific arguments.

        Returns:
            OperationResult from the command execution.
        """
        return _run_adt_command(cmd_args, self.arg_tool.cmdfn)

    def _run_gcts(
            self,
            cmd_args: SimpleNamespace
    ) -> OperationResult:
        """Execute a gCTS command.

        Args:
            conn_conf: HTTP connection configuration.
            cmd_args: Command-specific arguments.

        Returns:
            OperationResult from the command execution.
        """
        return _run_gcts_command(cmd_args, self.arg_tool.cmdfn)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the sapcli command with the given arguments.

        Args:
            arguments: Dictionary containing connection parameters and command arguments.

        Returns:
            ToolResult with the command output.

        Raises:
            SapcliCommandToolError: If cmdfn is None, required parameters are missing,
                or connection type is not supported.
        """
        if self.arg_tool.cmdfn is None:
            raise SapcliCommandToolError(
                f"Tool '{self.name}' has no command function (cmdfn is None)"
            )

        try:
            cmd_args = self.arg_tool.parse_args(arguments)
        except argparsertool.MissingArgument as ex:
            raise SapcliCommandToolError(str(ex))

        # pylint: disable-next=comparison-with-callable
        if self.arg_tool.conn_factory == sap.cli.adt_connection_from_args:
            result = self._run_adt(cmd_args)
        # pylint: disable-next=comparison-with-callable
        elif self.arg_tool.conn_factory == sap.cli.gcts_connection_from_args:
            result = self._run_gcts(cmd_args)
        else:
            raise SapcliCommandToolError(
                f"Tool '{self.name}' uses unsupported connection type. "
                "Only ADT and gCTS connections are currently supported."
            )

        # OperationResult is a NamedTuple which serializes as an array [bool, list[str], str]
        return ToolResult(
            content=result.Contents,
            structured_content={
                'result': [result.Success, result.LogMessages, result.Contents]
            }
        )

    @classmethod
    def from_argparser_tool(
        cls,
        cmd: ArgParserTool,
        description: str | None = None
    ) -> 'SapcliCommandTool':
        """Create a SapcliCommandTool from an ArgParserTool.

        Args:
            cmd: The ArgParserTool containing command definition.
            description: Optional description for the tool.

        Returns:
            A new SapcliCommandTool instance.

        Raises:
            SapcliCommandToolError: If cmd.cmdfn is None.
        """
        output_schema = TypeAdapter(_WrappedResult[OperationResult]).json_schema(mode='serialization')
        output_schema["x-fastmcp-wrap-result"] = True

        return cls(
            name=cmd.name,
            description=description or f"Execute sapcli command: {cmd.name}",
            parameters=cmd.to_mcp_input_schema(),
            output_schema=output_schema,
            arg_tool=cmd,
        )


def transform_sapcli_commands(server: FastMCP, allowed_commands: list[str] | None = None):
    """Transform sapcli commands into MCP tools and register them with the server.

    Args:
        server: The FastMCP server instance to register tools with.
    """
    args_tools = ArgParserTool("abap", None)
    args_tools.add_properties(COMMON_CONNECTION_PARAMS)

    # Mapping from connection factory functions to their specific parameters
    conn_factory_to_params = {
        sap.cli.adt_connection_from_args: ADT_CONNECTION_PARAMS,
        sap.cli.rfc_connection_from_args: RFC_CONNECTION_PARAMS,
        sap.cli.gcts_connection_from_args: GCTS_CONNECTION_PARAMS,
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
    # - GCTS - sap.cli.gcts_connection_from_args
    # - OData - sap.cli.odata_connection_from_args
    for conn_factory, cmd in sap.cli.get_commands():
        cmd_tool = args_tools.add_parser(cmd.name)

        # Set connection factory before install_parser so sub-parsers inherit it
        cmd_tool.conn_factory = conn_factory

        specific_params = conn_factory_to_params.get(conn_factory)
        if specific_params is not None:
            cmd_tool.add_properties(specific_params)

        # Install parser after adding connection properties and factory
        cmd.install_parser(cmd_tool)

    # pylint: disable-next=fixme
    # TODO: add name transformations such as "abap_gcts_delete" to "abap_gcts_repo_delete"

    for tool_name, cmd_tool in args_tools.tools.items():
        # Skip tools without a command function (not meaningful commands)
        if cmd_tool.cmdfn is None:
            _LOGGER.debug("Skipped tool without cmdfn: %s", tool_name)
            continue

        if allowed_commands is not None and tool_name not in allowed_commands:
            _LOGGER.debug("Ignored tool: %s", tool_name)
            continue

        server.add_tool(SapcliCommandTool.from_argparser_tool(cmd_tool))
