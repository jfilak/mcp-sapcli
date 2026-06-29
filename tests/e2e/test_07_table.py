"""Table lifecycle: create -> write -> activate -> read -> delete."""

import pytest
import pytest_asyncio

from .helpers import call_tool_check, call_tool_ok, safe_delete


class TestTableLifecycle:
    """Full CRUD lifecycle for a database table."""

    _failed: bool = False
    _table_name: str = ""

    @pytest.fixture(autouse=True, scope="class")
    def setup_names(self, run_id):
        TestTableLifecycle._table_name = f"ZE2E_TAB_{run_id}"
        TestTableLifecycle._failed = False

    @pytest_asyncio.fixture(autouse=True, scope="class", loop_scope="session")
    async def cleanup(self, mcp_client, system_name):
        yield
        await safe_delete(
            mcp_client,
            "abap_table_delete",
            {
                "name": [self.__class__._table_name],
                "system": system_name,
            },
        )

    @pytest.fixture(autouse=True)
    def skip_if_prior_failed(self):
        if self.__class__._failed:
            pytest.skip("prior lifecycle step failed")

    async def test_01_create(self, mcp_client, system_name, package_name):
        """Create the table."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_table_create",
                {
                    "name": self._table_name,
                    "description": "E2E test table",
                    "package": package_name,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_02_write(self, mcp_client, system_name):
        """Write table definition."""
        source = (
            "@EndUserText.label : 'E2E test table'\n"
            "@AbapCatalog.enhancement.category : #NOT_EXTENSIBLE\n"
            "@AbapCatalog.tableCategory : #TRANSPARENT\n"
            "@AbapCatalog.deliveryClass : #L\n"
            f"define table {self._table_name.lower()} {{\n"
            "  key mandt : mandt not null;\n"
            "  key id    : sysuuid_x16 not null;\n"
            "  name      : char30;\n"
            "}\n"
        )
        try:
            await call_tool_ok(
                mcp_client,
                "abap_table_write",
                {
                    "name": self._table_name,
                    "source_data": source,
                    "no_check": False,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_03_activate(self, mcp_client, system_name):
        """Activate the table."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_table_activate",
                {
                    "name": [self._table_name],
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_04_read(self, mcp_client, system_name):
        """Read back table definition and verify."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_table_read",
                {
                    "name": self._table_name,
                    "system": system_name,
                },
            )
            assert "mandt" in content.lower()
        except Exception:
            self.__class__._failed = True
            raise

    async def test_05_delete(self, mcp_client, system_name):
        """Delete the table and verify it's gone."""
        await call_tool_ok(
            mcp_client,
            "abap_table_delete",
            {
                "name": [self._table_name],
                "system": system_name,
            },
        )
        success, _, _ = await call_tool_check(
            mcp_client,
            "abap_table_read",
            {
                "name": self._table_name,
                "system": system_name,
            },
        )
        assert not success, "Table should not exist after deletion"
