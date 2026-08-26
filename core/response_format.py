"""
Applies core.formatters' exact-wording answers on top of raw API results,
shared by both mcp_server/server.py and webapp/chat_service.py so neither
one can drift from the other (same "single source of truth" principle as
knowledge.py).

Only operations with a self-contained formatter (the payload alone is
enough, no extra date/row-selection logic needed against the original
question) are wired here so far: getStock4KeyEvaluation and
getStock4KeyScreen. Other formatters ported into core/formatters.py
(waitbuy/waitsell value, cashflow ticker/branch, recent trade, recent
stock wave, branch drop) still need their date/row-selection glue ported
from orchestrator.py before they can be wired in here safely - tracked as
a known gap, not silently skipped.

Result shape: the raw API payload is always preserved (so any consuming
LLM keeps full structured data), with `_formatted_answer` added when a
formatter applies - the authoritative, non-hallucinated wording the model
should prefer to just paraphrasing the raw numbers itself.
"""

from typing import Any, Dict

from core.formatters import format_stock_4key_answer, format_stock_4key_list_answer

_FORMATTERS = {
    "getStock4KeyEvaluation": format_stock_4key_answer,
    "getStock4KeyScreen": format_stock_4key_list_answer,
}


def apply_formatter(operation_id: str, result: Any, user_text: str = "") -> Any:
    formatter = _FORMATTERS.get(operation_id)
    if not formatter or not isinstance(result, dict):
        return result

    try:
        answer = formatter(result, user_text=user_text)
    except Exception:
        return result

    if not answer:
        return result

    enriched = dict(result)
    enriched["_formatted_answer"] = answer
    return enriched
