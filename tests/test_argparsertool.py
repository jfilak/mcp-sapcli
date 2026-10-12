"""Unit tests for ArgParserTool class."""

import pytest

from sapclimcp.argparsertool import (
    ArgParserTool,
    ArgToToolConversionError,
)


class TestArgParserToolPositionalArgument:
    """Tests for positional arguments."""

    def test_positional_string_argument(self):
        """Test positional argument without dashes is treated as required string."""
        tool = ArgParserTool("test", None)
        tool.add_argument("name")

        schema = tool.to_mcp_input_schema()

        assert "name" in schema["properties"]
        assert schema["properties"]["name"] == {"type": "string"}
        assert "name" in schema["required"]

    def test_positional_with_type(self):
        """Test positional argument with explicit type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("count", type=int)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["count"] == {"type": "integer"}
        assert "count" in schema["required"]


class TestArgParserToolShortAndLongArgument:
    """Tests for arguments with -short and --long variants."""

    def test_short_and_long_variant(self):
        """Test argument with both -x and --xxx variants uses long form."""
        tool = ArgParserTool("test", None)
        tool.add_argument("-n", "--name")

        schema = tool.to_mcp_input_schema()

        assert "name" in schema["properties"]
        assert "n" not in schema["properties"]
        assert schema["properties"]["name"] == {"type": "string"}

    def test_short_and_long_with_default(self):
        """Test -x/--xxx argument with default is not required."""
        tool = ArgParserTool("test", None)
        tool.add_argument("-v", "--verbose", default=False)

        schema = tool.to_mcp_input_schema()

        assert "verbose" in schema["properties"]
        assert "verbose" not in schema["required"]
        assert schema["properties"]["verbose"] == {"type": "string", "default": False}


class TestArgParserToolLongOnlyArgument:
    """Tests for arguments with only --long variant."""

    def test_long_only_argument(self):
        """Test argument with only --xxx variant."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--output")

        schema = tool.to_mcp_input_schema()

        assert "output" in schema["properties"]
        assert schema["properties"]["output"] == {"type": "string"}
        assert "output" in schema["required"]

    def test_long_only_with_type(self):
        """Test --xxx argument with explicit type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--port", type=int)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["port"] == {"type": "integer"}


class TestArgParserToolNargs:
    """Tests for nargs parameter.

    Note: The current implementation requires an explicit type parameter
    for nargs to create an array type. Without type, it defaults to string.
    """

    def test_nargs_plus_with_type_str(self):
        """Test nargs='+' with type=str creates array of strings."""
        tool = ArgParserTool("test", None)
        tool.add_argument("files", nargs="+", type=str)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["files"] == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_nargs_star_with_type_str(self):
        """Test nargs='*' with type=str creates array of strings."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--items", nargs="*", type=str)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["items"] == {
            "type": "array",
            "items": {"type": "string"},
        }
        assert "items" not in schema["required"]

    def test_nargs_with_type_int(self):
        """Test nargs with type=int creates array of integers."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--numbers", nargs="+", type=int)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["numbers"] == {
            "type": "array",
            "items": {"type": "integer"},
        }

    def test_nargs_question_without_type(self):
        """Test nargs='?' without type creates optional string."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--as4user", nargs="?")

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["as4user"] == {"type": "string"}
        assert "as4user" not in schema["required"]


class TestArgParserToolDefault:
    """Tests for default parameter."""

    def test_default_string(self):
        """Test argument with string default."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--format", default="json")

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["format"] == {"type": "string", "default": "json"}
        assert "format" not in schema["required"]

    def test_default_integer(self):
        """Test argument with integer default."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--count", type=int, default=10)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["count"] == {"type": "integer", "default": 10}
        assert "count" not in schema["required"]

    def test_default_boolean(self):
        """Test argument with boolean default."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--recursive", action="store_true", default=False)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["recursive"] == {
            "type": "boolean",
            "default": False,
        }

    def test_no_default_is_required(self):
        """Test argument without default is required."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--name")

        schema = tool.to_mcp_input_schema()

        assert "name" in schema["required"]


class TestArgParserToolType:
    """Tests for type parameter."""

    def test_type_string(self):
        """Test explicit string type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--name", type=str)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["name"] == {"type": "string"}

    def test_type_int(self):
        """Test explicit int type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--count", type=int)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["count"] == {"type": "integer"}

    def test_type_bool(self):
        """Test explicit bool type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--flag", type=bool)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["flag"] == {"type": "boolean"}

    def test_unsupported_type_raises_error(self):
        """Test unsupported type raises ArgToToolConversionError."""
        tool = ArgParserTool("test", None)

        with pytest.raises(ArgToToolConversionError):
            tool.add_argument("--data", type=float)


class TestArgParserToolChoices:
    """Tests for choices parameter → enum in JSON schema."""

    def test_choices_adds_enum(self):
        """Test that choices parameter produces an enum field."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--type", default="main", choices=["main", "definitions", "testclasses"])

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["type"]["enum"] == [
            "main",
            "definitions",
            "testclasses",
        ]
        assert schema["properties"]["type"]["default"] == "main"

    def test_choices_with_positional(self):
        """Test choices on a positional argument."""
        tool = ArgParserTool("test", None)
        tool.add_argument("active", choices=["true", "false"])

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["active"]["enum"] == ["true", "false"]
        assert "active" in schema["required"]

    def test_no_choices_no_enum(self):
        """Test that without choices, no enum field is present."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--name")

        schema = tool.to_mcp_input_schema()

        assert "enum" not in schema["properties"]["name"]


class TestArgParserToolActionStoreTrue:
    """Tests for action='store_true'."""

    def test_store_true_creates_boolean(self):
        """Test store_true action creates boolean type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--verbose", action="store_true")

        schema = tool.to_mcp_input_schema()

        # store_true carries an implicit False default (matches argparse), so an
        # omitted optional flag resolves to False rather than None.
        assert schema["properties"]["verbose"] == {"type": "boolean", "default": False}

    def test_store_true_with_default(self):
        """Test store_true with explicit default."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--debug", action="store_true", default=False)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["debug"] == {"type": "boolean", "default": False}


class TestArgParserToolActionStoreFalse:
    """Tests for action='store_false'."""

    def test_store_false_creates_boolean(self):
        """Test store_false action creates boolean type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--no-cache", action="store_false")

        schema = tool.to_mcp_input_schema()

        # store_false carries an implicit True default (matches argparse).
        assert schema["properties"]["no_cache"] == {"type": "boolean", "default": True}

    def test_store_false_with_default(self):
        """Test store_false with explicit default."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--no-verify", action="store_false", default=True)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["no_verify"] == {"type": "boolean", "default": True}


class TestArgParserToolActionCount:
    """Tests for action='count'."""

    def test_count_creates_integer(self):
        """Test count action creates integer type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("-v", "--verbose", action="count")

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["verbose"] == {"type": "integer"}

    def test_count_with_default(self):
        """Test count action with default value."""
        tool = ArgParserTool("test", None)
        tool.add_argument("-v", "--verbosity", action="count", default=0)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["verbosity"] == {"type": "integer", "default": 0}


class TestArgParserToolActionAppend:
    """Tests for action='append'."""

    def test_append_creates_array(self):
        """Test append action creates array type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--include", action="append")

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["include"] == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_append_with_type(self):
        """Test append action with explicit item type."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--port", action="append", type=int)

        schema = tool.to_mcp_input_schema()

        assert schema["properties"]["port"] == {
            "type": "array",
            "items": {"type": "integer"},
        }


class TestArgParserToolInheritance:
    """Tests for property inheritance in add_parser."""

    def test_subparser_inherits_properties(self):
        """Test that subparser inherits parent's properties."""
        parent = ArgParserTool("parent", None)
        parent.add_argument("--config")

        child = parent.add_parser("child")
        child.add_argument("--name")

        schema = child.to_mcp_input_schema()

        assert "config" in schema["properties"]
        assert "name" in schema["properties"]

    def test_subparser_inherits_required(self):
        """Test that subparser inherits parent's required list."""
        parent = ArgParserTool("parent", None)
        parent.add_argument("--config")

        child = parent.add_parser("child")

        schema = child.to_mcp_input_schema()

        assert "config" in schema["required"]

    def test_subparser_inherits_conn_factory(self):
        """Test that subparser inherits parent's conn_factory."""

        def mock_factory():
            return None

        parent = ArgParserTool("parent", None, conn_factory=mock_factory)

        child = parent.add_parser("child")

        assert child.conn_factory is mock_factory

    def test_subparser_name_combines_parent_name(self):
        """Test that subparser name is parent_name + '_' + child_name."""
        parent = ArgParserTool("parent", None)
        child = parent.add_parser("child")

        assert child.name == "parent_child"

    def test_late_parent_argument_propagates_to_existing_subtools(self):
        """Test that adding an argument to the parent after subtools exist
        propagates the argument to those subtools."""
        parent = ArgParserTool("parent", None)
        child = parent.add_parser("child")

        # Add argument to parent AFTER child was created
        parent.add_argument("-i", "--impl_name", help="test")

        child_schema = child.to_mcp_input_schema()
        assert "impl_name" in child_schema["properties"]
        assert "impl_name" in child_schema["required"]

    def test_late_parent_optional_argument_propagates(self):
        """Test that optional arguments propagate without being required."""
        parent = ArgParserTool("parent", None)
        child = parent.add_parser("child")

        parent.add_argument("--optional_flag", default="foo")

        child_schema = child.to_mcp_input_schema()
        assert "optional_flag" in child_schema["properties"]
        assert "optional_flag" not in child_schema["required"]

    def test_late_propagation_does_not_overwrite_existing(self):
        """Test that propagation does not overwrite if child already has the property."""
        parent = ArgParserTool("parent", None)
        child = parent.add_parser("child")

        # Child adds its own version first
        child.add_argument("--shared", default="child_default")
        # Parent adds same name later
        parent.add_argument("--shared", default="parent_default")

        child_schema = child.to_mcp_input_schema()
        # Child's original definition should be preserved
        assert child_schema["properties"]["shared"]["default"] == "child_default"


class TestArgParserToolSetDefaults:
    """Tests for set_defaults method."""

    def test_set_defaults_with_execute(self):
        """Test set_defaults stores execute function."""
        tool = ArgParserTool("test", None)

        def my_command():
            pass

        tool.set_defaults(execute=my_command)

        assert tool.cmdfn is my_command


class TestArgParserToolAddProperties:
    """Tests for add_properties method."""

    def test_add_properties_required(self):
        """Test add_properties adds required properties."""
        tool = ArgParserTool("test", None)
        props = {
            "host": {"type": "string"},
            "port": {"type": "integer"},
        }

        tool.add_properties(props)

        schema = tool.to_mcp_input_schema()
        assert schema["properties"]["host"] == {"type": "string"}
        assert schema["properties"]["port"] == {"type": "integer"}
        assert "host" in schema["required"]
        assert "port" in schema["required"]

    def test_add_properties_not_required(self):
        """Test add_properties with required=False."""
        tool = ArgParserTool("test", None)
        props = {"optional": {"type": "boolean"}}

        tool.add_properties(props, required=False)

        schema = tool.to_mcp_input_schema()
        assert "optional" in schema["properties"]
        assert "optional" not in schema["required"]


class TestArgParserToolParseArgsArrayCoercion:
    """Tests for array coercion in parse_args.

    MCP clients may pass a single string for array-typed parameters
    (e.g. nargs='+'), so parse_args must wrap it in a list.
    """

    def test_string_coerced_to_list_for_nargs_plus(self):
        """Test that a string value is wrapped in a list for nargs='+' argument."""
        tool = ArgParserTool("test", None)
        tool.add_argument("name", nargs="+")

        result = tool.parse_args({"name": "ZPROG"})

        assert result.name == ["ZPROG"]

    def test_list_kept_as_list_for_nargs_plus(self):
        """Test that a list value stays as-is for nargs='+' argument."""
        tool = ArgParserTool("test", None)
        tool.add_argument("name", nargs="+")

        result = tool.parse_args({"name": ["ZPROG1", "ZPROG2"]})

        assert result.name == ["ZPROG1", "ZPROG2"]

    def test_string_coerced_to_list_for_append_action(self):
        """Test that a string value is wrapped in a list for action='append' argument."""
        tool = ArgParserTool("test", None)
        tool.add_argument("--items", action="append")

        result = tool.parse_args({"items": "one"})

        assert result.items == ["one"]

    def test_non_array_string_not_coerced(self):
        """Test that a plain string argument is not affected by array coercion."""
        tool = ArgParserTool("test", None)
        tool.add_argument("name")

        result = tool.parse_args({"name": "ZPROG"})

        assert result.name == "ZPROG"
