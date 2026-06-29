"""Unit tests for sapclimcp.toolpatches module."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sapclimcp.argparsertool import ArgParserTool
from sapclimcp.errors import ToolInputError
from sapclimcp.toolpatches import (
    ConnectionPatch,
    MissingGroupParamPatch,
    SourceDataPatch,
    SourceFileToInlinePatch,
    ToolPatch,
)


class TestToolPatchABC:
    """Tests for the ToolPatch abstract base class."""

    def test_cannot_instantiate(self):
        """ToolPatch ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ToolPatch()  # pylint: disable=abstract-class-instantiated


class TestSourceDataPatchAppliesTo:
    """Tests for SourceDataPatch.applies_to()."""

    def test_positive_source_array(self):
        """Returns True when tool has source property with type array."""
        tool = SimpleNamespace(
            input_schema=SimpleNamespace(
                properties={"source": {"type": "array", "items": {"type": "string"}}}
            )
        )
        patch = SourceDataPatch()
        assert patch.applies_to("some_write", tool) is True

    def test_negative_no_source(self):
        """Returns False when tool has no source property."""
        tool = SimpleNamespace(
            input_schema=SimpleNamespace(properties={"name": {"type": "string"}})
        )
        patch = SourceDataPatch()
        assert patch.applies_to("some_read", tool) is False

    def test_negative_source_string(self):
        """Returns False when source is not an array type."""
        tool = SimpleNamespace(
            input_schema=SimpleNamespace(properties={"source": {"type": "string"}})
        )
        patch = SourceDataPatch()
        assert patch.applies_to("some_tool", tool) is False


class TestSourceDataPatchApply:
    """Tests for SourceDataPatch.apply() — schema and cmdfn wrapping."""

    @staticmethod
    def _make_tool_with_source():
        """Create an ArgParserTool with a source array parameter."""
        apt = ArgParserTool("tester", None)
        tool = apt.add_parser("write")
        tool.add_argument("name")
        tool.add_argument("source", nargs="+")
        tool.set_defaults(execute=MagicMock())
        return apt.tools["tester_write"]

    def test_schema_removes_source_adds_source_data(self):
        """After apply, schema has source_data instead of source."""
        tool = self._make_tool_with_source()
        patch = SourceDataPatch()
        patch.apply(tool)

        assert "source" not in tool.input_schema.properties
        assert "source_data" in tool.input_schema.properties
        assert tool.input_schema.properties["source_data"]["type"] == "string"
        assert "source" not in tool.input_schema.required
        assert "source_data" in tool.input_schema.required
        # Preserves other properties
        assert "name" in tool.input_schema.properties

    def test_schema_source_data_in_mcp_output(self):
        """to_mcp_input_schema() reflects the patched schema."""
        tool = self._make_tool_with_source()
        patch = SourceDataPatch()
        patch.apply(tool)

        schema = tool.to_mcp_input_schema()
        assert "source_data" in schema["properties"]
        assert "source" not in schema["properties"]
        assert "source_data" in schema["required"]

    def test_wrapped_cmdfn_writes_tempfile_and_calls_original(self):
        """Wrapped cmdfn writes source_data to tempfile and calls original."""
        tool = self._make_tool_with_source()
        original_fn = tool.cmdfn
        source_content = 'REPORT zprog.\nWRITE: / "Hello".'

        patch = SourceDataPatch()
        patch.apply(tool)

        assert tool.cmdfn is not original_fn

        conn = MagicMock()
        args = SimpleNamespace(name="ZPROG", source_data=source_content)
        tool.cmdfn(conn, args)

        # Original was called
        original_fn.assert_called_once_with(conn, args)
        # source was set to a list with one path
        assert isinstance(args.source, list)
        assert len(args.source) == 1
        # Tempfile was cleaned up
        assert not os.path.exists(args.source[0])

    def test_tempfile_has_correct_content(self):
        """The tempfile written by the wrapper has the expected content."""
        tool = self._make_tool_with_source()
        source_content = 'REPORT zprog.\nWRITE: / "Hello".'
        captured_paths = []

        def capturing_fn(conn, args):
            path = args.source[0]
            captured_paths.append(path)
            with open(path, "r", encoding="utf-8") as fobj:
                assert fobj.read() == source_content

        tool.set_defaults(execute=capturing_fn)

        patch = SourceDataPatch()
        patch.apply(tool)

        args = SimpleNamespace(name="ZPROG", source_data=source_content)
        tool.cmdfn(MagicMock(), args)

        assert len(captured_paths) == 1
        assert not os.path.exists(captured_paths[0])

    def test_cleanup_on_command_error(self):
        """Tempfile is removed even when the original cmdfn raises."""
        tool = self._make_tool_with_source()
        captured_paths = []

        def failing_fn(conn, args):
            captured_paths.append(args.source[0])
            raise RuntimeError("boom")

        tool.set_defaults(execute=failing_fn)

        patch = SourceDataPatch()
        patch.apply(tool)

        args = SimpleNamespace(name="ZPROG", source_data="REPORT zprog.")
        with pytest.raises(RuntimeError, match="boom"):
            tool.cmdfn(MagicMock(), args)

        assert len(captured_paths) == 1
        assert not os.path.exists(captured_paths[0])

    def test_no_source_data_calls_original_directly(self):
        """When source_data is absent on args, original cmdfn is called as-is."""
        tool = self._make_tool_with_source()
        original_fn = tool.cmdfn

        patch = SourceDataPatch()
        patch.apply(tool)

        conn = MagicMock()
        args = SimpleNamespace(name="ZPROG")
        tool.cmdfn(conn, args)

        original_fn.assert_called_once_with(conn, args)

    def test_empty_source_data_raises(self):
        """Empty source_data string raises ToolInputError (user-input error)."""
        tool = self._make_tool_with_source()

        patch = SourceDataPatch()
        patch.apply(tool)

        with pytest.raises(ToolInputError, match="source_data must not be empty"):
            tool.cmdfn(MagicMock(), SimpleNamespace(source_data=""))

    def test_unicode_content(self):
        """Handles unicode source data correctly."""
        tool = self._make_tool_with_source()
        unicode_source = 'REPORT zprog.\n* Ünïcödé cömmënt\nWRITE: / "Héllo".'
        captured_content = []

        def capturing_fn(conn, args):
            with open(args.source[0], "r", encoding="utf-8") as fobj:
                captured_content.append(fobj.read())

        tool.set_defaults(execute=capturing_fn)

        patch = SourceDataPatch()
        patch.apply(tool)

        args = SimpleNamespace(source_data=unicode_source)
        tool.cmdfn(MagicMock(), args)

        assert captured_content == [unicode_source]

    def test_concurrent_calls_use_separate_tempfiles(self):
        """Multiple calls to the wrapped cmdfn create separate tempfiles."""
        tool = self._make_tool_with_source()
        captured_paths = []

        def capturing_fn(conn, args):
            captured_paths.append(args.source[0])

        tool.set_defaults(execute=capturing_fn)

        patch = SourceDataPatch()
        patch.apply(tool)

        tool.cmdfn(MagicMock(), SimpleNamespace(source_data="content1"))
        tool.cmdfn(MagicMock(), SimpleNamespace(source_data="content2"))

        assert len(captured_paths) == 2
        assert captured_paths[0] != captured_paths[1]


# ---------------------------------------------------------------------------
# ConnectionPatch
# ---------------------------------------------------------------------------


class TestConnectionPatchInit:
    """Tests for ConnectionPatch.__init__() validation."""

    def test_valid_default_system(self):
        patch = ConnectionPatch(["DEV", "QA"], "DEV")
        assert patch._default_system == "DEV"

    def test_none_default_system(self):
        patch = ConnectionPatch(["DEV", "QA"], None)
        assert patch._default_system is None

    def test_invalid_default_system_raises(self):
        with pytest.raises(ValueError, match="not in system_names"):
            ConnectionPatch(["DEV", "QA"], "PROD")

    def test_empty_system_names_with_default_raises(self):
        with pytest.raises(ValueError, match="not in system_names"):
            ConnectionPatch([], "DEV")


class TestConnectionPatchAppliesTo:
    """Tests for ConnectionPatch.applies_to()."""

    def test_positive_tool_with_connection_params(self):
        tool = SimpleNamespace(
            input_schema=SimpleNamespace(
                properties={"ashost": {"type": "string"}, "name": {"type": "string"}}
            )
        )
        patch = ConnectionPatch(["DEV"], "DEV")
        assert patch.applies_to("some_tool", tool) is True

    def test_negative_tool_without_connection_params(self):
        tool = SimpleNamespace(
            input_schema=SimpleNamespace(
                properties={"name": {"type": "string"}, "type": {"type": "string"}}
            )
        )
        patch = ConnectionPatch(["DEV"], "DEV")
        assert patch.applies_to("some_tool", tool) is False


class TestConnectionPatchApply:
    """Tests for ConnectionPatch.apply() — schema transformation."""

    @staticmethod
    def _make_tool_with_connection_params():
        """Create an ArgParserTool with typical connection + business params."""
        apt = ArgParserTool("tester", None)
        apt.add_properties(
            {
                "ashost": {"type": "string"},
                "client": {"type": "string"},
                "user": {"type": "string"},
                "password": {"type": "string"},
            }
        )
        tool = apt.add_parser("read")
        tool.add_properties(
            {
                "port": {"type": "integer"},
                "ssl": {"type": "boolean"},
                "verify": {"type": "boolean"},
            }
        )
        tool.add_argument("name")
        return apt.tools["tester_read"]

    def test_strips_all_connection_params(self):
        tool = self._make_tool_with_connection_params()
        patch = ConnectionPatch(["DEV"], "DEV")
        patch.apply(tool)

        props = tool.input_schema.properties
        for param in ConnectionPatch.CONNECTION_PARAMS:
            assert param not in props, f"{param} should be stripped"

        assert "name" in props  # business param preserved

    def test_strips_from_required(self):
        tool = self._make_tool_with_connection_params()
        patch = ConnectionPatch(["DEV"], "DEV")
        patch.apply(tool)

        for param in ConnectionPatch.CONNECTION_PARAMS:
            assert param not in tool.input_schema.required

    def test_single_system_adds_optional_system_param(self):
        tool = self._make_tool_with_connection_params()
        patch = ConnectionPatch(["DEV"], "DEV")
        patch.apply(tool)

        assert "system" in tool.input_schema.properties
        assert tool.input_schema.properties["system"]["default"] == "DEV"
        assert tool.input_schema.properties["system"]["enum"] == ["DEV"]
        assert "system" not in tool.input_schema.required

    def test_multi_system_with_default_adds_optional_system(self):
        tool = self._make_tool_with_connection_params()
        patch = ConnectionPatch(["DEV", "QA"], "DEV")
        patch.apply(tool)

        assert "system" in tool.input_schema.properties
        assert "system" not in tool.input_schema.required
        assert tool.input_schema.properties["system"]["enum"] == ["DEV", "QA"]
        desc = tool.input_schema.properties["system"]["description"]
        assert "DEV" in desc
        assert "QA" in desc

    def test_multi_system_no_default_requires_system(self):
        tool = self._make_tool_with_connection_params()
        patch = ConnectionPatch(["DEV", "QA"], None)
        patch.apply(tool)

        assert "system" in tool.input_schema.properties
        assert "system" in tool.input_schema.required

    def test_schema_reflected_in_mcp_output(self):
        tool = self._make_tool_with_connection_params()
        patch = ConnectionPatch(["DEV", "QA"], "DEV")
        patch.apply(tool)

        schema = tool.to_mcp_input_schema()
        for param in ConnectionPatch.CONNECTION_PARAMS:
            assert param not in schema["properties"]
        assert "system" in schema["properties"]
        assert "name" in schema["properties"]


# ---------------------------------------------------------------------------
# SourceFileToInlinePatch
# ---------------------------------------------------------------------------


class TestSourceFileToInlinePatchAppliesTo:
    """Tests for SourceFileToInlinePatch.applies_to()."""

    def test_positive_source_string(self):
        tool = ArgParserTool("test", None)
        tool.add_argument("source", type=str)
        tool.set_defaults(execute=lambda c, a: None)

        patch = SourceFileToInlinePatch()
        assert patch.applies_to("abap_abap_run", tool) is True

    def test_negative_source_array(self):
        tool = ArgParserTool("test", None)
        tool.add_argument("source", nargs="+", type=str)
        tool.set_defaults(execute=lambda c, a: None)

        patch = SourceFileToInlinePatch()
        assert patch.applies_to("some_write", tool) is False

    def test_negative_no_source(self):
        tool = ArgParserTool("test", None)
        tool.add_argument("--name")
        tool.set_defaults(execute=lambda c, a: None)

        patch = SourceFileToInlinePatch()
        assert patch.applies_to("some_tool", tool) is False


class TestSourceFileToInlinePatchApply:
    """Tests for SourceFileToInlinePatch.apply()."""

    def test_schema_removes_source_adds_source_data(self):
        tool = ArgParserTool("test", None)
        tool.add_argument("source", type=str)
        tool.set_defaults(execute=lambda c, a: None)

        patch = SourceFileToInlinePatch()
        patch.apply(tool)

        schema = tool.to_mcp_input_schema()
        assert "source" not in schema["properties"]
        assert "source_data" in schema["properties"]
        assert "source_data" in schema["required"]

    def test_wrapped_cmdfn_writes_tempfile_as_string(self):
        """source is set as a string (not a list) for single-file commands."""
        captured = {}

        def original_fn(conn, args):
            captured["source"] = args.source
            with open(args.source, "r", encoding="utf-8") as f:
                captured["content"] = f.read()

        tool = ArgParserTool("test", None)
        tool.add_argument("source", type=str)
        tool.set_defaults(execute=original_fn)

        patch = SourceFileToInlinePatch()
        patch.apply(tool)

        tool.cmdfn(MagicMock(), SimpleNamespace(source_data="hello world"))

        assert isinstance(captured["source"], str)
        assert captured["content"] == "hello world"
        assert not os.path.exists(captured["source"])  # cleaned up

    def test_empty_source_data_raises(self):
        """Empty source_data string raises ToolInputError (user-input error)."""
        tool = ArgParserTool("test", None)
        tool.add_argument("source", type=str)
        tool.set_defaults(execute=lambda c, a: None)

        patch = SourceFileToInlinePatch()
        patch.apply(tool)

        with pytest.raises(ToolInputError, match="source_data must not be empty"):
            tool.cmdfn(MagicMock(), SimpleNamespace(source_data=""))

    def test_cleanup_on_command_error(self):
        """Tempfile is cleaned up even if original command raises."""
        captured = {}

        def failing_fn(conn, args):
            captured["source"] = args.source
            raise RuntimeError("command failed")

        tool = ArgParserTool("test", None)
        tool.add_argument("source", type=str)
        tool.set_defaults(execute=failing_fn)

        patch = SourceFileToInlinePatch()
        patch.apply(tool)

        with pytest.raises(RuntimeError, match="command failed"):
            tool.cmdfn(MagicMock(), SimpleNamespace(source_data="content"))

        assert isinstance(captured["source"], str)
        assert not os.path.exists(captured["source"])


# ---------------------------------------------------------------------------
# MissingGroupParamPatch
# ---------------------------------------------------------------------------


class TestMissingGroupParamPatchAppliesTo:
    """Tests for MissingGroupParamPatch.applies_to()."""

    def test_positive_functionmodule_delete(self):
        tool = ArgParserTool("test", None)
        patch = MissingGroupParamPatch()
        assert patch.applies_to("abap_functionmodule_delete", tool) is True

    def test_positive_functionmodule_whereused(self):
        tool = ArgParserTool("test", None)
        patch = MissingGroupParamPatch()
        assert patch.applies_to("abap_functionmodule_whereused", tool) is True

    def test_positive_functiongroup_include_whereused(self):
        tool = ArgParserTool("test", None)
        patch = MissingGroupParamPatch()
        assert patch.applies_to("abap_functiongroup_include_whereused", tool) is True

    def test_positive_functiongroup_include_delete(self):
        tool = ArgParserTool("test", None)
        patch = MissingGroupParamPatch()
        assert patch.applies_to("abap_functiongroup_include_delete", tool) is True

    def test_negative_other_tool(self):
        tool = ArgParserTool("test", None)
        patch = MissingGroupParamPatch()
        assert patch.applies_to("abap_functionmodule_read", tool) is False


class TestMissingGroupParamPatchApply:
    """Tests for MissingGroupParamPatch.apply()."""

    def test_adds_group_parameter(self):
        tool = ArgParserTool("test", None)
        tool.add_argument("name", nargs="+", type=str)
        tool.set_defaults(execute=lambda c, a: None)

        patch = MissingGroupParamPatch()
        patch.apply(tool)

        schema = tool.to_mcp_input_schema()
        assert "group" in schema["properties"]
        assert schema["properties"]["group"] == {"type": "string"}
        assert "group" in schema["required"]

    def test_does_not_overwrite_existing_group(self):
        tool = ArgParserTool("test", None)
        tool.add_argument("--group", default="MY_GROUP")
        tool.set_defaults(execute=lambda c, a: None)

        patch = MissingGroupParamPatch()
        patch.apply(tool)

        schema = tool.to_mcp_input_schema()
        # Should not overwrite existing
        assert schema["properties"]["group"]["default"] == "MY_GROUP"
