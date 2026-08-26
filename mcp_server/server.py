"""
Real MCP server exposing StockTraders AI's data as MCP tools. Any MCP-capable
AI client (Claude Desktop, ChatGPT with MCP support, Cursor, ...) connects
here directly and pays for its own LLM tokens — StockTraders only serves
data, exactly like FiinPro-X MCP.

Tools are built straight from core.tool_registry (the same 29-tool surface
the webapp uses), with rule guidance embedded via core.knowledge so the
connecting AI's own model does intent recognition by reading tool
descriptions - no custom keyword classifier lives on this side of the wire.

Run (stdio, for Claude Desktop / local MCP inspector):
    python -m mcp_server.server

Run (HTTP, for remote clients):
    python -m mcp_server.server --http --port 8100
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from core.tool_registry import ToolRegistry
from core.executor import APIExecutor
from core.knowledge import augment_tool_descriptions, search_case_ideas, search_books, SERVER_INSTRUCTIONS
from core.response_format import apply_formatter


def _openai_tool_to_mcp(tool: dict) -> types.Tool:
    fn = tool["function"]
    return types.Tool(
        name=fn["name"],
        description=fn.get("description") or fn["name"],
        inputSchema=fn.get("parameters") or {"type": "object", "properties": {}},
    )


def build_server() -> Server:
    registry = ToolRegistry()
    registry.load()
    augment_tool_descriptions(registry.tools)

    executor = APIExecutor(registry)

    mcp_tools = [_openai_tool_to_mcp(t) for t in registry.tools]
    mcp_tools.append(
        types.Tool(
            name="searchCaseIdeas",
            description=(
                "Tim cac FAQ/case bo sung do admin StockTraders AI tao (ngoai cac tool du lieu chuan). "
                "Chi goi khi cau hoi khong khop ro voi bat ky tool du lieu nao khac. "
                "[QUAN TRONG] Cau hoi dang 'X la gi', 'khai niem X', 'the nao la X' KHONG duoc goi tool nay - "
                "PHAI goi searchKnowledgeBooks thay vao do, vi do la noi luu dinh nghia chinh thuc."
            ),
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
    )
    mcp_tools.append(
        types.Tool(
            name="searchKnowledgeBooks",
            description=(
                "[GOI TOOL NAY DAU TIEN cho moi cau hoi dang 'X la gi'/'khai niem X'/'the nao la X', "
                "TRUOC KHI can nhac searchCaseIdeas.] "
                "Tim kien thuc/khai niem trong tai lieu noi bo StockTraders AI - noi luu dinh nghia CHINH THUC "
                "cua cac thuat ngu nhu Cho Mua, Mua, Cho Ban, Ban, Chan Song, Song Lon, Song Hoi... "
                "(HDSD, tieu chi co phieu manh, loi ich giao dich tai chan song lon, vi sao nen mua dung day...). "
                "Dung khi user hoi 'X la gi', 'khai niem X', 'vi sao nen...', 'tieu chi...' - "
                "KHONG dung cho cau hoi can so lieu thuc te (hay dung cac tool du lieu khac cho truong hop do)."
            ),
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
    )

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=mcp_tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}

        if name == "searchCaseIdeas":
            result: Any = search_case_ideas(str(arguments.get("query") or ""))
        elif name == "searchKnowledgeBooks":
            result = search_books(str(arguments.get("query") or ""))
        else:
            real_operation_id = registry.resolve_operation_id(name)
            raw = executor.call(real_operation_id, arguments)
            result = apply_formatter(real_operation_id, raw)

        text = json.dumps(result, ensure_ascii=False)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    server: Server = Server(
        "stocktraders-ai",
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    return server


async def _run_stdio():
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _run_http(port: int):
    import uvicorn

    server = build_server()
    app = server.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="Run as HTTP server instead of stdio")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()

    if args.http:
        _run_http(args.port)
    else:
        asyncio.run(_run_stdio())
