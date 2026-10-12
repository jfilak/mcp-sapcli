"""Class lifecycle: create -> write (all types) -> activate -> AUnit -> read -> delete."""

import pytest
import pytest_asyncio

from .helpers import call_tool_check, call_tool_ok, safe_delete


class TestClassLifecycle:
    """Full CRUD lifecycle for an ABAP class with AUnit."""

    _failed: bool = False
    _class_name: str = ""

    @pytest.fixture(autouse=True, scope="class")
    def setup_names(self, run_id):
        """Set up object names."""
        TestClassLifecycle._class_name = f"ZCL_E2E_{run_id}"
        TestClassLifecycle._failed = False

    @pytest_asyncio.fixture(autouse=True, scope="class", loop_scope="session")
    async def cleanup(self, mcp_client, system_name):
        """Delete class after tests."""
        yield
        await safe_delete(
            mcp_client,
            "abap_class_delete",
            {
                "name": [self.__class__._class_name],
                "system": system_name,
            },
        )

    @pytest.fixture(autouse=True)
    def skip_if_prior_failed(self):
        if self.__class__._failed:
            pytest.skip("prior lifecycle step failed")

    async def test_01_create(self, mcp_client, system_name, package_name):
        """Create the class."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_class_create",
                {
                    "name": self._class_name,
                    "description": "E2E test class",
                    "package": package_name,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_02_write_main(self, mcp_client, system_name):
        """Write main source code."""
        source = (
            f"CLASS {self._class_name.lower()} DEFINITION PUBLIC CREATE PUBLIC.\n"
            "  PUBLIC SECTION.\n"
            "    METHODS get_value RETURNING VALUE(rv_value) TYPE i.\n"
            "ENDCLASS.\n"
            "\n"
            f"CLASS {self._class_name.lower()} IMPLEMENTATION.\n"
            "  METHOD get_value.\n"
            "    rv_value = 42.\n"
            "  ENDMETHOD.\n"
            "ENDCLASS.\n"
        )
        try:
            await call_tool_ok(
                mcp_client,
                "abap_class_write",
                {
                    "name": self._class_name,
                    "source_data": source,
                    "type": "main",
                    "no_check": False,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_03_write_definitions(self, mcp_client, system_name):
        """Write local definitions (CLASS-DATA, TYPES, etc.)."""
        source = (
            '*"* use this source file for any type of declarations (class\n'
            '*"* temporary constants, local types and so on) you need for\n'
            '*"* method implementations in the private/protected section.\n'
        )
        try:
            await call_tool_ok(
                mcp_client,
                "abap_class_write",
                {
                    "name": self._class_name,
                    "source_data": source,
                    "type": "definitions",
                    "no_check": True,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_04_write_testclasses(self, mcp_client, system_name):
        """Write test class source."""
        source = (
            "CLASS ltcl_test DEFINITION FINAL FOR TESTING\n"
            "  DURATION SHORT RISK LEVEL HARMLESS.\n"
            "  PRIVATE SECTION.\n"
            "    METHODS test_get_value FOR TESTING.\n"
            "ENDCLASS.\n"
            "\n"
            "CLASS ltcl_test IMPLEMENTATION.\n"
            "  METHOD test_get_value.\n"
            f"    DATA(lo_cut) = NEW {self._class_name.lower()}( ).\n"
            "    cl_abap_unit_assert=>assert_equals(\n"
            "      act = lo_cut->get_value( )\n"
            "      exp = 42 ).\n"
            "  ENDMETHOD.\n"
            "ENDCLASS.\n"
        )
        try:
            await call_tool_ok(
                mcp_client,
                "abap_class_write",
                {
                    "name": self._class_name,
                    "source_data": source,
                    "type": "testclasses",
                    "no_check": True,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_05_activate(self, mcp_client, system_name):
        """Activate the class."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_class_activate",
                {
                    "name": [self._class_name],
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_06_read_main(self, mcp_client, system_name):
        """Read back main source."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_class_read",
                {
                    "name": self._class_name,
                    "type": "main",
                    "system": system_name,
                },
            )
            assert "get_value" in content
        except Exception:
            self.__class__._failed = True
            raise

    async def test_07_read_testclasses(self, mcp_client, system_name):
        """Read back test class source."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_class_read",
                {
                    "name": self._class_name,
                    "type": "testclasses",
                    "system": system_name,
                },
            )
            assert "ltcl_test" in content
        except Exception:
            self.__class__._failed = True
            raise

    async def test_08_run_aunit(self, mcp_client, system_name):
        """Run AUnit tests on the class."""
        import re

        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_aunit_run",
                {
                    "type": "class",
                    "name": self._class_name,
                    "system": system_name,
                },
            )
            match = re.search(r"Successful:\s*(\d+)", content)
            assert match, f"AUnit output missing 'Successful: N': {content[:300]}"
            count = int(match.group(1))
            assert count > 0, f"Expected passing tests, got Successful: {count}"
        except Exception:
            self.__class__._failed = True
            raise

    async def test_09_run_atc(self, mcp_client, system_name):
        """Run ATC checks (non-fatal — may not be configured on sandbox)."""
        success, log_msgs, _ = await call_tool_check(
            mcp_client,
            "abap_atc_run",
            {
                "type": "class",
                "name": self._class_name,
                "system": system_name,
            },
        )
        if not success:
            pytest.skip(f"ATC not configured on this system: {log_msgs}")

    async def test_10_delete(self, mcp_client, system_name):
        """Delete the class and verify it's gone."""
        await call_tool_ok(
            mcp_client,
            "abap_class_delete",
            {
                "name": [self._class_name],
                "system": system_name,
            },
        )
        success, _, _ = await call_tool_check(
            mcp_client,
            "abap_class_read",
            {
                "name": self._class_name,
                "type": "main",
                "system": system_name,
            },
        )
        assert not success, "Class should not exist after deletion"
