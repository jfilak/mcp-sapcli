from io import StringIO
from typing import NamedTuple
from types import SimpleNamespace

from sap import (
    adt,
    cli,
    errors,
)

import sap.cli.core
import sap.cli.package

from fastmcp import FastMCP

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
        For HTTP features, you must provide:
        - HTTPPORT   : the HTTP port
        - USE_SSL    : true for HTTPS; false for naked HTTP
        - VERIFY_SSL : true to check ABAP server cert validity; otherwise false
        For RFC features, you must have the PyRFC library with NWRFC SDK
        installed on your machine and then you must provide:
        - SYSNR : 2 digits from 00 to 99 which will be translated to port
    """
)


class OutputBuffer(sap.cli.core.PrintConsole):
    def __init__(self):
        self.std_output = StringIO()
        self.err_output = StringIO()

        super(OutputBuffer, self).__init__(out_file=self.std_output, err_file=self.err_output)

    @property
    def capout(self):
        return self.std_output.getvalue()

    @property
    def caperr(self):
        return self.err_output.getvalue()

    def reset(self):
        self.std_output.truncate(0)
        self.std_output.seek(0)
        self.err_output.truncate


class OperationResult(NamedTuple):
    Success: bool
    LogMessages: list[str]
    Contents: str


@mcp.tool
def ABAP_ADT_ConnectionTest(ashost: str, http_port: int, client: str, user: str, password: str, use_ssl: bool, verify_ssl: bool) -> OperationResult:
    try:
        adt.Connection(ashost, client, user, password, http_port, ssl=use_ssl, verify=verify_ssl)
    except errors.SAPCliError as ex:
        return OperationResult(Success=False, LogMessages=[str(ex)])

    return OperationResult(Success=True, LogMessages=['Connected successfully'], Contents="")


@mcp.tool
def ABAP_ADT_PackageGetDetails(ashost: str, http_port: int, client: str, user: str, password: str, use_ssl: bool, verify_ssl: bool, name: str) -> OperationResult:

    output_buffer = OutputBuffer()

    try:
        adt_conn = adt.Connection(ashost, client, user, password, http_port, ssl=use_ssl, verify=verify_ssl)
        sap.cli.core.set_console(output_buffer)
        sap.cli.package.stat(adt_conn, SimpleNamespace(name=name))
    except errors.SAPCliError as ex:
        return OperationResult(Success=False, LogMessages=[str(ex)]+output_buffer.caperr)

    return OperationResult(Success=True, LogMessages=[output_buffer.caperr], Contents=output_buffer.capout)



if __name__ == "__main__":
    #mcp.run()
    mcp.run(transport="http", host="127.0.0.1", port=8000)
