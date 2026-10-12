"""Unit tests for sapcli-mcp-server.py"""

import os
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import sap.cli.core
from sap.errors import SAPCliError
from sap.http.errors import UnauthorizedError

from sapclimcp import mcptools
from sapclimcp.argparsertool import ArgParserTool
from sapclimcp.errors import ToolInputError
from sapclimcp.toolpatches import SourceDataPatch


@pytest.fixture
def sample_adt_config():
    """Sample ADT connection configuration."""
    return {
        "ashost": "test.sap.example.com",
        "port": 44300,
        "client": "001",
        "user": "TESTUSER",
        "password": "secret123",
        "ssl": True,
        "verify": False,
        "ssl_server_cert": None,
        "token_url": None,
        "client_id": None,
        "client_secret": None,
    }


class TestOutputBuffer:
    """Tests for OutputBuffer class."""

    def test_init(self):
        """Test OutputBuffer initialization."""
        buf = mcptools.OutputBuffer()
        assert isinstance(buf.std_output, StringIO)
        assert isinstance(buf.err_output, StringIO)

    def test_capout_empty(self):
        """Test capout property with empty buffer."""
        buf = mcptools.OutputBuffer()
        assert buf.capout == ""

    def test_capout_with_content(self):
        """Test capout property with content."""
        buf = mcptools.OutputBuffer()
        buf.std_output.write("test output")
        assert buf.capout == "test output"

    def test_caperr_empty(self):
        """Test caperr property with empty buffer."""
        buf = mcptools.OutputBuffer()
        assert buf.caperr == ""

    def test_caperr_with_content(self):
        """Test caperr property with content."""
        buf = mcptools.OutputBuffer()
        buf.err_output.write("test error")
        assert buf.caperr == "test error"

    def test_reset(self):
        """Test reset method clears buffers."""
        buf = mcptools.OutputBuffer()
        buf.std_output.write("output content")
        buf.err_output.write("error content")
        buf.reset()
        assert buf.capout == ""
        assert buf.caperr == ""


class TestOperationResult:
    """Tests for OperationResult NamedTuple."""

    def test_creation(self):
        """Test OperationResult creation."""
        result = mcptools.OperationResult(
            Success=True, LogMessages=["msg1", "msg2"], Contents="content"
        )
        assert result.Success is True
        assert result.LogMessages == ["msg1", "msg2"]
        assert result.Contents == "content"

    def test_creation_failure(self):
        """Test OperationResult for failure case."""
        result = mcptools.OperationResult(
            Success=False, LogMessages=["error occurred"], Contents=""
        )
        assert result.Success is False
        assert result.LogMessages == ["error occurred"]
        assert result.Contents == ""


class TestRunAdtCommand:
    """Tests for _run_adt_command function."""

    @patch("sap.adt.Connection")
    def test_success(self, mock_connection, sample_adt_config):
        """Test successful ADT command execution."""

        def mock_command(conn, args):
            console = args.console_factory()
            console.printout("test capture stdout")
            console.printerr("test capture stderr")

        config = SimpleNamespace(**sample_adt_config)

        result = mcptools._run_adt_command(config, mock_command)

        assert result.Success is True
        assert result.Contents == "test capture stdout\n"
        assert result.LogMessages == ["test capture stderr\n"]

    @patch("sap.adt.Connection")
    def test_connection_error(self, mock_connection, sample_adt_config):
        """Test ADT command with connection error."""
        mock_connection.side_effect = SAPCliError("Connection failed")

        config = SimpleNamespace(**sample_adt_config)

        def mock_command(conn, args):
            pass

        result = mcptools._run_adt_command(config, mock_command)
        assert result.Success is False
        assert any("ADT" in m for m in result.LogMessages)
        assert any("test.sap.example.com" in m for m in result.LogMessages)
        assert any("Connection failed" in m for m in result.LogMessages)
        assert result.Contents == ""


class TestRunSapcliCommand:
    """Tests for _run_sapcli_command function."""

    def test_success(self):
        """Test successful sapcli command execution."""
        mock_conn = MagicMock()

        def mock_command(conn, args):
            console = args.console_factory()
            console.printout("test capture stdout")
            console.printerr("test capture stderr")

        result = mcptools._run_sapcli_command(mock_command, mock_conn, SimpleNamespace())

        assert result.Success is True
        assert result.Contents == "test capture stdout\n"
        assert result.LogMessages == ["test capture stderr\n"]

    def test_command_error(self):
        """Test sapcli command with SAPCliError."""
        mock_conn = MagicMock()

        def mock_command(conn, args):
            console = args.console_factory()
            console.printout("test capture stdout")
            console.printerr("test capture stderr")
            raise SAPCliError("Command failed")

        result = mcptools._run_sapcli_command(mock_command, mock_conn, SimpleNamespace())

        assert result.Success is False
        assert result.Contents == "test capture stdout\n"
        assert result.LogMessages == ["Command failed", "test capture stderr\n"]

    def test_tool_input_error_returns_user_error(self):
        """B1 + R1#3: a ToolInputError raised by a tool patch (e.g.
        SourceDataPatch's empty-source_data check) is reported as a user error
        (Success=False with the message) by _run_sapcli_command.

        Note: _run_sapcli_command itself does not produce the "likely a bug"
        wrapper — that belongs to SapcliCommandTool.run()'s broad handler. This
        test pins that ToolInputError takes the user-error branch here.
        """
        mock_conn = MagicMock()

        def mock_command(conn, args):
            raise ToolInputError("source_data must not be empty")

        result = mcptools._run_sapcli_command(mock_command, mock_conn, SimpleNamespace())

        assert result.Success is False
        assert "source_data must not be empty" in result.LogMessages[0]

    def test_unexpected_value_error_propagates(self):
        """R1#3: a bare ValueError is deliberately NOT caught by
        _run_sapcli_command — it signals an unexpected bug and must bubble up to
        SapcliCommandTool.run()'s broad handler (which logs it and reports
        "likely a bug"). Catching every ValueError here would misclassify
        internal failures as user-input errors.
        """
        mock_conn = MagicMock()

        def mock_command(conn, args):
            raise ValueError("int() conversion blew up somewhere internal")

        with pytest.raises(ValueError, match="blew up somewhere internal"):
            mcptools._run_sapcli_command(mock_command, mock_conn, SimpleNamespace())

    def test_unicode_error_is_not_swallowed(self):
        """Review F1: UnicodeEncodeError is a ValueError subclass. There is a
        documented live bug where abap_datapreview_osql raises UnicodeEncodeError
        on LIKE/% queries; a bare `except ValueError` here would have masked it as
        a benign user-input error with no server-side stack trace. The fix catches
        only (SAPCliError, ToolInputError), so a UnicodeError propagates to
        SapcliCommandTool.run()'s logger instead of being silently downgraded.
        """
        mock_conn = MagicMock()

        def mock_command(conn, args):
            "ł".encode("ascii")  # raises UnicodeEncodeError (a ValueError)

        with pytest.raises(UnicodeEncodeError):
            mcptools._run_sapcli_command(mock_command, mock_conn, SimpleNamespace())


class TestSapcliCommandTool:
    """Tests for the class SapcliCommandTool"""

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_default_values(self, mock_adt_connection_from_args):
        """Test handling tool properties with defaults.

        A property with default is an argument with default and the return
        value of ArgumentParser.parse_args() has members for such arguments
        even if they were not given on the commandl line.

        Unless the MCP client does not call the MCP tool with the property
        explicitly, the FastMCP server will not call the Tool method run()
        with the parameter arguments populated with the property and its
        default value and so the method run must explicitly add the missing
        parameters with defaults.
        """

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        ran = []

        def tester_tool_fn(conn, args):
            # Check that the attribute exists with its default value
            assert hasattr(args, "logical")
            assert args.logical is False
            ran.append(True)

        apt = ArgParserTool(
            "tester", None, conn_factory=mock_adt_connection_from_args, conn_type="adt"
        )
        tester_tool_cmd = apt.add_parser("tool")
        tester_tool_cmd.add_argument("--logical", action="store_true", default=False)
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools["tester_tool"]
        assert tool.name == "tester_tool"

        sct = mcptools.SapcliCommandTool.from_argparser_tool(tool)

        result = await sct.run(
            {
                "ashost": "localhost",
                "client": "100",
                "user": "DEVELOPER",
                "password": "Welcome1!",
                "port": 50001,
                "use_ssl": True,
                "verify_ssl": False,
            }
        )

        # B5 fix (R1#1): assert the callback actually ran via a side-effect
        # flag. `Success=True` alone is the no-op default return, so asserting
        # only on it would pass vacuously if a refactor skipped execution and
        # left `tester_tool_fn`'s asserts uncovered.
        assert ran == [True], "callback was never invoked"
        assert result.structured_content["result"][0] is True

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_default_values_none(self, mock_adt_connection_from_args):
        """Test handling tool properties without explicit defaults.

        A property with nargs='?' or  nargs='*' does not need to have
        explicitily set default and in that case ArgumentParser.parse_args()
        use None in the case the argument was not present on command line.
        """

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        ran = []

        def tester_tool_fn(conn, args):
            # Check that the attribute exists with its default value
            assert hasattr(args, "dnul")
            assert args.dnul is None
            ran.append(True)

        apt = ArgParserTool(
            "tester", None, conn_factory=mock_adt_connection_from_args, conn_type="adt"
        )
        tester_tool_cmd = apt.add_parser("tool")
        tester_tool_cmd.add_argument("--dnul", nargs="?")
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools["tester_tool"]
        assert tool.name == "tester_tool"

        sct = mcptools.SapcliCommandTool.from_argparser_tool(tool)

        result = await sct.run(
            {
                "ashost": "localhost",
                "client": "100",
                "user": "DEVELOPER",
                "password": "Welcome1!",
                "port": 50001,
                "use_ssl": True,
                "verify_ssl": False,
            }
        )

        # B5 fix (R1#1): see test_default_values above — assert the callback
        # actually ran, not just the no-op Success=True default.
        assert ran == [True], "callback was never invoked"
        assert result.structured_content["result"][0] is True

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_missing_required_parameters(self, mock_adt_connection_from_args):
        """Test that missing required parameters raise SapcliCommandToolError."""

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        def tester_tool_fn(conn, args):
            pass

        apt = ArgParserTool(
            "tester", None, conn_factory=mock_adt_connection_from_args, conn_type="adt"
        )
        tester_tool_cmd = apt.add_parser("tool")
        tester_tool_cmd.add_argument("--ultrastrangeunique")  # required, no default
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools["tester_tool"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(tool)

        with pytest.raises(mcptools.SapcliCommandToolError) as exc_info:
            await sct.run(
                {
                    "ashost": "localhost",
                    "client": "100",
                    "user": "DEVELOPER",
                    "password": "Welcome1!",
                    "port": 50001,
                    "use_ssl": True,
                    "verify_ssl": False,
                    # 'ultrastrangeunique' is missing
                }
            )

        assert "missing required parameters" in str(exc_info.value)
        assert "ultrastrangeunique" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_argument_with_dash_in_name(self, mock_adt_connection_from_args):
        """Test that argument --name-with-dash is available as name_with_dash."""

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        ran = []

        def tester_tool_fn(conn, args):
            # Check that the attribute exists with underscore name
            assert hasattr(args, "name_with_dash")
            assert args.name_with_dash == "test_value"
            ran.append(True)

        apt = ArgParserTool(
            "tester", None, conn_factory=mock_adt_connection_from_args, conn_type="adt"
        )
        tester_tool_cmd = apt.add_parser("tool")
        tester_tool_cmd.add_argument("--name-with-dash")
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools["tester_tool"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(tool)

        result = await sct.run(
            {
                "ashost": "localhost",
                "client": "100",
                "user": "DEVELOPER",
                "password": "Welcome1!",
                "port": 50001,
                "use_ssl": True,
                "verify_ssl": False,
                "name_with_dash": "test_value",
            }
        )

        # R1#2: same B5 anti-pattern — prove the callback ran rather than
        # relying on the no-op Success=True default.
        assert ran == [True], "callback was never invoked"
        assert result.structured_content["result"][0] is True


class TestSapcliCommandToolWithPatches:
    """Integration tests: patch applied to ArgParserTool, SapcliCommandTool unaware."""

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_source_data_flows_through_patch(self, mock_adt_connection_from_args):
        """Test source_data -> tempfile -> command reads correct content -> cleanup."""
        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        source_content = 'REPORT zprog.\nWRITE: / "Hello".'
        captured_source_path = []

        def write_tool_fn(conn, args):
            path = args.source[0]
            captured_source_path.append(path)
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as fobj:
                assert fobj.read() == source_content

        apt = ArgParserTool(
            "tester", None, conn_factory=mock_adt_connection_from_args, conn_type="adt"
        )
        tester_tool_cmd = apt.add_parser("write")
        tester_tool_cmd.add_argument("name")
        tester_tool_cmd.add_argument("source", nargs="+")
        tester_tool_cmd.set_defaults(execute=write_tool_fn)

        tool = apt.tools["tester_write"]

        # Apply patch to ArgParserTool — just like transform_sapcli_commands does
        SourceDataPatch().apply(tool)

        # SapcliCommandTool is created with no patch knowledge
        sct = mcptools.SapcliCommandTool.from_argparser_tool(tool)

        # Verify MCP schema has source_data, not source
        assert "source_data" in sct.parameters["properties"]
        assert "source" not in sct.parameters["properties"]

        result = await sct.run(
            {
                "ashost": "localhost",
                "client": "100",
                "user": "DEVELOPER",
                "password": "Welcome1!",
                "port": 50001,
                "name": "ZPROG",
                "source_data": source_content,
            }
        )

        # R1#6: the callback ran (it appended the tempfile path) and the run
        # succeeded — assert both rather than discarding the result.
        assert len(captured_source_path) == 1, "write callback was never invoked"
        assert result.structured_content["result"][0] is True
        # Cleanup is part of this test's contract ("-> cleanup"): the tempfile
        # must be gone after the command returns.
        assert not os.path.exists(captured_source_path[0])

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_cleanup_runs_on_command_error(self, mock_adt_connection_from_args):
        """Test that tempfile cleanup runs even when the command raises SAPCliError."""
        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        captured_source_path = []

        def failing_write_fn(conn, args):
            captured_source_path.append(args.source[0])
            raise SAPCliError("Write failed")

        apt = ArgParserTool(
            "tester", None, conn_factory=mock_adt_connection_from_args, conn_type="adt"
        )
        tester_tool_cmd = apt.add_parser("write")
        tester_tool_cmd.add_argument("name")
        tester_tool_cmd.add_argument("source", nargs="+")
        tester_tool_cmd.set_defaults(execute=failing_write_fn)

        tool = apt.tools["tester_write"]

        SourceDataPatch().apply(tool)

        sct = mcptools.SapcliCommandTool.from_argparser_tool(tool)

        result = await sct.run(
            {
                "ashost": "localhost",
                "client": "100",
                "user": "DEVELOPER",
                "password": "Welcome1!",
                "port": 50001,
                "name": "ZPROG",
                "source_data": "REPORT zprog.",
            }
        )

        # Verify cleanup happened
        assert len(captured_source_path) == 1
        assert not os.path.exists(captured_source_path[0])

        # Command error should be captured in the result
        assert result.structured_content["result"][0] is False


class TestSapcliCommandToolWithConnectionManager:
    """Integration tests: server-side connection management via ConnectionManager."""

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_connection_flows_through_to_command(self, mock_adt_factory):
        """Happy path: connection_manager provides connection, command receives it."""
        mock_conn = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = mock_conn
        mock_manager.get_connection_params.return_value = {
            "client": "100",
            "user": "admin",
            "ashost": "h",
            "port": 443,
            "ssl": True,
            "verify": False,
        }

        received_conns = []

        def tool_fn(conn, args):
            received_conns.append(conn)

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

        await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        mock_manager.get_connection.assert_called_once_with("DEV", "adt")
        assert received_conns == [mock_conn]
        # adt_connection_from_args should NOT be called — manager provides connection
        mock_adt_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_popped_before_parse_args(self):
        """system parameter is removed from arguments before parse_args sees them."""
        mock_conn = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = mock_conn
        mock_manager.get_connection_params.return_value = {
            "client": "100",
            "user": "admin",
            "ashost": "h",
            "port": 443,
            "ssl": True,
            "verify": False,
        }

        received_args = []

        def tool_fn(conn, args):
            received_args.append(args)

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

        await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        assert len(received_args) == 1
        assert not hasattr(received_args[0], "system")
        assert received_args[0].name == "TEST_OBJ"

    @pytest.mark.asyncio
    async def test_connection_params_injected_into_args(self):
        """Connection params from manager are injected into cmd_args namespace."""
        mock_conn = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = mock_conn
        mock_manager.get_connection_params.return_value = {
            "client": "200",
            "user": "testuser",
            "ashost": "host.example.com",
            "port": 8443,
            "ssl": True,
            "verify": False,
        }

        received_args = []

        def tool_fn(conn, args):
            received_args.append(args)

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

        await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        args = received_args[0]
        assert args.client == "200"
        assert args.user == "testuser"
        assert args.ashost == "host.example.com"
        assert args.port == 8443
        assert not hasattr(args, "password")

    @pytest.mark.asyncio
    async def test_config_error_maps_to_tool_error(self):
        """ConfigError from connection_manager is converted to SapcliCommandToolError."""
        from sapclimcp.errors import ConfigError

        mock_manager = MagicMock()
        mock_manager.get_connection.side_effect = ConfigError("Unknown system 'BAD'")

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=MagicMock())

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

        with pytest.raises(mcptools.SapcliCommandToolError, match="Unknown system 'BAD'"):
            await sct.run({"name": "TEST_OBJ", "system": "BAD"})

    @pytest.mark.asyncio
    async def test_default_system_when_system_not_provided(self):
        """When system is not in arguments, None is passed to connection_manager."""
        mock_conn = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = mock_conn
        mock_manager.get_connection_params.return_value = {
            "client": "100",
            "user": "admin",
            "ashost": "h",
            "port": 443,
            "ssl": True,
            "verify": False,
        }

        def tool_fn(conn, args):
            pass

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

        await sct.run({"name": "TEST_OBJ"})

        mock_manager.get_connection.assert_called_once_with(None, "adt")


# ---------------------------------------------------------------------------
# Helper for creating UnauthorizedError instances in tests
# ---------------------------------------------------------------------------


def _make_unauthorized_error(user="TESTUSER"):
    mock_req = MagicMock(method="GET", url="http://sap.example.com/sap/bc/adt")
    mock_res = MagicMock(status_code=401, text="Unauthorized")
    return UnauthorizedError(mock_req, mock_res, user)


# ---------------------------------------------------------------------------
# Error propagation: UnauthorizedError re-raise
# ---------------------------------------------------------------------------


class TestUnauthorizedErrorPropagation:
    def test_run_sapcli_command_reraises_unauthorized(self):
        """UnauthorizedError propagates through _run_sapcli_command (not caught)."""
        mock_conn = MagicMock()

        def failing_command(conn, args):
            raise _make_unauthorized_error()

        with pytest.raises(UnauthorizedError):
            mcptools._run_sapcli_command(failing_command, mock_conn, SimpleNamespace())

    def test_run_adt_command_reraises_unauthorized(self):
        """UnauthorizedError from connection creation propagates through _run_adt_command."""
        with patch("sap.cli.adt_connection_from_args") as mock_factory:
            mock_factory.side_effect = _make_unauthorized_error()

            with pytest.raises(UnauthorizedError):
                mcptools._run_adt_command(SimpleNamespace(), MagicMock(), connection=None)

    def test_run_gcts_command_reraises_unauthorized(self):
        """UnauthorizedError from connection creation propagates through _run_gcts_command."""
        with patch("sap.cli.gcts_connection_from_args") as mock_factory:
            mock_factory.side_effect = _make_unauthorized_error()

            with pytest.raises(UnauthorizedError):
                mcptools._run_gcts_command(SimpleNamespace(), MagicMock(), connection=None)

    def test_non_auth_errors_still_caught_in_run_sapcli_command(self):
        """Generic SAPCliError is still caught and converted to OperationResult."""
        mock_conn = MagicMock()

        def failing_command(conn, args):
            raise SAPCliError("Some other error")

        result = mcptools._run_sapcli_command(failing_command, mock_conn, SimpleNamespace())
        assert result.Success is False
        assert "Some other error" in result.LogMessages[0]


# ---------------------------------------------------------------------------
# Retry on auth failure in SapcliCommandTool.run()
# ---------------------------------------------------------------------------


class TestRetryOnAuthFailure:
    @staticmethod
    def _make_tool_with_manager(tool_fn, mock_manager):
        """Helper: create SapcliCommandTool wired to mock_manager."""
        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_read"]
        return mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

    @pytest.mark.asyncio
    async def test_retry_on_unauthorized_succeeds(self):
        """First call raises 401, retry with fresh connection succeeds."""
        conn_stale = MagicMock(name="conn_stale")
        conn_fresh = MagicMock(name="conn_fresh")

        mock_manager = MagicMock()
        mock_manager.get_connection.side_effect = [conn_stale, conn_fresh]

        call_count = [0]

        def tool_fn(conn, args):
            call_count[0] += 1
            if conn is conn_stale:
                raise _make_unauthorized_error()
            console = args.console_factory()
            console.printout("success")

        sct = self._make_tool_with_manager(tool_fn, mock_manager)
        result = await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        success, log_messages, contents = result.structured_content["result"]
        assert success is True
        assert contents == "success\n"
        assert call_count[0] == 2
        mock_manager.evict.assert_called_once_with("DEV", "adt")
        assert mock_manager.get_connection.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_unauthorized_fails_twice(self):
        """Both attempts raise 401 — error reported to caller."""
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = MagicMock()
        mock_manager.get_auth_context.return_value = {
            "auth_type": "cookie",
            "host": "dev.sap.example.com",
            "system_name": "DEV",
        }

        def tool_fn(conn, args):
            raise _make_unauthorized_error()

        sct = self._make_tool_with_manager(tool_fn, mock_manager)

        with pytest.raises(mcptools.SapcliCommandToolError, match="after retry"):
            await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        mock_manager.evict.assert_called_once()

    @pytest.mark.asyncio
    async def test_unexpected_error_on_retry_is_wrapped_not_escaped(self):
        """Regression: a non-401 exception raised on the *retry* attempt must be
        wrapped as SapcliCommandToolError ('likely a bug') and logged — not escape
        raw. It previously slipped past run()'s broad handler because it was raised
        inside the `except UnauthorizedError` block; the retry now runs in a helper
        whose exceptions reach the unified handler."""
        conn_stale = MagicMock(name="conn_stale")
        conn_fresh = MagicMock(name="conn_fresh")

        mock_manager = MagicMock()
        mock_manager.get_connection.side_effect = [conn_stale, conn_fresh]

        def tool_fn(conn, args):
            if conn is conn_stale:
                raise _make_unauthorized_error()
            raise ValueError("boom on retry")  # non-401 failure during the retry

        sct = self._make_tool_with_manager(tool_fn, mock_manager)

        with patch.object(mcptools, "_LOGGER") as mock_logger:
            with pytest.raises(mcptools.SapcliCommandToolError, match="likely a bug"):
                await sct.run({"name": "TEST_OBJ", "system": "DEV"})
            # The broad handler must log the stack trace — asserting it means a
            # refactor that silently drops the `_LOGGER.exception` call fails here.
            mock_logger.exception.assert_called_once()

        mock_manager.evict.assert_called_once_with("DEV", "adt")

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_no_retry_without_connection_manager(self, mock_adt_factory):
        """Without connection_manager, 401 is immediately reported as tool error."""
        mock_adt_factory.return_value = MagicMock()

        def tool_fn(conn, args):
            raise _make_unauthorized_error()

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(cmd_tool)

        with pytest.raises(mcptools.SapcliCommandToolError, match="Authentication failed"):
            await sct.run(
                {
                    "ashost": "test",
                    "client": "100",
                    "user": "u",
                    "password": "p",
                    "port": 443,
                    "ssl": True,
                    "verify": False,
                    "name": "TEST_OBJ",
                }
            )

    @pytest.mark.asyncio
    async def test_non_auth_errors_not_retried(self):
        """Generic SAPCliError still produces OperationResult(Success=False), no evict."""
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = MagicMock()

        def tool_fn(conn, args):
            raise SAPCliError("Not found")

        sct = self._make_tool_with_manager(tool_fn, mock_manager)
        result = await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        assert result.structured_content["result"][0] is False
        mock_manager.evict.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_uses_fresh_connection(self):
        """The retry attempt must use a different connection object."""
        connections_used = []

        conn_stale = MagicMock(name="conn_stale")
        conn_fresh = MagicMock(name="conn_fresh")

        mock_manager = MagicMock()
        mock_manager.get_connection.side_effect = [conn_stale, conn_fresh]

        def tool_fn(conn, args):
            connections_used.append(conn)
            if conn is conn_stale:
                raise _make_unauthorized_error()

        sct = self._make_tool_with_manager(tool_fn, mock_manager)
        await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        assert connections_used == [conn_stale, conn_fresh]

    @pytest.mark.asyncio
    async def test_retry_get_connection_failure_maps_to_tool_error(self):
        """If get_connection raises on retry, it becomes SapcliCommandToolError."""
        from sapclimcp.errors import ConfigError

        mock_manager = MagicMock()
        mock_manager.get_connection.side_effect = [
            MagicMock(name="conn_stale"),
            ConfigError("System vanished"),
        ]

        def tool_fn(conn, args):
            raise _make_unauthorized_error()

        sct = self._make_tool_with_manager(tool_fn, mock_manager)

        with pytest.raises(mcptools.SapcliCommandToolError, match="System vanished"):
            await sct.run({"name": "TEST_OBJ", "system": "DEV"})

    @pytest.mark.asyncio
    async def test_retry_on_unauthorized_gcts(self):
        """Retry works through the gCTS execution path."""
        conn_stale = MagicMock(name="conn_stale")
        conn_fresh = MagicMock(name="conn_fresh")

        mock_manager = MagicMock()
        mock_manager.get_connection.side_effect = [conn_stale, conn_fresh]

        call_count = [0]

        def tool_fn(conn, args):
            call_count[0] += 1
            if conn is conn_stale:
                raise _make_unauthorized_error()
            console = args.console_factory()
            console.printout("gcts ok")

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.gcts_connection_from_args,
            conn_type="gcts",
        )
        tool = apt.add_parser("repolist")
        tool.add_argument("name")
        tool.set_defaults(execute=tool_fn)

        cmd_tool = apt.tools["tester_repolist"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(
            cmd_tool,
            connection_manager=mock_manager,
        )

        result = await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        success, log_messages, contents = result.structured_content["result"]
        assert success is True
        assert contents == "gcts ok\n"
        assert call_count[0] == 2
        mock_manager.evict.assert_called_once_with("DEV", "gcts")


# ---------------------------------------------------------------------------
# Actionable error messages
# ---------------------------------------------------------------------------


class TestActionableErrorMessages:
    """Tests that error messages contain actionable guidance."""

    @pytest.mark.asyncio
    async def test_cookie_auth_failure_mentions_sso_cookie(self):
        """Cookie auth failure message tells user to refresh SSO cookie."""
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = MagicMock()
        mock_manager.get_auth_context.return_value = {
            "auth_type": "cookie",
            "host": "dev.sap.example.com",
            "system_name": "DEV",
        }

        def tool_fn(conn, args):
            raise _make_unauthorized_error()

        sct = TestRetryOnAuthFailure._make_tool_with_manager(tool_fn, mock_manager)

        with pytest.raises(mcptools.SapcliCommandToolError) as exc_info:
            await sct.run({"name": "TEST_OBJ", "system": "DEV"})

        msg = str(exc_info.value)
        assert "SSO cookie" in msg
        assert "DEV" in msg
        assert "dev.sap.example.com" in msg

    @pytest.mark.asyncio
    async def test_basic_auth_failure_mentions_credentials(self):
        """Basic auth failure message tells user to check user/password."""
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = MagicMock()
        mock_manager.get_auth_context.return_value = {
            "auth_type": "basic",
            "host": "qas.sap.example.com",
            "system_name": "QAS",
        }

        def tool_fn(conn, args):
            raise _make_unauthorized_error()

        sct = TestRetryOnAuthFailure._make_tool_with_manager(tool_fn, mock_manager)

        with pytest.raises(mcptools.SapcliCommandToolError) as exc_info:
            await sct.run({"name": "TEST_OBJ", "system": "QAS"})

        msg = str(exc_info.value)
        assert "user" in msg
        assert "password" in msg
        assert "QAS" in msg

    @pytest.mark.asyncio
    @patch("sap.cli.adt_connection_from_args")
    async def test_connection_error_includes_host_info(self, mock_adt_factory):
        """Connection failure message includes host and port."""
        mock_adt_factory.side_effect = SAPCliError("Connection refused")

        apt = ArgParserTool(
            "tester",
            None,
            conn_factory=sap.cli.adt_connection_from_args,
            conn_type="adt",
        )
        apt.add_properties(mcptools.COMMON_CONNECTION_PARAMS)
        apt.add_properties(mcptools.ADT_CONNECTION_PARAMS)
        tool = apt.add_parser("read")
        tool.add_argument("name")
        tool.set_defaults(execute=MagicMock())

        cmd_tool = apt.tools["tester_read"]
        sct = mcptools.SapcliCommandTool.from_argparser_tool(cmd_tool)

        result = await sct.run(
            {
                "ashost": "myhost.sap.com",
                "client": "100",
                "user": "u",
                "password": "p",
                "port": 44300,
                "ssl": True,
                "verify": False,
                "name": "TEST_OBJ",
            }
        )

        success, log_messages, contents = result.structured_content["result"]
        assert success is False
        assert any("myhost.sap.com:44300" in m for m in log_messages)
        assert any("ADT" in m for m in log_messages)

    @pytest.mark.asyncio
    async def test_unexpected_exception_caught_as_bug(self):
        """Unexpected exceptions are caught and reported as likely bugs."""
        mock_manager = MagicMock()
        mock_manager.get_connection.return_value = MagicMock()
        mock_manager.get_connection_params.return_value = {
            "client": "100",
            "user": "admin",
            "ashost": "h",
            "port": 443,
            "ssl": True,
            "verify": False,
        }

        def tool_fn(conn, args):
            raise RuntimeError("something completely unexpected")

        sct = TestRetryOnAuthFailure._make_tool_with_manager(tool_fn, mock_manager)

        with patch("sapclimcp.mcptools._LOGGER") as mock_logger:
            with pytest.raises(mcptools.SapcliCommandToolError) as exc_info:
                await sct.run({"name": "TEST_OBJ", "system": "DEV"})

            # Full traceback logged server-side for debugging
            mock_logger.exception.assert_called_once()

        msg = str(exc_info.value)
        assert "Unexpected error" in msg
        assert "RuntimeError" in msg
        assert "bug" in msg
        # str(ex) should NOT be included (could leak credentials)
        assert "something completely unexpected" not in msg
