"""Function group + module lifecycle: create -> write -> activate -> read -> delete."""

import pytest
import pytest_asyncio

from .helpers import call_tool_check, call_tool_ok, safe_delete


class TestFunctionGroupLifecycle:
    """Full lifecycle for a function group and function module."""

    _failed: bool = False
    _fg_name: str = ""
    _fm_name: str = ""

    @pytest.fixture(autouse=True, scope="class")
    def setup_names(self, run_id):
        TestFunctionGroupLifecycle._fg_name = f"ZE2E_FG_{run_id}"
        TestFunctionGroupLifecycle._fm_name = f"ZE2E_FM_{run_id}"
        TestFunctionGroupLifecycle._failed = False

    @pytest_asyncio.fixture(autouse=True, scope="class", loop_scope="session")
    async def cleanup(self, mcp_client, system_name):
        yield
        # Deleting the function group cascades to the FM
        await safe_delete(
            mcp_client,
            "abap_functiongroup_delete",
            {
                "name": [self.__class__._fg_name],
                "system": system_name,
            },
        )

    @pytest.fixture(autouse=True)
    def skip_if_prior_failed(self):
        if self.__class__._failed:
            pytest.skip("prior lifecycle step failed")

    async def test_01_create_group(self, mcp_client, system_name, package_name):
        """Create the function group."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_functiongroup_create",
                {
                    "name": self._fg_name,
                    "description": "E2E test function group",
                    "package": package_name,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_02_create_module(self, mcp_client, system_name):
        """Create a function module in the group."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_functionmodule_create",
                {
                    "name": self._fm_name,
                    "group": self._fg_name,
                    "description": "E2E test function module",
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_03_write_module(self, mcp_client, system_name):
        """Write source code to the function module."""
        source = (
            f"FUNCTION {self._fm_name.lower()}.\n"
            '  " E2E test function module - no parameters\n'
            "ENDFUNCTION.\n"
        )
        try:
            await call_tool_ok(
                mcp_client,
                "abap_functionmodule_write",
                {
                    "name": self._fm_name,
                    "group": self._fg_name,
                    "source_data": source,
                    "no_check": False,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_04_activate_module(self, mcp_client, system_name):
        """Activate the function module."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_functionmodule_activate",
                {
                    "name": [self._fm_name],
                    "group": self._fg_name,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_05_read_module(self, mcp_client, system_name):
        """Read back function module source."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_functionmodule_read",
                {
                    "name": self._fm_name,
                    "group": self._fg_name,
                    "system": system_name,
                },
            )
            assert "FUNCTION" in content.upper()
        except Exception:
            self.__class__._failed = True
            raise

    async def test_06_read_group(self, mcp_client, system_name):
        """Read function group main include."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_functiongroup_read",
                {
                    "name": self._fg_name,
                    "system": system_name,
                },
            )
            assert content  # non-empty response
        except Exception:
            self.__class__._failed = True
            raise

    async def test_07_delete_group(self, mcp_client, system_name):
        """Delete the function group (cascading delete of FM) and verify."""
        await call_tool_ok(
            mcp_client,
            "abap_functiongroup_delete",
            {
                "name": [self._fg_name],
                "system": system_name,
            },
        )
        success, _, _ = await call_tool_check(
            mcp_client,
            "abap_functiongroup_read",
            {
                "name": self._fg_name,
                "system": system_name,
            },
        )
        assert not success, "Function group should not exist after deletion"
