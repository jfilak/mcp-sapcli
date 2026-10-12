"""
Server-side system configuration for mcp-sapcli.

Loads named SAP system definitions from a JSON config file
and manages cached connections per system.

Credential fields (user, password, cookie) support deferred resolution:
- ``keyring:<key>`` — resolved from OS keyring at connection time
- ``$ENV_VAR`` — resolved from environment variable at connection time
- literal string — used as-is
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any, Optional

try:
    import keyring  # type: ignore[import-not-found]
except ImportError:
    # Soft-import: keyring is an optional dependency. When unavailable, the
    # `keyring:` SecretRef prefix raises a clear ConfigError pointing the user
    # at the install hint. `$ENV_VAR` and literal credentials still work.
    keyring = None  # type: ignore[assignment]

import requests
import sap.adt
import sap.cli
from sap.http.errors import UnauthorizedError

from sapclimcp.errors import ConfigError, format_keyring_missing

_LOGGER = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_KEYRING_PREFIX = "keyring:"

KEYRING_SERVICE = "sapcli-mcp"


@dataclass(frozen=True, slots=True)
class SecretRef:
    """A deferred reference to a credential value.

    Supports three resolution modes:
    - ``keyring:<key>`` — resolved from OS keyring (service: sapcli-mcp)
    - ``$ENV_VAR`` — resolved from environment variable
    - literal string — returned as-is

    Resolution happens at connection-creation time, not at config load time.
    This allows external scripts to update credentials (e.g. refresh an SSO
    cookie in the keyring) without restarting the server.
    """

    raw: str

    @property
    def is_keyring_ref(self) -> bool:
        """True iff this reference resolves through the OS keyring."""
        return self.raw.startswith(_KEYRING_PREFIX)

    def resolve(self) -> str:
        """Resolve the reference to its current value."""
        if self.is_keyring_ref:
            key = self.raw[len(_KEYRING_PREFIX):]
            if keyring is None:
                raise ConfigError(format_keyring_missing(f"Cannot resolve 'keyring:{key}'"))
            value = keyring.get_password(KEYRING_SERVICE, key)
            if value is None:
                raise ConfigError(
                    f"No keyring entry for key '{key}' "
                    f"(service: {KEYRING_SERVICE}). "
                    f"Store it with: sapcli-mcp credential set {key} <value>"
                )
            return value

        match = _ENV_VAR_RE.match(self.raw)
        if match:
            var_name = match.group(1)
            value = os.environ.get(var_name)
            if value is None:
                raise ConfigError(f"Environment variable '{var_name}' is not set")
            return value

        return self.raw

    def __bool__(self) -> bool:
        """Non-empty check for validation."""
        return bool(self.raw)

    def __repr__(self) -> str:
        if self.is_keyring_ref:
            return "SecretRef('keyring:***')"
        if _ENV_VAR_RE.match(self.raw):
            return f"SecretRef('{self.raw}')"
        return "SecretRef('***')" if self.raw else "SecretRef('')"


class CookieSessionInitializer:
    """HTTPSessionInitializer that authenticates via pre-existing SSO cookies.

    Implements the sap.http.client.HTTPSessionInitializer protocol so that
    sapcli uses cookie-based auth instead of basic auth for the session.

    Cookies are loaded into the session's cookie jar (not into the static
    ``Cookie`` header). This is critical: ADT issues a ``sap-contextid``
    cookie on the first stateful request and expects clients to echo it
    back on follow-up calls so they land on the same server-side session.
    Setting ``session.headers["Cookie"]`` overrides the cookie jar in
    requests, so server-set cookies would be silently dropped — causing
    e.g. a write's ``UNLOCK`` to land on a different stateful session
    than the corresponding ``LOCK``, which leaves the underlying ENQUEUE
    lock orphaned.
    """

    def __init__(self, cookie: str) -> None:
        # Parse once at construction time so malformed cookies fail loudly
        # at connection setup, not silently at first ADT request.
        sc = SimpleCookie()
        sc.load(cookie)
        self._parsed: dict[str, str] = {name: morsel.value for name, morsel in sc.items()}
        if not self._parsed:
            raise ConfigError(
                "Cookie auth: cookie string parsed to zero entries. "
                "Expected 'name1=value1; name2=value2; ...'. "
                "Common causes: empty/whitespace-only string, missing '=' separator, "
                "or a leading reserved attribute name (max-age, path, domain, ...) "
                "that SimpleCookie treats as a directive instead of a cookie."
            )

    def initialize_session(self, session: requests.Session) -> requests.Session:
        """Load the parsed SSO cookies into the session jar and disable basic auth."""
        session.auth = None
        # Load each name=value pair into the session jar without a domain/path
        # so requests sends them on every call through this session.
        # Server-set cookies (notably sap-contextid) accumulate alongside,
        # preserving the stateful ADT session across lock/write/unlock.
        for name, value in self._parsed.items():
            session.cookies.set(name, value)
        return session

    def build_unauthorized_error(self, req, res) -> UnauthorizedError:
        """Construct an UnauthorizedError tagged as a cookie-auth failure."""
        return UnauthorizedError(req, res, "cookie-auth")


_VALID_AUTH_TYPES = frozenset({"basic", "cookie"})
# Tuple (not frozenset) for stable iteration order: `keyring_refs()` and the
# scanner's DEBUG output should be reproducible across Python invocations.
_SECRET_FIELDS = ("user", "password", "cookie")


@dataclass
class SystemConfig:  # pylint: disable=too-many-instance-attributes
    """Connection settings for a single SAP system."""

    ashost: str
    client: str
    port: int = 443
    ssl: bool = True
    verify: bool = True
    auth: str = "basic"

    # Credential fields — stored as SecretRef for deferred resolution.
    user: SecretRef = field(default_factory=lambda: SecretRef(""))
    password: SecretRef = field(default_factory=lambda: SecretRef(""))
    cookie: SecretRef = field(default_factory=lambda: SecretRef(""))

    def __post_init__(self) -> None:
        if self.auth not in _VALID_AUTH_TYPES:
            raise ConfigError(
                f"Invalid auth type '{self.auth}'. "
                f"Must be one of: {', '.join(sorted(_VALID_AUTH_TYPES))}"
            )
        if self.auth == "cookie" and not self.cookie:
            raise ConfigError("Cookie auth requires a non-empty 'cookie' field")
        if self.auth == "basic":
            if not self.user:
                raise ConfigError("Basic auth requires a non-empty 'user' field")
            if not self.password:
                raise ConfigError("Basic auth requires a non-empty 'password' field")


@dataclass
class ServerConfig:
    """Top-level server configuration holding named systems."""

    systems: dict[str, SystemConfig] = field(default_factory=dict)
    default_system: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.systems:
            raise ConfigError("At least one system must be configured")

        if self.default_system and self.default_system not in self.systems:
            raise ConfigError(
                f"Default system '{self.default_system}' not found in systems: "
                f"{', '.join(self.systems.keys())}"
            )

        # If only one system, make it the default
        if len(self.systems) == 1 and self.default_system is None:
            self.default_system = next(iter(self.systems))

    def keyring_refs(self) -> list[str]:
        """Return `<system>.<field>` paths for every secret field that
        references the keyring.

        Single source of truth: iterates `_SECRET_FIELDS` (a tuple, so
        the order is stable across Python processes) and delegates the
        prefix decision to `SecretRef.is_keyring_ref` so adding a
        fourth credential field updates this method without any change
        at the call site.
        """
        return [
            f"{name}.{field_name}"
            for name, sys_cfg in self.systems.items()
            for field_name in _SECRET_FIELDS
            if getattr(sys_cfg, field_name).is_keyring_ref
        ]


def is_keyring_available() -> bool:
    """Whether the optional `keyring` package is installed.

    Public seam over the soft-import sentinel in this module — callers
    should not poke at the private `keyring` module attribute directly.

    Note on placement: this is a module-level function while
    `ServerConfig.keyring_refs()` is an instance method. The asymmetry
    is intentional — keyring availability is a process-wide property
    (set once when this module is first imported), while keyring refs
    are a property of a particular config instance. Don't "fix" the
    asymmetry by moving one to match the other.
    """
    return keyring is not None


def load_config(path: str) -> ServerConfig:
    """Load server configuration from a JSON file.

    Credential fields (user, password, cookie) are wrapped in
    :class:`SecretRef` for deferred resolution at connection time.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        Parsed and validated ServerConfig.

    Raises:
        ConfigError: If the file cannot be read, parsed, or validated.
    """

    try:
        with open(path, encoding="utf-8") as fobj:
            raw = json.load(fobj)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Failed to load config from {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config must be a JSON object")

    raw_systems = raw.get("systems")
    if not isinstance(raw_systems, dict):
        raise ConfigError("Config must have a 'systems' object")

    systems: dict[str, SystemConfig] = {}
    for name, sys_raw in raw_systems.items():
        if not isinstance(sys_raw, dict):
            raise ConfigError(f"System '{name}' must be a JSON object")

        parsed: dict[str, Any] = {}
        for k, v in sys_raw.items():
            if k in _SECRET_FIELDS:
                # Fail fast at the config boundary so a credential field never
                # reaches the dataclass as anything but a SecretRef (otherwise it
                # blows up later with an opaque AttributeError, e.g. in keyring_refs).
                if v is None:
                    # JSON null == field not provided; let the dataclass default
                    # (an empty SecretRef) apply.
                    continue
                if not isinstance(v, str):
                    raise ConfigError(
                        f"System '{name}': credential field '{k}' must be a string "
                        f"(a literal, '$ENV_VAR', or 'keyring:<key>'), "
                        f"got {type(v).__name__}"
                    )
                parsed[k] = SecretRef(v)
            else:
                parsed[k] = v
        try:
            systems[name] = SystemConfig(**parsed)
        except (TypeError, ConfigError) as exc:
            raise ConfigError(f"System '{name}': {exc}") from exc

    default_system = raw.get("default_system")
    if default_system is not None:
        default_system = str(default_system)

    return ServerConfig(systems=systems, default_system=default_system)


DEFAULT_CACHE_TTL = 3600


@dataclass(slots=True)
class _CacheEntry:
    """A cached connection with its creation timestamp."""

    connection: Any
    created_at: float


class ConnectionManager:
    """Manages cached SAP connections per system and connection type.

    Connections are created lazily on first use and reused for
    subsequent calls to the same system with the same connection type.

    Cached connections expire after ``cache_ttl_seconds`` (default 1 hour).
    When a TTL-expired entry is requested, it is discarded and a fresh
    connection is created transparently.  Set ``cache_ttl_seconds=0`` to
    disable caching (every call creates a fresh connection).  The
    ``evict()`` method allows callers to force immediate removal
    (e.g. after an auth failure).

    Note: the cache is not thread-safe. In stdio mode this is moot
    (single-threaded). In HTTP mode, concurrent requests for the same
    uncached system may create duplicate connections; the last write
    wins and the other connection object is discarded. This is benign
    since connections are lightweight and produce identical results.
    """

    # Connectable subset — types this manager can actually serve.
    # Broader types (rfc, odata) may be registered on tools but are
    # not yet supported for server-managed connections.
    _SUPPORTED_CONN_TYPES = frozenset({"adt", "gcts"})

    def __init__(
        self,
        config: ServerConfig,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL,
    ) -> None:
        self._config = config
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._cache_ttl = cache_ttl_seconds

    @property
    def system_names(self) -> list[str]:
        """List of configured system names."""
        return list(self._config.systems.keys())

    @property
    def default_system(self) -> Optional[str]:
        """Default system name, if set."""
        return self._config.default_system

    def _resolve_system(self, system_name: Optional[str]) -> SystemConfig:
        """Resolve a system name to its configuration.

        Args:
            system_name: Explicit system name or None for default.

        Returns:
            The SystemConfig for the resolved system.

        Raises:
            ConfigError: If the system cannot be resolved.
        """

        if system_name is None:
            system_name = self._config.default_system

        if system_name is None:
            raise ConfigError(
                "No system specified and no default_system configured. "
                f"Available systems: {', '.join(self.system_names)}"
            )

        sys_config = self._config.systems.get(system_name)
        if sys_config is None:
            raise ConfigError(
                f"Unknown system '{system_name}'. Available systems: {', '.join(self.system_names)}"
            )

        return sys_config

    def get_connection_params(self, system_name: Optional[str]) -> dict[str, Any]:
        """Return connection parameters for the resolved system.

        Some sapcli commands access connection parameters (e.g. ``client``,
        ``user``) from the args namespace. When connections are managed
        server-side, these parameters are stripped from the tool schema
        but still need to be injected into the command args.

        Args:
            system_name: System name or None for default.

        Returns:
            Dictionary of connection parameter values.
        """

        sys_config = self._resolve_system(system_name)
        # This path only *injects* connection params into the command args
        # namespace; it does not authenticate. The authenticated connection is
        # built by get_connection() earlier in the same SapcliCommandTool.run()
        # call, which fails fast via _resolve_basic_credentials() if a basic-auth
        # credential resolves to empty. So `user` here is already validated for
        # basic auth, and this path stays deliberately tolerant (`or ""`). For
        # cookie auth `user` is inert and may carry an unresolved $ENV/keyring
        # ref, so we skip resolving it entirely rather than risk a spurious raise
        # after the cookie session was already established.
        return {
            "ashost": sys_config.ashost,
            "port": sys_config.port,
            "client": sys_config.client,
            "user": "" if sys_config.auth == "cookie" else (sys_config.user.resolve() or ""),
            "ssl": sys_config.ssl,
            "verify": sys_config.verify,
        }

    def _resolve_basic_credentials(self, sys_config: SystemConfig) -> tuple[str, str]:
        """Resolve user + password for basic auth, failing fast if either is empty.

        `SystemConfig.__post_init__` guarantees non-empty *raw* values for basic
        auth, but a `$ENV_VAR` reference can still resolve to an empty string at
        connection time (variable set but blank). Surface that as a clear
        ConfigError instead of silently substituting a placeholder and getting an
        opaque 401 from SAP.
        """
        user = sys_config.user.resolve()
        password = sys_config.password.resolve()
        if not user:
            raise ConfigError(
                f"Basic auth 'user' resolved to an empty value for host {sys_config.ashost} "
                "(check the referenced environment variable or keyring entry)."
            )
        if not password:
            raise ConfigError(
                f"Basic auth 'password' resolved to an empty value for host {sys_config.ashost} "
                "(check the referenced environment variable or keyring entry)."
            )
        return user, password

    def _make_gcts_connection_args(self, sys_config: SystemConfig) -> SimpleNamespace:
        """Build a SimpleNamespace matching what sapcli gCTS connection factory expects.

        gCTS is basic-auth only (`_create_gcts_connection` rejects cookie auth),
        so user/password are always required here.
        """

        user, password = self._resolve_basic_credentials(sys_config)
        return SimpleNamespace(
            ashost=sys_config.ashost,
            client=sys_config.client,
            port=sys_config.port,
            ssl=sys_config.ssl,
            verify=sys_config.verify,
            user=user,
            password=password,
            ssl_server_cert=None,
        )

    def _create_adt_connection(self, sys_config: SystemConfig) -> sap.adt.Connection:
        """Create an ADT connection, using session_initializer for cookie auth."""

        if sys_config.auth == "cookie":
            initializer = CookieSessionInitializer(sys_config.cookie.resolve())
            # Cookie auth authenticates via the session initializer, which clears
            # session.auth. sap.adt.Connection still requires non-None user/
            # password strings, so pass explicit placeholders — they are inert.
            user, password = "unused", "unused"
        else:
            initializer = None
            user, password = self._resolve_basic_credentials(sys_config)

        return sap.adt.Connection(
            host=sys_config.ashost,
            client=sys_config.client,
            user=user,
            password=password,
            port=sys_config.port,
            ssl=sys_config.ssl,
            verify=sys_config.verify,
            session_initializer=initializer,
        )

    def _create_gcts_connection(self, sys_config: SystemConfig) -> Any:
        """Create a gCTS connection."""

        if sys_config.auth == "cookie":
            raise ConfigError(
                "Cookie auth is not supported for gCTS connections. "
                "Use basic auth for gCTS systems."
            )

        args = self._make_gcts_connection_args(sys_config)
        return sap.cli.gcts_connection_from_args(args)

    def get_auth_context(self, system_name: Optional[str]) -> dict[str, str]:
        """Return auth metadata for error message formatting.

        Args:
            system_name: System name or None for default.

        Returns:
            Dict with 'auth_type', 'host', 'system_name'.
        """
        sys_config = self._resolve_system(system_name)
        # _resolve_system raises if both are None; "unknown" is for error display only
        resolved_name = system_name or self._config.default_system or "unknown"
        return {
            "auth_type": sys_config.auth,
            "host": sys_config.ashost,
            "system_name": resolved_name,
        }

    def evict(
        self,
        system_name: Optional[str],
        conn_type: str,
    ) -> None:
        """Remove a cached connection, forcing recreation on next access.

        Unlike ``get_connection``, this method does not validate
        ``conn_type`` — it silently no-ops for unknown types to avoid
        raising during error recovery paths.

        Args:
            system_name: System name or None for default.
            conn_type: Connection type string ('adt' or 'gcts').
        """

        resolved_name = system_name or self._config.default_system
        if resolved_name is None:
            return

        if resolved_name not in self._config.systems:
            _LOGGER.debug("evict: system '%s' not in config, skipping", resolved_name)
            return

        self._cache.pop((resolved_name, conn_type), None)

    def get_connection(
        self,
        system_name: Optional[str],
        conn_type: str,
    ) -> Any:
        """Get or create a cached connection for the given system.

        Returns a cached connection if one exists and has not exceeded
        the TTL. Otherwise creates a fresh connection and caches it.

        Args:
            system_name: System name or None for default.
            conn_type: Connection type string ('adt' or 'gcts').

        Returns:
            A cached or newly created connection.

        Raises:
            ConfigError: If the system cannot be resolved or
                the connection type is not supported.
        """

        if conn_type not in self._SUPPORTED_CONN_TYPES:
            raise ConfigError(
                f"Unsupported connection type '{conn_type}'. "
                f"Supported: {', '.join(sorted(self._SUPPORTED_CONN_TYPES))}"
            )

        sys_config = self._resolve_system(system_name)
        # _resolve_system raises if both are None; fallback satisfies type checker
        resolved_name = system_name or self._config.default_system or ""

        cache_key = (resolved_name, conn_type)
        entry = self._cache.get(cache_key)

        if entry is not None:
            age = time.monotonic() - entry.created_at
            if age >= self._cache_ttl:
                self._cache.pop(cache_key, None)
                entry = None

        if entry is None:
            if conn_type == "adt":
                conn = self._create_adt_connection(sys_config)
            else:
                conn = self._create_gcts_connection(sys_config)
            entry = _CacheEntry(connection=conn, created_at=time.monotonic())
            self._cache[cache_key] = entry

        return entry.connection
