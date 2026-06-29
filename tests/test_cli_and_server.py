"""Tests for sapclimcp.server and sapclimcp.cli."""

import asyncio
import json
import logging
import re
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import keyring.errors as kr_errors
import pytest

from sapclimcp.cli import main, parse_args
from sapclimcp.config import KEYRING_SERVICE
from sapclimcp.errors import KEYRING_INSTALL_HINT, ConfigError
from sapclimcp.server import VERIFIED_COMMANDS, create_mcp_server
from sapclimcp.toolpatches import ConnectionPatch


def _write_config(tmp_path, systems: dict, default: str | None = None) -> str:
    """Write a config JSON to tmp_path and return the path string.

    Helper for tests that build small server configs to drive
    `create_mcp_server(config_path=...)` — keeps the JSON shape in one
    place so test bodies stay focused on assertions.
    """
    payload: dict = {"systems": systems}
    if default is not None:
        payload["default_system"] = default
    path = tmp_path / "cfg.json"
    # Match `load_config`'s read encoding — Windows defaults to cp1252,
    # so any non-ASCII data (hostname, password) would otherwise mismatch.
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_defaults(self):
        args = parse_args([])
        assert args.experimental is False
        assert args.config is None
        assert args.stdio is False
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.log_level is None

    def test_log_level_valid(self):
        args = parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"

    def test_log_level_case_insensitive(self):
        args = parse_args(["--log-level", "debug"])
        assert args.log_level == "DEBUG"

    def test_log_level_invalid(self):
        with pytest.raises(SystemExit):
            parse_args(["--log-level", "VERBOSE"])

    def test_experimental(self):
        args = parse_args(["--experimental"])
        assert args.experimental is True

    def test_stdio(self):
        args = parse_args(["--stdio"])
        assert args.stdio is True

    def test_config(self):
        args = parse_args(["--config", "/path/to/config.json"])
        assert args.config == "/path/to/config.json"

    def test_host_and_port(self):
        args = parse_args(["--host", "0.0.0.0", "--port", "9000"])
        assert args.host == "0.0.0.0"
        assert args.port == 9000


class TestCreateMcpServer:
    """Tests for create_mcp_server."""

    def test_creates_server_with_verified_tools(self):
        server = create_mcp_server()
        assert server.name == "sapcli"
        # Verify tools were actually registered (not an empty server)
        tools = asyncio.run(server.list_tools())
        assert len(tools) >= len(VERIFIED_COMMANDS)

        # Spot-check critical tools by name + key schema fields. A pure
        # count assertion would still pass if upstream sapcli renamed e.g.
        # `program_write`'s `--no-check` to `--skip-check` (the tool would
        # still register, just with a different schema). These targeted
        # assertions catch rename-without-removal regressions on a sapcli
        # bump before anyone hits them at runtime.
        by_name = {t.name: t for t in tools}
        for required in ("abap_program_write", "abap_class_write", "abap_program_read"):
            assert required in by_name, f"Missing critical tool: {required}"

        write_props = by_name["abap_program_write"].parameters.get("properties", {})
        assert "source_data" in write_props, (
            "abap_program_write must expose source_data (SourceDataPatch transforms "
            "sapcli's source-as-array into inline source_data; loss of this property "
            "means the patch silently stopped applying)"
        )
        assert "no_check" in write_props, (
            "abap_program_write must expose no_check; renaming this upstream would "
            "silently break callers"
        )

    def test_managed_mode_strips_credentials_from_all_schemas(self, tmp_path):
        """Headline security property: in server-managed mode, no generated tool
        schema exposes any connection/credential parameter — the LLM only ever
        sees business params plus an optional `system` selector. Asserted against
        the full exposed tool set (`--experimental`) so a future tool can't quietly
        ship credential params."""
        config_path = _write_config(
            tmp_path,
            {
                "DEV": {
                    "ashost": "dev.example.com",
                    "client": "001",
                    "user": "u",
                    "password": "p",
                }
            },
        )
        server = create_mcp_server(experimental=True, config_path=config_path)
        tools = asyncio.run(server.list_tools())

        leaks = {
            tool.name: sorted(set(tool.parameters.get("properties", {})) & ConnectionPatch.CONNECTION_PARAMS)
            for tool in tools
            if set(tool.parameters.get("properties", {})) & ConnectionPatch.CONNECTION_PARAMS
        }
        assert not leaks, f"managed-mode tool schemas leak credential params: {leaks}"

        # The managed-mode `system` selector IS injected on connection-bound tools.
        by_name = {t.name: t for t in tools}
        assert "system" in by_name["abap_program_write"].parameters.get("properties", {})

    def test_warns_on_keyring_refs_without_keyring(self, tmp_path, caplog):
        """If config references `keyring:` credentials but the keyring extra
        is not installed, log a startup WARNING with a count + install hint
        (per-field detail goes to DEBUG so it doesn't leak credential layout
        to MCP clients in stdio mode)."""
        # NOTE: DEV intentionally has BOTH `auth: basic` (using user/password)
        # AND a `keyring:`-prefixed `cookie` field. The scanner must catch
        # keyring refs in *any* field, not just the auth-active one — a user
        # might leave a stale cookie value in config while testing basic auth.
        config_path = _write_config(
            tmp_path,
            systems={
                "DEV": {
                    "ashost": "h.example.com",
                    "client": "100",
                    "user": "u",
                    "password": "p",
                    "auth": "basic",
                    "cookie": "keyring:DEV-cookie",
                },
                "QAS": {
                    "ashost": "q.example.com",
                    "client": "200",
                    "auth": "cookie",
                    "cookie": "keyring:QAS-cookie",
                },
            },
            default="DEV",
        )
        with (
            patch("sapclimcp.config.keyring", None),
            caplog.at_level(logging.DEBUG, logger="sapclimcp.server"),
        ):
            create_mcp_server(config_path=config_path)

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name == "sapclimcp.server"
        ]
        assert any("keyring" in r.getMessage() for r in warnings), (
            f"Expected a keyring warning, got: {[r.getMessage() for r in warnings]}"
        )
        warning_text = " ".join(r.getMessage() for r in warnings)
        # Warning is summary-only: count + install hint (NO per-field detail).
        # Use word boundaries to avoid matching e.g. "host2.example.com".
        assert re.search(r"\b2\b", warning_text), (
            "warning should mention the affected count as a standalone number"
        )
        assert KEYRING_INSTALL_HINT in warning_text
        assert "DEV.cookie" not in warning_text, (
            "per-field detail must NOT appear at WARNING level (would leak "
            "credential layout to stdio MCP clients)"
        )

        # Per-field detail belongs at DEBUG level — opt-in via --log-level=DEBUG
        debug_text = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG and r.name == "sapclimcp.server"
        )
        assert "DEV.cookie" in debug_text
        assert "QAS.cookie" in debug_text

    def test_no_warning_when_keyring_installed(self, tmp_path, caplog):
        """No warning fires when keyring is available — the soft-import
        sentinel is the trigger, not the presence of `keyring:` refs."""
        config_path = _write_config(
            tmp_path,
            systems={
                "DEV": {
                    "ashost": "h.example.com",
                    "client": "100",
                    "auth": "cookie",
                    "cookie": "keyring:DEV-cookie",
                }
            },
        )
        # Stub keyring to a truthy MagicMock — config.keyring is not None
        with (
            patch("sapclimcp.config.keyring", MagicMock()),
            caplog.at_level(logging.WARNING, logger="sapclimcp.server"),
        ):
            create_mcp_server(config_path=config_path)

        keyring_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "sapclimcp.server"
            and "keyring" in r.getMessage()
        ]
        assert not keyring_warnings, (
            f"No keyring warning expected when keyring is installed, got: "
            f"{[r.getMessage() for r in keyring_warnings]}"
        )

    def test_no_warning_when_no_keyring_refs(self, tmp_path, caplog):
        """Without `keyring:` refs, the scanner is a no-op even when keyring
        is not installed (covers the early-return path on empty refs)."""
        config_path = _write_config(
            tmp_path,
            systems={
                "DEV": {
                    "ashost": "h.example.com",
                    "client": "100",
                    "user": "u",
                    "password": "p",
                    "auth": "basic",
                }
            },
        )
        with (
            patch("sapclimcp.config.keyring", None),
            caplog.at_level(logging.WARNING, logger="sapclimcp.server"),
        ):
            create_mcp_server(config_path=config_path)

        keyring_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "sapclimcp.server"
            and "keyring" in r.getMessage()
        ]
        assert not keyring_warnings, (
            f"No keyring warning expected when no keyring refs, got: "
            f"{[r.getMessage() for r in keyring_warnings]}"
        )

    def test_warning_counts_all_keyring_fields(self, tmp_path, caplog):
        """All-three-fields-keyring-refs counts every reference, not just one
        per system."""
        config_path = _write_config(
            tmp_path,
            systems={
                "DEV": {
                    "ashost": "h.example.com",
                    "client": "100",
                    "user": "keyring:DEV-user",
                    "password": "keyring:DEV-pass",
                    "auth": "basic",
                    "cookie": "keyring:DEV-cookie",
                }
            },
        )
        with (
            patch("sapclimcp.config.keyring", None),
            caplog.at_level(logging.DEBUG, logger="sapclimcp.server"),
        ):
            create_mcp_server(config_path=config_path)

        warning_text = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name == "sapclimcp.server"
        )
        assert re.search(r"\b3\b", warning_text), "warning should count all 3 keyring refs"
        debug_text = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG and r.name == "sapclimcp.server"
        )
        assert "DEV.user" in debug_text
        assert "DEV.password" in debug_text
        assert "DEV.cookie" in debug_text

    def test_creates_server_with_name(self):
        server = create_mcp_server(name="test-server")
        assert server.name == "test-server"

    def test_raises_config_error_on_bad_config(self, tmp_path):
        bad_config = tmp_path / "bad.json"
        bad_config.write_text("not json")
        with pytest.raises(ConfigError):
            create_mcp_server(config_path=str(bad_config))

    def test_raises_config_error_on_missing_config(self):
        with pytest.raises(ConfigError):
            create_mcp_server(config_path="/nonexistent/path.json")


class TestCliMain:
    """Tests for cli.main()."""

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_stdio(self, mock_create, monkeypatch):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--stdio"])

        mock_create.assert_called_once_with(
            experimental=False,
            config_path=None,
        )
        mock_server.run.assert_called_once_with(transport="stdio")

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_http(self, mock_create, monkeypatch):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--host", "0.0.0.0", "--port", "9000"])

        mock_create.assert_called_once_with(
            experimental=False,
            config_path=None,
        )
        mock_server.run.assert_called_once_with(transport="http", host="0.0.0.0", port=9000)

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_experimental_with_config(self, mock_create):
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--experimental", "--config", "my.json", "--stdio"])

        mock_create.assert_called_once_with(
            experimental=True,
            config_path="my.json",
        )

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_config_from_env(self, mock_create, monkeypatch):
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        monkeypatch.setenv("SAPCLI_MCP_CONFIG", "/env/config.json")
        main(["--stdio"])

        mock_create.assert_called_once_with(
            experimental=False,
            config_path="/env/config.json",
        )

    @patch("sapclimcp.cli.create_mcp_server")
    def test_cli_arg_takes_precedence_over_env(self, mock_create, monkeypatch):
        """CLI --config flag takes priority over SAPCLI_MCP_CONFIG env var."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        monkeypatch.setenv("SAPCLI_MCP_CONFIG", "/env/config.json")
        main(["--stdio", "--config", "explicit.json"])

        mock_create.assert_called_once_with(
            experimental=False,
            config_path="explicit.json",
        )

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_exits_on_config_error(self, mock_create):
        mock_create.side_effect = ConfigError("bad config")

        with pytest.raises(SystemExit) as exc_info:
            main(["--stdio", "--config", "bad.json"])

        msg = str(exc_info.value)
        assert "configuration error" in msg
        assert "bad config" in msg

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_exits_on_unexpected_error(self, mock_create):
        """Unexpected exceptions produce actionable error instead of traceback."""
        mock_create.side_effect = RuntimeError("something broke")

        with pytest.raises(SystemExit) as exc_info:
            main(["--stdio"])

        msg = str(exc_info.value)
        assert "unexpected error" in msg
        assert "RuntimeError" in msg
        assert "something broke" in msg

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_exits_on_import_error(self, mock_create):
        """Generic ImportError produces guidance about installing dependencies."""
        mock_create.side_effect = ImportError("No module named 'foo'", name="foo")

        with pytest.raises(SystemExit) as exc_info:
            main(["--stdio"])

        msg = str(exc_info.value)
        assert "missing dependency" in msg
        assert "sapcli is not installed" not in msg

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_exits_on_sapcli_import_error(self, mock_create):
        """ImportError for sap.* produces sapcli-specific install guidance."""
        mock_create.side_effect = ImportError("No module named 'sap'", name="sap")

        with pytest.raises(SystemExit) as exc_info:
            main(["--stdio"])

        msg = str(exc_info.value)
        assert "sapcli is not installed" in msg
        assert "uv pip install" in msg

    @patch("sapclimcp.cli.logging.basicConfig")
    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_log_level_calls_basicConfig(self, mock_create, mock_basic, monkeypatch):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        monkeypatch.delenv("SAPCLI_MCP_LOG_LEVEL", raising=False)
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--stdio", "--log-level", "DEBUG"])

        mock_basic.assert_called_once()
        kwargs = mock_basic.call_args.kwargs
        assert kwargs["level"] == logging.DEBUG
        assert kwargs["stream"] is sys.stderr
        assert kwargs["force"] is True

    @patch("sapclimcp.cli.logging.basicConfig")
    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_no_log_level_skips_basicConfig(self, mock_create, mock_basic, monkeypatch):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        monkeypatch.delenv("SAPCLI_MCP_LOG_LEVEL", raising=False)
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--stdio"])

        mock_basic.assert_not_called()

    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_stdio_debug_mode_logs_to_stderr(self, mock_create, monkeypatch, capsys):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        monkeypatch.delenv("SAPCLI_MCP_LOG_LEVEL", raising=False)
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--stdio", "--log-level", "DEBUG"])

        captured = capsys.readouterr()
        # Pin a substring distinctive to the actual log message
        # ("may be visible to MCP client in stdio mode") so the test
        # doesn't pass on unrelated rewordings that happen to contain
        # the bare word "stdio".
        assert "MCP client in stdio mode" in captured.err

    @patch("sapclimcp.cli.logging.basicConfig")
    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_log_level_from_env(self, mock_create, mock_basic, monkeypatch):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        monkeypatch.setenv("SAPCLI_MCP_LOG_LEVEL", "warning")
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--stdio"])

        mock_basic.assert_called_once()
        kwargs = mock_basic.call_args.kwargs
        assert kwargs["level"] == logging.WARNING
        assert kwargs["stream"] is sys.stderr
        assert kwargs["force"] is True

    @patch("sapclimcp.cli.logging.basicConfig")
    @patch("sapclimcp.cli.create_mcp_server")
    def test_main_log_level_invalid_env_skips(self, mock_create, mock_basic, monkeypatch):
        monkeypatch.delenv("SAPCLI_MCP_CONFIG", raising=False)
        monkeypatch.setenv("SAPCLI_MCP_LOG_LEVEL", "VERBOSE")
        mock_server = MagicMock()
        mock_create.return_value = mock_server

        main(["--stdio"])

        mock_basic.assert_not_called()


# ---------------------------------------------------------------------------
# Credential CLI subcommand
# ---------------------------------------------------------------------------


class TestCliCredential:
    """Tests for sapcli-mcp credential set/get/delete."""

    def test_parse_credential_set(self):
        args = parse_args(["credential", "set", "MY_KEY", "my_value"])
        assert args.command == "credential"
        assert args.cred_action == "set"
        assert args.key == "MY_KEY"
        assert args.value == "my_value"

    def test_parse_credential_set_no_value(self):
        args = parse_args(["credential", "set", "MY_KEY"])
        assert args.value is None

    def test_parse_credential_get(self):
        args = parse_args(["credential", "get", "MY_KEY"])
        assert args.command == "credential"
        assert args.cred_action == "get"
        assert args.key == "MY_KEY"

    def test_parse_credential_delete(self):
        args = parse_args(["credential", "delete", "MY_KEY"])
        assert args.command == "credential"
        assert args.cred_action == "delete"
        assert args.key == "MY_KEY"

    @patch("sapclimcp.cli.keyring")
    def test_credential_set(self, mock_keyring, capsys):
        main(["credential", "set", "TEST_KEY", "test_value"])
        mock_keyring.set_password.assert_called_once_with(KEYRING_SERVICE, "TEST_KEY", "test_value")
        assert "Stored credential: TEST_KEY" in capsys.readouterr().out

    @patch("sapclimcp.cli.keyring")
    def test_credential_set_from_stdin(self, mock_keyring, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", StringIO("stdin_value\n"))
        main(["credential", "set", "TEST_KEY"])
        mock_keyring.set_password.assert_called_once_with(
            KEYRING_SERVICE, "TEST_KEY", "stdin_value"
        )

    @patch("sapclimcp.cli.keyring")
    def test_credential_set_empty_exits(self, mock_keyring, monkeypatch):
        monkeypatch.setattr("sys.stdin", StringIO(""))
        with pytest.raises(SystemExit) as exc_info:
            main(["credential", "set", "TEST_KEY"])
        assert exc_info.value.code == 1
        mock_keyring.set_password.assert_not_called()

    @patch("sapclimcp.cli.keyring")
    def test_credential_get_found(self, mock_keyring, capsys):
        mock_keyring.get_password.return_value = "found_value"
        main(["credential", "get", "TEST_KEY"])
        mock_keyring.get_password.assert_called_once_with(KEYRING_SERVICE, "TEST_KEY")
        assert "found_value" in capsys.readouterr().out

    @patch("sapclimcp.cli.keyring")
    def test_credential_get_missing_exits(self, mock_keyring):
        mock_keyring.get_password.return_value = None
        with pytest.raises(SystemExit) as exc_info:
            main(["credential", "get", "MISSING_KEY"])
        assert exc_info.value.code == 1

    @patch("sapclimcp.cli.keyring")
    def test_credential_delete_found(self, mock_keyring, capsys):
        main(["credential", "delete", "TEST_KEY"])
        mock_keyring.delete_password.assert_called_once_with(KEYRING_SERVICE, "TEST_KEY")
        assert "Deleted credential: TEST_KEY" in capsys.readouterr().out

    @patch("sapclimcp.cli.keyring")
    def test_credential_delete_missing_exits(self, mock_keyring):
        # The deferred `from keyring.errors import PasswordDeleteError` in
        # _credential_delete resolves through sys.modules to the REAL
        # installed `keyring` package, not via `mock_keyring.errors`.
        # mock_keyring only stands in for the module-level `keyring`
        # binding's `delete_password` call.
        mock_keyring.delete_password.side_effect = kr_errors.PasswordDeleteError("not found")
        with pytest.raises(SystemExit) as exc_info:
            main(["credential", "delete", "MISSING_KEY"])
        assert exc_info.value.code == 1

    def test_credential_no_action_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["credential"])
        assert exc_info.value.code == 1


class TestKeyringMissing:
    """Tests for the soft-import fallback when keyring is not installed.

    Simulates `import keyring` failure by patching `sapclimcp.cli.keyring`
    to None — this is what the module-level try/except sets when the
    optional `[keyring]` extra is not installed.
    """

    @patch("sapclimcp.cli.keyring", None)
    def test_set_exits_with_install_hint(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["credential", "set", "K", "v"])
        assert exc_info.value.code == 1
        assert KEYRING_INSTALL_HINT in capsys.readouterr().err

    @patch("sapclimcp.cli.keyring", None)
    def test_get_exits_with_install_hint(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["credential", "get", "K"])
        assert exc_info.value.code == 1
        assert KEYRING_INSTALL_HINT in capsys.readouterr().err

    @patch("sapclimcp.cli.keyring", None)
    def test_delete_exits_with_install_hint(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["credential", "delete", "K"])
        assert exc_info.value.code == 1
        assert KEYRING_INSTALL_HINT in capsys.readouterr().err
