# Call TasteFit from Claude Desktop or Cursor

TasteFit's MCP server is `mcp_server.py`. It speaks newline-delimited JSON-RPC on stdin and stdout.
The process is local. It reads `QLOO_API_KEY` from the environment you set in the client. It does not
write Qloo responses to disk.

A `tenant_match` call runs the same pipeline as the website: at most 25 Qloo calls, and the process
stops itself at 230 seconds so it finishes inside a 300 second host limit. `city_scan` and
`city_scan_unit` read the committed City Scan packs and make no Qloo call.

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

Use the absolute path to `mcp_server.py` and to the Python that can import the TasteFit modules
(run it from the repository, so the `args` path is enough if that Python's working directory is the
repo; Claude starts the process with the repo as cwd only if you set it). The reliable form is a
one-line shell command:

```json
{
  "mcpServers": {
    "tastefit": {
      "command": "python3",
      "args": ["/absolute/path/to/tastefit/mcp_server.py"],
      "env": {
        "QLOO_API_KEY": "your hackathon key",
        "QLOO_BASE_URL": "https://hackathon.api.qloo.com"
      }
    }
  }
}
```

`python3` must be the interpreter that can `import tenant` when its current directory is the
repository. If the client starts the process somewhere else, set the args to a wrapper:

```bash
#!/bin/sh
cd /absolute/path/to/tastefit
exec python3 mcp_server.py
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

Open Cursor Settings, then MCP. Add a server with the same command, args and env as the JSON above.
Or put the same `mcpServers` object in the project's `.cursor/mcp.json` if you keep secrets out of
git: prefer the user-level MCP config, and do not commit the key.

In a chat, Agent mode, ask the same `city_scan` sentence first. Then the `tenant_match` sentence.
Cursor shows the tool call and the JSON result. The result is not written into the repository.

## 4. What the clip should show

1. `tools/list` in a terminal, with the three names visible.
2. Claude Desktop or Cursor calling `city_scan` and showing three Chicago blocks.
3. One line of the reply that the block is a map cell, not a vetted address, if the tool result
   includes the pack's note. Do not claim a survival forecast. The Chicago backtest found no
   measurable gain.
