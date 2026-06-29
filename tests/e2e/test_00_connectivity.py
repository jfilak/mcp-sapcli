"""Smoke test: verify connectivity to the SAP system."""

import pytest

from .helpers import call_tool_check, call_tool_ok


class TestConnectivity:
    """Verify basic connectivity to the SAP sandbox."""

    _failed: bool = False

    @pytest.fixture(autouse=True)
    def skip_if_prior_failed(self):
        if self.__class__._failed:
            pytest.skip("prior connectivity check failed")

    async def test_01_system_info(self, mcp_client, system_name):
        """Verify system is reachable via abap_abap_systeminfo."""
        try:
            content = await call_tool_ok(
                mcp_client, "abap_abap_systeminfo", {"system": system_name}
            )
            assert "SID" in content
        except Exception:
            self.__class__._failed = True
            raise

    async def test_02_package_list_tmp(self, mcp_client, system_name):
        """Verify $TMP is listable (basic ADT connectivity check)."""
        try:
            content = await call_tool_ok(
                mcp_client,
                "abap_package_list",
                {
                    "name": "$TMP",
                    "system": system_name,
                },
            )
            # $TMP always exists — call_tool_ok guarantees success
            assert content  # non-empty response
        except Exception:
            self.__class__._failed = True
            raise

    async def test_03_gcts_repolist(self, mcp_client, system_name):
        """Verify gCTS connectivity (different conn_type than ADT)."""
        success, log_msgs, content = await call_tool_check(
            mcp_client, "abap_gcts_repolist", {"system": system_name}
        )
        if not success:
            pytest.skip(f"gCTS not available on this system: {log_msgs}")
        assert content  # non-empty response
