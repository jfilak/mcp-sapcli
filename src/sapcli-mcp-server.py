"""
Export sapcli commands as MCP tools.
"""

import argparse

from fastmcp import FastMCP

from sapclimcp.mcptools import transform_sapcli_commands

# List of verified and supported sapcli commands exposed as MCP tools
VERIFIED_COMMANDS = [
    "abap_package_list",
    "abap_package_stat",
    "abap_gcts_repolist",
    "abap_class_read",
    "abap_aunit_run",
    "abap_atc_run",
    "abap_ddl_read",
]

MCP_SERVER_INSTRUCTIONS = """
    This server connects to various SAP products and allows you to read and
    write their contents.
    For ABAP functions you can use features that requires HTTP or RFC.
    Both HTTP and RFC requires:
    - ASHOST   : Application Server host name
    - CLIENT   : ABAP Client (3 upper case letters+digits)
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


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="MCP server exposing sapcli commands as tools"
    )

    parser.add_argument(
        "--experimental",
        action="store_true",
        help="Expose all meaningful sapcli commands as tools (not just verified ones)"
    )

    return parser.parse_args()


def create_mcp_server(name: str = "sapcli", experimental: bool = False) -> FastMCP:
    """Create and initialize the MCP server with sapcli commands.

    Args:
        name: Name for the MCP server instance.
        experimental: If True, expose all meaningful commands; otherwise only verified ones.

    Returns:
        Initialized FastMCP server with registered sapcli tools.
    """
    mcp = FastMCP(name=name, instructions=MCP_SERVER_INSTRUCTIONS)
    allowed_commands = None if experimental else VERIFIED_COMMANDS
    transform_sapcli_commands(mcp, allowed_commands)
    return mcp


if __name__ == "__main__":
    args = parse_args()
    server = create_mcp_server(experimental=args.experimental)
    server.run(transport="http", host="127.0.0.1", port=8000)
