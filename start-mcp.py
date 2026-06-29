"""Launch the sapcli MCP server with default config.

Requires: pip install -e . (or uv pip install -e .)

This is a convenience launcher that:
- Captures stderr to a temp file (required for clean MCP stdio transport)
  and displays it only on non-zero exit (so startup errors are visible)
- Defaults to --stdio --experimental with local sapcli-mcp.json config

For direct usage, prefer the installed entry point: sapcli-mcp
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    """Launch the stdio MCP server, surfacing captured stderr on failure."""
    config = os.path.join(HERE, 'sapcli-mcp.json')

    # Capture stderr to a file so MCP stdio transport stays clean,
    # but we can still surface errors on startup failure.
    # Note: stderr accumulates for the server's lifetime (logging output).
    # This is acceptable — the file is deleted on exit and only displayed
    # if the process exits with an error code.
    fd, stderr_log = tempfile.mkstemp(suffix='.log', prefix='sapcli-mcp-')
    exit_code = 1
    try:
        with os.fdopen(fd, 'w') as stderr_file:
            exit_code = subprocess.call(
                [sys.executable, '-m', 'sapclimcp',
                 '--stdio', '--config', config, '--experimental'],
                stderr=stderr_file,
            )

        if exit_code != 0:
            try:
                with open(stderr_log, 'r', encoding="utf-8") as f:
                    error_output = f.read().strip()
                if error_output:
                    print(
                        f"MCP server failed (exit {exit_code}):\n{error_output}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"MCP server exited with code {exit_code} (no details)",
                        file=sys.stderr,
                    )
            except OSError:
                pass
    finally:
        try:
            os.unlink(stderr_log)
        except OSError:
            pass

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
