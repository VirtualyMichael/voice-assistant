"""A tiny MCP server used by the tests (run over stdio)."""
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("demo")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.tool()
def delete_everything(path: str) -> str:
    """Pretend to delete something (needs confirmation)."""
    return f"deleted {path}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def leaked_env() -> str:
    """Report whether a secret from the parent's environment reached this process."""
    return "LEAK" if os.environ.get("VA_TEST_SECRET") else "clean"


if __name__ == "__main__":
    mcp.run()
