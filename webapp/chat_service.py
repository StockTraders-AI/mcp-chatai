"""
Web chat, in-process, sharing the exact same tool list/guides as
mcp_server/server.py (both read from core/). This is what structurally
guarantees the "khong duoc thieu case nao" requirement: there is only one
place tools+guides are defined, so the web app and the MCP server can never
drift apart the way the old system's 4 separate routing layers did.

Each request uses the calling user's OWN OpenAI API key (BYOK) - retrieved,
decrypted, and used for exactly this one request. StockTraders' own
OPENAI_API_KEY is never touched here.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

from core.tool_registry import ToolRegistry
from core.executor import APIExecutor
from core.knowledge import (
    augment_tool_descriptions,
    search_case_ideas,
    search_books,
    SERVER_INSTRUCTIONS,
    is_pure_cashflow_query,
    CASH_FLOW_TOOLS,
)
from core.response_format import apply_formatter
from webapp.settings import DEFAULT_CHAT_MODEL, MAX_TOOL_LOOPS
from webapp.byok import get_key

# NOTE: no multi-turn context carry-forward here (see core/context_state.py,
# kept but unused for now) - by explicit decision, the webapp is single-turn
# stateless per request for the time being, same as the MCP server. If this
# changes later, the correct fix is sending full conversation history per
# request (like Claude Desktop/ChatGPT do) so the model carries context
# forward itself, not reviving the custom carry-forward heuristic.

SYSTEM_PROMPT = (
    "Ban la tro ly du lieu chung khoan StockTraders AI. Tra loi bang tieng Viet, ngan gon, "
    "dua tren du lieu that tu cac tool duoc cung cap - khong tu suy dien so lieu. "
    "Neu guide trong mo ta tool noi phai goi API truoc khi tra loi thi bat buoc goi, khong duoc "
    "tra loi bang suy luan chung chung khi chua co ket qua API. "
    "Neu ket qua tool co truong '_formatted_answer', hay uu tien dung nguyen van hoac dien giai sat "
    "noi dung do thay vi tu tinh toan lai tu so lieu tho, vi day la cau tra loi da duoc kiem tra chinh xac."
    "\n\n" + SERVER_INSTRUCTIONS
)

_registry: Optional[ToolRegistry] = None
_executor: Optional[APIExecutor] = None


def _get_registry() -> ToolRegistry:
    global _registry, _executor
    if _registry is None:
        _registry = ToolRegistry()
        _registry.load()
        augment_tool_descriptions(_registry.tools)
        _executor = APIExecutor(_registry)
    return _registry


def _get_executor() -> APIExecutor:
    _get_registry()
    assert _executor is not None
    return _executor


class MissingApiKeyError(Exception):
    pass


def chat(user_id: str, user_text: str, model: Optional[str] = None) -> str:
    api_key = get_key(user_id)
    if not api_key:
        raise MissingApiKeyError(
            "Chua thiet lap OpenAI API key rieng. Goi POST /auth/key truoc khi chat."
        )

    registry = _get_registry()
    executor = _get_executor()

    tools = list(registry.tools) + [
        {
            "type": "function",
            "function": {
                "name": "searchCaseIdeas",
                "description": (
                    "Tim cac FAQ/case bo sung do admin tao (ngoai cac tool du lieu chuan). "
                    "Chi goi khi cau hoi khong khop ro voi bat ky tool du lieu nao khac. "
                    "[QUAN TRONG] Cau hoi dang 'X la gi', 'khai niem X', 'the nao la X' KHONG duoc goi tool "
                    "nay - PHAI goi searchKnowledgeBooks thay vao do, vi do la noi luu dinh nghia chinh thuc."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "searchKnowledgeBooks",
                "description": (
                    "[GOI TOOL NAY DAU TIEN cho moi cau hoi dang 'X la gi'/'khai niem X'/'the nao la X', "
                    "TRUOC KHI can nhac searchCaseIdeas.] "
                    "Tim kien thuc/khai niem trong tai lieu noi bo StockTraders AI - noi luu dinh nghia CHINH "
                    "THUC cua cac thuat ngu nhu Cho Mua, Mua, Cho Ban, Ban, Chan Song, Song Lon, Song Hoi... "
                    "(HDSD, tieu chi co phieu manh, loi ich giao dich tai chan song lon, vi sao nen mua dung "
                    "day...). Dung khi user hoi 'X la gi', 'khai niem X', 'vi sao nen...', 'tieu chi...' - "
                    "KHONG dung cho cau hoi can so lieu thuc te."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
    ]

    system_text = SYSTEM_PROMPT + f"\n\nNgay hien tai la {datetime.now().strftime('%Y-%m-%d')}"

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]

    client = OpenAI(api_key=api_key)
    chosen_model = model or DEFAULT_CHAT_MODEL

    # Hard, deterministic override for a specific known failure mode: the
    # model kept picking an SMDT tool for plain "dòng tiền" questions even
    # with warnings on every SMDT tool's description (see
    # core.knowledge.is_pure_cashflow_query for why this can't be enforced
    # on the MCP/Claude Desktop path, only here). On the very first turn,
    # if the question is unambiguously about cash flow, don't even offer
    # the model a choice - only the 2 real cash-flow tools are on the menu.
    force_cashflow = is_pure_cashflow_query(user_text)

    for loop_index in range(MAX_TOOL_LOOPS):
        if force_cashflow and loop_index == 0:
            turn_tools = [t for t in tools if t["function"]["name"] in CASH_FLOW_TOOLS]
            turn_tool_choice = "required"
        else:
            turn_tools = tools
            turn_tool_choice = "auto"

        resp = client.chat.completions.create(
            model=chosen_model,
            messages=messages,
            tools=turn_tools,
            tool_choice=turn_tool_choice,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return msg.content or ""

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            if tc.function.name == "searchCaseIdeas":
                result = search_case_ideas(str(args.get("query") or ""))
            elif tc.function.name == "searchKnowledgeBooks":
                result = search_books(str(args.get("query") or ""))
            else:
                real_operation_id = registry.resolve_operation_id(tc.function.name)
                raw = executor.call(real_operation_id, args, user_text=user_text)
                result = apply_formatter(real_operation_id, raw, user_text=user_text)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "Vuot qua so lan goi tool cho phep, vui long thu lai voi cau hoi cu the hon."
