"""Adapt sapcli's argparse-based command definitions into MCP tool schemas.

Provides :class:`ArgParserTool`, a drop-in stand-in for ``argparse.ArgumentParser``
that records ``add_argument``/``add_parser`` calls and converts them into MCP tool
names and JSON input schemas instead of parsing a command line.
"""

import builtins
import copy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


class ArgToToolConversionError(Exception):
    """Raised when an argparse argument cannot be mapped to a JSON schema spec."""

    def __init__(self, message):
        super().__init__(message)


class MissingArgument(Exception):
    """Raised when required tool parameters are absent from the supplied arguments."""

    def __init__(self, message):
        super().__init__(message)


def _add_default_if_specified(spec, default):
    if default is not None:
        spec["default"] = default


def _builtin_to_spec(builtin_type):
    match builtin_type:
        case builtins.bool:
            return {"type": "boolean"}
        case builtins.str:
            return {"type": "string"}
        case builtins.int:
            return {"type": "integer"}
        case _:
            raise ArgToToolConversionError("Unsupported type: " + str(builtin_type))


def _argument_spec_to_json_spec(argparserArgument):
    action = argparserArgument.get("action", None)
    typ = argparserArgument.get("type", None)
    nargs = argparserArgument.get("nargs", "")

    if (action == "append") or (nargs in ["+", "*"]):
        if typ is None:
            item_spec = _builtin_to_spec(str)
        else:
            item_spec = _builtin_to_spec(typ)
        spec = {"type": "array", "items": item_spec}
    elif action in ["store_true", "store_false"]:
        spec = _builtin_to_spec(bool)
    elif action in ["count"]:
        spec = _builtin_to_spec(int)
    elif action is None and typ is None:
        spec = _builtin_to_spec(str)
    else:
        spec = _builtin_to_spec(typ)

    # store_true/store_false carry an implicit argparse default (False/True);
    # emit it so an omitted optional flag resolves to that instead of None.
    if action == "store_true":
        default = argparserArgument.get("default", False)
    elif action == "store_false":
        default = argparserArgument.get("default", True)
    else:
        default = argparserArgument.get("default", None)
    _add_default_if_specified(spec, default)

    choices = argparserArgument.get("choices", None)
    if choices is not None:
        spec["enum"] = list(choices)

    return spec


@dataclass
class ArgParserToolInputSchema:
    """JSON-schema fragment for a tool: its properties and required parameter names."""

    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)


class ArgParserTool:  # pylint: disable=too-many-instance-attributes
    """Monkey patching the standard python argparser.ArgParser to
    transform command line arguments into MCP tool name and input schema.

    MCP tools also need output schema but we do not deal with that in this
    class as all commands will have a common schema because they all
    print output to standard IO.

    Every defined parser is supposed to be a new MCP tool.

    The root ArgParser instance has all the MCP definitions stored
    in the instance member tools.

    The tools have the JSON input schema definition in the members:

    To make this class working properly, you need to call set_defaults
    with "execute=<Callable[connection, SimpleNamespace]>". The value will
    be stored in the member cmdfn.
    """

    def __init__(self, name, parent, conn_factory=None, conn_type=None):
        self.name = name
        self.cmdfn = None
        self.conn_factory = conn_factory
        self.conn_type = conn_type
        self.input_schema = ArgParserToolInputSchema()
        self.tools = {}
        self._defaults: dict[str, Any] = {}

        self._parent = parent
        self._parameters = {}

    def add_argument(self, *args, **kwargs):
        """Convert command line argument to MCP Tool input property"""

        # Property name - use this one if it is a positional argument (no --)
        parameter = args[0]

        if parameter[0] == "-":
            # We are dealing with non-positional argument and we have to use
            # the long form which is prefixed with --
            if parameter[1] != "-":
                parameter = args[1]

            # MCP tool properties must not start with -
            # Also replace dashes with underscores (matching argparse behavior)
            parameter = parameter.lstrip("-").replace("-", "_")

        # Save the original configuration for debugging purposes
        self._parameters[parameter] = kwargs
        try:
            prop_spec = _argument_spec_to_json_spec(kwargs)
        except ArgToToolConversionError as ex:
            raise ArgToToolConversionError(
                self.name + " " + parameter + ": " + str(ex) + " " + str(kwargs)
            ) from ex

        self.input_schema.properties[parameter] = prop_spec

        hasdefault = "default" in kwargs
        # nargs='?' or nargs='*' means the argument is optional
        optional_nargs = kwargs.get("nargs") in ["?", "*"]
        # These argparse actions supply their own implicit default (False/0/[]),
        # so the flag is optional even without an explicit `default=` — matching
        # argparse and avoiding e.g. a store_true `no_check` being forced on the
        # caller (which could nudge an LLM into skipping write safety checks).
        optional_action = kwargs.get("action") in [
            "store_true",
            "store_false",
            "count",
            "append",
            "store_const",
        ]
        # Required is either specified or True if the parameter has no default and
        # is not optional due to nargs or an action with an implicit default.
        required = kwargs.get(
            "required", not hasdefault and not optional_nargs and not optional_action
        )

        if required:
            self.input_schema.required.append(parameter)

        # Propagate to existing subtools: some sapcli commands add arguments
        # to the parent parser AFTER install_parser has already created
        # sub-parsers (e.g. badi adds --enhancement_implementation after
        # 'list' and 'set-active' sub-commands exist). Without propagation,
        # those sub-tools would be missing the argument in their schema.
        for subtool in self.tools.values():
            if parameter not in subtool.input_schema.properties:
                subtool.input_schema.properties[parameter] = copy.deepcopy(prop_spec)
                if required and parameter not in subtool.input_schema.required:
                    subtool.input_schema.required.append(parameter)

    def set_defaults(self, **kwargs):
        """Record argparse defaults; ``execute`` becomes the tool's command function."""
        for key, value in kwargs.items():
            if key == "execute":
                self.cmdfn = value
            elif key == "console_factory":
                self._defaults[key] = value
            else:
                raise ArgToToolConversionError(
                    f"set_defaults: {self.name}: unknown parameter: {key}"
                )

    def add_subparsers(self):
        """Return self so subsequent ``add_parser`` calls register sub-tools here."""
        # I am not exactly sure what is the goal of "subparsers"
        # because commands are defined by "add_parser".
        return self

    def add_parser(self, name, help: str | None = None):  # pylint: disable=redefined-builtin,unused-argument
        """Create new MCP tool from the parser.

        The new parser inherits the parent's input schema properties,
        connection factory, and connection type.
        """

        subtool_name = self.name + "_" + name
        subtool = ArgParserTool(
            subtool_name, self, conn_factory=self.conn_factory, conn_type=self.conn_type
        )

        # Inherit parent's properties
        for prop_name, prop_spec in self.input_schema.properties.items():
            subtool.input_schema.properties[prop_name] = copy.deepcopy(prop_spec)
        subtool.input_schema.required.extend(self.input_schema.required)

        self._add_subtool(subtool)
        return subtool

    def _add_subtool(self, subtool):
        if self._parent is not None:
            self._parent._add_subtool(subtool)  # pylint: disable=protected-access

        self.tools[subtool.name] = subtool

    def to_mcp_input_schema(self) -> dict[str, Any]:
        """Render this tool's parameters as an MCP/JSON object input schema."""
        return {
            "properties": self.input_schema.properties,
            "required": self.input_schema.required,
            "type": "object",
        }

    def add_properties(self, properties: dict[str, dict[str, str]], required: bool = True) -> None:
        """Add extra input properties to the tool schema.

        Args:
            properties: Dictionary mapping property names to their JSON schema specs.
                        Each spec should contain at least a 'type' key.
            required: Whether the properties should be marked as required (default: True).
        """
        for param_name, param_spec in properties.items():
            self.input_schema.properties[param_name] = copy.deepcopy(param_spec)
            if required:
                self.input_schema.required.append(param_name)

    def _validate_arguments(self, arguments: dict[str, Any]) -> list[str]:
        """Validate that all required parameters are present in arguments.

        Args:
            arguments: Dictionary of provided arguments.

        Returns:
            List of missing required parameter names. Empty list if all present.
        """
        return [p for p in self.input_schema.required if p not in arguments]

    def parse_args(
        self,
        arguments: dict[str, Any],
        exclude_params: set[str] | frozenset[str] | None = None,  # pylint: disable=unused-argument
    ) -> SimpleNamespace:
        """Prepare command arguments with defaults for missing optional parameters.

        This method handles the typical ArgumentParser behavior of:
        - Using provided values from arguments
        - Applying defaults for missing parameters that have them
        - Setting None for optional parameters without defaults

        Args:
            arguments: Dictionary of provided arguments.

        Returns:
            SimpleNamespace with prepared arguments.
        """

        # Validate required parameters are present
        missing_params = self._validate_arguments(arguments)
        if missing_params:
            raise MissingArgument(
                f"Tool '{self.name}' missing required parameters: {', '.join(missing_params)}"
            )

        prepared = {}

        for prop_name, prop_spec in self.input_schema.properties.items():
            if prop_name in arguments:
                value = arguments[prop_name]
                # Ensure array-typed properties are always lists
                # (MCP clients may pass a single string instead of a list)
                if prop_spec.get("type") == "array" and not isinstance(value, list):
                    value = [value]
                prepared[prop_name] = value
            elif "default" in prop_spec:
                prepared[prop_name] = prop_spec["default"]
            elif prop_name not in self.input_schema.required:
                # Optional properties without defaults get None (ArgumentParser behavior)
                prepared[prop_name] = None

        # Include defaults from set_defaults (e.g. console_factory)
        # that are not part of the input schema
        for key, value in self._defaults.items():
            if key not in prepared:
                prepared[key] = value

        return SimpleNamespace(**prepared)
