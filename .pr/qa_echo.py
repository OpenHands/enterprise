from fastmcp import FastMCP

mcp = FastMCP('Enterprise QA')


@mcp.tool()
def qa_echo(value: str) -> str:
    return value


mcp.run(show_banner=False)
