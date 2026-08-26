import json
from typing import Any, Dict, List, Tuple
from core.settings import OPENAPI_PATH

# Display-name aliases: the name the AI sees in the tool list vs. the real
# operationId used internally to look up the API path (core.executor calls
# ToolRegistry.resolve_operation_id() first). Live test on Claude Desktop
# showed cash-flow questions ("Dòng tiền SSI hiện nay thế nào?") getting
# routed to an SMDT tool instead - "sức mạnh dòng tiền" (SMDT's Vietnamese
# name) and "dòng tiền" are near-identical Vietnamese phrases, but the real
# cash-flow tools were named in English ("getCashFlowTicker"), forcing the
# model to translate before it could match. Renaming them to literally
# contain "DongTien" gives a direct string match against what the user
# actually typed, instead of relying on semantic translation every time.
DISPLAY_NAME_ALIASES: Dict[str, str] = {
    "getDongTienTheoMa": "getCashFlowTicker",
    "getDongTienTheoNganh": "getCashFlowBranch",
}
_REAL_OPERATION_TO_DISPLAY_NAME: Dict[str, str] = {v: k for k, v in DISPLAY_NAME_ALIASES.items()}


class ToolRegistry:
    """
    - Load OpenAPI schema
    - Build tools[] for OpenAI
    - Provide lookup: operationId -> {server_url, path, method}
    """

    def __init__(self, openapi_path: str = str(OPENAPI_PATH)):
        self.openapi_path = openapi_path
        self.schema: Dict[str, Any] = {}
        self.server_url: str = ""
        self.operations: Dict[str, Dict[str, Any]] = {}
        self.tools: List[Dict[str, Any]] = []

    def load(self):
        with open(self.openapi_path, "r", encoding="utf-8") as f:
            self.schema = json.load(f)

        servers = self.schema.get("servers") or []
        if not servers or not servers[0].get("url"):
            raise ValueError("OpenAPI schema missing servers[0].url")
        self.server_url = servers[0]["url"].rstrip("/")

        self.operations = self._parse_operations()
        self.tools = self._build_tools()
        self._register_custom_tools()
        self._apply_display_name_aliases()

    def _apply_display_name_aliases(self):
        for tool in self.tools:
            real_name = tool["function"]["name"]
            display_name = _REAL_OPERATION_TO_DISPLAY_NAME.get(real_name)
            if display_name:
                tool["function"]["name"] = display_name

    def resolve_operation_id(self, name: str) -> str:
        """Maps a display name (what the AI called) back to the real
        operationId (what self.operations is keyed by). Returns `name`
        unchanged if it's not an alias."""
        return DISPLAY_NAME_ALIASES.get(name, name)

    def _register_custom_tools(self):
        # Real endpoint on the same official server (stocktradersai.vn), not
        # present in the stock_api.json OpenAPI snapshot on disk so it's
        # registered here like the other custom tools. Confirmed live:
        # POST /service/data/getWaveBottomConfirmPairs, optional
        # dateFrom/dateTo (YYYY-MM-DD), returns {dateFrom, dateTo, count,
        # pairs: [{prepare_bottom_date, confirm_wave_date, reliability}]}.
        # This replaces the old getChanSong, which called a completely
        # different third-party domain (chan-song-api.onrender.com) with a
        # hardcoded account ("uyen.png") - that was a stopgap, not the real
        # API, and is now removed.
        self.operations["getWaveBottomConfirmPairs"] = {
            "path": "/service/data/getWaveBottomConfirmPairs",
            "method": "POST",
            "summary": "Lay lich su cac cap phien chuan bi tao day va xac nhan tao day (chan song).",
            "parameters": [],
        }
        self.tools.append({
            "type": "function",
            "function": {
                "name": "getWaveBottomConfirmPairs",
                "description": (
                    "Lay lich su cac cap phien 'chuan bi tao day' (prepare_bottom_date) va "
                    "'xac nhan tao day' (confirm_wave_date), kem do tin cay (reliability). "
                    "Tuy chon truyen dateFrom/dateTo (YYYY-MM-DD) de loc theo khoang thoi gian; "
                    "khong truyen gi de lay toan bo lich su."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dateFrom": {"type": "string", "description": "YYYY-MM-DD"},
                        "dateTo": {"type": "string", "description": "YYYY-MM-DD"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        })

        # Real endpoint, same server, confirmed live: POST
        # /service/data/getStock4KeyHistory?ticker=X&group=dd/ds/sd/ss
        # &date=YYYY-MM-DD|YYYY-MM|YYYY (optional, same convention as
        # getTotalTrade/getSMDTBranch elsewhere in this API - one "date"
        # param whose precision controls day/month/year filtering).
        # Confirmed live: omitting date returns the full history (140
        # matches for TCB/dd); date=2026-07 correctly filters to just that
        # month (3 matches) - server-side filtering verified working after
        # the API owner fixed it (an earlier dateFrom/dateTo pair on this
        # same endpoint was silently ignored server-side).
        # Returns every historical date where the ticker matched that
        # exact 4-key group. Answers "TCB đạt chuẩn đúng sóng đúng ngành
        # khi nào?" (take the latest entry in matches) and the same
        # question filtered to a month/year (use date=YYYY-MM or YYYY).
        self.operations["getStock4KeyHistory"] = {
            "path": "/service/data/getStock4KeyHistory",
            "method": "POST",
            "summary": "Lay lich su cac moc ngay 1 ma dat dung 1 nhom 4-key cu the (dd/ds/sd/ss).",
            "parameters": [],
        }
        self.tools.append({
            "type": "function",
            "function": {
                "name": "getStock4KeyHistory",
                "description": (
                    "Lay lich su cac moc ngay ma [ticker] dat dung nhom 4-key [group] "
                    "(dd=dung song dung nganh, ds=dung song sai nganh, sd=sai song dung nganh/dung nganh sai "
                    "song, ss=sai song sai nganh). Tra ve mang 'matches'. "
                    "Cau hoi '[ticker] dat chuan [nhom 4-key] khi nao?' (khong noi thang/nam): goi khong "
                    "truyen date, lay phan tu co ngay MOI NHAT trong matches de tra loi. "
                    "Cau hoi '...trong thang X/nam Y': truyen date=YYYY-MM. "
                    "Cau hoi '...trong nam Y': truyen date=YYYY. "
                    "Ca 2 truong hop tren: liet ke TAT CA phan tu trong matches tra ve."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "group": {"type": "string", "enum": ["dd", "ds", "sd", "ss"]},
                        "date": {
                            "type": "string",
                            "description": "YYYY-MM-DD (1 ngay), YYYY-MM (1 thang), hoac YYYY (1 nam). Bo trong de lay toan bo lich su.",
                        },
                    },
                    "required": ["ticker", "group"],
                    "additionalProperties": False,
                },
            },
        })

        self.operations["getStock4KeyEvaluation"] = {
            "path": "",
            "method": "CUSTOM",
            "summary": "Danh gia 4-key/composite score co phieu.",
            "parameters": [],
        }
        self.tools.append({
            "type": "function",
            "function": {
                "name": "getStock4KeyEvaluation",
                "description": (
                    "Phan tich/score/rating/4-key co phieu. "
                    "Phan tich/score/rating 1 ma hoac cau hoi vi sao/tai sao/ly do [ticker] thuoc nhom 4-key: mode=single, include_composite=true; neu co 4-key thi neu Nhom 4 Key. "
                    "Hoi rieng 4-key 1 ma: mode=single, include_composite=false. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["single", "batch", "history"]},
                        "ticker": {"type": "string"},
                        "tickers": {"type": "array", "items": {"type": "string"}},
                        "date": {"type": "string", "description": "YYYY-MM-DD cho single/batch."},
                        "from_date": {"type": "string", "description": "YYYY-MM-DD cho history."},
                        "include_composite": {"type": "boolean"},
                        "lookback_sessions": {"type": "integer"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        })

        self.operations["getStock4KeyScreen"] = {
            "path": "/service/data/getStock4KeyScreen",
            "method": "POST",
            "summary": "Lay danh sach ma theo nhom 4-key tai mot ngay.",
            "parameters": [],
        }
        self.tools.append({
            "type": "function",
            "function": {
                "name": "getStock4KeyScreen",
                "description": (
                    "Lay danh sach/cac ma theo nhom 4-key. "
                    "Dung khi user hoi cung cap/liet ke/danh sach cac ma dung song dung nganh, dung song sai nganh, sai song dung nganh, hoac sai song sai nganh. "
                    "Chi truyen date=YYYY-MM-DD va group=dd/ds/sd/ss. Khong truyen ticker."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "group": {"type": "string", "enum": ["dd", "ds", "sd", "ss"]}
                    },
                    "required": ["date", "group"],
                    "additionalProperties": False,
                },
            },
        })

    def _parse_operations(self) -> Dict[str, Dict[str, Any]]:
        ops: Dict[str, Dict[str, Any]] = {}
        paths = self.schema.get("paths", {})

        for path, methods in paths.items():
            for method, detail in (methods or {}).items():
                if not isinstance(detail, dict):
                    continue
                op_id = detail.get("operationId")
                if not op_id:
                    continue

                ops[op_id] = {
                    "path": path,
                    "method": method.upper(),
                    "summary": detail.get("summary", "") or detail.get("description", "") or op_id,
                    "parameters": detail.get("parameters", []) or [],
                }
        return ops

    def _param_schema_to_jsonschema(self, p: Dict[str, Any]) -> Tuple[str, Dict[str, Any], bool]:
        """
        OpenAPI parameter -> JSON schema property
        returns: (name, jsonschema, required)
        """
        name = p.get("name", "")
        required = bool(p.get("required", False))
        schema = p.get("schema", {}) or {}

        # Pass through useful schema fields if present
        prop: Dict[str, Any] = {}
        if "type" in schema:
            prop["type"] = schema["type"]
        else:
            prop["type"] = "string"

        for k in ["enum", "pattern", "format", "minimum", "maximum", "default", "description"]:
            if k in schema:
                prop[k] = schema[k]

        # If OpenAPI parameter has description, keep it
        if "description" in p and "description" not in prop:
            prop["description"] = p["description"]

        return name, prop, required

    def _build_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []

        for op_id, meta in self.operations.items():
            properties: Dict[str, Any] = {}
            required_list: List[str] = []

            for p in meta.get("parameters", []):
                if not isinstance(p, dict):
                    continue
                pname, pschema, preq = self._param_schema_to_jsonschema(p)
                if not pname:
                    continue
                properties[pname] = pschema
                if preq:
                    required_list.append(pname)

            tool = {
                "type": "function",
                "function": {
                    "name": op_id,
                    "description": meta.get("summary", op_id),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_list,
                        "additionalProperties": False,
                    },
                },
            }
            tools.append(tool)

        return tools
