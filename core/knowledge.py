"""
Replaces the old should_force_rules() / _pick_explicit_rule_doc() / RULES-BOOKS
LLM-classifier cascade from chatbotgpt/backend/core/orchestrator.py + rag.py.

Instead of pre-filtering the user's question with hand-written keyword layers
before the model ever sees it, the guidance from each of the 11 rule/guide
.txt files is embedded directly into the `description` of the tool it tells
you to call. Whichever LLM is driving the conversation (the web app's GPT
call, or a foreign AI connected through the MCP server) reads the tool list
itself and decides what to call — the same way it already reads every other
tool's description. This is the single structural fix for the "3 different
layers, 3 different bugs, for the same feature" failure mode from the old
system.

case_ideas (dynamic admin-authored FAQ entries) becomes a first-class,
explicitly-callable tool (`searchCaseIdeas`) instead of a silent pre-filter
that could hijack routing before RULES ever got a chance to run.
"""

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

from core.constants import MAIN_BRANCHES, MAIN_BRANCH_ALIASES
from core.settings import RULES_DIR, BOOKS_DIR, SQLITE_PATH


def _normalize_plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", normalized.lower()).strip()


CASH_FLOW_TOOLS = ("getDongTienTheoNganh", "getDongTienTheoMa")


def is_pure_cashflow_query(text: str) -> bool:
    """True only when the question is unambiguously about cash flow.

    This is a deliberate exception to "let the AI decide from tool
    descriptions" — a live test on Claude Desktop showed the model still
    picking an SMDT tool (getSMDTLastN) for "Dòng tiền SSI hiện nay thế
    nào?" even with warnings on every SMDT tool's own description. Per
    explicit user instruction: this is a hard, deterministic pre-filter
    used ONLY on the webapp path (chat_service.py), where the raw question
    text is actually visible before the model runs — the MCP server never
    sees the raw text (only the tool call the client's own model already
    decided on), so this same enforcement is NOT possible there; tool
    descriptions are the only lever on that side.

    Trigger: contains "dong tien" AND NOT "suc manh dong tien" (SMDT, a
    different metric that happens to contain the same substring) AND NOT
    "dan song" (a "dòng tiền ngành dẫn sóng"-style compound question the
    user explicitly wants excluded from this hard rule).
    """
    normalized = _normalize_plain(text)
    if "dong tien" not in normalized:
        return False
    if "suc manh dong tien" in normalized:
        return False
    if "dan song" in normalized:
        return False
    return True

# =====================================================================
# GLOBAL SERVER INSTRUCTIONS — cross-cutting business definitions that
# don't belong to any single tool. First version of this file only put
# per-tool guidance in TOOL_GUIDES below and left these definitions
# unwritten anywhere, which is exactly why the connecting AI invented its
# own definition of "mã mạnh"/"ngành mạnh" instead of using the real one:
# nothing told it there WAS a real one. This is passed as the MCP
# `Server(instructions=...)` field (mcp_server/server.py) so it reaches
# the connecting AI once, for the whole server — and mirrored into the
# webapp's system prompt so both paths see the same ground truth.
# =====================================================================

_CORE_BRANCHES_TEXT = "\n".join(f"- {b}" for b in MAIN_BRANCHES)
_CORE_BRANCH_ALIASES_TEXT = "\n".join(f"- {alias} = {canonical}" for alias, canonical in MAIN_BRANCH_ALIASES.items())

SERVER_INSTRUCTIONS = f"""
StockTraders AI — định nghĩa nghiệp vụ bắt buộc phải dùng đúng, không được tự suy diễn.

6 NGÀNH CHỦ LỰC (danh sách cố định, không được thêm/bớt):
{_CORE_BRANCHES_TEXT}

Alias tên gọi khác của các ngành trên:
{_CORE_BRANCH_ALIASES_TEXT}

"MÃ MẠNH" (đạt chuẩn mã mạnh) — cấp độ TỪNG MÃ:
- Hỏi "mã [X] bắt đầu mạnh/đạt chuẩn mã mạnh từ khi nào": PHẢI gọi getSMDTTickerCross(keyValue=[X]),
  KHÔNG truyền date, lấy bản ghi có date MỚI NHẤT trong mảng smdts trả về. Không tự tính bằng cách
  khác, không tự đặt ngưỡng % khác.
- Hỏi "mã nào đạt chuẩn mã mạnh vào [ngày/tháng/năm]": gọi getSMDTTickerCross(date=mốc được hỏi).
- Hỏi "mã nào có SMDT tăng dần đều/liên tiếp 3 phiên" (khác với "mã mạnh" ở trên — đây là 1 tiêu chí
  lọc riêng): dùng getSMDTIncreasing3.
- "Ngành mạnh" theo nghĩa đếm số mã (nhiều mã trong ngành cùng đạt chuẩn mã mạnh) là một khái niệm
  KHÁC với "ĐẠT CHUẨN NGÀNH MẠNH"/"DẪN SÓNG" ở dưới — không được lẫn hai khái niệm này.

"ĐẠT CHUẨN NGÀNH MẠNH" và "DẪN SÓNG" — cấp độ TOÀN NGÀNH (composite SMDT của cả ngành, một con số
duy nhất đại diện ngành đó, KHÔNG phải đếm số mã mạnh trong ngành):
- SMDT ngành (composite) vượt 70% → ngành đó "đạt chuẩn ngành mạnh".
- Nếu ngành "đạt chuẩn ngành mạnh" đó đồng thời là 1 trong 6 NGÀNH CHỦ LỰC ở trên → gọi là ngành
  đang "DẪN SÓNG" (dẫn sóng = đạt chuẩn ngành mạnh + thuộc 6 ngành chủ lực). Ngành đạt chuẩn ngành
  mạnh nhưng KHÔNG thuộc 6 ngành chủ lực thì chỉ gọi là "đạt chuẩn ngành mạnh", KHÔNG được gọi là
  "dẫn sóng".
- SMDT ngành trước đó dưới 60%, sau vượt lên 60% nhưng vẫn dưới 70% → "có tiềm năng dẫn sóng/sắp
  tham gia dẫn sóng" (chỉ áp dụng cho 6 ngành chủ lực).
- Hỏi "ngành [X] bắt đầu dẫn sóng/đạt chuẩn ngành mạnh từ khi nào": gọi getSMDTBranchCross(keyName=[X]).
  Trong danh sách ngày trả về, CHỈ LẤY ĐÚNG 1 NGÀY GẦN NHẤT so với hôm nay — KHÔNG liệt kê hay nhắc
  các mốc cũ hơn. Khuôn mẫu trả lời bắt buộc: "Ngành [X] bắt đầu dẫn sóng (đạt chuẩn ngành mạnh) từ
  ngày [ngày mới nhất]." Không áp dụng quy trình này cho câu hỏi dạng "Sóng vào [date] ngành nào dẫn
  sóng?" (đó là câu hỏi khác — hỏi tại 1 thời điểm, không phải hỏi mốc bắt đầu).
- Hỏi "ngành chủ lực nào dẫn sóng vào [date]" hoặc "dòng nào dẫn sóng vào [tháng/năm]": gọi
  getSMDTBranchCross(date=mốc được hỏi), sau đó CHỈ GIỮ LẠI các ngành nằm trong 6 ngành chủ lực.
- Hỏi "dòng nào đạt chuẩn ngành mạnh vào [tháng/năm]" (không nói "dẫn sóng"): gọi
  getSMDTBranchCross(date=mốc được hỏi), sau đó LOẠI BỎ các ngành thuộc 6 ngành chủ lực, chỉ trả lời
  các ngành còn lại — ngược lại hoàn toàn với case phía trên.
- Hỏi "ngành chủ lực nào dẫn sóng hôm nay": gọi getSMDTBranch(date=hôm nay) cho từng ngành, lọc
  trong 6 ngành chủ lực có SMDT ≥ 70%.
- Hỏi "[ngành] mất vai trò dẫn sóng khi nào": gọi getBranchPath lấy path ngành, rồi gọi
  getSMDTBranchDrop(path) lấy lastDate.

DÒNG TIỀN (cash flow) LÀ CHỈ SỐ HOÀN TOÀN KHÁC VỚI SMDT (sức mạnh dòng tiền):
- Bất kỳ câu hỏi nào nhắc "dòng tiền [ngành/mã]" → PHẢI gọi getDongTienTheoNganh (ngành) hoặc
  getDongTienTheoMa (mã). TUYỆT ĐỐI KHÔNG được gọi API SMDT (getSMDTBranch/getSMDTTicker/...) hay
  suy luận dòng tiền dựa trên % SMDT — đây là lỗi nghiệp vụ nghiêm trọng, hai chỉ số này độc lập nhau.
""".strip()


# =====================================================================
# RULE GUIDES -> TOOL DESCRIPTION AUGMENTATION
# =====================================================================

# Maps operationId -> extra guidance text pulled from data/knowledge/rules/*.txt.
# This is the "case catalog" from the plan's checklist, made explicit and
# co-located with the tool it applies to instead of living in a separate
# keyword-matching module.
TOOL_GUIDES: Dict[str, List[str]] = {
    "getStock4KeyEvaluation": [
        'Khi user hỏi "vì sao/tại sao/lý do/giải thích/chi tiết [ticker] thuộc nhóm 4-key" '
        'hoặc "phân tích cổ phiếu [ticker]": mode=single, include_composite=true. '
        "Hỏi riêng 4-key 1 mã (không cần composite): mode=single, include_composite=false. "
        "Nếu user không nói ngày thì dùng ngày hiện tại.",
    ],
    "getStock4KeyScreen": [
        "Dùng khi user hỏi cung cấp/liệt kê/danh sách các mã đúng sóng đúng ngành (group=dd), "
        "đúng sóng sai ngành (ds), sai sóng đúng ngành (sd), hoặc sai sóng sai ngành (ss) tại một ngày. "
        "Chỉ truyền date=YYYY-MM-DD và group, không truyền ticker.",
    ],
    "getTotalTradeReal": [
        'Khi user hỏi giá cổ phiếu "hôm nay/hiện tại/bây giờ/latest/current" (không có ngày cụ thể): '
        "chỉ truyền tham số ticker, không truyền date.",
    ],
    "getTotalTrade": [
        "Khi user hỏi giá cổ phiếu tại MỘT NGÀY CỤ THỂ (yyyy-mm-dd hoặc dd-mm-yyyy): "
        "truyền ticker và date. "
        'Câu hỏi "chỉ số/giá của mã [X] trong [N] phiên gần nhất": truyền ticker và lastDates=N, liệt kê '
        "giá của tất cả các phiên trả về. "
        'Câu hỏi "lập bảng thống kê [mã] trong tháng/năm/khoảng thời gian dài": nếu 1 tháng cụ thể truyền '
        "date=YYYY-MM; nếu 1 năm cụ thể truyền date=YYYY; nếu trải dài nhiều tháng/năm thì GỌI LẶP LẠI nhiều "
        "lần, mỗi lần 1 mốc date theo đúng trình tự thời gian, không bỏ sót năm/tháng nào, rồi ghép bảng.",
    ],
    "getStockSignal": [
        'Câu hỏi "lịch sử mua bán [mã] trong giai đoạn YYYY-YYYY": gọi với ticker=[mã], '
        "sau đó tự lọc kết quả trả về theo các năm được hỏi — API không nhận tham số năm trực tiếp. "
        "Cũng dùng API này khi hỏi về tín hiệu mua/bán, giá vốn trung bình (ave), tỷ trọng nắm giữ (hold), "
        "tỷ trọng giao dịch (percent), SMDT (smdt), trạng thái giao dịch (trade) của một mã — BẮT BUỘC gọi "
        "API này, không được tự suy luận các trường này. Lấy bản ghi lastdate để trả lời trạng thái hiện tại. "
        'Câu hỏi "tín hiệu mua/bán gần nhất của [mã]": lấy tín hiệu cuối cùng trong kết quả trả về.',
    ],
    "getSMDTTickerCross": [
        'Câu hỏi "mã [X] bắt đầu mạnh từ khi nào?" / "đạt chuẩn mã mạnh từ khi nào?": '
        "truyền keyValue=[mã], KHÔNG truyền date. Trong mảng smdts trả về, luôn lấy bản ghi có date "
        "mới nhất (last date) làm câu trả lời.",
    ],
    "getLeadingCoreBranches": [
        'Câu hỏi "ngành nào đang dẫn dắt": truyền date nếu user có hỏi ngày, không thì không truyền date. '
        "Lấy TẤT CẢ ngành trả về để trả lời kèm SMDT của từng ngành, không được bỏ sót.",
    ],
    "getCoreBranchLeader": [
        'Câu hỏi "lộ trình các dòng dẫn sóng?": truyền date được hỏi, lập bảng trả lời đầy đủ theo thời gian.',
    ],
    "getStockWave": [
        "Câu hỏi về số lượng mua/bán, chờ mua, chờ bán, độ tin cậy: truyền date được hỏi (hoặc hôm nay nếu "
        "không nói ngày). Các trường: buy=số mã tín hiệu MUA, sell=số mã tín hiệu BÁN, waitbuy=số mã CHỜ MUA, "
        "waitsell=số mã CHỜ BÁN, total=tổng số mã theo dõi, reliability=độ tin cậy (%). "
        "Trả lời CHÍNH XÁC số liệu trả về, không tự làm tròn hay suy diễn thêm.",
    ],
    "getSMDTBranch": [
        'Câu hỏi "sức mạnh dòng tiền (SMDT) các ngành chủ lực ngày [date]" hoặc "SMDT ngành chủ lực vào '
        '[date] thế nào": gọi LẶP LẠI cho từng ngành trong 6 ngành chủ lực, truyền path/tên ngành và date '
        "được hỏi, lập bảng trả lời. "
        'Câu hỏi "SMDT ngành [X] từ [date] đến nay": gọi lặp lại theo từng tháng-năm từ mốc được hỏi tới '
        "tháng-năm hiện tại, không truyền date đơn lẻ. "
        'Câu hỏi "SMDT [ngành] là bao nhiêu?": truyền ngành và date được hỏi, trả lời kèm ký hiệu %. '
        "Đây là tool RIÊNG CHO NGÀNH — nếu giá trị hỏi là tên ngành (ngân hàng, chứng khoán, thép, bất động "
        "sản...) PHẢI dùng tool này, KHÔNG dùng getSMDTTicker. "
        'Câu hỏi "phân tích ngành [X]" (không kèm ngày/mốc cụ thể): KHÔNG được hiểu là hỏi tại thời điểm '
        "hiện tại/hôm nay — phải hiểu là yêu cầu xem xét toàn bộ NĂM GẦN NHẤT có dữ liệu của ngành đó.",
    ],
    "getSMDTTicker": [
        'Câu hỏi "SMDT [mã cổ phiếu] là bao nhiêu?": CHỈ gọi tool này khi giá trị là MÃ CỔ PHIẾU THẬT '
        "(SSI, VCB, HPG...). Nếu giá trị là tên ngành thì phải dùng getSMDTBranch thay vì tool này. "
        'Câu hỏi "[ngày] cổ phiếu nào mạnh nhất dòng [ngành X]": gọi lặp lại keyValue cho từng mã trong '
        "ngành đó (lấy danh sách mã bằng getBranchPath trước) tại ngày được hỏi, mã có SMDT cao nhất là mã "
        "mạnh nhất ngành — CHỈ trả lời 1 mã cao nhất. "
        'Câu hỏi "SMDT các mã dòng [ngành] của [ngày]" (không hỏi "mạnh nhất"): cùng cách gọi (getBranchPath '
        "lấy danh sách mã, rồi getSMDTTicker từng mã) nhưng phải trả lời TOÀN BỘ danh sách mã và SMDT tương "
        "ứng, không chỉ 1 mã cao nhất. "
        'Câu hỏi "SMDT cổ phiếu [X] từ tháng [month] đến nay": gọi lặp lại theo từng tháng tới tháng hiện tại.',
    ],
    "getSMDTIncreasing3": [
        'Câu hỏi "mã có SMDT tăng dần đều/liên tiếp 3 phiên vào [date]": truyền date được hỏi (bỏ trống nếu '
        "câu hỏi không nói ngày, bỏ trống ticker nếu không nói mã cụ thể). Đây là tiêu chí lọc RIÊNG (SMDT "
        'tăng liên tiếp 3 phiên và phiên cao nhất >= 70%) — KHÁC với "mã mạnh" của getSMDTTickerCross, '
        "không được lẫn hai khái niệm.",
    ],
    "getSMDTTickerDrop": [
        'Câu hỏi "lộ trình các mã suy yếu trong khoảng [date]": truyền date được hỏi, hoặc dateFrom/dateTo '
        "nếu hỏi 1 khoảng, rồi lập bảng.",
    ],
    "getSMDTBranchDrop": [
        'Câu hỏi "[ngành] mất vai trò dẫn sóng khi nào": gọi getBranchPath để lấy path ngành trước, rồi gọi '
        "tool này với path đó, lấy lastDate để trả lời. "
        'Câu hỏi "lộ trình các dòng chủ lực suy yếu trong [date]": truyền date hoặc dateFrom/dateTo, lập bảng.',
    ],
    "getSMDTBranchCross": [
        'Câu hỏi "ngành [X] bắt đầu dẫn sóng/đạt chuẩn ngành mạnh từ khi nào": truyền keyName=[X], KHÔNG '
        "truyền date. CHỈ LẤY 1 NGÀY GẦN NHẤT so với hôm nay trong danh sách trả về — không nhắc mốc cũ hơn. "
        'Câu hỏi "thời điểm dòng đạt chuẩn ngành mạnh của mã [X]" (hỏi theo MÃ thay vì tên ngành): trước '
        "tiên xác định mã đó thuộc ngành nào, rồi áp dụng ĐÚNG quy trình như trên với keyName=tên ngành đó "
        "(vẫn chỉ lấy 1 ngày gần nhất, cùng khuôn mẫu trả lời). "
        'Câu hỏi "ngành chủ lực nào dẫn sóng vào [date]" hoặc "dòng nào dẫn sóng vào [tháng/năm]": truyền '
        "date=mốc được hỏi, rồi CHỈ GIỮ các ngành thuộc 6 ngành chủ lực. "
        'Câu hỏi "dòng nào đạt chuẩn ngành mạnh vào [tháng/năm]" (không nói "dẫn sóng"): truyền date=mốc '
        "được hỏi, rồi LOẠI BỎ các ngành thuộc 6 ngành chủ lực, chỉ trả lời ngành còn lại. "
        "Xem định nghĩa đầy đủ về dẫn sóng vs đạt chuẩn ngành mạnh trong phần hướng dẫn chung của server.",
    ],
    "getBranchPath": [
        "Dùng để lấy path của 1 ngành theo tên (name=tên ngành), KHÔNG truyền date. Thường là bước trung "
        "gian trước khi gọi getPerformance/getSMDTBranchDrop cần branch_path/path, không phải câu trả lời "
        "cuối cùng cho user.",
    ],
    "getBranchSMDTTickers": [
        'Câu hỏi "SMDT các mã dòng [ngành] từ tháng [month] đến nay": truyền ngành và date được hỏi (from_date).',
    ],
    "getSMDTLastN": [
        'Câu hỏi "SMDT của [mã/ngành] trong X phiên vừa qua": truyền n=X, và ticker (nếu là mã) hoặc '
        "path/keyName của ngành (nếu là ngành).",
    ],
    "getTopBranchSMDTIncreasing": [
        'Câu hỏi "ngành nào có số mã có SMDT tăng dần đều nhiều nhất vào [date]": truyền date được hỏi (bỏ '
        "trống nếu câu hỏi không có ngày), lập bảng.",
    ],
    "getBranchStrongSMDTWithPrice": [
        'Câu hỏi "danh sách các mã vượt 70% kèm giá của [ngành] vào [date]": truyền keyName/path ngành và '
        "date được hỏi, lập bảng.",
    ],
    "getTickersPriceDownSMDTIncreasing": [
        'Câu hỏi "lập bảng thống kê mã giá giảm hôm nay mà SMDT tăng dần đều": gọi KHÔNG truyền date, trả lời '
        'TOÀN BỘ mã trả về, không được rút gọn bằng dấu "...".',
    ],
    "getDongTienTheoNganh": [
        "DÒNG TIỀN NGÀNH — khác hoàn toàn với SMDT, không được lẫn lộn hay dùng SMDT để trả lời thay. "
        'Câu hỏi "dòng tiền [ngành] hiện nay/là bao nhiêu": truyền path=path ngành, date=hôm nay (hoặc ngày '
        "được hỏi); nếu ngày đó không có data thì gọi lại KHÔNG truyền date, lấy ngày gần nhất có data. "
        'Câu hỏi "dòng tiền bắt đầu đổ vào tháng X khi nào": truyền path, lọc kết quả theo tháng X, lấy DATA '
        "ĐẦU TIÊN của tháng đó.",
    ],
    "getDongTienTheoMa": [
        "DÒNG TIỀN MÃ — khác hoàn toàn với SMDT, không được lẫn lộn hay dùng SMDT để trả lời thay. "
        'Câu hỏi "dòng tiền [mã] hiện nay/là bao nhiêu": truyền ticker, date=hôm nay (hoặc ngày được hỏi); '
        "nếu ngày đó không có data thì gọi lại KHÔNG truyền date, lấy ngày gần nhất có data. "
        'Câu hỏi "dòng tiền bắt đầu đổ vào tháng X khi nào": truyền ticker, lọc kết quả theo tháng X, lấy '
        "DATA ĐẦU TIÊN của tháng đó. "
        'Câu hỏi "tín hiệu dòng tiền [ticker] từ [date] đến nay": gọi LẶP LẠI theo từng tháng-năm từ mốc '
        "được hỏi tới tháng-năm hiện tại rồi lập bảng.",
    ],
    "getAnalyzeWave": [
        'Khi user hỏi "ngày [DD/MM] có xác nhận chân sóng không?" hoặc "sóng DD-MM là sóng lớn hay sóng hồi": '
        "BẮT BUỘC gọi API này truyền date được hỏi. Trả lời CHÍNH XÁC 100% nội dung trả về, "
        "không tự diễn giải định nghĩa sóng lớn/sóng hồi bằng suy luận chung chung.",
    ],
    "getWaveBottomConfirmPairs": [
        'Câu hỏi "chân sóng gần nhất là ngày nào?": gọi không truyền tham số, lấy phần tử có '
        "confirm_wave_date MỚI NHẤT trong mảng pairs trả về, trả lời ngày đó. "
        'Câu hỏi "phiên chuẩn bị tạo đáy gần nhất là ngày nào?": lấy prepare_bottom_date MỚI NHẤT thay vì '
        "confirm_wave_date. "
        'Câu hỏi "trong [tháng/năm] có những phiên chuẩn bị/xác nhận tạo đáy nào?": truyền dateFrom/dateTo '
        "tương ứng với tháng/năm được hỏi, liệt kê tất cả các cặp trả về theo thời gian. "
        "Mỗi phần tử trong pairs gồm: prepare_bottom_date (ngày chuẩn bị tạo đáy), confirm_wave_date (ngày "
        "xác nhận tạo đáy), reliability (độ tin cậy %). Không có trường chờ mua/mua trong API này — nếu "
        "user hỏi thêm chờ mua/mua tại đúng ngày đó thì phải gọi thêm getStockWave(date=ngày đó). "
        "Không nhắc tên API trong câu trả lời.",
    ],
    "getPerformance": [
        "Câu hỏi hiệu suất cổ phiếu [X] khi ngành dẫn sóng / đạt chuẩn ngành mạnh: trước tiên xác định "
        "branch_path của mã, rồi gọi API này với branch_path đó. Trình bày đầy đủ toàn bộ data trả về dưới "
        "dạng bảng, không được bỏ sót mã.",
    ],
}

# Every SMDT tool gets this warning prepended (not just relying on
# SERVER_INSTRUCTIONS once, globally). Why: a live test on Claude Desktop
# showed "Dòng tiền ngành Ngân hàng bắt đầu đổ vào tháng 07/2026 khi nào?"
# still got routed to getSMDTBranchCross despite the global instructions
# saying not to — the phrase "bắt đầu ... khi nào" pattern-matches the
# getSMDTBranchCross guide text ("ngành X bắt đầu dẫn sóng từ khi nào")
# closely enough that the model picked it anyway. Repeating the warning
# at the exact point of confusion (on the wrong tool itself) is much
# harder to miss than one global mention buried in a long instructions
# block, and costs nothing since it's generated once at import time.
_CASH_FLOW_VS_SMDT_WARNING = (
    "[CANH BAO QUAN TRONG] Neu cau hoi co chu 'dong tien' (khong phai 'suc manh dong tien'/SMDT) thi "
    "DAY KHONG PHAI tool dung, du cau chu co ve giong (vi du ca hai deu co dang 'bat dau ... khi nao'). "
    "Cau hoi ve dong tien phai goi getDongTienTheoNganh (nganh) hoac getDongTienTheoMa (ma) thay vi tool nay."
)
for _smdt_tool in (
    "getSMDTBranch", "getSMDTTicker", "getSMDTTickerCross", "getSMDTBranchCross",
    "getSMDTBranchDrop", "getSMDTTickerDrop", "getSMDTIncreasing3", "getSMDTLastN",
    "getBranchSMDTTickers", "getTopBranchSMDTIncreasing", "getBranchStrongSMDTWithPrice",
    "getTickersPriceDownSMDTIncreasing",
):
    TOOL_GUIDES.setdefault(_smdt_tool, []).insert(0, _CASH_FLOW_VS_SMDT_WARNING)

_GUIDE_APPENDED_MARK = "\n\n[GUIDE]\n"


def augment_tool_descriptions(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append TOOL_GUIDES text to each tool's function.description in-place.

    Returns the same list for convenience. Safe to call multiple times
    (idempotent — won't double-append).
    """
    for tool in tools:
        fn = tool.get("function") or {}
        name = fn.get("name")
        guides = TOOL_GUIDES.get(name)
        if not guides:
            continue
        current = fn.get("description") or ""
        if _GUIDE_APPENDED_MARK in current:
            continue
        fn["description"] = current + _GUIDE_APPENDED_MARK + "\n".join(f"- {g}" for g in guides)
    return tools


# =====================================================================
# RAW RULE TEXT (fallback reference, e.g. for humans reviewing coverage)
# =====================================================================

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def load_rule_docs() -> Dict[str, str]:
    docs: Dict[str, str] = {}
    if RULES_DIR.exists():
        for p in sorted(RULES_DIR.glob("*.txt")):
            docs[p.name] = _read_text(p)
    return docs


# =====================================================================
# BOOKS (definition/concept knowledge) — ported from rag.py's book-RAG,
# now exposed as an explicit `searchKnowledgeBooks` tool instead of a
# hidden BOOKS-vs-RULES pre-classifier. The model calls this itself when
# a question is about a concept ("X la gi", "vi sao...") rather than
# needing live data — same "let the LLM decide, don't pre-filter"
# principle as the rest of this file.
# =====================================================================

_BOOK_SPECIAL_PHRASES = [
    "chan song", "song cuoi thang", "ma manh", "smdt", "day", "downtrend",
    "gia von", "cho ban", "lap dinh",
]


def _read_book_file(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            return "\n".join(p.text for p in d.paragraphs if p.text)
        if ext == ".txt":
            return _read_text(path)
    except Exception:
        return ""
    return ""


def _extract_book_chunks(text: str) -> List[str]:
    text = text.replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    chunks: List[str] = []
    buffer: List[str] = []

    def flush():
        nonlocal buffer
        if buffer:
            chunk = " ".join(buffer).strip()
            if len(chunk) >= 60:
                chunks.append(chunk)
            buffer = []

    for ln in lines:
        is_heading = len(ln) <= 80 and (
            ln.isupper() or re.match(r"^\d+[\.\)]", ln) or ln.startswith(("•", "-"))
        )
        if is_heading and buffer:
            flush()
        buffer.append(ln)
        if len(" ".join(buffer)) >= 500:
            flush()
    flush()

    if not chunks and text.strip():
        chunks = [text.strip()]
    return chunks


_BOOK_CACHE: Dict[str, List[str]] = {}


def load_book_docs() -> Dict[str, List[str]]:
    """doc_name -> list of text chunks. Cached after first call (files
    don't change at runtime); parsing PDFs on every request would be slow.
    """
    if _BOOK_CACHE:
        return _BOOK_CACHE
    if not BOOKS_DIR.exists():
        return _BOOK_CACHE
    for p in sorted(BOOKS_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".pdf", ".docx", ".txt"):
            continue
        text = _read_book_file(p)
        _BOOK_CACHE[p.name] = _extract_book_chunks(text)
    return _BOOK_CACHE


_DEFINITION_QUERY_PHRASES = ("la gi", "nghia la gi", "khai niem", "dinh nghia", "hieu the nao")


def _definition_query_term(query_normalized: str) -> str:
    """Strips a trailing 'la gi'/'nghia la gi'/... suffix to isolate the
    term being asked about, e.g. 'cho mua la gi' -> 'cho mua'."""
    term = query_normalized
    for phrase in _DEFINITION_QUERY_PHRASES:
        term = re.sub(rf"\b{re.escape(phrase)}\b", "", term)
    return term.strip()


def _score_book_chunk(query: str, doc_title: str, chunk: str) -> int:
    q = _normalize(query)
    t = _normalize(chunk)
    title = _normalize(doc_title)

    q_tokens = set(re.findall(r"[a-z0-9]+", q))
    t_tokens = set(re.findall(r"[a-z0-9]+", t))
    title_tokens = set(re.findall(r"[a-z0-9]+", title))

    score = len(q_tokens & t_tokens) * 2 + len(q_tokens & title_tokens) * 5

    words = q.split()
    for n in (5, 4, 3, 2):
        if len(words) < n:
            continue
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            if phrase in t:
                score += n * 4
            if phrase in title:
                score += n * 6

    for sp in _BOOK_SPECIAL_PHRASES:
        if sp in q and sp in t:
            score += 8

    # Definition-style questions ("X la gi") on a long, text-heavy doc: plain
    # token overlap can't tell a chunk that just MENTIONS the term (in an
    # unrelated example) from one that actually DEFINES it. These source
    # docs write definitions as "Term : explanation" bullets (e.g.
    # "Chờ Mua : Tín hiệu dùng để nhận diện..."), so reward that pattern
    # heavily when the query is asking for a definition of that exact term.
    if any(phrase in q for phrase in _DEFINITION_QUERY_PHRASES):
        term = _definition_query_term(q)
        if term and re.search(rf"{re.escape(term)}\s*:", t):
            score += 100

    return score


def search_books(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    docs = load_book_docs()
    scored = []
    for doc_name, chunks in docs.items():
        for chunk in chunks:
            score = _score_book_chunk(query, doc_name, chunk)
            if score > 0:
                scored.append((score, doc_name, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [{"doc_name": name, "excerpt": chunk} for _, name, chunk in scored[:top_k]]


# =====================================================================
# CASE IDEAS (dynamic FAQ) — explicit searchable tool, not a hidden
# pre-filter. Starts empty in this new project; old chat.db case_ideas
# are intentionally NOT auto-migrated (see plan's "Việc KHÔNG làm").
# =====================================================================

_CASE_IDEAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS case_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    indicators TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", normalized.lower()).strip()


def ensure_case_ideas_table():
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_PATH) as db:
        db.executescript(_CASE_IDEAS_SCHEMA)


def search_case_ideas(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Simple substring/token-overlap search over supported case_ideas.

    Called explicitly by the model as a tool (searchCaseIdeas), so it can
    never silently hijack a question the way the old pre-filter did — the
    model only gets these results if it chose to ask for them.
    """
    ensure_case_ideas_table()
    q_norm = _normalize(query)
    q_tokens = set(re.findall(r"[a-z0-9]+", q_norm))

    with sqlite3.connect(SQLITE_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, name, indicators, description FROM case_ideas WHERE status='supported'"
        ).fetchall()

    scored = []
    for row in rows:
        hay = _normalize(" ".join([row["name"], row["indicators"], row["description"]]))
        hay_tokens = set(re.findall(r"[a-z0-9]+", hay))
        overlap = len(q_tokens & hay_tokens)
        if q_norm and q_norm in hay:
            overlap += 5
        if overlap > 0:
            scored.append((overlap, dict(row)))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:limit]]
