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


def _rate(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _interval_below_zero(interval):
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        return False
    lo, hi = interval
    return _rate(lo) and _rate(hi) and hi < 0


def _signed_points(value):
    points = round(100 * value)
    if points < 0:
        return f"\u2212{abs(points)}"
    if points > 0:
        return f"+{points}"
    return "0"


def _span(interval):
    return f"{_signed_points(interval[0])} to {_signed_points(interval[1])} points"


def _comparison(source, chicago):
    """The gain sentence is true only when the committed rates and intervals say so."""
    methods = (source or {}).get("methods") or {}
    qloo = (methods.get("qloo") or {}).get("hit_at_k")
    prior = (methods.get("prior") or {}).get("hit_at_k")
    tgi_hit = (methods.get("tgi") or {}).get("hit_at_k")
    q_ci = None
    for doc in (chicago, source):
        if isinstance(doc, dict) and doc.get("qloo_minus_prior_ci90") is not None:
            q_ci = doc.get("qloo_minus_prior_ci90")
            break
    t_ci = source.get("tgi_minus_prior_ci90") if isinstance(source, dict) else None
    qloo_below = _rate(qloo) and _rate(prior) and qloo < prior and _interval_below_zero(q_ci)
    tgi_known = _rate(tgi_hit)
    tgi_below = tgi_known and _rate(prior) and tgi_hit < prior and _interval_below_zero(t_ci)
    tail = (" Read both as taste and supply evidence, not as a survival forecast. "
            "Qloo describes today's taste, after these places opened.")
    if qloo_below and (not tgi_known or tgi_below):
        text = (f"No measurable gain. Qloo's top-3 hit rate is {_pct(qloo)} and the citywide baseline "
                f"is {_pct(prior)}. The 90% interval for Qloo minus that baseline, {_span(q_ci)}, "
                "stays below zero.")
        if tgi_below:
            text += (f" The Taste Gap Index hit rate is {_pct(tgi_hit)}. Its 90% interval versus the "
                     f"baseline, {_span(t_ci)}, stays below zero.")
        return text + tail, True
    return ("The committed hit rates and intervals do not support a gain claim." + tail), False


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


def _interval_above_zero(interval):
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        return False
    lo, hi = interval
    return _rate(lo) and _rate(hi) and lo > 0


def _footfall_section(doc):
    """Hit rates and intervals from the committed footfall backtest. No gain sentence unless the numbers say so."""
    if not isinstance(doc, dict):
        return ""
    methods = doc.get("methods") or {}
    labels = (("footfall", "Footfall alone"), ("footfall_tgi", "Footfall + Taste Gap supply"),
              ("footfall_prior", "Footfall + citywide baseline"))
    published = doc.get("published_citywide_hit_at_k")
    prior = (methods.get("prior") or {}).get("hit_at_k")
    bits, point_above, interval_above = [], False, False
    for key, label in labels:
        hit = (methods.get(key) or {}).get("hit_at_k")
        if not _rate(hit):
            continue
        ci = doc.get(f"{key}_minus_prior_ci90")
        span = _span(ci) if isinstance(ci, (list, tuple)) and len(ci) == 2 and _rate(ci[0]) and _rate(ci[1]) else "not in the file"
        bits.append(f"{label} hit {_pct(hit)} (90% interval versus this run's citywide baseline, {span}).")
        if _rate(published) and hit > published:
            point_above = True
        if _interval_above_zero(ci):
            interval_above = True
    if not bits:
        return ""
    text = " ".join(bits)
    if _rate(prior):
        text += f" This run's citywide baseline on the same areas is {_pct(prior)}."
    if _rate(published):
        text += f" The published nine-area citywide baseline is {_pct(published)}."
    if interval_above and point_above:
        text += " At least one variant's 90% interval versus the baseline sits above zero."
    elif point_above:
        text += " A higher point estimate is not a gain: no 90% interval versus the baseline sits entirely above zero."
    else:
        if _rate(published):
            text += f" No footfall variant beats the citywide {_pct(published)}."
        else:
            text += " No footfall variant beats the published citywide baseline."
    cited = [f"{s.get('id')} ({s.get('start')} to {s.get('end')})" for s in (doc.get("sources") or [])
             if s.get("id") and s.get("start") and s.get("end")]
    if cited:
        text += " Sources: " + "; ".join(cited) + "."
    text += (" The Taste Gap term is the supply leg only. Over-index D was not recomputed, so this is not the "
             "published Taste Gap Index. Zero Qloo calls. The weights are stated priors, not fitted. "
             "The counts are community-area totals from before 2023, not a door count and not a vetted address.")
    return "<h2>Footfall, before the window</h2><p>" + _esc(text) + "</p>"


def _live_street(doc):
    """Certificate line plus event kinds and timings. No Qloo body."""
    if not doc:
        return ""
    events = "".join(
        "<li>" + _esc(event.get("kind")) + " at " + _esc(event.get("ms")) + " ms</li>"
        for event in doc.get("events") or [])
    assigned = ", ".join(
        _esc(match.get("unit")) + ": " + _esc(match.get("concept") or "unmatched")
        + " (" + _esc(match.get("verdict") or "none") + ")"
        for match in doc.get("matches") or [])
    return ("<h2>Milwaukee Avenue, representative rents</h2><p>"
            + _esc(doc.get("notice")) + " "
            + _esc(doc.get("place")) + " on " + _esc(doc.get("ran_on"))
            + ". Rents in USD: " + _esc(", ".join(str(r) for r in doc.get("rents_usd") or []))
            + ". Elapsed " + _esc(doc.get("elapsed_ms")) + " ms. "
            + _esc(doc.get("server"))
            + " Steps that hit a rate limit: " + _esc(doc.get("rate_limit_steps"))
            + ".</p><p>" + _esc(doc.get("certificate")) + "</p><p>"
            + _esc(doc.get("proof")) + "</p><p>Assignment: " + assigned
            + ". Not a vetted address.</p><ul>" + events + "</ul>")


def page(chicago=None, tgi=None, bias=None, ledger_doc=None, footfall=None, street_run=None,
         contact="youssefbenzidiya@gmail.com"):
    """HTML. Pass dicts in tests; otherwise the committed JSON files are read."""
    chicago = _load("backtest_chicago.json") if chicago is None else chicago
    tgi = _load("backtest_chicago_tgi.json") if tgi is None else tgi
    bias = _load("backtest_bias.json") if bias is None else bias
    ledger_doc = _load("data/ledger.json") if ledger_doc is None else ledger_doc
    footfall = _load("backtest_chicago_footfall.json") if footfall is None else footfall
    street_run = _load("data/milwaukee_street.json") if street_run is None else street_run
    source = tgi or chicago or {}
    comparison, no_gain = _comparison(source, chicago if isinstance(chicago, dict) else source)
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
        "<p>",
        comparison,
        "</p>",
        _footfall_section(footfall),
        "<h2>Survivorship and intervals</h2><p>",
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
<h2>Test before you lease</h2>
<p>The top concept can be checked with a 4 to 8 week pop-up. The success line is the mode break-even at the
rent-first ceiling, on the check that value of information ranks first. The length is a stated rule in that
band. The figures use TasteFit's stated cost assumptions, not yours. Monthly figures, no interest and no discounting. The plan has its own SHA-256. It is not part of the
five-field prediction fingerprint, so the committed Chicago batch hash does not change. A pop-up is not a
vetted address. Logging the observed value, the weeks run and whether it cleared does not rewrite that hash.</p>
<h2>Street match</h2>
<p>With 3 to 5 monthly rents, Stable Street certifies a mix from the concepts already scored in that run.
Stand-alone value is this run's fit plus a rent-first shift. Neighbour effects are an uncalibrated map of
audience overlap already in the run. The page states when no replacement or swap raises the surplus, and
when fixed rents have no stable assignment. No extra Qloo call. The certificate is not a survival forecast.
Baseline: ignores neighbours. Gale and Shapley (1962) matched on fit and the rent-first verdict and did not
use neighbour effects.</p>
""" + _live_street(street_run) + """
<h2>Find an improving move our certificate missed</h2>
<p>The published street is <code>docs/published-street.json</code>. Scores and overlap in that file are stated
for the example. It stores no Qloo response. The units are an example line, not vetted addresses. From a checkout:</p>
<pre>python3 docs/verify_certificate.py docs/published-street.json</pre>
<p>Exit 0 means no replacement or swap raises the surplus. Exit 1 prints the move. The script uses the same
surplus as the certificate and makes no Qloo call. The certificate is not a survival forecast.</p>
<h2>Call it from an agent</h2>
<p>The MCP server is <code>mcp_server.py</code> on stdio. Nothing is written to disk. The snippet below is the
client shape. This change does not add a step-by-step client guide.</p>
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
<li>""" + ("The Chicago backtest found no measurable gain. The conformal interval does not narrow that."
          if no_gain else
          "The Chicago table above is the comparison. The conformal interval does not add a gain the table does not show.") + """</li>
</ul>
<p>Write <a href="mailto:""" + _esc(contact) + """">""" + _esc(contact) + """</a>.</p>
</main></body></html>
""",
    ]
    return "".join(parts)
