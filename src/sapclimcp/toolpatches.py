"""
Generic tool patching mechanism for sapcli MCP tools.

Patches modify ArgParserTool instances in-place so that
SapcliCommandTool sees an already-adapted tool and needs
no patching awareness at all.
"""

import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Optional

from sapclimcp.errors import ToolInputError


class ToolPatch(ABC):
    """Abstract base class for tool patches.

    A patch modifies an ArgParserTool in-place: its input schema
    and its cmdfn. After apply(), the tool looks like it was always
    defined with the patched schema and the wrapped command function.
    """

    @abstractmethod
    def applies_to(self, tool_name: str, tool: Any) -> bool:
        """Check whether this patch should be applied to the given tool.

        Args:
            tool_name: The tool's registered name.
            tool: The ArgParserTool instance.

        Returns:
            True if this patch applies.
        """

    @abstractmethod
    def apply(self, tool: Any) -> None:
        """Modify the ArgParserTool in-place: schema and cmdfn.

        Args:
            tool: The ArgParserTool instance to patch.
        """


class SourceDataPatch(ToolPatch):
    """Replace file-based ``source`` (array) parameter with inline ``source_data``.

    sapcli write commands expect ``source`` as a list of file paths.
    MCP clients cannot provide file paths, so this patch:
    - Replaces ``source`` with ``source_data`` (a string) in the tool schema
    - Wraps cmdfn so that at invocation time source_data is written to
      a tempfile, ``source=[tmppath]`` is set on args, and the tempfile
      is cleaned up in a finally block.
    """

    def applies_to(self, tool_name: str, tool: Any) -> bool:
        props = tool.input_schema.properties
        source_spec = props.get("source")
        return source_spec is not None and source_spec.get("type") == "array"

    def _set_source(self, args: SimpleNamespace, tmppath: str) -> None:
        args.source = [tmppath]

    def apply(self, tool: Any) -> None:
        schema = tool.input_schema

        schema.properties.pop("source", None)
        if "source" in schema.required:
            schema.required.remove("source")

        schema.properties["source_data"] = {
            "type": "string",
            "description": "Inline source code content",
        }
        if "source_data" not in schema.required:
            schema.required.append("source_data")

        original_cmdfn = tool.cmdfn
        set_source = self._set_source

        def wrapped_cmdfn(conn: Any, args: SimpleNamespace) -> None:
            source_data = getattr(args, "source_data", None)
            if source_data is None:
                original_cmdfn(conn, args)
                return

            if not source_data:
                raise ToolInputError("source_data must not be empty")

            fd, tmppath = tempfile.mkstemp(suffix=".abap")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fobj:
                    fobj.write(source_data)
            except Exception:
                os.unlink(tmppath)
                raise

            set_source(args, tmppath)
            try:
                original_cmdfn(conn, args)
            finally:
                try:
                    os.unlink(tmppath)
                except OSError:
                    pass

        tool.cmdfn = wrapped_cmdfn


class SourceFileToInlinePatch(SourceDataPatch):
    """Replace file-based ``source`` (string) parameter with inline content.

    Some sapcli commands (e.g. ``abap_abap_run``) expect ``source`` as a
    single file path (string type, not array). Inherits ``apply()`` from
    parent; customizes only ``applies_to`` and ``_set_source``.
    """

    def applies_to(self, tool_name: str, tool: Any) -> bool:
        props = tool.input_schema.properties
        source_spec = props.get("source")
        return source_spec is not None and source_spec.get("type") == "string"

    def _set_source(self, args: SimpleNamespace, tmppath: str) -> None:
        args.source = tmppath


class MissingGroupParamPatch(ToolPatch):
    """Add missing ``group`` parameter to function module/include tools that lack it.

    Workaround for upstream sapcli bug: CommandGroupFunctionModule and
    CommandGroupFunctionGroupInclude do not override ``define_delete`` or
    ``define_whereused`` to add the ``group`` argument (unlike create, read,
    write, activate which all call ``insert_argument(0, 'group')``).

    This patch is forward-compatible: if sapcli fixes the issue upstream,
    the guard in apply() makes this a no-op. Remove this patch once sapcli
    ships the fix and our minimum version requires it.
    """

    _AFFECTED_TOOLS = frozenset(
        {
            "abap_functionmodule_delete",
            "abap_functionmodule_whereused",
            "abap_functiongroup_include_whereused",
            "abap_functiongroup_include_delete",
        }
    )

    def applies_to(self, tool_name: str, tool: Any) -> bool:
        return tool_name in self._AFFECTED_TOOLS

    def apply(self, tool: Any) -> None:
        schema = tool.input_schema
        if "group" not in schema.properties:
            schema.properties["group"] = {"type": "string"}
            schema.required.append("group")


class ConnectionPatch(ToolPatch):
    """Strip connection parameters from tool schemas and add a system selector.

    When the server manages connections server-side, the LLM should not
    see or provide ``ashost``, ``client``, ``user``, ``password``, etc.
    This patch:

    - Removes all connection parameters from the tool's input schema
    - Adds an optional ``system`` parameter when multiple systems are
      configured (or a single system with a default)
    """

    CONNECTION_PARAMS = frozenset(
        {
            "ashost",
            "port",
            "client",
            "user",
            "password",
            "ssl",
            "verify",
            "sysnr",
        }
    )

    def __init__(
        self,
        system_names: list[str],
        default_system: Optional[str] = None,
    ) -> None:
        if default_system is not None and default_system not in system_names:
            raise ValueError(
                f"default_system {default_system!r} not in system_names {system_names}"
            )
        self._system_names = system_names
        self._default_system = default_system

    def applies_to(self, tool_name: str, tool: Any) -> bool:
        return bool(self.CONNECTION_PARAMS & set(tool.input_schema.properties.keys()))

    def apply(self, tool: Any) -> None:
        schema = tool.input_schema

        for param in self.CONNECTION_PARAMS:
            schema.properties.pop(param, None)
            if param in schema.required:
                schema.required.remove(param)

        if len(self._system_names) > 1:
            desc = f"Target SAP system. Available: {', '.join(self._system_names)}"
            if self._default_system:
                desc += f". Default: {self._default_system}"
            schema.properties["system"] = {
                "type": "string",
                "description": desc,
                "enum": self._system_names,
            }
            if not self._default_system:
                schema.required.append("system")
        elif len(self._system_names) == 1:
            schema.properties["system"] = {
                "type": "string",
                "description": f"Target SAP system (default: {self._system_names[0]})",
                "default": self._system_names[0],
                "enum": self._system_names,
            }


def apply_patches(
    tool_name: str,
    tool: Any,
    patch_registry: Sequence[ToolPatch],
) -> None:
    """Find and apply applicable patches to a tool in-place.

    Args:
        tool_name: The tool name.
        tool: The ArgParserTool instance to patch.
        patch_registry: List of available patches.
    """
    for patch in patch_registry:
        if patch.applies_to(tool_name, tool):
            patch.apply(tool)
