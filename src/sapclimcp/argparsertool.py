import builtins

from dataclasses import dataclass, field
from typing import Any


class ArgToToolConversionError(Exception):

    def __init__(self, message):
        super().__init__(message)


def _add_default_if_specified(spec, default):
    if default is not None:
        spec['default'] = default

    return spec


def _builtin_to_spec(builtinTyp):
    match builtinTyp:
        case builtins.bool:
            return {'type': 'boolean'}
        case builtins.str:
            return {'type': 'string'}
        case builtins.int:
            return {'type': 'integer'}
        case _:
            raise ArgToToolConversionError('Unsupported type: ' + str(builtinTyp))


def _argument_spec_to_json_spec(argparserArgument):
    action = argparserArgument.get('action', None)
    typ = argparserArgument.get('type', None)
    nargs = argparserArgument.get('nargs', '')

    if (action is None and typ is None):
        spec = _builtin_to_spec(str)
    elif (action == 'append') or (nargs in ['+', '*']):
        if typ is None:
            item_spec = _builtin_to_spec(str)
        else:
            item_spec = _builtin_to_spec(typ)
        spec = {'type': 'array', 'items': item_spec }
    elif action in ['store_true', 'store_false']:
        spec = _builtin_to_spec(bool)
    elif action in ['count']:
        spec = _builtin_to_spec(int)
    else:
        spec = _builtin_to_spec(typ)

    default = argparserArgument.get('default', None)
    return _add_default_if_specified(spec, default)


@dataclass
class ArgPaserToolInputSchema:
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)


class ArgParserTool:
    """Monkey patching the standard python argparser.ArgParser to
       transform command line arguments into MCP tool name and input schema.

       MCP tools also need output schema but we do not deal with that in this
       class as all commands will have a common schema because they all
       print output to standard IO.

       Every defined parser is supposed to be a new MCP tool.

       The root ArgParser instance has all the MCP definitions stored
       in the instance member tools.

       The tools have the JSON input schema definition in the members:\

    """

    def __init__(self, name, parent, conn_factory=None):
        self.name = name
        self.cmdfn = None
        self.conn_factory = conn_factory
        self.input_schema = ArgPaserToolInputSchema()
        self.tools = {}

        self._parent = parent
        self._parameters = {}

    def add_argument(self, *args, **kwargs):
        """Convert command line argument to MCP Tool input property"""

        # Property name - use this one if it is a possitional argument (no --)
        parameter = args[0]

        if parameter[0] == '-':
            # We are dealing with non-positional argument and we have to use
            # the long form which is prefixed with --
            if parameter[1] != '-':
                parameter = args[1]

            # MCP tool properties must not start with -
            # Also replace dashes with underscores (matching argparse behavior)
            parameter = parameter.lstrip('-').replace('-', '_')

        # Save the original configuration for debugging purposes
        self._parameters[parameter] = kwargs
        try:
            self.input_schema.properties[parameter] = _argument_spec_to_json_spec(kwargs)
        except ArgToToolConversionError as ex:
            raise ArgToToolConversionError(self.name + " " + parameter + ': ' + str(ex) + " " + str(kwargs))

        hasdefault = 'default' in kwargs
        # nargs='?' or nargs='*' means the argument is optional
        optional_nargs = kwargs.get('nargs') in ['?', '*']
        # Required is either specified or True if the parameter does not have default
        # and is not optional due to nargs
        required = kwargs.get('required', not hasdefault and not optional_nargs)

        if required:
            self.input_schema.required.append(parameter)

    def set_defaults(self, **kwargs):
        if len(kwargs.keys()) != 1 or 'execute' not in kwargs:
            ArgToToolConversionError('set_defaults: ' + self.name + ' ' + str(kwargs))

        self.cmdfn = kwargs['execute']

    def add_subparsers(self):
        # I am not exactly sure what is the goal of "subparsers"
        # because commands are defined by "add_parser".
        return self

    def add_parser(self, name):
        """Create new MCP tool from the parser.

        The new parser inherits the parent's input schema properties and connection factory.
        """

        subtool_name = self.name + "_" + name
        subtool = ArgParserTool(subtool_name, self, conn_factory=self.conn_factory)

        # Inherit parent's properties
        for prop_name, prop_spec in self.input_schema.properties.items():
            subtool.input_schema.properties[prop_name] = prop_spec.copy()
        subtool.input_schema.required.extend(self.input_schema.required)

        self._add_subtool(subtool)
        return subtool

    def _add_subtool(self, subtool):
        if self._parent is not None:
            self._parent._add_subtool(subtool)

        self.tools[subtool.name] = subtool

    def to_mcp_input_schema(self) -> dict[str, Any]:
        return {
            'properties': self.input_schema.properties,
            'required': self.input_schema.required,
            'type': 'object',
        }

    def to_mcp_output_schema(self) -> dict[str, Any]:
        # TODO: return the default FastMCP output schema
        return {}

    def add_properties(self, properties: dict[str, dict[str, str]], required: bool = True) -> None:
        """Add extra input properties to the tool schema.

        Args:
            properties: Dictionary mapping property names to their JSON schema specs.
                        Each spec should contain at least a 'type' key.
            required: Whether the properties should be marked as required (default: True).
        """
        for param_name, param_spec in properties.items():
            self.input_schema.properties[param_name] = param_spec.copy()
            if required:
                self.input_schema.required.append(param_name)
