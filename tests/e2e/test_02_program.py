"""Program lifecycle: create -> write -> activate -> read -> modify -> delete."""

import pytest
import pytest_asyncio

from .helpers import call_tool_check, call_tool_ok, safe_delete


class TestProgramLifecycle:
    """Full CRUD lifecycle for an ABAP program."""

    _failed: bool = False
    _prog_name: str = ""

    @pytest.fixture(autouse=True, scope="class")
    def setup_names(self, run_id):
        """Set up object names for the class."""
        TestProgramLifecycle._prog_name = f"ZE2E_PROG_{run_id}"
        TestProgramLifecycle._failed = False

    @pytest_asyncio.fixture(autouse=True, scope="class", loop_scope="session")
    async def cleanup(self, mcp_client, system_name, run_id):
        """Ensure program is deleted after all tests in this class."""
        yield
        await safe_delete(
            mcp_client,
            "abap_program_delete",
            {
                "name": [self.__class__._prog_name],
                "system": system_name,
            },
        )

    @pytest.fixture(autouse=True)
    def skip_if_prior_failed(self):
        """Skip this test if a prior step in the lifecycle already failed."""
        if self.__class__._failed:
            pytest.skip("prior lifecycle step failed")

    async def test_01_create(self, mcp_client, system_name, package_name):
        """Create the program."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_program_create",
                {
                    "name": self._prog_name,
                    "description": "E2E test program",
                    "package": package_name,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_02_write(self, mcp_client, system_name):
        """Write source code to the program."""
        source = f"REPORT {self._prog_name.lower()}.\n\nWRITE: / 'Hello from E2E test'.\n"
        try:
            await call_tool_ok(
                mcp_client,
                "abap_program_write",
                {
                    "name": self._prog_name,
                    "source_data": source,
                    "no_check": False,
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_03_activate(self, mcp_client, system_name):
        """Activate the program."""
        try:
            await call_tool_ok(
                mcp_client,
                "abap_program_activate",
                {
                    "name": [self._prog_name],
                    "system": system_name,
                },
            )
        except Exception:
            self.__class__._failed = True
            raise

    async def test_04_read_and_verify(self, mcp_client, system_name):
        """Read back source and verify it matches what was written."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_program_read",
                {
                    "name": self._prog_name,
                    "system": system_name,
                },
            )
            assert "Hello from E2E test" in content
        except Exception:
            self.__class__._failed = True
            raise

    async def test_05_modify_and_reverify(self, mcp_client, system_name):
        """Modify source, activate, read back, verify change."""
        new_source = f"REPORT {self._prog_name.lower()}.\n\nWRITE: / 'Modified by E2E test'.\n"
        try:
            await call_tool_ok(
                mcp_client,
                "abap_program_write",
                {
                    "name": self._prog_name,
                    "source_data": new_source,
                    "no_check": False,
                    "system": system_name,
                },
            )
            await call_tool_ok(
                mcp_client,
                "abap_program_activate",
                {
                    "name": [self._prog_name],
                    "system": system_name,
                },
            )
            content = await call_tool_ok(
                mcp_client,
                "abap_program_read",
                {
                    "name": self._prog_name,
                    "system": system_name,
                },
            )
            assert "Modified by E2E test" in content
        except Exception:
            self.__class__._failed = True
            raise

    async def test_06_delete(self, mcp_client, system_name):
        """Delete the program and verify it's gone."""
        await call_tool_ok(
            mcp_client,
            "abap_program_delete",
            {
                "name": [self._prog_name],
                "system": system_name,
            },
        )
        success, _, _ = await call_tool_check(
            mcp_client,
            "abap_program_read",
            {
                "name": self._prog_name,
                "system": system_name,
            },
        )
        assert not success, "Program should not exist after deletion"
