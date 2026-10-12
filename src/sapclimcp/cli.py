"""CLI entry point for the sapcli MCP server."""

import argparse
import logging
import os
import sys

try:
    import keyring  # type: ignore[import-not-found]
except ImportError:
    # Soft-import: keyring is an optional dependency. The `credential` subcommands
    # raise a clear, actionable error when invoked without it installed.
    keyring = None  # type: ignore[assignment]

from sapclimcp.config import KEYRING_SERVICE
from sapclimcp.errors import format_keyring_missing, format_startup_error
from sapclimcp.server import create_mcp_server

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _require_keyring() -> None:
    """Exit with a clear message if keyring is not installed."""
    if keyring is None:
        print(format_keyring_missing(), file=sys.stderr)
        sys.exit(1)


def _credential_set(args: argparse.Namespace) -> None:
    """Store a credential in the OS keyring."""
    _require_keyring()
    assert keyring is not None  # post-guard invariant for type-checker + readers
    value = args.value if args.value is not None else sys.stdin.readline().rstrip("\r\n")
    if not value:
        print("No value provided (pass as argument or pipe via stdin)", file=sys.stderr)
        sys.exit(1)
    keyring.set_password(KEYRING_SERVICE, args.key, value)
    print(f"Stored credential: {args.key}")


def _credential_get(args: argparse.Namespace) -> None:
    """Retrieve a credential from the OS keyring."""
    _require_keyring()
    assert keyring is not None  # post-guard invariant for type-checker + readers
    value = keyring.get_password(KEYRING_SERVICE, args.key)
    if value is None:
        print(f"No credential found for key: {args.key}", file=sys.stderr)
        sys.exit(1)
    print(value)


def _credential_delete(args: argparse.Namespace) -> None:
    """Delete a credential from the OS keyring."""
    _require_keyring()
    # Make the post-guard invariant machine-checkable: the deferred
    # `from keyring.errors` below assumes `keyring is not None`.
    assert keyring is not None
    # Defer the keyring.errors lookup until after _require_keyring() has
    # confirmed `keyring is not None` — keeps the no-keyring code path
    # free of attribute access on the soft-imported module.
    from keyring.errors import PasswordDeleteError  # pylint: disable=import-outside-toplevel

    try:
        keyring.delete_password(KEYRING_SERVICE, args.key)
        print(f"Deleted credential: {args.key}")
    except PasswordDeleteError:
        print(f"No credential found for key: {args.key}", file=sys.stderr)
        sys.exit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="MCP server exposing sapcli commands as tools")

    subparsers = parser.add_subparsers(dest="command")

    # credential subcommand
    cred_parser = subparsers.add_parser("credential", help="Manage credentials in the OS keyring")
    cred_sub = cred_parser.add_subparsers(dest="cred_action")

    set_parser = cred_sub.add_parser("set", help="Store a credential")
    set_parser.add_argument("key", help="Credential key (e.g. DEV, SBX.password)")
    set_parser.add_argument(
        "value",
        nargs="?",
        default=None,
        help="Credential value (read from stdin if omitted)",
    )

    get_parser = cred_sub.add_parser("get", help="Retrieve a credential")
    get_parser.add_argument("key", help="Credential key")

    del_parser = cred_sub.add_parser("delete", help="Delete a credential")
    del_parser.add_argument("key", help="Credential key")

    # server flags (used when no subcommand)
    parser.add_argument(
        "--experimental",
        action="store_true",
        help="Expose all meaningful sapcli commands as tools (not just verified ones)",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="Path to JSON config file with system definitions (env: SAPCLI_MCP_CONFIG)",
    )

    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run in stdio transport mode (for Claude Code / MCP clients)",
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1, HTTP mode only)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000, HTTP mode only)",
    )

    parser.add_argument(
        "--log-level",
        default=None,
        type=str.upper,
        choices=list(_VALID_LOG_LEVELS),
        help="Set logging level (output goes to stderr; env: SAPCLI_MCP_LOG_LEVEL)",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    """Run the sapcli MCP server."""
    args = parse_args(argv)

    # Configure logging early so it applies to all code paths
    env_level = os.environ.get("SAPCLI_MCP_LOG_LEVEL", "").upper()
    log_level = args.log_level or (env_level if env_level in _VALID_LOG_LEVELS else None)
    if log_level:
        logging.basicConfig(
            level=getattr(logging, log_level),
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            stream=sys.stderr,
            force=True,
        )
        if args.stdio and log_level == "DEBUG":
            logging.getLogger(__name__).debug(
                "DEBUG logging active on stderr — may be visible to MCP client in stdio mode"
            )

    # Handle credential subcommand
    if args.command == "credential":
        if args.cred_action == "set":
            _credential_set(args)
        elif args.cred_action == "get":
            _credential_get(args)
        elif args.cred_action == "delete":
            _credential_delete(args)
        else:
            print("Usage: sapcli-mcp credential {set|get|delete}", file=sys.stderr)
            sys.exit(1)
        return

    config_path = args.config or os.environ.get("SAPCLI_MCP_CONFIG")

    try:
        server = create_mcp_server(
            experimental=args.experimental,
            config_path=config_path,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        sys.exit(format_startup_error(exc))

    try:
        if args.stdio:
            server.run(transport="stdio")
        else:
            server.run(transport="http", host=args.host, port=args.port)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        sys.exit(format_startup_error(exc))
