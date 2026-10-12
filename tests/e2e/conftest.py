"""
E2E test fixtures for mcp-sapcli.

Required environment variables:
    E2E_SAP_HOST     - SAP system hostname
    E2E_SAP_PORT     - SAP system port (e.g. 50001)
    E2E_SAP_CLIENT   - SAP client (e.g. 100)
    E2E_SAP_USER     - SAP user
    E2E_SAP_PASSWORD - SAP password

Optional:
    E2E_SAP_SSL      - Use SSL (true/false, default true)
    E2E_SAP_VERIFY   - Verify SSL cert (true/false, default false for sandbox
                       systems with self-signed certificates)
    E2E_SAP_SYSTEM   - System name in config (default: "E2E")

Credentials target a sandbox system with limited authorizations.
SSL verification is disabled by default for internal sandboxes with
self-signed certificates.
"""

import json
import logging
import os
import secrets

import pytest
import pytest_asyncio
from fastmcp import Client

logger = logging.getLogger("e2e")

# ─── pytest configuration (E2E-only) ────────────────────────────────────────


def pytest_collection_modifyitems(items):
    """Apply 'e2e' marker to all tests in this directory."""
    e2e_marker = pytest.mark.e2e
    for item in items:
        if "/e2e/" in item.nodeid or "\\e2e\\" in item.nodeid:
            item.add_marker(e2e_marker)


# ─── Skip guard ─────────────────────────────────────────────────────────────

REQUIRED_ENV_VARS = [
    "E2E_SAP_HOST",
    "E2E_SAP_PORT",
    "E2E_SAP_CLIENT",
    "E2E_SAP_USER",
    "E2E_SAP_PASSWORD",
]

_missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
_skip_reason = f"E2E tests require environment variables: {', '.join(_missing)}"


def _check_env():
    """Raise pytest.skip if required env vars are missing."""
    if _missing:
        pytest.skip(_skip_reason, allow_module_level=True)


# ─── Run ID ─────────────────────────────────────────────────────────────────


def _generate_run_id() -> str:
    """Generate a short unique ID for this test run (12 random hex chars).

    Used in package/object names on a shared sandbox, so the entropy keeps
    concurrent runs from colliding on (and cleaning up) each other's artifacts.
    """
    return secrets.token_hex(6).upper()


# ─── Config file creation ───────────────────────────────────────────────────


def _create_config_file(tmp_dir: str) -> str:
    """Create a temporary config JSON file from environment variables."""
    system_name = os.environ.get("E2E_SAP_SYSTEM", "E2E")
    config = {
        "systems": {
            system_name: {
                "ashost": os.environ["E2E_SAP_HOST"],
                "port": int(os.environ["E2E_SAP_PORT"]),
                "client": os.environ["E2E_SAP_CLIENT"],
                "ssl": os.environ.get("E2E_SAP_SSL", "true").lower() == "true",
                "verify": os.environ.get("E2E_SAP_VERIFY", "false").lower() == "true",
                "auth": "basic",
                "user": os.environ["E2E_SAP_USER"],
                "password": os.environ["E2E_SAP_PASSWORD"],
            }
        },
        "default_system": system_name,
    }
    path = os.path.join(tmp_dir, "e2e-config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f)
    os.chmod(path, 0o600)
    return path


# ─── Session-scoped fixtures ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def run_id():
    """Unique run identifier for collision-free object naming."""
    _check_env()
    rid = _generate_run_id()
    logger.info("E2E run ID: %s", rid)
    return rid


@pytest.fixture(scope="session")
def system_name():
    """The system name used in tool calls."""
    _check_env()
    return os.environ.get("E2E_SAP_SYSTEM", "E2E")


@pytest.fixture(scope="session")
def e2e_config_dir(tmp_path_factory):
    """Temporary directory for the E2E config file."""
    _check_env()
    return str(tmp_path_factory.mktemp("e2e_config"))


@pytest.fixture(scope="session")
def config_path(e2e_config_dir):
    """Path to the E2E config JSON file."""
    return _create_config_file(e2e_config_dir)


@pytest.fixture(scope="session")
def mcp_server(config_path):
    """Create the MCP server with experimental tools enabled."""
    from sapclimcp.server import create_mcp_server

    return create_mcp_server(experimental=True, config_path=config_path)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def mcp_client(mcp_server):
    """In-process MCP client connected to the test server."""
    client = Client(mcp_server)
    async with client:
        yield client


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def package_name(mcp_client, system_name, run_id):
    """Attempt to create a test package; fall back to $TMP if it fails.

    Returns the package name to use for all test objects.
    Cleanup of the package (if created) is handled by session_cleanup.
    """
    from .helpers import call_tool_check

    pkg_name = f"ZE2E_{run_id}"
    success, log_msgs, _ = await call_tool_check(
        mcp_client,
        "abap_package_create",
        {
            "name": pkg_name,
            "description": "E2E test package (auto-generated, safe to delete)",
            "package": "$TMP",
            "software_component": "LOCAL",
            "system": system_name,
        },
    )
    if success:
        logger.info("Created test package: %s", pkg_name)
        return pkg_name

    logger.warning("Package creation failed (using $TMP instead): %s", log_msgs)
    return "$TMP"


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def session_cleanup(mcp_client, system_name, run_id, package_name):
    """Final cleanup: attempt to delete the test package after all tests."""
    from .helpers import safe_delete

    yield
    if package_name != "$TMP":
        logger.info("Session cleanup: deleting package %s", package_name)
        await safe_delete(
            mcp_client,
            "abap_package_delete",
            {
                "name": package_name,
                "system": system_name,
            },
        )
