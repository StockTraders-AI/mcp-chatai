"""
Replaces chatbotgpt/backend/core/context_resolver.py.

The old file hand-wrote a separate topic classifier per feature
(market_wave, wave_classification, stock_4key, cashflow, price,
stock_analysis...) — every time a new feature was added, someone had to
remember to also teach context_resolver.py about it, or short follow-up
questions for that feature would silently lose the date/ticker from the
previous turn (exactly the "có xác nhận chân sóng không" bug from today's
session, which only got fixed because wave_classification happened to get
its own bespoke branch).

This version is topic-agnostic: it just remembers the last explicit date
and the last explicit ticker(s) mentioned in the conversation, and offers
them to the model as plain context — the model itself (which already reads
tool descriptions and decides what to call) decides whether the current
short/vague question should reuse them. No per-topic classifier to keep in
sync with the tool list.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

TICKER_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,6}\b")


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    normalized = normalized.replace("đ", "d").replace("Đ", "D").lower()
    return re.sub(r"\s+", " ", normalized).strip()


def extract_explicit_date(text: str, now: Optional[datetime] = None) -> Optional[str]:
    """Returns YYYY-MM-DD if the message names a concrete date, else None.

    Deliberately does NOT resolve relative words like "hôm nay" to a date
    here — that ambiguity is exactly what the model should see and reason
    about (today's date is already in its system prompt), not something a
    regex should silently decide for a follow-up question.
    """
    now = now or datetime.now()
    normalized = _normalize(text)

    iso = re.search(r"\b(20\d{2})-(1[0-2]|0[1-9])-(3[01]|[12]\d|0[1-9])\b", normalized)
    if iso:
        return iso.group(0)

    full = re.search(r"\b(3[01]|[12]\d|0?[1-9])[/-](1[0-2]|0?[1-9])[/-]((?:20)?\d{2})\b", normalized)
    if full:
        day, month, year = int(full.group(1)), int(full.group(2)), int(full.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    day_month = re.search(r"\b(3[01]|[12]\d|0?[1-9])[/-](1[0-2]|0?[1-9])\b", normalized)
    if day_month:
        day, month = int(day_month.group(1)), int(day_month.group(2))
        try:
            return datetime(now.year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def extract_tickers(text: str, allowed: Optional[set] = None) -> List[str]:
    found = []
    for match in TICKER_RE.finditer(text or ""):
        ticker = match.group(0).upper()
        if allowed is not None and ticker not in allowed:
            continue
        if ticker not in found:
            found.append(ticker)
    return found


@dataclass
class ConversationState:
    last_date: Optional[str] = None
    last_tickers: List[str] = field(default_factory=list)

    def update(self, user_text: str, allowed_tickers: Optional[set] = None, now: Optional[datetime] = None):
        date_value = extract_explicit_date(user_text, now)
        if date_value:
            self.last_date = date_value

        tickers = extract_tickers(user_text, allowed_tickers)
        if tickers:
            self.last_tickers = tickers

    def as_context_hint(self) -> Optional[str]:
        """A short, honest system-prompt hint — NOT a rewrite of the user's
        question. The model decides whether it's relevant; this file never
        silently substitutes text like the old render_resolved_query() did.
        """
        if not self.last_date and not self.last_tickers:
            return None
        parts = []
        if self.last_date:
            parts.append(f"ngày gần nhất được nhắc tới trong hội thoại: {self.last_date}")
        if self.last_tickers:
            parts.append(f"mã gần nhất được nhắc tới trong hội thoại: {', '.join(self.last_tickers)}")
        return (
            "NGỮ CẢNH HỘI THOẠI (chỉ dùng nếu câu hỏi hiện tại không tự nêu rõ ngày/mã riêng, "
            "đừng áp đặt nếu câu hỏi đã rõ ràng khác chủ đề): " + "; ".join(parts)
        )
