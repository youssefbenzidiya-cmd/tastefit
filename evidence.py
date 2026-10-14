"""The /evidence page. Numbers come from the committed backtest files. Standard library only."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).parent

METHOD_LABEL = {
    "qloo": "Qloo rank",
    "prior": "Citywide baseline",
    "qloo_D": "Over-index only",
    "osm_under": "OSM under-supply only",
    "tgi": "Taste Gap Index",
    "prior_tgi": "Baseline plus Taste Gap Index",
}


def _load(name):
    path = ROOT / name
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _pct(value):
    if value is None:
        return "—"
    return f"{round(100 * value)}%"


def _table(doc):
    methods = (doc or {}).get("methods") or {}
    rows = []
    for key, label in METHOD_LABEL.items():
        row = methods.get(key)
        if not row:
            continue
        rows.append(
            "<tr><td>" + _esc(label) + "</td><td>" + _esc(_pct(row.get("hit_at_k"))) + "</td><td>"
            + _esc(row.get("spearman")) + "</td><td>" + _esc(row.get("survivors")) + "</td></tr>")
    if not rows:
        return "<p>No backtest table is committed.</p>"
    return ("<table><thead><tr><th>Ordering</th><th>Top-3 hit rate</th><th>Rank correlation</th>"
            "<th>Survivors</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def page(chicago=None, tgi=None, bias=None, ledger_doc=None):
    """HTML. Pass dicts in tests; otherwise the committed JSON files are read."""
    chicago = _load("backtest_chicago.json") if chicago is None else chicago
    tgi = _load("backtest_chicago_tgi.json") if tgi is None else tgi
    bias = _load("backtest_bias.json") if bias is None else bias
    ledger_doc = _load("data/ledger.json") if ledger_doc is None else ledger_doc
    source = tgi or chicago or {}
    heckman = (bias or {}).get("heckman") or {}
    prior = ((bias or {}).get("methods") or {}).get("prior") or {}
    conformal = (bias or {}).get("conformal") or {}
    batch = ((ledger_doc or {}).get("batches") or [{}])[0]
    diff = (chicago or {}).get("qloo_minus_prior_ci90")
    diff_text = _esc(diff) if diff else "not in the file"
    reason = _esc(heckman.get("reason") or "The Heckman check is not in the committed file.")
    parts = [
        """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TasteFit evidence</title><link rel="stylesheet" href="/styles.css"></head>
<body><main class="wrap">
<p><a href="/">TasteFit</a> · <a href="/ledger">Prediction ledger</a></p>
<h1>Evidence</h1>
<p>TasteFit is the location tool you can prove wrong. The figures below are the committed backtests.
A negative result stays on the page.</p>
<h2>Backtest, Chicago 2023–2024</h2>
<p>Nine areas where a Qloo rank call returned an order. Surviving catalog openings in the top 3 of each
ordering. The Qloo-minus-baseline difference has a 90% bootstrap interval of """,
        diff_text,
        ".</p>",
        _table(source),
        """
<p>No measurable gain. Qloo's rank did not beat the citywide baseline. The Taste Gap Index did not either.
Read both as taste and supply evidence, not as a survival forecast. Qloo describes today's taste, after
these places opened.</p>
<h2>Survivorship and intervals</h2>
<p>""",
        reason,
        "</p><p>Inverse-probability weights (Horvitz and Thompson 1952) on a later register pull with no Qloo call: "
        "citywide top-3 hit rate ",
        _esc(_pct(prior.get("hit_at_k"))),
        " on survivors, ",
        _esc(_pct(prior.get("ipw_hit_at_k"))),
        " after weighting. Openings dropped because the propensity was 0: ",
        _esc(prior.get("dropped_for_positivity")),
        """. This pull is not the nine-area Qloo table, and it does not reverse that table.
The Qloo column was not recomputed, because rank orders are not stored.</p>
<p>Split conformal (Vovk, Gammerman and Shafer 2005), 90%, around the leave-one-area citywide share:
quantile """,
        _esc(conformal.get("quantile")),
        ", mean width ",
        _esc(conformal.get("mean_width")),
        ". ",
        _esc(conformal.get("note")),
        """ A width of 2 on a rate in [0, 1] is the whole range. The interval does not tighten the claim.</p>
<h2>Claim ledger</h2>
<p>The "without Qloo" panel asks the same model, with no tools, for concepts and up to five competitor names.
Each concept is marked confirmed, contradicted, partly supported, or not tested against this run's Taste Gap
label. Each named venue is checked against OpenStreetMap. The counts are agreement with this run and existence
in the open map, not accuracy. The map misses real venues too.</p>
<h2>Pre-registered predictions</h2>
<p>The <a href="/ledger">prediction ledger</a> stores a SHA-256 of the concept, the area, the shortlist verdicts
and the UTC time. No Qloo payload is stored. The committed Chicago batch is """,
        _esc(batch.get("concept")),
        " in ",
        _esc(batch.get("area")),
        ", verdict ",
        _esc(batch.get("verdict")),
        ", check date ",
        _esc(batch.get("check_on")),
        """. The first listed concept is not a ranking. No probability was registered, so the Brier score
(Brier 1950) is not defined for that batch. A later scored batch would be a new fingerprint.</p>
<h2>Street match</h2>
<p>Baseline: ignores neighbours. When a run has at least two monthly rents, Gale and Shapley (1962) match up to five units to the concepts
already scored in that run. Landlords rank by the rent-first verdict, then fit. Tenants rank by fit, then a
lower rent. At most one concept of each rent category is matched unless the request sets k from 1 to 5.
The page states when there is no blocking pair. The match makes no extra Qloo call. Stability is not a
survival forecast.</p>
<h2>Call it from an agent</h2>
<p>The MCP server is <code>mcp_server.py</code> on stdio. Nothing is written to disk. Steps for Claude Desktop
and Cursor are in <code>docs/MCP-DEMO.md</code>.</p>
<pre>{"mcpServers": {"tastefit": {"command": "python3", "args": ["/path/to/tastefit/mcp_server.py"],
  "env": {"QLOO_API_KEY": "...", "QLOO_BASE_URL": "https://hackathon.api.qloo.com"}}}}</pre>
<h2>Infrastructure</h2>
<p>$0. The app is a standard-library server. The ledger, the backtests and this page are files in the
repository. Hosting is the Cloud Run free tier and a free container registry. No database and no paid API
beyond the hackathon keys already in the environment.</p>
<h2>Limits</h2>
<ul>
<li>Qloo is aggregate taste, not foot traffic, sales or rent.</li>
<li>The suggested spot is a block to scout, not a vetted address.</li>
<li>Rent ceilings are a survival model for Paris and Chicago, not a forecast. No discounting and no interest.</li>
<li>A run stops itself at 230 seconds so the memo arrives inside the host's 300 second limit, and it makes at most 25 Qloo calls.</li>
<li>The Chicago backtest found no measurable gain. The conformal interval does not narrow that.</li>
</ul>
</main></body></html>
""",
    ]
    return "".join(parts)
