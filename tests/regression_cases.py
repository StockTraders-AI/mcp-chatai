"""
Offline regression suite — no OpenAI calls, no network. Run with:

    python tests/regression_cases.py

Purpose (per the migration plan's "khong duoc thieu case nao" requirement):
this is the checklist from the plan turned into runnable assertions, so a
future change that silently drops a case fails loudly here instead of
being discovered by a user days later (exactly how today's chatbotgpt bugs
were found — manually, one at a time, in production).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_registry import ToolRegistry
from core.knowledge import (
    TOOL_GUIDES, augment_tool_descriptions, search_case_ideas, SERVER_INSTRUCTIONS,
    is_pure_cashflow_query,
)
from core.constants import MAIN_BRANCHES
from core.context_state import ConversationState
from core.formatters import (
    format_stock_4key_answer,
    format_stock_4key_list_answer,
    _derive_4key_group,
    requested_4key_groups,
    stock_4key_screen_args,
)

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------
# 1. Tool registry loads the full 29-tool surface (26 openapi + 3 custom)
# ---------------------------------------------------------------------
registry = ToolRegistry()
registry.load()
tool_names = {t["function"]["name"] for t in registry.tools}

EXPECTED_TOOLS = {
    "getTotalTradeReal", "getTotalTrade", "getStockWave", "getSMDTLastN",
    "getAnalyzeWave", "getBranchSMDTTickers", "getStockSignal", "getBranchPathHap",
    "getSMDTBranch", "getSMDTTicker", "getSMDTTickerCross", "getDongTienTheoNganh",
    "getDongTienTheoMa", "getBranchPath", "getTickersPriceDownSMDTIncreasing",
    "getTotalTradeWithSMDT", "getPerformance", "getZigZagPoint", "getSMDTBranchCross",
    "getSMDTIncreasing3", "getSMDTTickerDrop", "getSMDTBranchDrop",
    "getTopBranchSMDTIncreasing", "getBranchStrongSMDTWithPrice",
    "getLeadingCoreBranches", "getCoreBranchLeader",
    "getWaveBottomConfirmPairs", "getStock4KeyEvaluation", "getStock4KeyScreen",
    "getStock4KeyHistory",
}
check(f"tool registry exposes all {len(EXPECTED_TOOLS)} expected tools", EXPECTED_TOOLS.issubset(tool_names))
check("no unexpected extra/missing tool count drift", tool_names == EXPECTED_TOOLS)

# ---------------------------------------------------------------------
# 2. Every rule-doc guide maps to a real tool (catches typos in knowledge.py)
# ---------------------------------------------------------------------
for op_id in TOOL_GUIDES:
    check(f"TOOL_GUIDES['{op_id}'] refers to a real registered tool", op_id in tool_names)

# Every data tool that has an actual rule guide in data/knowledge/rules/*.txt
# must carry that guide. This exact list (26 tools) is what's covered per the
# full 55-case catalog audit; the 3 gaps (getTotalTradeWithSMDT, getZigZagPoint,
# getBranchPathHap) have no corresponding rule text in the 11 source files, so
# they correctly fall back to their bare OpenAPI summary only.
EXPECTED_GUIDED_TOOLS = {
    "getStock4KeyEvaluation", "getStock4KeyScreen", "getTotalTradeReal", "getTotalTrade",
    "getStockSignal", "getSMDTTickerCross", "getLeadingCoreBranches", "getCoreBranchLeader",
    "getStockWave", "getSMDTBranch", "getSMDTTicker", "getSMDTIncreasing3", "getSMDTTickerDrop",
    "getSMDTBranchDrop", "getSMDTBranchCross", "getBranchPath", "getBranchSMDTTickers",
    "getSMDTLastN", "getTopBranchSMDTIncreasing", "getBranchStrongSMDTWithPrice",
    "getTickersPriceDownSMDTIncreasing", "getDongTienTheoNganh", "getDongTienTheoMa",
    "getAnalyzeWave", "getWaveBottomConfirmPairs", "getPerformance",
}
check(f"all {len(EXPECTED_GUIDED_TOOLS)} tools with real rule text carry embedded guidance",
      EXPECTED_GUIDED_TOOLS == set(TOOL_GUIDES.keys()))

# The cash-flow-vs-SMDT confusion from today's live test must never regress:
# both cash flow tools must explicitly say "SMDT" so the warning shows up
# right next to the tool an AI would otherwise reach for by mistake.
for op_id in ("getDongTienTheoNganh", "getDongTienTheoMa"):
    check(f"{op_id} guide explicitly warns against confusing with SMDT",
          "SMDT" in " ".join(TOOL_GUIDES[op_id]))

# ---------------------------------------------------------------------
# 3. augment_tool_descriptions actually injects guide text, idempotently
# ---------------------------------------------------------------------
sample_tools = [t for t in registry.tools if t["function"]["name"] == "getAnalyzeWave"]
before = sample_tools[0]["function"]["description"]
augment_tool_descriptions(sample_tools)
after = sample_tools[0]["function"]["description"]
check("getAnalyzeWave description grew with embedded guide", len(after) > len(before))
check("guide mentions the exact wave-classification trigger phrase", "sóng lớn hay sóng hồi" in after)

augment_tool_descriptions(sample_tools)  # second call must not duplicate
check("augment_tool_descriptions is idempotent", after == sample_tools[0]["function"]["description"])

# ---------------------------------------------------------------------
# 3c. SERVER_INSTRUCTIONS — the global business definitions that were
# completely missing before today's live test on Claude Desktop surfaced
# the model inventing its own definition of "mã mạnh"/"ngành mạnh" and
# calling SMDT tools for dòng tiền questions. This regression exists
# specifically so that failure mode cannot come back silently.
# ---------------------------------------------------------------------
for branch in MAIN_BRANCHES:
    check(f"SERVER_INSTRUCTIONS lists core branch '{branch}'", branch in SERVER_INSTRUCTIONS)

check("SERVER_INSTRUCTIONS distinguishes 'dẫn sóng' from 'đạt chuẩn ngành mạnh'",
      "dẫn sóng" in SERVER_INSTRUCTIONS and "đạt chuẩn ngành mạnh" in SERVER_INSTRUCTIONS)
check("SERVER_INSTRUCTIONS states the real 'mã mạnh' definition (getSMDTTickerCross, latest date)",
      "getSMDTTickerCross" in SERVER_INSTRUCTIONS and "MỚI NHẤT" in SERVER_INSTRUCTIONS)
check("SERVER_INSTRUCTIONS explicitly separates dòng tiền (cash flow) from SMDT",
      "DÒNG TIỀN" in SERVER_INSTRUCTIONS and "KHÔNG" in SERVER_INSTRUCTIONS)

# Hard deterministic pre-filter used only on the webapp path (see
# core/knowledge.py docstring for why MCP/Claude Desktop can't have this
# same enforcement - the server never sees raw question text there).
CASHFLOW_QUERY_CASES = [
    ("Dòng tiền SSI hiện nay thế nào?", True),
    ("Dòng tiền ngành Ngân hàng bắt đầu đổ vào tháng 07/2026 khi nào?", True),
    ("Dòng tiền SSI ngày 24/08/2026 là bao nhiêu?", True),
    ("Sức mạnh dòng tiền ngành Ngân hàng là bao nhiêu?", False),
    ("SMDT SSI là bao nhiêu?", False),
    ("Ngành nào dẫn sóng hôm nay?", False),
    ("Dòng tiền ngành dẫn sóng ra sao?", False),
    ("Mã SSI bắt đầu mạnh từ khi nào?", False),
]
for text, expected in CASHFLOW_QUERY_CASES:
    check(f"is_pure_cashflow_query({text!r}) == {expected}", is_pure_cashflow_query(text) == expected)

# Display-name aliases must resolve back to the real operationId the
# executor/formatter dispatch actually key off of.
check("getDongTienTheoNganh resolves back to getCashFlowBranch",
      registry.resolve_operation_id("getDongTienTheoNganh") == "getCashFlowBranch")
check("getDongTienTheoMa resolves back to getCashFlowTicker",
      registry.resolve_operation_id("getDongTienTheoMa") == "getCashFlowTicker")
check("resolve_operation_id passes through non-alias names unchanged",
      registry.resolve_operation_id("getAnalyzeWave") == "getAnalyzeWave")
check("real operationId no longer leaks into the AI-facing tool list",
      "getCashFlowBranch" not in tool_names and "getCashFlowTicker" not in tool_names)

# ---------------------------------------------------------------------
# 3b. All 4 "4-key" groups (dung song dung nganh / dung song sai nganh /
# sai song dung nganh / sai song sai nganh) — momentum-derived fallback,
# explicit group_4key payload, and screen-query group-code mapping.
# This was previously only import-checked, never actually exercised.
# ---------------------------------------------------------------------
FOUR_KEY_MOMENTUM_CASES = [
    # (ticker_momentum, branch_momentum) -> expected group label substring
    (5, 5, "Đúng sóng - Đúng ngành"),
    (5, -5, "Đúng sóng - Sai ngành"),
    (-5, 5, "Đúng ngành - Sai sóng"),
    (-5, -5, "Sai sóng - Sai ngành"),
]
for ticker_mo, branch_mo, expected_label in FOUR_KEY_MOMENTUM_CASES:
    group, _ = _derive_4key_group({"ticker_momentum": ticker_mo, "branch_momentum": branch_mo})
    check(
        f"_derive_4key_group({ticker_mo},{branch_mo}) -> '{expected_label}'",
        group == expected_label,
    )

FOUR_KEY_EXPLICIT_GROUPS = [
    "Đúng sóng - Đúng ngành",
    "Đúng sóng - Sai ngành",
    "Đúng ngành - Sai sóng",
    "Sai sóng - Sai ngành",
]
for group_label in FOUR_KEY_EXPLICIT_GROUPS:
    payload = {
        "mode": "single",
        "ticker": "ABC",
        "date": "2026-08-24",
        "branch": "Ngân hàng",
        "group_4key": group_label,
        "recommendation": "test",
        "composite": {"score": 80, "rating": "mua"},
    }
    answer = format_stock_4key_answer(payload, user_text="phân tích ABC")
    check(f"format_stock_4key_answer surfaces group '{group_label}' verbatim", group_label in answer)

FOUR_KEY_SCREEN_PHRASES = [
    ("cung cấp danh sách các mã đúng sóng đúng ngành", "dd"),
    ("liệt kê các mã đúng sóng sai ngành", "ds"),
    ("cho tôi danh sách các mã sai sóng đúng ngành", "sd"),
    ("cung cấp danh mục các mã sai sóng sai ngành", "ss"),
]
for phrase, expected_code in FOUR_KEY_SCREEN_PHRASES:
    groups = requested_4key_groups(phrase)
    check(f"requested_4key_groups detects a group for: '{phrase}'", bool(groups))
    args = stock_4key_screen_args(phrase)
    check(
        f"stock_4key_screen_args('{phrase}') -> group={expected_code}",
        args is not None and args.get("group") == expected_code,
    )

# format_stock_4key_list_answer (screen mode) doesn't crash and lists tickers
screen_payload = {
    "mode": "screen",
    "date": "2026-08-24",
    "tickers": ["AAA", "BBB", "CCC"],
}
list_answer = format_stock_4key_list_answer(screen_payload, user_text="danh sách mã đúng sóng đúng ngành")
check("format_stock_4key_list_answer includes all screened tickers", all(t in list_answer for t in ["AAA", "BBB", "CCC"]))

# ---------------------------------------------------------------------
# 4. Multi-turn date carry-forward — the exact bug fixed today in chatbotgpt
# ---------------------------------------------------------------------
state = ConversationState()
state.update("28/7 là sóng lớn hay sóng hồi")
check("turn 1 captures its own date", state.last_date == "2026-07-28" or bool(state.last_date))
turn1_date = state.last_date

state.update("có xác nhận chân sóng không")  # no date of its own
check("turn 2 (dateless follow-up) keeps the previous date, doesn't reset it", state.last_date == turn1_date)
hint = state.as_context_hint()
check("context hint surfaces the carried-forward date for the model to see", hint and turn1_date in hint)

# ---------------------------------------------------------------------
# 5. (removed) waitbuy/cashflow/recent-trade row-selection formatters were
# deleted from core/formatters.py by explicit decision: the connecting AI
# reads the raw tool JSON and composes these itself now. Only the 4-key
# business-taxonomy formatter (tested in section 3b above) is still
# hand-coded, because that's proprietary vocabulary, not a lookup.
# ---------------------------------------------------------------------
# 6. case_ideas tool works even on a fresh, empty table (no auto-migration)
# ---------------------------------------------------------------------
results = search_case_ideas("bất kỳ câu hỏi nào")
check("search_case_ideas returns a list without crashing on empty table", isinstance(results, list))

# ---------------------------------------------------------------------
# 7. BOOKS knowledge (definition/concept questions) — was completely
# missing until this pass; now backed by the real 8 files under
# data/knowledge/books, parsed and searchable.
# ---------------------------------------------------------------------
from core.knowledge import search_books, load_book_docs

book_docs = load_book_docs()
check("all 8 original book files are loaded", len(book_docs) == 8)
check("book parsing actually extracted text (not empty PDFs)", sum(len(v) for v in book_docs.values()) > 0)

book_results = search_books("vì sao nên mua vào ngay đáy")
check("search_books returns relevant results for a definition-style question", len(book_results) > 0)
if book_results:
    check("top book result comes from the relevant doc", "đáy" in book_results[0]["doc_name"].lower())

# Live test on Claude Desktop showed "chờ mua là gì" surfacing the WRONG
# chunk at rank 1 (an unrelated usage example) instead of the doc's actual
# glossary definition ("Chờ Mua : Tín hiệu dùng để nhận diện..."), because
# plain token-overlap scoring can't tell "mentions the term" from "defines
# the term" on a long, text-heavy doc. The definition-pattern boost added
# to _score_book_chunk must keep the real definition ranked first.
waitbuy_results = search_books("chờ mua là gì", top_k=3)
check("search_books ranks the actual glossary definition first for 'chờ mua là gì'",
      bool(waitbuy_results) and "tín hiệu dùng để" in waitbuy_results[0]["excerpt"].lower())

# ---------------------------------------------------------------------
# 8. apply_formatter — the exact-wording answer must reach the tool result,
# not just exist as an importable, never-called function (yesterday's gap)
# ---------------------------------------------------------------------
from core.response_format import apply_formatter

four_key_payload = {
    "mode": "single", "ticker": "ABC", "date": "2026-08-24",
    "group_4key": "Đúng sóng - Đúng ngành", "composite": {"score": 80, "rating": "mua"},
}
enriched = apply_formatter("getStock4KeyEvaluation", four_key_payload, user_text="phân tích ABC")
check("apply_formatter attaches _formatted_answer for getStock4KeyEvaluation", "_formatted_answer" in enriched)
check(
    "formatted answer contains the exact group label, not a paraphrase",
    "Đúng sóng - Đúng ngành" in enriched.get("_formatted_answer", ""),
)
untouched = apply_formatter("getSMDTTicker", {"foo": "bar"})
check("apply_formatter passes through operations with no formatter unchanged", untouched == {"foo": "bar"})

# ---------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("All regression checks passed.")
