"""
MCP tool classes and helpers for sapcli commands.
"""

import logging
from dataclasses import dataclass
from functools import partial
from io import StringIO
from types import SimpleNamespace
from typing import (
    Any,
    Callable,
    ClassVar,
    Generic,
    NamedTuple,
)

import sap.cli
import sap.cli.core
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from fastmcp.tools.tool import ToolResult  # pylint: disable=import-error
from pydantic import TypeAdapter
from sap import (
    adt,
    errors,
)
from sap.http.errors import UnauthorizedError
from typing_extensions import TypeVar

from sapclimcp import argparsertool
from sapclimcp.argparsertool import ArgParserTool
from sapclimcp.errors import (
    ConfigError,
    ToolInputError,
    format_auth_error,
    format_connection_error,
)
from sapclimcp.toolpatches import (
    ConnectionPatch,
    MissingGroupParamPatch,
    SourceDataPatch,
    SourceFileToInlinePatch,
    apply_patches,
)

_LOGGER = logging.getLogger(__name__)

# Type aliases for SAP connections and commands
CommandType = Callable[[adt.Connection, SimpleNamespace], None]

# Connection parameters for MCP tools
# Common parameters required for all connection types
COMMON_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    "ashost": {"type": "string"},
    "client": {"type": "string"},
    "user": {"type": "string"},
    "password": {"type": "string"},
}

# ADT connection specific parameters
ADT_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    "port": {"type": "integer"},
    "ssl": {"type": "boolean"},
    "verify": {"type": "boolean"},
}

# RFC connection specific parameters
RFC_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    "sysnr": {"type": "string"},
}

# OData connection specific parameters
ODATA_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    "port": {"type": "integer"},
    "ssl": {"type": "boolean"},
    "verify": {"type": "boolean"},
}

# REST/gCTS connection specific parameters
GCTS_CONNECTION_PARAMS: dict[str, dict[str, str]] = {
    "port": {"type": "integer"},
    "ssl": {"type": "boolean"},
    "verify": {"type": "boolean"},
}


class OutputBuffer(sap.cli.core.PrintConsole):
    """Capture output of sapcli commands in memory buffer."""

    def __init__(self):
        self.std_output = StringIO()
        self.err_output = StringIO()

        super().__init__(out_file=self.std_output, err_file=self.err_output)

    @property
    def capout(self) -> str:
        """Captured standard output"""

        return self.std_output.getvalue()

    @property
    def caperr(self) -> str:
        """Captured error output"""

        return self.err_output.getvalue()

    def reset(self) -> None:
        """Reset captured contents"""

        self.std_output.truncate(0)
        self.std_output.seek(0)
        self.err_output.truncate(0)
        self.err_output.seek(0)


class OperationResult(NamedTuple):
    """MCP tool results"""

    Success: bool
    LogMessages: list[str]
    Contents: str


# UnauthorizedError is a subclass of SAPCliError — its except clause must
# precede the SAPCliError catch in the functions below.


def _run_adt_command(
    args: SimpleNamespace, command: CommandType, connection: Any = None
) -> OperationResult:
    if connection is None:
        try:
            connection = sap.cli.adt_connection_from_args(args)
        except UnauthorizedError:
            raise
        except errors.SAPCliError as ex:
            messages = format_connection_error(
                host=getattr(args, "ashost", "unknown"),
                port=getattr(args, "port", 443),
                ssl=getattr(args, "ssl", True),
                original_error=ex,
                service_type="ADT",
            )
            return OperationResult(Success=False, LogMessages=messages, Contents="")

    return _run_sapcli_command(command, connection, args)


def _run_gcts_command(
    args: SimpleNamespace,
    command: CommandType,
    connection: Any = None,
) -> OperationResult:
    if connection is None:
        try:
            connection = sap.cli.gcts_connection_from_args(args)
        except UnauthorizedError:
            raise
        except errors.SAPCliError as ex:
            messages = format_connection_error(
                host=getattr(args, "ashost", "unknown"),
                port=getattr(args, "port", 443),
                ssl=getattr(args, "ssl", True),
                original_error=ex,
                service_type="gCTS",
            )
            return OperationResult(Success=False, LogMessages=messages, Contents="")

    return _run_sapcli_command(command, connection, args)


def _run_sapcli_command(
    command: CommandType, conn: adt.Connection, args: SimpleNamespace
) -> OperationResult:

    output_buffer = OutputBuffer()

    # Override console_factory so the command uses our per-invocation buffer
    # instead of the global console (thread-safe)
    args.console_factory = lambda: output_buffer

    try:
        command(conn, args)
    except UnauthorizedError:
        raise
    except (errors.SAPCliError, ToolInputError) as ex:
        # Two expected, user-facing failure classes reported back as a failed
        # OperationResult (not a server bug):
        #   - sap.errors.SAPCliError: the backend/library rejected the request.
        #   - ToolInputError: our own tool patches rejected the LLM's input
        #     (e.g. SourceDataPatch's "source_data must not be empty").
        # A bare ValueError is deliberately NOT caught here — it signals an
        # unexpected bug and is left to bubble up to SapcliCommandTool.run()'s
        # broad handler, which logs it and reports "likely a bug". This matters
        # concretely: UnicodeEncodeError (the live abap_datapreview_osql OSQL
        # bug) is a ValueError subclass, and catching it here would mask a real
        # bug as benign user input with no server-side stack trace.
        return OperationResult(
            Success=False,
            LogMessages=[str(ex), output_buffer.caperr],
            Contents=output_buffer.capout,
        )

    return OperationResult(
        Success=True, LogMessages=[output_buffer.caperr], Contents=output_buffer.capout
    )


T = TypeVar("T", default=Any)


@dataclass
class _WrappedResult(Generic[T]):
    """Generic wrapper for non-object return types."""

    result: T


class SapcliCommandToolError(ToolError):
    """Error raised by SapcliCommandTool."""


class SapcliCommandTool(Tool):  # pylint: disable=abstract-method
    """MCP Tool for executing sapcli commands.

    This tool wraps sapcli commands transformed from ArgParserTool
    and executes them via the appropriate connection type.
    Supported connection types: ADT, gCTS.

    When ``connection_manager`` is set, connections are resolved
    server-side and the LLM never sees credential parameters.
    """

    # Pydantic requires this for fields with non-serializable types (ArgParserTool)
    model_config = {"arbitrary_types_allowed": True}

    arg_tool: ArgParserTool
    connection_manager: Any = None

    # HTTP connection parameter names used by ADT and gCTS
    HTTP_CONNECTION_PARAMS: ClassVar[frozenset[str]] = frozenset(
        {"ashost", "port", "client", "user", "password", "ssl", "verify"}
    )

    def _run_adt(
        self,
        cmd_args: SimpleNamespace,
        connection: Any = None,
    ) -> OperationResult:
        """Execute an ADT command.

        Args:
            cmd_args: Command-specific arguments.
            connection: Optional pre-built connection from ConnectionManager.

        Returns:
            OperationResult from the command execution.
        """
        return _run_adt_command(cmd_args, self.arg_tool.cmdfn, connection)

    def _run_gcts(
        self,
        cmd_args: SimpleNamespace,
        connection: Any = None,
    ) -> OperationResult:
        """Execute a gCTS command.

        Args:
            cmd_args: Command-specific arguments.
            connection: Optional pre-built connection from ConnectionManager.

        Returns:
            OperationResult from the command execution.
        """
        return _run_gcts_command(cmd_args, self.arg_tool.cmdfn, connection)

    def _execute_command(
        self,
        cmd_args: SimpleNamespace,
        connection: Any = None,
    ) -> OperationResult:
        """Dispatch to the appropriate connection-type runner."""

        if self.arg_tool.conn_type == "adt":
            return self._run_adt(cmd_args, connection)
        if self.arg_tool.conn_type == "gcts":
            return self._run_gcts(cmd_args, connection)

        raise SapcliCommandToolError(
            f"Tool '{self.name}' uses unsupported connection type "
            f"'{self.arg_tool.conn_type}'. "
            "Only ADT and gCTS connections are currently supported."
        )

    def _get_auth_context(
        self, system: Any, cmd_args: SimpleNamespace | None = None
    ) -> dict[str, str]:
        """Get auth context for error formatting."""
        if self.connection_manager is not None:
            try:
                return self.connection_manager.get_auth_context(system)
            except ConfigError:
                pass
        # Legacy mode: extract from cmd_args if available
        host = getattr(cmd_args, "ashost", "unknown") if cmd_args else "unknown"
        return {
            "auth_type": "basic",
            "host": host,
            "system_name": str(system or "default"),
        }

    def _execute_with_auth_retry(
        self, cmd_args: SimpleNamespace, connection: Any, system: Any
    ) -> OperationResult:
        """Execute the command, retrying once on HTTP 401 with a fresh connection.

        Retry is safe: ``UnauthorizedError`` fires during session/CSRF
        establishment (``sap.http.client.HTTPClient.build_session``), before the
        command body executes any writes. Auth failures are converted to a
        user-facing ``SapcliCommandToolError``; every other exception — on the
        first attempt OR the retry — propagates unchanged so ``run()``'s single
        handler logs and wraps it (rather than letting a retry-path error escape
        raw).
        """
        try:
            return self._execute_command(cmd_args, connection)
        except UnauthorizedError as exc:
            if self.connection_manager is None:
                ctx = self._get_auth_context(system, cmd_args)
                raise SapcliCommandToolError(
                    format_auth_error(
                        auth_type=ctx["auth_type"],
                        system_name=ctx["system_name"],
                        host=ctx["host"],
                    )
                ) from exc

            _LOGGER.info(
                "Auth failure for system '%s', evicting connection and retrying",
                system,
            )
            self.connection_manager.evict(system, self.arg_tool.conn_type)

            try:
                connection = self.connection_manager.get_connection(system, self.arg_tool.conn_type)
            except ConfigError as ex:
                raise SapcliCommandToolError(str(ex)) from ex

            try:
                return self._execute_command(cmd_args, connection)
            except UnauthorizedError as retry_exc:
                ctx = self._get_auth_context(system, cmd_args)
                raise SapcliCommandToolError(
                    format_auth_error(
                        auth_type=ctx["auth_type"],
                        system_name=ctx["system_name"],
                        host=ctx["host"],
                        is_retry=True,
                    )
                ) from retry_exc

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the sapcli command with the given arguments.

        When a ``connection_manager`` is set and the command fails with
        an ``UnauthorizedError`` (HTTP 401), the stale connection is
        evicted and the command is retried once with a fresh connection.

        Args:
            arguments: Dictionary containing command arguments
                (and optionally ``system`` when server-managed connections are used).

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

        # Work on a copy to avoid mutating the caller's dict
        arguments = dict(arguments)
        system = arguments.pop("system", None)

        # Resolve connection from manager if available
        connection = None
        if self.connection_manager is not None:
            try:
                connection = self.connection_manager.get_connection(system, self.arg_tool.conn_type)
            except ConfigError as ex:
                raise SapcliCommandToolError(str(ex)) from ex

        try:
            cmd_args = self.arg_tool.parse_args(arguments)
        except argparsertool.MissingArgument as ex:
            raise SapcliCommandToolError(str(ex)) from ex

        # Inject connection parameters into args namespace.
        # Some sapcli commands access connection params (e.g. client, user)
        # directly from args even though they were stripped from the schema
        # by ConnectionPatch. Since ConnectionPatch removes these from the
        # schema, parse_args never sets them — unconditional setattr is safe.
        if self.connection_manager is not None:
            try:
                conn_params = self.connection_manager.get_connection_params(system)
            except ConfigError as ex:
                raise SapcliCommandToolError(str(ex)) from ex
            for key, value in conn_params.items():
                setattr(cmd_args, key, value)

        try:
            result = self._execute_with_auth_retry(cmd_args, connection, system)
        except SapcliCommandToolError:
            # Already a clean, user-facing error (auth failure, missing
            # parameter, or config error) — re-raise as-is, without the
            # "likely a bug" wrapping below.
            raise
        except Exception as ex:
            # Any unexpected error from the first attempt OR the retry reaches
            # here (both run inside _execute_with_auth_retry), so it is always
            # logged with a stack trace and surfaced as a wrapped tool error
            # rather than escaping raw.
            _LOGGER.exception(
                "Unexpected error in tool '%s'",
                self.name,
            )
            raise SapcliCommandToolError(
                f"Unexpected error in tool '{self.name}': "
                f"{type(ex).__name__}. This is likely a bug — "
                f"check server logs for details."
            ) from ex

        # OperationResult is a NamedTuple which serializes as an array [bool, list[str], str]
        return ToolResult(
            content=result.Contents,
            structured_content={"result": [result.Success, result.LogMessages, result.Contents]},
        )

    @classmethod
    def from_argparser_tool(
        cls,
        cmd: ArgParserTool,
        description: str | None = None,
        connection_manager: Any = None,
    ) -> "SapcliCommandTool":
        """Create a SapcliCommandTool from an ArgParserTool.

        Args:
            cmd: The ArgParserTool containing command definition.
            description: Optional description for the tool.
            connection_manager: Optional ConnectionManager for server-side connections.

        Returns:
            A new SapcliCommandTool instance.

        Raises:
            SapcliCommandToolError: If cmd.cmdfn is None.
        """
        output_schema = TypeAdapter(_WrappedResult[OperationResult]).json_schema(
            mode="serialization"
        )
        output_schema["x-fastmcp-wrap-result"] = True

        return cls(
            name=cmd.name,
            description=description or f"Execute sapcli command: {cmd.name}",
            parameters=cmd.to_mcp_input_schema(),
            output_schema=output_schema,
            arg_tool=cmd,
            connection_manager=connection_manager,
        )


def transform_sapcli_commands(
    server: FastMCP,
    allowed_commands: list[str] | None = None,
    connection_manager: Any = None,
):
    """Transform sapcli commands into MCP tools and register them with the server.

    Args:
        server: The FastMCP server instance to register tools with.
        allowed_commands: Optional list of tool names to expose. None = all.
        connection_manager: Optional ConnectionManager for server-side connections.
            When provided, connection parameters are stripped from tool schemas
            and a ``system`` selector is added.
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

    # Resolve factory → type string once at registration time
    conn_factory_to_type = {
        sap.cli.adt_connection_from_args: "adt",
        sap.cli.rfc_connection_from_args: "rfc",
        sap.cli.gcts_connection_from_args: "gcts",
        sap.cli.odata_connection_from_args: "odata",
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

        # Set connection factory and type before install_parser so sub-parsers inherit them
        cmd_tool.conn_factory = conn_factory

        # Resolve connection type — handle functools.partial wrappers
        # (e.g. partial(odata_connection_from_args, 'UI5/...') for BSP/FLP)
        lookup_fn = conn_factory.func if isinstance(conn_factory, partial) else conn_factory
        conn_type = conn_factory_to_type.get(lookup_fn)
        if conn_type is None:
            _LOGGER.warning("Unknown connection factory %s, conn_type not set", conn_factory)
        cmd_tool.conn_type = conn_type

        specific_params = conn_factory_to_params.get(lookup_fn)
        if specific_params is not None:
            cmd_tool.add_properties(specific_params)

        # Install parser after adding connection properties and factory
        cmd.install_parser(cmd_tool)

    # pylint: disable-next=fixme
    # TODO: add name transformations such as "abap_gcts_delete" to "abap_gcts_repo_delete"

    # ConnectionPatch must be last — it adds the 'system' selector after all
    # other patches have finished modifying schemas.
    patch_registry = [
        SourceDataPatch(),
        SourceFileToInlinePatch(),
        MissingGroupParamPatch(),
    ]
    if connection_manager is not None:
        patch_registry.append(
            ConnectionPatch(
                system_names=connection_manager.system_names,
                default_system=connection_manager.default_system,
            )
        )

    for tool_name, cmd_tool in args_tools.tools.items():
        # Skip tools without a command function (not meaningful commands)
        if cmd_tool.cmdfn is None:
            _LOGGER.debug("Skipped tool without cmdfn: %s", tool_name)
            continue

        if allowed_commands is not None and tool_name not in allowed_commands:
            _LOGGER.debug("Ignored tool: %s", tool_name)
            continue

        apply_patches(tool_name, cmd_tool, patch_registry)

        server.add_tool(
            SapcliCommandTool.from_argparser_tool(
                cmd_tool,
                connection_manager=connection_manager,
            )
        )
