"""MCP server factory for sapcli.

This module contains the server creation logic, importable from the
package without module-level side effects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from sapclimcp.errors import KEYRING_INSTALL_HINT

if TYPE_CHECKING:
    from sapclimcp.config import ServerConfig

_LOGGER = logging.getLogger(__name__)

# List of verified and supported sapcli commands exposed as MCP tools
VERIFIED_COMMANDS = [
    "abap_package_list",
    "abap_package_stat",
    "abap_package_create",
    "abap_program_create",
    "abap_program_read",
    "abap_program_write",
    "abap_program_activate",
    "abap_gcts_repolist",
    "abap_class_read",
    "abap_class_write",
    "abap_include_read",
    "abap_include_write",
    "abap_interface_read",
    "abap_interface_write",
    "abap_aunit_run",
    "abap_atc_run",
    "abap_ddl_read",
    "abap_ddl_write",
]

MCP_SERVER_INSTRUCTIONS = """
    This server connects to various SAP products and allows you to read and
    write their contents.
    For ABAP functions you can use features that requires HTTP or RFC.
    Both HTTP and RFC requires:
    - ASHOST   : Application Server host name
    - CLIENT   : ABAP Client (3-digit number, e.g. 001, 100)
    - USER     : user name (case insensitive)
    - PASSWORD : password (case sensitive)
    For HTTP features (ADT, gCTS both use HTTP), you must provide:
    - HTTPPORT   : the HTTP port
    - USE_SSL    : true for HTTPS; false for naked HTTP
    - VERIFY_SSL : true to check ABAP server cert validity; otherwise false
    For RFC features, you must have the PyRFC library with NWRFC SDK
    installed on your machine and then you must provide:
    - SYSNR : 2 digits from 00 to 99 which will be translated to port
"""

MCP_SERVER_INSTRUCTIONS_MANAGED = """
    This server connects to SAP ABAP systems via ADT (ABAP Development Tools)
    REST API. Connections are managed server-side — you do not need to provide
    credentials.

    Available systems: {systems}
    {system_guidance}
"""


def create_mcp_server(
    name: str = "sapcli",
    experimental: bool = False,
    config_path: str | None = None,
) -> FastMCP:
    """Create and initialize the MCP server with sapcli commands.

    Args:
        name: Name for the MCP server instance.
        experimental: If True, expose all meaningful commands; otherwise only verified ones.
        config_path: Optional path to JSON config file with system definitions.

    Returns:
        Initialized FastMCP server with registered sapcli tools.
    """
    if config_path:
        from sapclimcp.config import ConnectionManager, load_config  # pylint: disable=import-outside-toplevel

        server_config = load_config(config_path)
        _warn_if_keyring_refs_without_keyring(server_config)
        connection_manager = ConnectionManager(server_config)
        # `system` is required (no implicit default) exactly when there is no
        # default_system — keep the instructions honest about that so the LLM
        # doesn't omit `system` on a multi-system / no-default server.
        if connection_manager.default_system:
            system_guidance = (
                f"Default system: {connection_manager.default_system}\n\n"
                "    Use the optional 'system' parameter to target a specific "
                "system; if omitted, the default system is used."
            )
        else:
            system_guidance = (
                "There is no default system, so the 'system' parameter is "
                "REQUIRED — specify which system to target on every call "
                "(allowed values are the systems listed above)."
            )
        instructions = MCP_SERVER_INSTRUCTIONS_MANAGED.format(
            systems=", ".join(connection_manager.system_names),
            system_guidance=system_guidance,
        )
    else:
        connection_manager = None
        instructions = MCP_SERVER_INSTRUCTIONS

    mcp = FastMCP(name=name, instructions=instructions)
    allowed_commands = None if experimental else VERIFIED_COMMANDS

    # Deferred: mcptools imports sap.* which may not be installed
    from sapclimcp.mcptools import transform_sapcli_commands  # pylint: disable=import-outside-toplevel

    transform_sapcli_commands(mcp, allowed_commands, connection_manager=connection_manager)
    return mcp


def _warn_if_keyring_refs_without_keyring(server_config: ServerConfig) -> None:
    """Log a warning if any system references a `keyring:` credential
    while the `keyring` package is not installed.

    Catches the misconfiguration at startup rather than at first ADT
    request, so users see the problem before connection attempts fail.

    The WARNING is intentionally summary-only (count + install hint) so
    that in stdio mode, where Python's lastResort handler can route the
    message to stderr and onward to the MCP client, we don't enumerate
    the user's credential layout (system_name × field) over the wire.
    Per-field detail is demoted to DEBUG, which only surfaces when the
    operator explicitly opts in with --log-level=DEBUG.
    """
    from sapclimcp.config import is_keyring_available  # pylint: disable=import-outside-toplevel

    if is_keyring_available():
        return

    refs = server_config.keyring_refs()
    if not refs:
        return

    _LOGGER.warning(
        "Config references %d keyring credential(s) but the 'keyring' package "
        "is not installed; these will fail at first connection. "
        "Install with: %s",
        len(refs),
        KEYRING_INSTALL_HINT,
    )
    _LOGGER.debug("keyring-referencing fields: %s", ", ".join(refs))
