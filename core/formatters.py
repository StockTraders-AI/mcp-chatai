"""
Deterministic answer formatting for the 4-Key business taxonomy, ported
from chatbotgpt/backend/core/orchestrator.py.

This is the one place in the whole system that still hand-codes an answer
instead of letting the connecting AI compose it, and on purpose: the
group labels ("Đúng sóng - Đúng ngành", "Sai sóng - Sai ngành"...) and
recommendations are a proprietary StockTraders naming convention, not
something derivable from the raw numbers by general reasoning - an AI
reading `ticker_momentum=+5, branch_momentum=-5` has no way to know that
means "Đúng sóng - Sai ngành" unless told the exact mapping. That's
business vocabulary, not formatting.

Everything else this file used to contain (should_force_rules, case_idea
matching, RULES/BOOKS classification, waitbuy/cashflow/recent-trade
row-selection formatters, context/history helpers...) was deleted on
purpose: those are plain routing or "read a JSON row and describe it"
tasks that the connecting AI (Claude, GPT, whichever MCP client) already
handles correctly on its own once the tool schema is clear - re-coding
them here would just be rebuilding the same case-sprawl this rewrite was
meant to get rid of. See mcp_server/server.py and webapp/chat_service.py
for how the rest of the system deliberately leaves that to the model.
"""

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from core.ticker_policy import ALLOWED_TICKERS

NON_TICKER_SYMBOLS = frozenset({"RSI", "NAV", "SMDT", "GPT", "AI", "API", "MACD"})


def normalize_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    normalized = normalized.replace("đ", "d").replace("Đ", "D")
    normalized = normalized.lower()
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_intent_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_search_text(text)).strip()


def extract_ticker(text: str) -> Optional[str]:
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,6}\b", text or ""):
        ticker = token.upper()
        if ticker not in NON_TICKER_SYMBOLS and ticker in ALLOWED_TICKERS:
            return ticker
    return None


def extract_date_value(text: str) -> Optional[str]:
    normalized = normalize_search_text(text)

    iso = re.search(r"\b(20\d{2})-(1[0-2]|0[1-9])-(3[01]|[12]\d|0[1-9])\b", normalized)
    if iso:
        return iso.group(0)

    full = re.search(r"\b(3[01]|[12]\d|0?[1-9])[/-](1[0-2]|0?[1-9])[/-]((?:20)?\d{2})\b", normalized)
    if full:
        day = int(full.group(1))
        month = int(full.group(2))
        year = int(full.group(3))
        if year < 100:
            year += 2000
        return f"{day:02d}/{month:02d}/{year}"

    day_month = re.search(r"\b(3[01]|[12]\d|0?[1-9])[/-](1[0-2]|0?[1-9])\b", normalized)
    if day_month:
        day = int(day_month.group(1))
        month = int(day_month.group(2))
        return f"{day:02d}/{month:02d}/{datetime.now().year}"

    month_year = re.search(r"\b(?:thang\s*)?(1[0-2]|0?[1-9])[/-](20\d{2})\b", normalized)
    if month_year:
        return f"tháng {int(month_year.group(1))}/{month_year.group(2)}"

    iso_month = re.search(r"\b(20\d{2})-(1[0-2]|0[1-9])\b", normalized)
    if iso_month:
        return f"tháng {int(iso_month.group(2))}/{iso_month.group(1)}"

    year = re.search(r"\b(20\d{2})\b", normalized)
    if year:
        return year.group(1)

    if "hom qua" in normalized or "ngay hom qua" in normalized:
        return (datetime.now().date() - timedelta(days=1)).isoformat()

    if any(value in normalized for value in ("hom nay", "hien nay", "hien tai", "bay gio", "gan nhat")):
        return "hom nay"
    return None


def _derive_4key_group(payload: Dict[str, Any]) -> tuple[str, str]:
    group = str(payload.get("group_4key") or "").strip()
    recommendation = str(payload.get("recommendation") or "").strip()
    if group:
        return group, recommendation

    ticker_momentum = payload.get("ticker_momentum")
    branch_momentum = payload.get("branch_momentum")
    try:
        right_wave = float(ticker_momentum) > 0
        right_branch = float(branch_momentum) > 0
    except (TypeError, ValueError):
        return "Chưa xác định", recommendation or "Chưa đủ dữ liệu xác định nhóm 4 Key"

    if right_wave and right_branch:
        return "Đúng sóng - Đúng ngành", "MUA - tín hiệu thuận cả 2 chiều"
    if right_wave and not right_branch:
        return "Đúng sóng - Sai ngành", "CÂN NHẮC - mã mạnh riêng lẻ, ngược dòng ngành"
    if not right_wave and right_branch:
        return "Đúng ngành - Sai sóng", "THEO DÕI - ngành thuận nhưng mã chưa xác nhận"
    return "Sai sóng - Sai ngành", "TRÁNH - cả 2 chiều bất lợi"


def _display_lookup_key(value: Any) -> str:
    normalized = normalize_search_text(str(value or "").strip())
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _display_4key_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    mapping = {
        "dung song dung nganh": "Đúng sóng - Đúng ngành",
        "dung song sai nganh": "Đúng sóng - Sai ngành",
        "dung nganh sai song": "Đúng ngành - Sai sóng",
        "sai song dung nganh": "Đúng ngành - Sai sóng",
        "sai song sai nganh": "Sai sóng - Sai ngành",
        "mua manh": "MUA MẠNH",
        "mua": "MUA",
        "trung lap": "TRUNG LẬP",
        "ban": "BÁN",
        "ban manh": "BÁN MẠNH",
        "mua tin hieu thuan ca ma va nganh": "MUA - tín hiệu thuận cả 2 chiều",
        "mua tin hieu thuan ca 2 chieu": "MUA - tín hiệu thuận cả 2 chiều",
        "can nhac ma manh rieng nguoc dong nganh": "CÂN NHẮC - mã mạnh riêng lẻ, ngược dòng ngành",
        "can nhac ma manh rieng le nguoc dong nganh": "CÂN NHẮC - mã mạnh riêng lẻ, ngược dòng ngành",
        "theo doi nganh thuan nhung ma chua xac nhan": "THEO DÕI - ngành thuận nhưng mã chưa xác nhận",
        "tranh ca ma va nganh deu bat loi": "TRÁNH - cả 2 chiều bất lợi",
        "tranh ca 2 chieu bat loi": "TRÁNH - cả 2 chiều bất lợi",
        "chua du du lieu xac dinh nhom 4 key": "Chưa đủ dữ liệu xác định nhóm 4 Key",
    }
    return mapping.get(_display_lookup_key(text), text)


def _fmt_metric(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "chưa có dữ liệu"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{number:.1f}{suffix}"
    return f"{number:.2f}".rstrip("0").rstrip(".") + suffix


def _fmt_vn_date(value: Any) -> str:
    raw = str(value or "").strip()[:10]
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return f"{dt.day}/{dt.month}/{dt.year}"
    except ValueError:
        return raw or "hôm nay"


FOUR_KEY_ONLY_PHRASES = (
    "key nao",
    "key gi",
    "co key gi",
    "4 key nao",
    "4key nao",
    "nhom nao",
    "nhom 4 key nao",
    "thuoc key",
    "thuoc nhom",
    "dang thuoc key",
    "dang thuoc nhom",
    "co dung song dung nganh",
    "co dung song sai nganh",
    "co sai song dung nganh",
    "co sai song sai nganh",
    "danh gia",
    "trang thai",
    "phan tich",
)


FOUR_KEY_DETAIL_PHRASES = (
    "score",
    "composite",
    "diem",
    "rating",
    "xep hang",
    "vi sao",
    "tai sao",
    "ly do",
    "giai thich",
    "chi tiet",
    "smdt",
    "suc manh dong tien",
    "dong luc",
    "phan ky",
    "bonus",
    "khuyen nghi",
)


REQUESTED_4KEY_GROUPS = (
    ("dung song dung nganh", ("Đúng sóng - Đúng ngành",)),
    ("dung song sai nganh", ("Đúng sóng - Sai ngành",)),
    ("sai song dung nganh", ("Đúng ngành - Sai sóng",)),
    ("dung nganh sai song", ("Đúng ngành - Sai sóng",)),
    ("sai song sai nganh", ("Sai sóng - Sai ngành",)),
)


FOUR_KEY_GROUP_API_CODES = {
    "Đúng sóng - Đúng ngành": "dd",
    "Đúng sóng - Sai ngành": "ds",
    "Đúng ngành - Sai sóng": "sd",
    "Sai sóng - Sai ngành": "ss",
}


def is_stock_4key_only_query(user_text: str) -> bool:
    normalized = normalize_intent_text(user_text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in FOUR_KEY_DETAIL_PHRASES):
        return False
    return any(phrase in normalized for phrase in FOUR_KEY_ONLY_PHRASES)


def requested_4key_groups(user_text: str) -> tuple[str, ...]:
    normalized = normalize_intent_text(user_text)
    for phrase, groups in REQUESTED_4KEY_GROUPS:
        if phrase in normalized:
            return groups
    return ()


FOUR_KEY_SCREEN_QUERY = "cung cap danh sach cac ma dung song dung nganh"


FOUR_KEY_SCREEN_INTENT_PHRASES = (
    "cung cap",
    "danh sach",
    "danh muc",
    "cac ma",
    "nhung ma",
    "loc danh sach",
    "loc danh muc",
    "liet ke",
    "cho toi danh sach",
    "cho toi danh muc",
)


def is_stock_4key_screen_query(user_text: str) -> bool:
    normalized = normalize_intent_text(user_text)
    if not normalized:
        return False
    if normalized == FOUR_KEY_SCREEN_QUERY:
        return True
    if not requested_4key_groups(user_text):
        return False
    return any(phrase in normalized for phrase in FOUR_KEY_SCREEN_INTENT_PHRASES)


def stock_4key_screen_args(user_text: str) -> Optional[Dict[str, Any]]:
    groups = requested_4key_groups(user_text)
    if not groups or not is_stock_4key_screen_query(user_text):
        return None
    requested_date = _normalize_waitbuy_lookup_date(user_text) or datetime.now().strftime("%Y-%m-%d")
    return {
        "date": requested_date,
        "group": FOUR_KEY_GROUP_API_CODES.get(groups[0], groups[0]),
    }


def _change_word(now: Any, prev: Any) -> str:
    try:
        return "tăng" if float(now) >= float(prev) else "giảm"
    except (TypeError, ValueError):
        return "so với"


def stock_4key_single_args(user_text: str) -> Optional[Dict[str, Any]]:
    ticker = extract_ticker(user_text)
    if not ticker:
        return None

    normalized = normalize_intent_text(user_text)
    groups = requested_4key_groups(user_text)
    has_4key_phrase = any(phrase in normalized for phrase in FOUR_KEY_ONLY_PHRASES)
    has_detail_phrase = any(phrase in normalized for phrase in FOUR_KEY_DETAIL_PHRASES)
    if not (groups or has_4key_phrase or "4 key" in normalized or "4key" in normalized):
        return None

    requested_date = _normalize_waitbuy_lookup_date(user_text) or datetime.now().strftime("%Y-%m-%d")
    return {
        "mode": "single",
        "ticker": ticker,
        "date": requested_date,
        "include_composite": bool(has_detail_phrase or groups),
    }


def _format_4key_result_line(index: int, item: Dict[str, Any]) -> str:
    ticker = str(item.get("ticker") or "ma").strip().upper()
    if not item.get("ok"):
        error = str(item.get("error") or "khong du du lieu").strip()
        return f"{index}. {ticker}: khong danh gia duoc ({error})."
    group = _display_4key_label(item.get("group_4key"))
    recommendation = _display_4key_label(item.get("recommendation"))
    branch = str(item.get("branch") or "nganh").strip()
    parts = [f"{index}. {ticker}: {group}"]
    if branch:
        parts.append(f"nganh {branch}")
    if recommendation:
        parts.append(recommendation)
    return " - ".join(parts) + "."


def format_stock_4key_list_answer(payload: Dict[str, Any], user_text: str = "") -> str:
    mode = str(payload.get("mode") or "").strip().lower()
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    date_text = _fmt_vn_date(payload.get("date") or payload.get("requested_date"))

    if mode == "screen":
        raw_tickers = payload.get("tickers")
        if isinstance(raw_tickers, list):
            tickers = [str(item or "").strip().upper() for item in raw_tickers if str(item or "").strip()]
        else:
            tickers = [
                str(item.get("ticker") or "").strip().upper()
                for item in results
                if isinstance(item, dict) and item.get("ok", True) is not False and item.get("ticker")
            ]
        if not tickers:
            return "Khong co ma nao thoa dieu kien."
        return ", ".join(tickers)

    if mode == "history":
        ticker = str(payload.get("ticker") or "ma").strip().upper()
        from_date = _fmt_vn_date(payload.get("from_date"))
        lines = [f"Lich su 4 Key cua {ticker} tu {from_date} den {date_text}:", ""]
        if not results:
            return f"Chua co lich su 4 Key cua {ticker} tu {from_date}."
        for index, item in enumerate(results, start=1):
            item_date = _fmt_vn_date(item.get("date") or item.get("requested_date"))
            group = _display_4key_label(item.get("group_4key"))
            recommendation = _display_4key_label(item.get("recommendation"))
            suffix = f" - {recommendation}" if recommendation else ""
            lines.append(f"{index}. {item_date}: {group}{suffix}.")
        return "\n".join(lines).strip()

    lines = [f"Danh gia 4 Key cac ma ngay {date_text}:", ""]
    if not results:
        return f"Chua co ket qua 4 Key cho cac ma duoc hoi ngay {date_text}."
    for index, item in enumerate(results, start=1):
        lines.append(_format_4key_result_line(index, item))
    return "\n".join(lines).strip()


def format_stock_4key_answer(payload: Dict[str, Any], user_text: str = "") -> str:
    if str(payload.get("mode") or "").strip().lower() in {"screen", "batch", "history"}:
        return format_stock_4key_list_answer(payload, user_text=user_text)

    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        return str(
            payload.get("error")
            or "Không xác định được mã cổ phiếu cần phân tích."
        )
    branch = str(payload.get("branch") or "ngành").strip()
    date_text = _fmt_vn_date(payload.get("date") or payload.get("requested_date"))
    composite = payload.get("composite") or {}
    breakdown = composite.get("breakdown") or {}
    notes = composite.get("notes") or []

    score = _fmt_metric(composite.get("score"))
    rating = _display_4key_label(composite.get("rating"))
    raw_group, raw_recommendation = _derive_4key_group(payload)
    group = _display_4key_label(raw_group)
    recommendation = _display_4key_label(raw_recommendation)

    if is_stock_4key_only_query(user_text):
        requested_groups = requested_4key_groups(user_text)
        if requested_groups:
            if group in requested_groups:
                return f"Có, {ticker} đang thuộc Nhóm 4 Key \"{group}\"."
            return f"Không, {ticker} đang thuộc Nhóm 4 Key \"{group}\"."
        return f"{ticker} đang thuộc Nhóm 4 Key: \"{group}\"."

    lines = [f"Phân tích cổ phiếu {ticker} tính đến ngày {date_text} như sau:", ""]
    lines.append(f"1. Điểm Composite: Cổ phiếu {ticker} có điểm tổng hợp là {score}, xếp hạng \"{rating}\".")
    lines.append("")
    lines.append(f"2. Nhóm 4 Key: \"{group}\", khuyến nghị \"{recommendation}\".")
    lines.append("")
    lines.append("3. SMDT và Động lực:")
    lines.append(
        f" - SMDT của {ticker}: {_fmt_metric(payload.get('smdt_ticker'), '%')}, "
        f"{_change_word(payload.get('smdt_ticker'), payload.get('smdt_ticker_prev'))} từ {_fmt_metric(payload.get('smdt_ticker_prev'), '%')}."
    )
    lines.append(f" - Động lượng của mã: {_fmt_metric(payload.get('ticker_momentum'))}.")
    lines.append(
        f" - SMDT ngành {branch}: {_fmt_metric(payload.get('smdt_branch'), '%')}, "
        f"{_change_word(payload.get('smdt_branch'), payload.get('smdt_branch_prev'))} từ {_fmt_metric(payload.get('smdt_branch_prev'), '%')}; "
        f"động lượng ngành {_fmt_metric(payload.get('branch_momentum'))}."
    )
    lines.append("")

    if composite.get("co_phan_ky"):
        phan_ky = "Có phân kỳ SMDT tăng nhưng giá chưa tăng tương ứng."
    else:
        phan_ky = "Không có phân kỳ."
    lines.append(f"4. Phân kỳ: {phan_ky}")
    lines.append("")

    lines.append("5. Bonus/Ghi chú:")
    lines.append(f" - Bonus phân kỳ: {_fmt_metric(composite.get('bonus_phan_ky', 0))} điểm.")
    if breakdown:
        labels = {
            "smdt_vs_nganh": "SMDT so với ngành",
            "smdt_delta": "Động lượng SMDT",
            "gia_dong_luong": "Động lượng giá",
            "gia_return_1d_pct": "Lợi nhuận 1 ngày (%)",
            "dong_tien": "Dòng tiền",
        }
        parts = [f"{labels.get(key, key)} {_fmt_metric(value)}" for key, value in breakdown.items()]
        lines.append(" - Breakdown: " + "; ".join(parts) + ".")
    for note in notes:
        lines.append(f" - {note}.")

    return "\n".join(lines).strip()


def _normalize_stock_wave_lookup_date(text: str) -> Optional[str]:
    value = extract_date_value(text)
    if not value:
        return None

    normalized = normalize_search_text(str(value))
    if normalized == "hom nay":
        return datetime.now().strftime("%Y-%m-%d")

    raw = str(value).strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw):
        return raw

    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", raw)
    if match:
        day, month, year = map(int, match.groups())
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def _normalize_waitbuy_lookup_date(text: str) -> Optional[str]:
    return _normalize_stock_wave_lookup_date(text)
