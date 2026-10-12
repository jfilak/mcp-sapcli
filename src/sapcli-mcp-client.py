"""
MCP client for sapcli server - supports both HTTP and local in-memory modes.
"""

import argparse
import asyncio
import json

from fastmcp import Client


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="MCP client for sapcli server")

    # Connection mode
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--http",
        metavar="URL",
        help="Connect to HTTP server at the specified URL (e.g., http://localhost:8000/mcp)",
    )
    mode_group.add_argument(
        "--local", action="store_true", help="Run with local in-memory MCP server"
    )

    # Action to perform
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--list",
        action="store_true",
        dest="list_tools",
        default=True,
        help="List available tools (default)",
    )
    action_group.add_argument(
        "--list-md",
        action="store_true",
        dest="list_tools_md",
        help="List available tools in Markdown format (name and description)",
    )
    action_group.add_argument(
        "--inspect", metavar="TOOL", help="Print the definition of the specified tool"
    )
    action_group.add_argument(
        "--execute", action="store_true", help="Execute the preconfigured tool"
    )
    action_group.add_argument(
        "--test-program",
        action="store_true",
        help="Test ABAP program lifecycle: create package, create program, "
        "write source, read source, activate program",
    )

    # Experimental mode
    parser.add_argument(
        "--experimental",
        action="store_true",
        help="Expose all meaningful sapcli commands as tools (not just verified ones)",
    )

    return parser.parse_args()


def create_client(args):
    """Create MCP client based on command line arguments."""
    if args.http:
        return Client(args.http)

    # Local in-memory mode - import and use the server directly
    # Import here to avoid loading SAP modules when using HTTP mode
    # pylint: disable=import-outside-toplevel
    from sapclimcp.server import create_mcp_server

    mcp = create_mcp_server(name="sapcli-local", experimental=args.experimental)
    return Client(mcp)


async def list_tools(client):
    """List available tools from the MCP server."""
    async with client:
        await client.ping()

        tools = await client.list_tools()
        for t in tools:
            print(t.name)


async def list_tools_md(client):
    """List available tools in Markdown format."""
    async with client:
        await client.ping()

        tools = await client.list_tools()
        for t in tools:
            desc = (t.description or "").split("\n")[0].strip()
            print(f"- **{t.name}** - {desc}")


async def inspect_tool(client, tool_name: str):
    """Print the definition of the specified tool."""
    async with client:
        await client.ping()

        tools = await client.list_tools()
        for t in tools:
            if t.name == tool_name:
                tool_def = {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                print(json.dumps(tool_def, indent=2))
                return

        print(f"Tool not found: {tool_name}")


async def execute_tool(client):
    """Execute the preconfigured tool."""

    http_connection_parameters = {
        "ashost": "localhost",
        "client": "001",
        "user": "DEVELOPER",
        "password": "ABAPtr2023#00",
        "port": 50001,
        "ssl": True,
        "verify": False,
    }

    async with client:
        print("### ping ###")
        await client.ping()

        print("### abap_package_stat ###")
        package_stat_parameters = dict(http_connection_parameters)
        package_stat_parameters.update({"name": "SOOL"})

        result = await client.call_tool(
            "abap_package_stat",
            package_stat_parameters,
        )

        print(result)

        print("### abap_package_list ###")
        package_list_parameters = dict(http_connection_parameters)
        package_list_parameters.update(
            {
                "name": "SOOL",
                "recursive": "true",
            }
        )

        result = await client.call_tool(
            "abap_package_list",
            package_list_parameters,
        )

        print(result)

        print("### abap_gcts_repolist ###")
        result = await client.call_tool(
            "abap_gcts_repolist",
            http_connection_parameters,
        )

        print(result)

        print("### abap_class_read ###")
        class_read_parameters = dict(http_connection_parameters)
        class_read_parameters.update({"name": "CL_MESSAGE"})

        result = await client.call_tool(
            "abap_class_read",
            class_read_parameters,
        )

        print(result)

        print("### abap_ddl_read ###")
        ddl_read_parameters = dict(http_connection_parameters)
        ddl_read_parameters.update({"name": "/DMO/I_Connection_R"})

        result = await client.call_tool(
            "abap_ddl_read",
            ddl_read_parameters,
        )

        print(result)

        print("### abap_aunit_run ###")
        aunit_parameters = dict(http_connection_parameters)
        aunit_parameters.update(
            {
                "type": "package",
                "name": "SOOL",
            }
        )

        result = await client.call_tool(
            "abap_aunit_run",
            aunit_parameters,
        )

        print(result)

        print("### abap_atc_run ###")
        atc_parameters = dict(http_connection_parameters)
        atc_parameters.update(
            {
                "type": "package",
                "name": "SOOL",
            }
        )

        result = await client.call_tool(
            "abap_atc_run",
            atc_parameters,
        )

        print(result)


async def test_program(client):
    """Test ABAP program lifecycle: create, write source, read, activate."""

    mock_connection_parameters = {
        "ashost": "localhost",
        "client": "100",
        "user": "DEVELOPER",
        "password": "Welcome1!",
        "port": 50001,
        "ssl": False,
        "verify": False,
    }

    async with client:
        print("### ping ###")
        await client.ping()

        print("### abap_package_create ###")
        package_create_parameters = dict(mock_connection_parameters)
        package_create_parameters.update(
            {
                "name": "ZTEST_MCP_PKG",
                "description": "Test package for MCP program test",
                "software_component": "LOCAL",
            }
        )

        result = await client.call_tool(
            "abap_package_create",
            package_create_parameters,
        )

        print(result)

        print("### abap_program_create ###")
        program_create_parameters = dict(mock_connection_parameters)
        program_create_parameters.update(
            {
                "name": "ZTEST_MCP_PROG",
                "description": "Test program created via MCP",
                "package": "ZTEST_MCP_PKG",
            }
        )

        result = await client.call_tool(
            "abap_program_create",
            program_create_parameters,
        )

        print(result)

        print("### abap_program_write ###")
        program_write_parameters = dict(mock_connection_parameters)
        program_write_parameters.update(
            {
                "name": "ZTEST_MCP_PROG",
                "source_data": ("REPORT ztest_mcp_prog.\n\nWRITE: / 'Hello from MCP'.\n"),
            }
        )

        result = await client.call_tool(
            "abap_program_write",
            program_write_parameters,
        )

        print(result)

        print("### abap_program_read ###")
        program_read_parameters = dict(mock_connection_parameters)
        program_read_parameters.update(
            {
                "name": "ZTEST_MCP_PROG",
            }
        )

        result = await client.call_tool(
            "abap_program_read",
            program_read_parameters,
        )

        print(result)

        print("### abap_program_activate ###")
        program_activate_parameters = dict(mock_connection_parameters)
        program_activate_parameters.update(
            {
                "name": ["ZTEST_MCP_PROG"],
            }
        )

        result = await client.call_tool(
            "abap_program_activate",
            program_activate_parameters,
        )

        print(result)


async def main(client, args):
    """Main client logic."""
    if args.test_program:
        await test_program(client)
    elif args.execute:
        await execute_tool(client)
    elif args.inspect:
        await inspect_tool(client, args.inspect)
    elif args.list_tools_md:
        await list_tools_md(client)
    else:
        await list_tools(client)


if __name__ == "__main__":
    parsed_args = parse_args()
    mcp_client = create_client(parsed_args)
    asyncio.run(main(mcp_client, parsed_args))
