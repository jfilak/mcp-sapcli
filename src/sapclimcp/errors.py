"""
Error types and actionable error-message formatting for mcp-sapcli.

This module owns the package's own exception hierarchy (`ConfigError`,
`ToolInputError`) alongside the message formatters. Each formatter produces a
message with:
- What happened (brief description)
- Why (likely cause)
- What to do (concrete action)
"""


class ConfigError(Exception):
    """Raised for configuration loading or validation errors."""


class ToolInputError(Exception):
    """Raised by a tool patch when LLM-supplied input fails validation.

    Distinct on purpose from both `sap.errors.SAPCliError` (a backend/library
    failure) and a bare `ValueError` (which signals an unexpected bug).
    `_run_sapcli_command` converts this into a user-facing
    `OperationResult(Success=False)`, whereas a plain `ValueError` bubbles up to
    `SapcliCommandTool.run()`'s logger and is reported as "likely a bug". Using
    a dedicated type keeps that classification deliberate instead of catching
    every `ValueError` raised anywhere under the command call.

    Intentionally based on `Exception`, NOT `ValueError`. The whole motivation
    is that `ValueError` is too broad a net: `UnicodeEncodeError` (the live
    `abap_datapreview_osql` OSQL bug) is a `ValueError` subclass, so a
    `ValueError`-based sentinel could still be caught by a stray
    `except ValueError` and mask a genuine bug. Keeping this type outside the
    `ValueError` hierarchy guarantees it can only be caught by name.
    """


# Install hint shared between the keyring-missing error and the
# cookie-refresh action hint. The package is git-installed (not on PyPI),
# so the editable form is the correct invocation.
KEYRING_INSTALL_HINT = "pip install -e .[keyring]"


def format_keyring_missing(context: str | None = None) -> str:
    """Format the canonical "keyring extra not installed" message.

    Centralizes the install hint so the wording stays consistent across
    the credential CLI (`cli.py`) and the SecretRef resolver (`config.py`)
    instead of drifting in two places.

    Args:
        context: Optional caller-specific prefix (e.g. "Cannot resolve
            'keyring:DEV'"). When omitted, the message stands alone.
    """
    base = (
        f"The 'keyring' package is not installed. "
        f"Install with: {KEYRING_INSTALL_HINT}, "
        f"or use $ENV_VAR / literal credentials instead."
    )
    return f"{context}: {base}" if context else base


def format_auth_error(
    auth_type: str,
    system_name: str,
    host: str,
    is_retry: bool = False,
) -> str:
    """Format an authentication failure with context-aware guidance.

    Args:
        auth_type: 'cookie' or 'basic'.
        system_name: The configured system name (e.g. 'DEV').
        host: The SAP host.
        is_retry: Whether this is after a retry attempt.
    """
    header = f"Authentication failed for system '{system_name}' ({host})"
    if is_retry:
        header += " after retry"

    if auth_type == "cookie":
        cause = "The SSO cookie has likely expired."
        # Cover both credential resolution modes — telling a $ENV_VAR user
        # to run `sapcli-mcp credential set` would itself fail if keyring
        # is not installed.
        action = (
            "Refresh the SSO cookie in whichever store the config references. "
            "For `keyring:<key>`: run `sapcli-mcp credential set <key> <fresh-cookie>` "
            f"(requires the [keyring] extra: {KEYRING_INSTALL_HINT}). "
            "For `$ENV_VAR`: update the variable and restart the server. "
            "For a literal cookie in config: edit the config file and restart "
            "(consider migrating to keyring: or $ENV_VAR to avoid storing a "
            "bearer-equivalent cookie in plaintext at rest)."
        )
    else:
        cause = "Invalid username or password, or the account is locked."
        action = (
            "Verify the 'user' and 'password' wherever the config references them: "
            "a literal value in the config file, a `$ENV_VAR`, or a `keyring:<key>` "
            f"(set via `sapcli-mcp credential set <key>`; requires the [keyring] "
            f"extra: {KEYRING_INSTALL_HINT}). "
            "Check that the account is not locked in the SAP system."
        )

    return f"{header}.\nCause: {cause}\nAction: {action}"


def format_connection_error(
    host: str,
    port: int,
    ssl: bool,
    original_error: Exception,
    service_type: str = "ADT",
) -> list[str]:
    """Format a connection failure with actionable guidance.

    Args:
        host: Target hostname.
        port: Target port.
        ssl: Whether SSL/TLS was used.
        original_error: The original exception.
        service_type: 'ADT' or 'gCTS'.

    Returns:
        List of log messages for OperationResult.LogMessages.
    """
    header = f"Could not connect to {service_type} on {host}:{port}."

    ssl_hint = (
        "SSL is enabled — verify the server supports HTTPS on this port"
        if ssl
        else "SSL is disabled — the server may require HTTPS"
    )

    guidance = (
        f"Likely causes: host unreachable (check VPN/network), wrong port, "
        f"or an SSL/HTTPS mismatch ({ssl_hint})."
    )

    action = f"Action: verify the host is reachable and port {port} is correct."

    return [f"{header} {guidance} {action}", str(original_error)]


def format_startup_error(error: Exception) -> str:
    """Format a startup failure into a user-friendly message.

    Args:
        error: The exception that caused the startup failure.

    Returns:
        Formatted error message suitable for stderr output.
    """
    if isinstance(error, ConfigError):
        return (
            f"Server startup failed: configuration error.\n"
            f"{error}\n"
            f"Action: check your config file path and contents."
        )

    if isinstance(error, ImportError):
        module = getattr(error, "name", None) or ""
        if module == "sap" or module.startswith("sap."):
            return (
                f"Server startup failed: sapcli is not installed.\n"
                f"{error}\n"
                f"Action: install sapcli — see pyproject.toml for the pinned commit. "
                f"Example: pip install -e . (or uv pip install -e .)."
            )
        return (
            f"Server startup failed: missing dependency.\n"
            f"{error}\n"
            f"Action: ensure all dependencies are installed "
            f"(pip install -e . or uv pip install -e .)."
        )

    return (
        f"Server startup failed: unexpected error.\n"
        f"{type(error).__name__}: {error}\n"
        f"Action: this is likely a bug — please report it."
    )
