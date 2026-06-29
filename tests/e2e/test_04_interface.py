"""Interface lifecycle: create -> write -> activate -> read -> delete."""

import pytest
import pytest_asyncio

from .helpers import call_tool_check, call_tool_ok, safe_delete


class TestInterfaceLifecycle:
    """Full CRUD lifecycle for an ABAP interface."""

    _failed: bool = False
    _intf_name: str = ""

    @pytest.fixture(autouse=True, scope="class")
    def setup_names(self, run_id):
        TestInterfaceLifecycle._intf_name = f"ZIF_E2E_{run_id}"
        TestInterfaceLifecycle._failed = False

    @pytest_asyncio.fixture(autouse=True, scope="class", loop_scope="session")
    async def cleanup(self, mcp_client, system_name):
        yield
        await safe_delete(
            mcp_client,
            "abap_interface_delete",
            {
                "name": [self.__class__._intf_name],
                "system": system_name,
            },
        )

    @pytest.fixture(autouse=True)
    def skip_if_prior_failed(self):
        if self.__class__._failed:
            pytest.skip("prior lifecycle step failed")

    async def test_01_create(self, mcp_client, system_name, package_name):
        """Create the interface."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_interface_create",
                {
                    "name": self._intf_name,
                    "description": "E2E test interface",
                    "package": package_name,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_02_write(self, mcp_client, system_name):
        """Write interface definition."""
        source = (
            f"INTERFACE {self._intf_name.lower()} PUBLIC.\n"
            "  METHODS get_id RETURNING VALUE(rv_id) TYPE string.\n"
            "ENDINTERFACE.\n"
        )
        try:
            await call_tool_ok(
                mcp_client,
                "abap_interface_write",
                {
                    "name": self._intf_name,
                    "source_data": source,
                    "no_check": False,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_03_activate(self, mcp_client, system_name):
        """Activate the interface."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_interface_activate",
                {
                    "name": [self._intf_name],
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_04_read(self, mcp_client, system_name):
        """Read back and verify content."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_interface_read",
                {
                    "name": self._intf_name,
                    "system": system_name,
                },
            )
            assert "get_id" in content
        except Exception:
            self.__class__._failed = True
            raise

    async def test_05_delete(self, mcp_client, system_name):
        """Delete the interface and verify it's gone."""
        await call_tool_ok(
            mcp_client,
            "abap_interface_delete",
            {
                "name": [self._intf_name],
                "system": system_name,
            },
        )
        success, _, _ = await call_tool_check(
            mcp_client,
            "abap_interface_read",
            {
                "name": self._intf_name,
                "system": system_name,
            },
        )
        assert not success, "Interface should not exist after deletion"
