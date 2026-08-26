# stocktraders-mcp

Song song, độc lập hoàn toàn với `chatbotgpt/chatbotgpt` cũ — không share code, không share DB, không đụng gì tới hệ thống đang chạy.

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # rồi điền BYOK_ENCRYPTION_KEY
```

## Chạy regression test (bắt buộc chạy trước khi đổi bất cứ gì trong core/)

```bash
python tests/regression_cases.py
```

## Chạy MCP server (cho Claude Desktop / ChatGPT / Cursor kết nối)

```bash
# stdio (Claude Desktop, MCP inspector)
python -m mcp_server.server

# HTTP (client từ xa)
python -m mcp_server.server --http --port 8100
```

## Chạy web app BYOK

```bash
uvicorn webapp.main:app --port 8000
```

Flow:
1. `POST /auth/key {"user_id": "...", "openai_api_key": "sk-..."}` — user tự lưu key riêng.
2. `POST /chat {"user_id": "...", "message": "..."}` — dùng đúng key vừa lưu, không đụng tới key công ty.
3. Không có key → 400, không fallback âm thầm sang key chung.

## Kiến trúc

Xem chi tiết trong `core/*.py` docstring đầu file — mỗi file ghi rõ nó thay thế thành phần nào của hệ thống cũ và tại sao.

- `core/` — tool registry + executor (port 1:1 từ code cũ) + formatters (số liệu chính xác) + knowledge (guide nhúng vào tool description) + context_state (carry-forward ngày/mã đa lượt, tổng quát hoá).
- `mcp_server/` — MCP server thật, expose `core/` cho AI client bên ngoài.
- `webapp/` — web chat BYOK, dùng chung `core/` với MCP server (đảm bảo không lệch case).

## Đã verify

- `tests/regression_cases.py`: 24/24 pass — tool registry đủ 29 tool, guide nhúng đúng, carry-forward ngữ cảnh đúng (case bug đã sửa hôm nay bên hệ thống cũ), formatter số liệu chính xác.
- MCP server: round-trip thật qua giao thức MCP (in-memory client/server) — list 30 tool (29 + searchCaseIdeas), `getAnalyzeWave` mang đúng guide "sóng lớn hay sóng hồi".
- Webapp: chạy uvicorn thật, test `/health`, `/auth/key`, `/auth/key/status`, `/chat` (từ chối đúng khi chưa có key).

## Chưa làm (cần OpenAI key thật + thời gian thực tế để verify)

- Test hội thoại thật qua Claude Desktop/MCP inspector với 11 case rule doc gốc.
- Test hội thoại thật qua webapp với OpenAI key thật, đối chiếu từng câu với bản cũ.
- Deploy lên VPS (production hiện tại là `chatbotgpt/chatbotgpt`, project này chưa deploy đâu cả).
- Migrate case_ideas cũ (cố ý không tự động — xem plan).
