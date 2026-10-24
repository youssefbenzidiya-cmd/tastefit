"""TasteFit as an MCP server: newline-delimited JSON-RPC 2.0 over stdio, standard library only.

    python3 mcp_server.py        # an MCP client launches this; QLOO_API_KEY comes from its environment

Tools: tenant_match (the broker pipeline in tenant.py), city_scan and city_scan_unit (the prebuilt City Scan packs).
Nothing is written to disk; Qloo results live for one tool call, as in the web app. Logs go to stderr, because stdout
carries the protocol.
"""
import json
import os
import sys

import scan
import tenant

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "tastefit", "version": "1.0.0"}

TOOLS = [
    {"name": "tenant_match",
     "description": "Which food concepts the local audience wants for a vacant retail unit, and whether each can pay "
                    "the rent. Runs TasteFit's tenant pipeline (at most 25 Qloo calls, about 2-4 minutes) and returns "
                    "the scored shortlist, the LLM-only claim ledger, the memo and the call budget. Give lat/lon, or "
                    "city plus location (a neighbourhood name).",
     "inputSchema": {"type": "object", "properties": {
         "city": {"type": "string", "description": "City, e.g. Paris or Chicago"},
         "location": {"type": "string", "description": "Neighbourhood name, e.g. Le Marais"},
         "lat": {"type": "number"}, "lon": {"type": "number"},
         "asking_rent": {"type": "number", "description": "Monthly asking rent in the local currency"}},
         "additionalProperties": False}},
    {"name": "city_scan",
     "description": "Top blocks of a city for a food concept from TasteFit's prebuilt City Scan pack (no Qloo call).",
     "inputSchema": {"type": "object", "properties": {
         "city": {"type": "string"}, "concept": {"type": "string", "description": "A scanned concept, e.g. coffee shops"},
         "top": {"type": "integer", "minimum": 1}}, "required": ["city", "concept"], "additionalProperties": False}},
    {"name": "city_scan_unit",
     "description": "Rent ceiling per tenant type for one point, from the City Scan pack (no Qloo call).",
     "inputSchema": {"type": "object", "properties": {
         "city": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}},
         "required": ["city", "lat", "lon"], "additionalProperties": False}},
]


def tenant_match(args):
    out = {"steps": []}
    for kind, data in tenant.run(args.get("city"), args.get("location"), args.get("lat"), args.get("lon"),
                                 args.get("asking_rent")):
        if kind == "error":
            return {"error": data}, True
        if kind == "step":
            out["steps"].append({k: data.get(k) for k in ("workflow", "purpose", "error", "skipped") if data.get(k)})
        elif kind in ("unit", "shortlist", "baseline", "budget", "portfolio"):
            out[kind] = data
        elif kind == "memo":
            out["memo"] = data["markdown"]
            out["memo_author"] = data["author"]
    return out, False


def _http_result(code_body):
    code, body = code_body
    return body, code >= 400


def call_tool(name, args):
    if name == "tenant_match":
        if args.get("lat") is None and not (args.get("city") and args.get("location")):
            return {"error": "give lat and lon, or city and location"}, True
        return tenant_match(args)
    if name == "city_scan":
        return _http_result(scan.scan(args.get("city"), args.get("concept"), args.get("top")))
    if name == "city_scan_unit":
        return _http_result(scan.unit(args.get("city"), args.get("lat"), args.get("lon")))
    raise KeyError(name)


def _redact(text):
    for var in ("QLOO_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY"):
        secret = os.environ.get(var)
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    return text


def handle(msg):
    """One JSON-RPC message in, the response dict out (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:
        return None
    try:
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                      "instructions": "TasteFit: taste-based tenant and site checks. Qloo describes aggregate taste, "
                                      "not foot traffic or sales; read the memo's Limits section."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
            if not isinstance(args, dict):
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "arguments must be an object"}}
            body, is_error = call_tool(name, args)
            result = {"content": [{"type": "text", "text": _redact(json.dumps(body, ensure_ascii=False))}],
                      "isError": is_error}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except Exception as e:  # noqa: BLE001  a tool failure must not kill the server
        print(f"tool error: {type(e).__name__}", file=sys.stderr)
        result = {"content": [{"type": "text", "text": f"Internal error ({type(e).__name__})"}], "isError": True}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
