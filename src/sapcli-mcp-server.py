"""
Export sapcli commands as MCP tools.
"""

from fastmcp import FastMCP

from sapclimcp.mcptools import transform_sapcli_commands

mcp = FastMCP(
    name="sapcli",
    instructions="""
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
)


if __name__ == "__main__":
    transform_sapcli_commands(mcp)
    mcp.run(transport="http", host="127.0.0.1", port=8000)
