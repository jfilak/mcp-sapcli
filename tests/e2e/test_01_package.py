"""Package lifecycle: verify package was created (or $TMP fallback)."""

from .helpers import call_tool_ok


class TestPackageLifecycle:
    """Verify the test package exists and is usable."""

    async def test_01_package_exists(self, mcp_client, system_name, package_name):
        """Verify the package (created or $TMP) is accessible via stat."""
        content = await call_tool_ok(
            mcp_client,
            "abap_package_stat",
            {
                "name": package_name,
                "system": system_name,
            },
        )
        assert content  # non-empty response

    async def test_02_package_listable(self, mcp_client, system_name, package_name):
        """Verify the package can be listed."""
        content = await call_tool_ok(
            mcp_client,
            "abap_package_list",
            {
                "name": package_name,
                "system": system_name,
            },
        )
        assert content  # non-empty response
