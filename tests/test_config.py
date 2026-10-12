"""Tests for sapclimcp.config — server-side system configuration."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests
from sap.http.errors import UnauthorizedError

from sapclimcp.config import (
    _SECRET_FIELDS,
    KEYRING_SERVICE,
    ConfigError,
    ConnectionManager,
    CookieSessionInitializer,
    SecretRef,
    ServerConfig,
    SystemConfig,
    load_config,
)

# ---------------------------------------------------------------------------
# SecretRef
# ---------------------------------------------------------------------------


class TestSecretRef:
    def test_literal_resolves_to_itself(self):
        ref = SecretRef("hello")
        assert ref.resolve() == "hello"

    def test_empty_literal(self):
        ref = SecretRef("")
        assert ref.resolve() == ""

    def test_bool_true_for_non_empty(self):
        assert bool(SecretRef("value")) is True
        assert bool(SecretRef("keyring:key")) is True
        assert bool(SecretRef("$ENV")) is True

    def test_bool_false_for_empty(self):
        assert bool(SecretRef("")) is False

    def test_env_var_resolved(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "resolved_value")
        ref = SecretRef("$MY_SECRET")
        assert ref.resolve() == "resolved_value"

    def test_env_var_missing_raises(self):
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        ref = SecretRef("$NONEXISTENT_VAR_12345")
        with pytest.raises(ConfigError, match="NONEXISTENT_VAR_12345"):
            ref.resolve()

    def test_env_var_must_be_entire_string(self):
        """$VAR must be the entire string to be treated as a reference."""
        ref = SecretRef("prefix_$VAR")
        assert ref.resolve() == "prefix_$VAR"

    @patch("keyring.get_password")
    def test_keyring_resolved(self, mock_get):
        mock_get.return_value = "secret_from_keyring"
        ref = SecretRef("keyring:MY_KEY")
        assert ref.resolve() == "secret_from_keyring"
        mock_get.assert_called_once_with(KEYRING_SERVICE, "MY_KEY")

    @patch("keyring.get_password")
    def test_keyring_missing_raises(self, mock_get):
        mock_get.return_value = None
        ref = SecretRef("keyring:MISSING_KEY")
        with pytest.raises(ConfigError, match="MISSING_KEY"):
            ref.resolve()

    @patch("sapclimcp.config.keyring", None)
    def test_keyring_not_installed_raises_install_hint(self):
        """When the `keyring` package is unavailable (optional extra not
        installed), `keyring:` references must fail with a clear install
        hint, not an AttributeError on `None.get_password`."""
        ref = SecretRef("keyring:ANY_KEY")
        with pytest.raises(ConfigError, match=r"pip install -e \.\[keyring\]"):
            ref.resolve()

    @patch("sapclimcp.config.keyring", None)
    def test_env_var_still_works_without_keyring(self, monkeypatch):
        """Without keyring installed, $ENV credentials must still resolve."""
        monkeypatch.setenv("FALLBACK_SECRET", "ok")
        assert SecretRef("$FALLBACK_SECRET").resolve() == "ok"

    @patch("sapclimcp.config.keyring", None)
    def test_literal_still_works_without_keyring(self):
        """Without keyring installed, literal credentials must still resolve."""
        assert SecretRef("plain_password").resolve() == "plain_password"

    # ── is_keyring_ref property ────────────────────────────────────

    def test_is_keyring_ref_for_keyring_prefix(self):
        assert SecretRef("keyring:DEV").is_keyring_ref is True

    def test_is_keyring_ref_for_env_var(self):
        assert SecretRef("$MY_VAR").is_keyring_ref is False

    def test_is_keyring_ref_for_literal(self):
        assert SecretRef("plain_password").is_keyring_ref is False

    def test_is_keyring_ref_for_empty(self):
        assert SecretRef("").is_keyring_ref is False

    def test_repr_hides_literals(self):
        assert "***" in repr(SecretRef("password123"))
        assert "password123" not in repr(SecretRef("password123"))

    def test_repr_shows_env_var_name(self):
        assert "$MY_VAR" in repr(SecretRef("$MY_VAR"))

    def test_repr_hides_keyring_key(self):
        r = repr(SecretRef("keyring:DEV"))
        assert "keyring:***" in r
        assert "DEV" not in r

    def test_repr_empty(self):
        assert repr(SecretRef("")) == "SecretRef('')"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def _write_json(tmp_path, data: dict) -> str:
    """Write data to a JSON file in tmp_path and return the path string."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# CookieSessionInitializer
# ---------------------------------------------------------------------------


class TestCookieSessionInitializer:
    def test_satisfies_upstream_session_initializer_protocol(self):
        """CookieSessionInitializer must satisfy sap.http.client.HTTPSessionInitializer.

        The upstream Protocol is @runtime_checkable, so isinstance() catches
        renames or signature changes on the next sapcli pin bump immediately
        — before any live ADT call happens.
        """
        from sap.http.client import HTTPSessionInitializer

        initializer = CookieSessionInitializer("SAP_SESSIONID_X=abc")
        assert isinstance(initializer, HTTPSessionInitializer)

    def test_parses_multiple_cookies_into_jar(self):
        """A 'name1=v1; name2=v2' input string is split into separate jar entries."""
        initializer = CookieSessionInitializer(
            "SAP_SESSIONID_X=abc; sap-usercontext=sap-client=815"
        )

        session = initializer.initialize_session(requests.Session())
        assert session.cookies.get("SAP_SESSIONID_X") == "abc"
        assert session.cookies.get("sap-usercontext") == "sap-client=815"

    def test_does_not_set_cookie_header(self):
        """Cookies must NOT be set on session.headers['Cookie'] — that would
        override the jar in requests and silently drop server-set cookies."""
        initializer = CookieSessionInitializer("SAP_SESSIONID_X=abc")

        session = initializer.initialize_session(requests.Session())
        assert "Cookie" not in session.headers

    def test_clears_existing_basic_auth(self):
        """Cookie auth must clear any pre-existing basic auth on the session."""
        initializer = CookieSessionInitializer("SAP_SESSIONID_X=abc")
        session = requests.Session()
        session.auth = ("preexisting_user", "preexisting_password")

        result = initializer.initialize_session(session)
        assert result.auth is None

    def test_preserves_server_set_cookies_through_session_prepare(self):
        """Regression for PR #17: server-issued cookies (sap-contextid)
        must accumulate in the jar and be echoed back on subsequent requests.

        Uses ``session.prepare_request`` which exercises the same merge path
        as ``session.send`` — including any ``headers["Cookie"]`` override.
        Constructing a bare ``PreparedRequest`` would NOT catch a regression
        where someone re-introduces ``session.headers["Cookie"]`` because
        ``PreparedRequest.prepare`` ignores ``session.headers`` entirely.
        """
        initializer = CookieSessionInitializer("SAP_SESSIONID_X=abc")
        session = initializer.initialize_session(requests.Session())

        # Simulate the server setting sap-contextid on a response
        session.cookies.set("sap-contextid", "SID:ANON:host_X_00:context-token")

        prep = session.prepare_request(
            requests.Request(
                "POST",
                "https://example.invalid/sap/bc/adt/oo/classes/cl_x",
            )
        )
        cookie_header = prep.headers.get("Cookie", "")
        assert "SAP_SESSIONID_X=abc" in cookie_header
        assert "sap-contextid=" in cookie_header

    def test_empty_cookie_string_raises_config_error(self):
        """Empty / unparseable cookie strings fail loudly at construction
        time so misconfiguration surfaces at config resolution rather than
        as a deferred 401 on the first ADT call."""
        with pytest.raises(ConfigError, match="parsed to zero entries"):
            CookieSessionInitializer("")

    def test_whitespace_only_cookie_string_raises_config_error(self):
        with pytest.raises(ConfigError, match="parsed to zero entries"):
            CookieSessionInitializer("   ")

    def test_cookie_string_without_equals_raises_config_error(self):
        with pytest.raises(ConfigError, match="parsed to zero entries"):
            CookieSessionInitializer("just_a_token_no_equals")

    def test_leading_reserved_name_raises_config_error(self):
        """SimpleCookie treats 'max-age', 'path', 'domain', 'secure' etc. as
        Morsel attributes, not cookie names. A string that starts with one of
        them parses to zero entries — covered by the same error path as the
        other malformed cases."""
        with pytest.raises(ConfigError, match="parsed to zero entries"):
            CookieSessionInitializer("max-age=3600")


class TestLoadConfig:
    def test_basic_single_system(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "100",
                        "port": 443,
                        "user": "admin",
                        "password": "p",
                    }
                }
            },
        )
        cfg = load_config(path)
        assert "DEV" in cfg.systems
        assert cfg.systems["DEV"].ashost == "dev.example.com"
        assert cfg.default_system == "DEV"  # auto-default for single system

    def test_credential_fields_are_secret_refs(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "100",
                        "user": "admin",
                        "password": "secret",
                        "cookie": "SAP=abc",
                    }
                }
            },
        )
        cfg = load_config(path)
        sys = cfg.systems["DEV"]
        assert isinstance(sys.user, SecretRef)
        assert isinstance(sys.password, SecretRef)
        assert isinstance(sys.cookie, SecretRef)

    def test_env_var_deferred_resolution(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEST_SAP_USER", "admin")
        monkeypatch.setenv("TEST_SAP_PASS", "s3cret")
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "100",
                        "auth": "basic",
                        "user": "$TEST_SAP_USER",
                        "password": "$TEST_SAP_PASS",
                    }
                }
            },
        )
        cfg = load_config(path)
        # SecretRef stores raw value, resolves lazily
        assert cfg.systems["DEV"].user.raw == "$TEST_SAP_USER"
        assert cfg.systems["DEV"].user.resolve() == "admin"
        assert cfg.systems["DEV"].password.resolve() == "s3cret"

    def test_multi_system_with_default(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "100",
                        "user": "u",
                        "password": "p",
                    },
                    "QA": {
                        "ashost": "qa.example.com",
                        "client": "200",
                        "user": "u",
                        "password": "p",
                    },
                },
                "default_system": "DEV",
            },
        )
        cfg = load_config(path)
        assert len(cfg.systems) == 2
        assert cfg.default_system == "DEV"

    def test_multi_system_no_default(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "100",
                        "user": "u",
                        "password": "p",
                    },
                    "QA": {
                        "ashost": "qa.example.com",
                        "client": "200",
                        "user": "u",
                        "password": "p",
                    },
                },
            },
        )
        cfg = load_config(path)
        assert cfg.default_system is None

    def test_bad_default_system_raises(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "100",
                        "user": "u",
                        "password": "p",
                    },
                },
                "default_system": "NONEXISTENT",
            },
        )
        with pytest.raises(ConfigError, match="NONEXISTENT"):
            load_config(path)

    def test_empty_systems_raises(self, tmp_path):
        path = _write_json(tmp_path, {"systems": {}})
        with pytest.raises(ConfigError, match="At least one system"):
            load_config(path)

    def test_missing_file_raises(self):
        with pytest.raises(ConfigError, match="Failed to load"):
            load_config("/nonexistent/path.json")

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json{{{")
        with pytest.raises(ConfigError, match="Failed to load"):
            load_config(str(path))

    def test_non_string_secret_field_raises(self, tmp_path):
        # A credential field given as a non-string must fail fast at load, not
        # slip past SecretRef wrapping and blow up later.
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "001",
                        "user": "u",
                        "password": 123,
                    }
                }
            },
        )
        with pytest.raises(ConfigError, match="must be a string"):
            load_config(path)

    def test_null_secret_field_uses_default(self, tmp_path):
        # JSON null on a secret field must become the default empty SecretRef, not
        # a bare None — a None would crash keyring_refs() with an AttributeError.
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "001",
                        "auth": "basic",
                        "user": "u",
                        "password": "p",
                        "cookie": None,
                    }
                }
            },
        )
        cfg = load_config(path)
        assert cfg.systems["DEV"].cookie.raw == ""
        assert cfg.keyring_refs() == []

    def test_cookie_auth_system(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "001",
                        "auth": "cookie",
                        "cookie": "SAP_SESSIONID=abc123",
                    }
                }
            },
        )
        cfg = load_config(path)
        assert cfg.systems["DEV"].auth == "cookie"
        assert cfg.systems["DEV"].cookie.resolve() == "SAP_SESSIONID=abc123"

    def test_keyring_reference_stored_as_raw(self, tmp_path):
        path = _write_json(
            tmp_path,
            {
                "systems": {
                    "DEV": {
                        "ashost": "dev.example.com",
                        "client": "001",
                        "auth": "cookie",
                        "cookie": "keyring:DEV",
                    }
                }
            },
        )
        cfg = load_config(path)
        assert cfg.systems["DEV"].cookie.raw == "keyring:DEV"


# ---------------------------------------------------------------------------
# ServerConfig
# ---------------------------------------------------------------------------


class TestServerConfig:
    def test_single_system_auto_default(self):
        cfg = ServerConfig(
            systems={
                "DEV": SystemConfig(
                    ashost="h", client="c", user=SecretRef("u"), password=SecretRef("p")
                )
            }
        )
        assert cfg.default_system == "DEV"

    def test_multi_system_no_auto_default(self):
        cfg = ServerConfig(
            systems={
                "A": SystemConfig(
                    ashost="a", client="1", user=SecretRef("u"), password=SecretRef("p")
                ),
                "B": SystemConfig(
                    ashost="b", client="2", user=SecretRef("u"), password=SecretRef("p")
                ),
            }
        )
        assert cfg.default_system is None

    # ── keyring_refs() ──────────────────────────────────────────────

    def test_keyring_refs_returns_empty_when_no_refs(self):
        cfg = ServerConfig(
            systems={
                "DEV": SystemConfig(
                    ashost="h",
                    client="c",
                    user=SecretRef("u"),
                    password=SecretRef("p"),
                )
            }
        )
        assert cfg.keyring_refs() == []

    def test_keyring_refs_returns_matching_fields(self):
        cfg = ServerConfig(
            systems={
                "DEV": SystemConfig(
                    ashost="h",
                    client="c",
                    auth="cookie",
                    cookie=SecretRef("keyring:DEV-cookie"),
                ),
            }
        )
        assert cfg.keyring_refs() == ["DEV.cookie"]

    def test_keyring_refs_excludes_non_keyring(self):
        """Mixed config: only keyring-prefixed fields appear; literal and
        $ENV_VAR refs are excluded."""
        cfg = ServerConfig(
            systems={
                "MIXED": SystemConfig(
                    ashost="h",
                    client="c",
                    user=SecretRef("$SAP_USER"),  # env var — NOT keyring
                    password=SecretRef("literal-pass"),  # literal — NOT keyring
                    auth="cookie",
                    cookie=SecretRef("keyring:MIXED-cookie"),  # keyring — INCLUDED
                ),
            }
        )
        assert cfg.keyring_refs() == ["MIXED.cookie"]

    def test_keyring_refs_iteration_order_is_stable(self):
        """`_SECRET_FIELDS` is a tuple, so the iteration order is fixed at
        ('user', 'password', 'cookie') across processes — protect this
        contract since a future test could otherwise become flaky."""
        # R4-1: pin the type contract so a frozenset regression fails fast
        # rather than appearing as a flaky list-comparison failure.
        assert isinstance(_SECRET_FIELDS, tuple), (
            f"_SECRET_FIELDS must be a tuple for stable iteration order; "
            f"got {type(_SECRET_FIELDS).__name__}"
        )
        cfg = ServerConfig(
            systems={
                "DEV": SystemConfig(
                    ashost="h",
                    client="c",
                    user=SecretRef("keyring:DEV-user"),
                    password=SecretRef("keyring:DEV-pass"),
                    auth="cookie",
                    cookie=SecretRef("keyring:DEV-cookie"),
                ),
            }
        )
        # Field order matches _SECRET_FIELDS
        assert cfg.keyring_refs() == ["DEV.user", "DEV.password", "DEV.cookie"]

    def test_keyring_refs_multi_system_outer_iteration_order(self):
        """R4-3: multi-system case isolates the outer-loop ordering contract.
        `dict.items()` preserves insertion order in Python 3.7+; combined
        with the stable `_SECRET_FIELDS` tuple inner order, callers can
        rely on a fully deterministic flattened order."""
        cfg = ServerConfig(
            systems={
                "ALPHA": SystemConfig(
                    ashost="a",
                    client="1",
                    auth="cookie",
                    cookie=SecretRef("keyring:ALPHA-cookie"),
                ),
                "BETA": SystemConfig(
                    ashost="b",
                    client="2",
                    user=SecretRef("keyring:BETA-user"),
                    password=SecretRef("keyring:BETA-pass"),
                ),
                "GAMMA": SystemConfig(
                    ashost="g",
                    client="3",
                    user=SecretRef("$ENV"),  # excluded — env var, not keyring
                    password=SecretRef("literal"),  # excluded — literal
                ),
            }
        )
        # Flattened order: outer = systems insertion order, inner = _SECRET_FIELDS order
        assert cfg.keyring_refs() == [
            "ALPHA.cookie",
            "BETA.user",
            "BETA.password",
        ]


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------


class TestConnectionManager:
    @staticmethod
    def _make_manager(**overrides) -> ConnectionManager:
        defaults = dict(
            ashost="test.example.com",
            client="100",
            port=443,
            user=SecretRef("admin"),
            password=SecretRef("secret"),
        )
        # Allow overrides to pass SecretRef or plain strings for cookie
        if "cookie" in overrides and isinstance(overrides["cookie"], str):
            overrides["cookie"] = SecretRef(overrides["cookie"])
        if "user" in overrides and isinstance(overrides["user"], str):
            overrides["user"] = SecretRef(overrides["user"])
        if "password" in overrides and isinstance(overrides["password"], str):
            overrides["password"] = SecretRef(overrides["password"])
        defaults.update(overrides)
        sys_config = SystemConfig(**defaults)
        cfg = ServerConfig(systems={"DEV": sys_config}, default_system="DEV")
        return ConnectionManager(cfg)

    def test_system_names(self):
        mgr = self._make_manager()
        assert mgr.system_names == ["DEV"]

    def test_default_system(self):
        mgr = self._make_manager()
        assert mgr.default_system == "DEV"

    @patch("sap.adt.Connection")
    def test_get_adt_connection(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        mgr = self._make_manager()
        conn = mgr.get_connection("DEV", "adt")

        assert conn is mock_conn
        mock_conn_cls.assert_called_once()

        # Verify connection args
        kwargs = mock_conn_cls.call_args.kwargs
        assert kwargs["host"] == "test.example.com"
        assert kwargs["client"] == "100"

    @patch("sap.adt.Connection")
    def test_connection_caching(self, mock_conn_cls):
        mock_conn_cls.return_value = MagicMock()

        mgr = self._make_manager()
        conn1 = mgr.get_connection("DEV", "adt")
        conn2 = mgr.get_connection("DEV", "adt")

        assert conn1 is conn2
        assert mock_conn_cls.call_count == 1  # Only created once

    @patch("sap.adt.Connection")
    def test_default_system_resolution(self, mock_conn_cls):
        mock_conn_cls.return_value = MagicMock()

        mgr = self._make_manager()
        conn = mgr.get_connection(None, "adt")

        assert conn is not None
        mock_conn_cls.assert_called_once()
        # Verify it resolved to the default system's host
        assert mock_conn_cls.call_args.kwargs["host"] == "test.example.com"

    def test_unknown_system_raises(self):
        mgr = self._make_manager()
        with pytest.raises(ConfigError, match="Unknown system"):
            mgr.get_connection("NONEXISTENT", "adt")

    def test_no_default_no_system_raises(self):
        cfg = ServerConfig(
            systems={
                "A": SystemConfig(
                    ashost="a", client="1", user=SecretRef("u"), password=SecretRef("p")
                ),
                "B": SystemConfig(
                    ashost="b", client="2", user=SecretRef("u"), password=SecretRef("p")
                ),
            },
        )
        mgr = ConnectionManager(cfg)
        with pytest.raises(ConfigError, match="No system specified"):
            mgr.get_connection(None, "adt")

    @patch("sap.adt.Connection")
    def test_cookie_auth_uses_session_initializer(self, mock_conn_cls):
        """Cookie auth passes a CookieSessionInitializer (with the resolved
        cookie value) to sap.adt.Connection.

        Behavior of the initializer itself is covered by
        TestCookieSessionInitializer; this test only verifies wiring.
        """
        mock_conn_cls.return_value = MagicMock()

        mgr = self._make_manager(auth="cookie", cookie="SAP_SESSION=abc")
        mgr.get_connection("DEV", "adt")

        mock_conn_cls.assert_called_once()
        kwargs = mock_conn_cls.call_args.kwargs
        initializer = kwargs["session_initializer"]

        assert isinstance(initializer, CookieSessionInitializer)

        # Sanity: confirm the resolved cookie value reached the initializer.
        session = initializer.initialize_session(requests.Session())
        assert session.cookies.get("SAP_SESSION") == "abc"

        # build_unauthorized_error returns the right exception type
        err = initializer.build_unauthorized_error(MagicMock(), MagicMock())
        assert isinstance(err, UnauthorizedError)

    @patch("sap.adt.Connection")
    def test_basic_auth_no_session_initializer(self, mock_conn_cls):
        """Basic auth passes session_initializer=None to sap.adt.Connection."""
        mock_conn_cls.return_value = MagicMock()

        mgr = self._make_manager()
        mgr.get_connection("DEV", "adt")

        mock_conn_cls.assert_called_once()
        kwargs = mock_conn_cls.call_args.kwargs
        assert kwargs["session_initializer"] is None

    @patch("sap.adt.Connection")
    def test_cookie_auth_passes_unused_credential_placeholders(self, mock_conn_cls):
        """R1#5: cookie auth passes explicit 'unused' user/password to
        sap.adt.Connection. They are inert (the session initializer clears
        session.auth) but the ctor still requires non-None strings."""
        mock_conn_cls.return_value = MagicMock()

        mgr = self._make_manager(auth="cookie", cookie="SAP_SESSION=abc")
        mgr.get_connection("DEV", "adt")

        kwargs = mock_conn_cls.call_args.kwargs
        assert kwargs["user"] == "unused"
        assert kwargs["password"] == "unused"

    @patch("sap.adt.Connection")
    def test_basic_auth_user_resolving_to_empty_raises(self, mock_conn_cls, monkeypatch):
        """R1#5: a $ENV_VAR user that is set but blank passes config-load
        validation (raw is non-empty) yet resolves to '' at connection time.
        Fail fast with a clear ConfigError instead of silently substituting
        'unused' and getting an opaque 401 from SAP."""
        monkeypatch.setenv("SAP_USER_BLANK", "")
        mgr = self._make_manager(user="$SAP_USER_BLANK")

        with pytest.raises(ConfigError, match=r"'user' resolved to an empty value"):
            mgr.get_connection("DEV", "adt")

        mock_conn_cls.assert_not_called()

    @patch("sap.adt.Connection")
    def test_basic_auth_password_resolving_to_empty_raises(self, mock_conn_cls, monkeypatch):
        """R1#5: same as the user case, for a $ENV_VAR password resolving to ''."""
        monkeypatch.setenv("SAP_PASS_BLANK", "")
        mgr = self._make_manager(password="$SAP_PASS_BLANK")

        with pytest.raises(ConfigError, match=r"'password' resolved to an empty value"):
            mgr.get_connection("DEV", "adt")

        mock_conn_cls.assert_not_called()

    @patch("sap.adt.Connection")
    @patch("keyring.get_password", return_value="")
    def test_basic_auth_keyring_resolving_to_empty_raises(self, mock_kr, mock_conn_cls):
        """Review F2: a keyring entry that resolves to an empty string (not None)
        fails fast too — the same fail-fast path as a blank $ENV_VAR, regardless
        of the resolution source."""
        mgr = self._make_manager(password="keyring:PRD")

        with pytest.raises(ConfigError, match=r"'password' resolved to an empty value"):
            mgr.get_connection("DEV", "adt")

        mock_conn_cls.assert_not_called()

    @patch("sap.cli.gcts_connection_from_args")
    def test_gcts_basic_auth_resolving_to_empty_raises(self, mock_factory, monkeypatch):
        """R1#5: the gCTS path (_make_gcts_connection_args) routes through the
        same _resolve_basic_credentials fail-fast helper as ADT. Without this
        test, reverting just the gCTS call site to `or "unused"` would go
        undetected (the ADT tests above wouldn't catch it)."""
        monkeypatch.setenv("SAP_USER_BLANK", "")
        mgr = self._make_manager(user="$SAP_USER_BLANK")

        with pytest.raises(ConfigError, match=r"'user' resolved to an empty value"):
            mgr.get_connection("DEV", "gcts")

        mock_factory.assert_not_called()

    @patch("sap.cli.gcts_connection_from_args")
    def test_gcts_basic_auth_password_resolving_to_empty_raises(self, mock_factory, monkeypatch):
        """Review R2 N4: gCTS password-empty symmetry with the ADT path and the
        gCTS user case above — a blank-resolving $ENV_VAR password fails fast via
        _resolve_basic_credentials before gcts_connection_from_args is called."""
        monkeypatch.setenv("SAP_PASS_BLANK", "")
        mgr = self._make_manager(password="$SAP_PASS_BLANK")

        with pytest.raises(ConfigError, match=r"'password' resolved to an empty value"):
            mgr.get_connection("DEV", "gcts")

        mock_factory.assert_not_called()

    @patch("sap.cli.gcts_connection_from_args")
    def test_get_gcts_connection(self, mock_factory):
        mock_conn = MagicMock()
        mock_factory.return_value = mock_conn

        mgr = self._make_manager()
        conn = mgr.get_connection("DEV", "gcts")

        assert conn is mock_conn

    def test_unsupported_conn_type_raises(self):
        mgr = self._make_manager()
        with pytest.raises(ConfigError, match="Unsupported connection type"):
            mgr.get_connection("DEV", "odata")

    def test_gcts_cookie_auth_raises(self):
        mgr = self._make_manager(auth="cookie", cookie="SAP_SESSION=abc")
        with pytest.raises(ConfigError, match="not supported for gCTS"):
            mgr.get_connection("DEV", "gcts")

    @patch("keyring.get_password")
    @patch("sap.adt.Connection")
    def test_keyring_resolved_at_connection_time(self, mock_conn_cls, mock_keyring):
        """keyring: references are resolved when creating a connection, not at load time."""
        mock_conn_cls.return_value = MagicMock()
        mock_keyring.return_value = "SAP_SESSIONID_X=fresh"

        mgr = self._make_manager(auth="cookie", cookie="keyring:DEV")
        # Keyring should NOT have been consulted yet (deferred resolution)
        assert mock_keyring.call_count == 0

        mgr.get_connection("DEV", "adt")

        mock_keyring.assert_called_once_with(KEYRING_SERVICE, "DEV")
        kwargs = mock_conn_cls.call_args.kwargs
        initializer = kwargs["session_initializer"]
        # Verify the resolved cookie was used
        session = requests.Session()
        initializer.initialize_session(session)
        assert session.cookies.get("SAP_SESSIONID_X") == "fresh"

    @patch("keyring.get_password")
    @patch("sap.adt.Connection")
    def test_keyring_re_resolved_after_eviction(self, mock_conn_cls, mock_keyring):
        """After eviction, keyring is re-read to get a fresh credential."""
        mock_conn_cls.return_value = MagicMock()
        mock_keyring.side_effect = ["SAP_SESSIONID_X=old", "SAP_SESSIONID_X=new"]

        mgr = self._make_manager(auth="cookie", cookie="keyring:DEV")
        mgr.get_connection("DEV", "adt")
        mgr.evict("DEV", "adt")
        mgr.get_connection("DEV", "adt")

        assert mock_keyring.call_count == 2
        # Verify second connection used the fresh cookie
        second_call_kwargs = mock_conn_cls.call_args_list[1].kwargs
        initializer = second_call_kwargs["session_initializer"]
        session = requests.Session()
        initializer.initialize_session(session)
        assert session.cookies.get("SAP_SESSIONID_X") == "new"


# ---------------------------------------------------------------------------
# ConnectionManager — TTL and eviction
# ---------------------------------------------------------------------------


class TestConnectionManagerTTL:
    @staticmethod
    def _make_manager(cache_ttl_seconds=3600, **overrides) -> ConnectionManager:
        defaults = dict(
            ashost="test.example.com",
            client="100",
            port=443,
            user=SecretRef("admin"),
            password=SecretRef("secret"),
        )
        if "cookie" in overrides and isinstance(overrides["cookie"], str):
            overrides["cookie"] = SecretRef(overrides["cookie"])
        defaults.update(overrides)
        sys_config = SystemConfig(**defaults)
        cfg = ServerConfig(systems={"DEV": sys_config}, default_system="DEV")
        return ConnectionManager(cfg, cache_ttl_seconds=cache_ttl_seconds)

    @patch("sapclimcp.config.time.monotonic")
    @patch("sap.adt.Connection")
    def test_ttl_expired_connection_recreated(self, mock_conn_cls, mock_time):
        """After TTL expires, get_connection must create a new connection."""
        conn_first = MagicMock(name="conn_first")
        conn_second = MagicMock(name="conn_second")
        mock_conn_cls.side_effect = [conn_first, conn_second]

        mgr = self._make_manager(cache_ttl_seconds=300)

        mock_time.return_value = 1000.0
        result1 = mgr.get_connection("DEV", "adt")
        assert result1 is conn_first

        # Advance past TTL
        mock_time.return_value = 1301.0
        result2 = mgr.get_connection("DEV", "adt")
        assert result2 is conn_second
        assert result2 is not result1
        assert mock_conn_cls.call_count == 2

    @patch("sapclimcp.config.time.monotonic")
    @patch("sap.adt.Connection")
    def test_ttl_not_expired_returns_cached(self, mock_conn_cls, mock_time):
        """Before TTL expires, the same cached connection is returned."""
        mock_conn_cls.return_value = MagicMock()

        mgr = self._make_manager(cache_ttl_seconds=300)

        mock_time.return_value = 1000.0
        conn1 = mgr.get_connection("DEV", "adt")

        # Advance but NOT past TTL
        mock_time.return_value = 1299.0
        conn2 = mgr.get_connection("DEV", "adt")

        assert conn1 is conn2
        assert mock_conn_cls.call_count == 1

    @patch("sap.adt.Connection")
    def test_evict_removes_cached_connection(self, mock_conn_cls):
        """After evict(), the next get_connection creates a fresh connection."""
        conn_first = MagicMock(name="conn_first")
        conn_second = MagicMock(name="conn_second")
        mock_conn_cls.side_effect = [conn_first, conn_second]

        mgr = self._make_manager()
        result1 = mgr.get_connection("DEV", "adt")
        assert result1 is conn_first

        mgr.evict("DEV", "adt")

        result2 = mgr.get_connection("DEV", "adt")
        assert result2 is conn_second
        assert mock_conn_cls.call_count == 2

    def test_evict_noop_for_uncached(self):
        """Evicting a connection that was never cached does not raise."""
        mgr = self._make_manager()
        mgr.evict("DEV", "adt")

    @patch("sap.adt.Connection")
    def test_evict_none_system_uses_default(self, mock_conn_cls):
        """evict(None, factory) resolves to the default system."""
        conn_first = MagicMock(name="conn_first")
        conn_second = MagicMock(name="conn_second")
        mock_conn_cls.side_effect = [conn_first, conn_second]

        mgr = self._make_manager()
        mgr.get_connection(None, "adt")

        mgr.evict(None, "adt")

        result = mgr.get_connection(None, "adt")
        assert result is conn_second
        assert mock_conn_cls.call_count == 2

    @patch("sapclimcp.config.time.monotonic")
    @patch("sap.adt.Connection")
    def test_custom_ttl(self, mock_conn_cls, mock_time):
        """A short custom TTL triggers recreation sooner."""
        conn_first = MagicMock(name="conn_first")
        conn_second = MagicMock(name="conn_second")
        mock_conn_cls.side_effect = [conn_first, conn_second]

        mgr = self._make_manager(cache_ttl_seconds=60)

        mock_time.return_value = 0.0
        mgr.get_connection("DEV", "adt")

        mock_time.return_value = 60.0
        result = mgr.get_connection("DEV", "adt")
        assert result is conn_second

    def test_evict_unsupported_conn_type_is_noop(self):
        """Evicting with an unrecognized conn_type does not raise."""
        mgr = self._make_manager()
        mgr.evict("DEV", "odata")

    def test_evict_none_system_no_default_is_noop(self):
        """evict(None, factory) is a no-op when no default_system is configured."""
        sys_a = SystemConfig(
            ashost="a.example.com",
            client="100",
            port=443,
            user=SecretRef("admin"),
            password=SecretRef("secret"),
        )
        sys_b = SystemConfig(
            ashost="b.example.com",
            client="200",
            port=443,
            user=SecretRef("admin"),
            password=SecretRef("secret"),
        )
        cfg = ServerConfig(systems={"A": sys_a, "B": sys_b})
        assert cfg.default_system is None
        mgr = ConnectionManager(cfg)
        mgr.evict(None, "adt")


# ---------------------------------------------------------------------------
# SystemConfig validation
# ---------------------------------------------------------------------------


class TestSystemConfigValidation:
    def test_invalid_auth_type_raises(self):
        with pytest.raises(ConfigError, match="Invalid auth type 'cookies'"):
            SystemConfig(ashost="h", client="c", auth="cookies")

    def test_cookie_auth_without_cookie_raises(self):
        with pytest.raises(ConfigError, match="non-empty"):
            SystemConfig(ashost="h", client="c", auth="cookie", cookie=SecretRef(""))

    def test_basic_auth_without_user_raises(self):
        with pytest.raises(ConfigError, match=r"non-empty 'user'"):
            SystemConfig(ashost="h", client="c", auth="basic", user=SecretRef(""))

    def test_basic_auth_without_password_raises(self):
        """B2: basic auth must reject empty password at config-load time
        rather than silently substituting 'unused' and failing later at the
        SAP layer."""
        with pytest.raises(ConfigError, match=r"non-empty 'password'"):
            SystemConfig(
                ashost="h",
                client="c",
                auth="basic",
                user=SecretRef("admin"),
                password=SecretRef(""),
            )

    def test_valid_basic_auth(self):
        cfg = SystemConfig(
            ashost="h", client="c", user=SecretRef("admin"), password=SecretRef("pass")
        )
        assert cfg.auth == "basic"

    def test_valid_cookie_auth(self):
        cfg = SystemConfig(ashost="h", client="c", auth="cookie", cookie=SecretRef("SAP=abc"))
        assert cfg.auth == "cookie"


# ---------------------------------------------------------------------------
# ConnectionManager.get_connection_params
# ---------------------------------------------------------------------------


class TestGetConnectionParams:
    def _make_manager(self):
        sys = SystemConfig(
            ashost="dev.example.com",
            client="100",
            port=443,
            user=SecretRef("admin"),
            password=SecretRef("secret"),
            ssl=True,
            verify=False,
        )
        cfg = ServerConfig(systems={"DEV": sys}, default_system="DEV")
        return ConnectionManager(cfg)

    def test_returns_expected_keys(self):
        mgr = self._make_manager()
        params = mgr.get_connection_params("DEV")
        assert set(params.keys()) == {
            "ashost",
            "port",
            "client",
            "user",
            "ssl",
            "verify",
        }

    def test_password_not_included(self):
        mgr = self._make_manager()
        params = mgr.get_connection_params("DEV")
        assert "password" not in params

    def test_values_match_config(self):
        mgr = self._make_manager()
        params = mgr.get_connection_params("DEV")
        assert params["ashost"] == "dev.example.com"
        assert params["client"] == "100"
        assert params["port"] == 443
        assert params["user"] == "admin"
        assert params["ssl"] is True
        assert params["verify"] is False

    def test_none_resolves_to_default(self):
        mgr = self._make_manager()
        params = mgr.get_connection_params(None)
        assert params["ashost"] == "dev.example.com"

    def test_unknown_system_raises(self):
        mgr = self._make_manager()
        with pytest.raises(ConfigError, match="Unknown system"):
            mgr.get_connection_params("PROD")
