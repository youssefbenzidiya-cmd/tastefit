# Call TasteFit from Claude Desktop or Cursor

TasteFit's MCP server is `mcp_server.py`. It speaks newline-delimited JSON-RPC on stdin and stdout.
The process is local. It reads `QLOO_API_KEY` from the environment you set in the client. It does not
write Qloo responses to disk.

A `tenant_match` call runs the same pipeline as the website: at most 25 Qloo calls. At 230 seconds,
later LLM calls are cut short. If fewer than 8 seconds remain, the run skips the LLM and uses the
template memo, which includes the Limits section. The process is not killed, and a Qloo call already
started is not cancelled. `city_scan` and `city_scan_unit` read the committed City Scan packs and
make no Qloo call.

The client's chat can keep the tool JSON after the call. TasteFit does not write that JSON to disk.
Do not paste it into the repository.

## 1. Check the server before the client

From the TasteFit directory:

```bash
export QLOO_API_KEY="your hackathon key"
export QLOO_BASE_URL="https://hackathon.api.qloo.com"
python3 mcp_server.py
```

Paste this one line and press Enter. Then Ctrl-D.

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
```

The reply lists `tenant_match`, `city_scan` and `city_scan_unit`. If it does not, the client will not
see the tools either.

## 2. Claude Desktop

Quit Claude Desktop. Edit the config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Claude Desktop does not start the process in the TasteFit directory, so `import tenant` fails if
`command` is `python3` and `args` points only at `mcp_server.py`. Save this wrapper and mark it
executable:

```bash
#!/bin/sh
cd /absolute/path/to/tastefit
exec python3 mcp_server.py
```

Point `command` at that wrapper. Do not also pass `mcp_server.py` in `args`.

```json
{
  "mcpServers": {
    "tastefit": {
      "command": "/absolute/path/to/tastefit/run-mcp.sh",
      "env": {
        "QLOO_API_KEY": "your hackathon key",
        "QLOO_BASE_URL": "https://hackathon.api.qloo.com"
      }
    }
  }
}
```

Save the config. Open Claude Desktop. In the tool list, TasteFit should show three tools.

Ask, in the chat:

```text
Use tastefit city_scan for coffee shops in Chicago, top 3.
```

That call does not use Qloo. The reply is blocks from the committed pack, not a vetted address.

For a tenant match, which does use Qloo and can take a few minutes:

```text
Use tastefit tenant_match at latitude 41.9088, longitude -87.6796, asking rent 4000.
```

The reply includes the shortlist, the claim ledger, the memo and the call budget. The memo's Limits
section stays. The fingerprint in the memo is a hash, not a promise that the forecast is right.

## 3. Cursor

Open Cursor Settings, then MCP. Add a server with the same command and env as the JSON above.
Put that config in your user-level MCP settings. Do not put `QLOO_API_KEY` in the project's
`.cursor/mcp.json`. That path is gitignored so a local copy is not committed. The key still belongs
in the user-level config or the client's environment, never in the repository.

In a chat, Agent mode, ask the same `city_scan` sentence first. Then the `tenant_match` sentence.
Cursor shows the tool call and the JSON result. The result is not written into the repository.

## 4. What the clip should show

1. `tools/list` in a terminal, with the three names visible.
2. Claude Desktop or Cursor calling `city_scan` and showing three Chicago blocks.
3. In the `city_scan` JSON, the `limits` list includes "A cell is a block to scout, not a vetted
   address." `city_scan` does not return a separate note field. Do not claim a survival forecast.
   The Chicago backtest found no measurable gain.
