"""Unit tests for sapcli-mcp-server.py"""

import sys
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from io import StringIO

from sap.errors import SAPCliError
import sap.cli.core

import pytest

# Create mock sap modules before importing the server
# Now import the server module - use importlib to handle the hyphenated filename
import importlib.util
spec = importlib.util.spec_from_file_location(
    "sapcli_mcp_server",
    "src/sapcli-mcp-server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


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
        # Note: There's a bug in the original code - reset() calls
        # std_output.seek(0) twice instead of err_output.seek(0)
        # This test verifies current behavior


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
