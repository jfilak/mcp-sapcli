"""Unit tests for sapcli-mcp-server.py"""

from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from io import StringIO

from sap.errors import SAPCliError
import sap.cli.core

import pytest

from sapclimcp.argparsertool import ArgParserTool
from sapclimcp import mcptools as server


@pytest.fixture
def sample_adt_config():
    """Sample ADT connection configuration."""
    return {
        'ashost': 'test.sap.example.com',
        'http_port': 44300,
        'client': '001',
        'user': 'TESTUSER',
        'password': 'secret123',
        'use_ssl': True,
        'verify_ssl': False,
    }


class TestOutputBuffer:
    """Tests for OutputBuffer class."""

    def test_init(self):
        """Test OutputBuffer initialization."""
        buf = server.OutputBuffer()
        assert isinstance(buf.std_output, StringIO)
        assert isinstance(buf.err_output, StringIO)

    def test_capout_empty(self):
        """Test capout property with empty buffer."""
        buf = server.OutputBuffer()
        assert buf.capout == ""

    def test_capout_with_content(self):
        """Test capout property with content."""
        buf = server.OutputBuffer()
        buf.std_output.write("test output")
        assert buf.capout == "test output"

    def test_caperr_empty(self):
        """Test caperr property with empty buffer."""
        buf = server.OutputBuffer()
        assert buf.caperr == ""

    def test_caperr_with_content(self):
        """Test caperr property with content."""
        buf = server.OutputBuffer()
        buf.err_output.write("test error")
        assert buf.caperr == "test error"

    def test_reset(self):
        """Test reset method clears buffers."""
        buf = server.OutputBuffer()
        buf.std_output.write("output content")
        buf.err_output.write("error content")
        buf.reset()
        assert buf.capout == ""
        assert buf.caperr == ""


class TestOperationResult:
    """Tests for OperationResult NamedTuple."""

    def test_creation(self):
        """Test OperationResult creation."""
        result = server.OperationResult(
            Success=True,
            LogMessages=["msg1", "msg2"],
            Contents="content"
        )
        assert result.Success is True
        assert result.LogMessages == ["msg1", "msg2"]
        assert result.Contents == "content"

    def test_creation_failure(self):
        """Test OperationResult for failure case."""
        result = server.OperationResult(
            Success=False,
            LogMessages=["error occurred"],
            Contents=""
        )
        assert result.Success is False
        assert result.LogMessages == ["error occurred"]
        assert result.Contents == ""


class TestADTConnectionConfig:
    """Tests for ADTConnectionConfig NamedTuple."""

    def test_creation(self):
        """Test ADTConnectionConfig creation."""
        config = server.ADTConnectionConfig(
            ASHost="host.example.com",
            HTTP_Port=443,
            Client="001",
            User="user",
            Password="pass",
            UseSSL=True,
            VerifySSL=False
        )
        assert config.ASHost == "host.example.com"
        assert config.HTTP_Port == 443
        assert config.Client == "001"
        assert config.User == "user"
        assert config.Password == "pass"
        assert config.UseSSL is True
        assert config.VerifySSL is False


class TestNewAdtConnection:
    """Tests for _new_adt_connection function."""

    @patch('sap.adt.Connection')
    def test_creates_connection(self, mock_connection):
        """Test that _new_adt_connection creates an ADT connection."""
        config = server.ADTConnectionConfig(
            ASHost="host.example.com",
            HTTP_Port=443,
            Client="001",
            User="user",
            Password="pass",
            UseSSL=True,
            VerifySSL=False
        )
        server._new_adt_connection(config)
        mock_connection.assert_called_once_with(
            "host.example.com",
            "001",
            "user",
            "pass",
            port=443,
            ssl=True,
            verify=False
        )


class TestRunAdtCommand:
    """Tests for _run_adt_command function."""

    @patch('sap.adt.Connection')
    def test_success(self, mock_connection, sample_adt_config):
        """Test successful ADT command execution."""
        def mock_command(conn, args):
            console = sap.cli.core.get_console()
            console.printout("test capture stdout")
            console.printerr("test capture stderr")
            pass

        config = server.ADTConnectionConfig(**{
            'ASHost': sample_adt_config['ashost'],
            'HTTP_Port': sample_adt_config['http_port'],
            'Client': sample_adt_config['client'],
            'User': sample_adt_config['user'],
            'Password': sample_adt_config['password'],
            'UseSSL': sample_adt_config['use_ssl'],
            'VerifySSL': sample_adt_config['verify_ssl'],
        })

        result = server._run_adt_command(config, mock_command, SimpleNamespace())

        assert result.Success is True
        assert result.Contents == "test capture stdout\n"
        assert result.LogMessages == ["test capture stderr\n"]

    @patch('sap.adt.Connection')
    def test_connection_error(self, mock_connection, sample_adt_config):
        """Test ADT command with connection error."""
        mock_connection.side_effect = SAPCliError("Connection failed")

        config = server.ADTConnectionConfig(**{
            'ASHost': sample_adt_config['ashost'],
            'HTTP_Port': sample_adt_config['http_port'],
            'Client': sample_adt_config['client'],
            'User': sample_adt_config['user'],
            'Password': sample_adt_config['password'],
            'UseSSL': sample_adt_config['use_ssl'],
            'VerifySSL': sample_adt_config['verify_ssl'],
        })

        def mock_command(conn, args):
            pass

        result = server._run_adt_command(config, mock_command, SimpleNamespace())
        assert result.Success is False
        assert ['Could not connect to ADT Server', 'Connection failed'] == result.LogMessages
        assert result.Contents == ""


class TestRunSapcliCommand:
    """Tests for _run_sapcli_command function."""

    def test_success(self):
        """Test successful sapcli command execution."""
        mock_conn = MagicMock()

        def mock_command(conn, args):
            console = sap.cli.core.get_console()
            console.printout("test capture stdout")
            console.printerr("test capture stderr")

        result = server._run_sapcli_command(mock_conn, mock_command, SimpleNamespace())

        assert result.Success is True
        assert result.Contents == "test capture stdout\n"
        assert result.LogMessages == ["test capture stderr\n"]

    def test_command_error(self):
        """Test sapcli command with SAPCliError."""
        mock_conn = MagicMock()

        def mock_command(conn, args):
            console = sap.cli.core.get_console()
            console.printout("test capture stdout")
            console.printerr("test capture stderr")
            raise SAPCliError("Command failed")

        result = server._run_sapcli_command(mock_conn, mock_command, SimpleNamespace())

        assert result.Success is False
        assert result.Contents == "test capture stdout\n"
        assert result.LogMessages == ["Command failed", "test capture stderr\n"]


class TestSapcliCommandTool:
    """Tests for the class SapcliCommandTool"""

    @pytest.mark.asyncio
    @patch('sap.cli.adt_connection_from_args')
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

        def tester_tool_fn(conn, args):
            # Check that the attribute exists with its default value
            assert hasattr(args, 'logical')
            assert args.logical is False

        apt = ArgParserTool('tester', None, conn_factory=mock_adt_connection_from_args)
        tester_tool_cmd = apt.add_parser('tool')
        tester_tool_cmd.add_argument('--logical', action='store_true', default=False)
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools['tester_tool']
        assert tool.name == 'tester_tool'

        sct = server.SapcliCommandTool.from_argparser_tool(
            tool,
            mock_adt_connection_from_args,
        )

        await sct.run({
            'ashost': 'localhost',
            'client': '100',
            'user': 'DEVELOPER',
            'password': 'Welcome1!',
            'http_port': 50001,
            'use_ssl': True,
            'verify_ssl': False,
        })

    @pytest.mark.asyncio
    @patch('sap.cli.adt_connection_from_args')
    async def test_default_values_none(self, mock_adt_connection_from_args):
        """Test handling tool properties without explicit defaults.

           A property with nargs='?' or  nargs='*' does not need to have
           explicitily set default and in that case ArgumentParser.parse_args()
           use None in the case the argument was not present on command line.
        """

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        def tester_tool_fn(conn, args):
            # Check that the attribute exists with its default value
            assert hasattr(args, 'dnul')
            assert args.dnul is None

        apt = ArgParserTool('tester', None, conn_factory=mock_adt_connection_from_args)
        tester_tool_cmd = apt.add_parser('tool')
        tester_tool_cmd.add_argument('--dnul', nargs='?')
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools['tester_tool']
        assert tool.name == 'tester_tool'

        sct = server.SapcliCommandTool.from_argparser_tool(
            tool,
            mock_adt_connection_from_args,
        )

        await sct.run({
            'ashost': 'localhost',
            'client': '100',
            'user': 'DEVELOPER',
            'password': 'Welcome1!',
            'http_port': 50001,
            'use_ssl': True,
            'verify_ssl': False,
        })

    @pytest.mark.asyncio
    @patch('sap.cli.adt_connection_from_args')
    async def test_missing_required_parameters(self, mock_adt_connection_from_args):
        """Test that missing required parameters raise SapcliCommandToolError."""

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        def tester_tool_fn(conn, args):
            pass

        apt = ArgParserTool('tester', None, conn_factory=mock_adt_connection_from_args)
        tester_tool_cmd = apt.add_parser('tool')
        tester_tool_cmd.add_argument('--ultrastrangeunique')  # required, no default
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools['tester_tool']
        sct = server.SapcliCommandTool.from_argparser_tool(
            tool,
            mock_adt_connection_from_args,
        )

        with pytest.raises(server.SapcliCommandToolError) as exc_info:
            await sct.run({
                'ashost': 'localhost',
                'client': '100',
                'user': 'DEVELOPER',
                'password': 'Welcome1!',
                'http_port': 50001,
                'use_ssl': True,
                'verify_ssl': False,
                # 'ultrastrangeunique' is missing
            })

        assert "missing required parameters" in str(exc_info.value)
        assert "ultrastrangeunique" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('sap.cli.adt_connection_from_args')
    async def test_argument_with_dash_in_name(self, mock_adt_connection_from_args):
        """Test that argument --name-with-dash is available as name_with_dash."""

        mock_conn = MagicMock()
        mock_adt_connection_from_args.return_value = mock_conn

        def tester_tool_fn(conn, args):
            # Check that the attribute exists with underscore name
            assert hasattr(args, 'name_with_dash')
            assert args.name_with_dash == 'test_value'

        apt = ArgParserTool('tester', None, conn_factory=mock_adt_connection_from_args)
        tester_tool_cmd = apt.add_parser('tool')
        tester_tool_cmd.add_argument('--name-with-dash')
        tester_tool_cmd.set_defaults(execute=tester_tool_fn)

        tool = apt.tools['tester_tool']
        sct = server.SapcliCommandTool.from_argparser_tool(
            tool,
            mock_adt_connection_from_args,
        )

        await sct.run({
            'ashost': 'localhost',
            'client': '100',
            'user': 'DEVELOPER',
            'password': 'Welcome1!',
            'http_port': 50001,
            'use_ssl': True,
            'verify_ssl': False,
            'name_with_dash': 'test_value',
        })
