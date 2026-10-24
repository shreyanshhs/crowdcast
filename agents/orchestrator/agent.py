"""
Crowdcast — ORCHESTRATOR agent (hosted on Agentverse).

This is the only agent users talk to in ASI:One. It:
  1. receives the content (pitch / post / ad) through the Agent Chat Protocol,
     either pasted directly or through a setup card (platform, region, size)
  2. fans it out to 5 regional Crowd agents in parallel
                                       -> real persona reactions (ASI:One LLM)
  3. merges them and sends to Simulation -> weighted expansion to N impressions
  4. sends objections to Rewrite agent -> improved version (ASI:One LLM)
  5. replies with a text report + an ASI:One interactive card with charts
  6. on "Test the rewrite", runs the rewrite through the same crowd on its own
     and replies with a before/after card

Card clicks come back as a ChatMessage whose text is the button's selection
(JSON when the user @mentions us, prose when ASI:One's planner routed them).
Charts are PNGs rendered by QuickChart from the real numbers; every chart has
a text fallback in the card and in the plain-text report.

Hosted-agent rules: no global state (use ctx.storage), no custom seed,
message model classes must be IDENTICAL in every agent file.
"""

import json
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import uuid4

import os

from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

# ---- Addresses of the other hosted agents ----------------------------------
CROWD_AGENTS = {
    "north": "agent1qw6elpnrg0wqsw59unxnw5ymynypt9wh0g8n47jf5v7250vxwsn872e5saf",
    "south": "agent1qddxa9d59mzv6ggdmff8d0kd2c5hssdv2w3ufx5kd0uaaxec6tvrzj8u6xh",
    "west": "agent1q0yyadpxdy50xrfa35r6jltp5mla7pspfa6aqu83e4pwvrtlmepqgpv09dy",
    "east": "agent1q2vtvps6krssghyx7vctv3np6py4022jn5njs4pcdl6hpykjs6y3g97zdng",
    "central": "agent1qf98p6vzlwdd3ze43x4q2e3ghptcxyje53khlp60qe64c8a3rfdnwq9yj9d",
}
CROWD_ADDRESSES = list(CROWD_AGENTS.values())
SIM_ADDRESS = "agent1qv4tvuw3sklnf2wuufq7dq6pnkqt8mq9kq03kyyjl0w9p9n44d4xvr6p0h2"
REWRITE_ADDRESS = "agent1qtumj924vkyrkzw6smrlkrg5ss0tqeevv34l4ehuhg88uttnxa7aq3wwrdl"

DEFAULT_SIZE = 10_000
MAX_SIZE = 100_000
CROWD_TIMEOUT_S = 75  # go ahead with whichever crowd agents answered by then
NOISE_PTS = 5         # identical runs differ by ~4 points; smaller gaps are "no clear difference"
USE_CARDS = True      # set False if the card ever causes trouble
SEND_PROGRESS = True  # short "testing..." message while the crowd reacts

# loop guards: one sender may only have this many tests running / started per window
MAX_RUNNING_PER_SENDER = 1
MAX_TESTS_PER_WINDOW = 6
RATE_WINDOW_S = 600
NOTICE_COOLDOWN_S = 60
LOG_PREVIEW = 150  # characters of each incoming message written to the agent log

REGION_LABELS = {
    "all": "All India", "north": "North India", "south": "South India",
    "west": "West India", "east": "East India", "central": "Central India",
}
PLATFORMS = {
    "unspecified": "Not specified", "pitch": "Startup pitch", "instagram": "Instagram post",
    "linkedin": "LinkedIn post", "x": "X (Twitter) post", "youtube": "YouTube video", "ad": "Ad",
}
SIZES = [10_000, 50_000, 100_000]
ACTIONS = ("run", "setup", "retest", "regions", "social", "variants", "unlock")

# ---- Paid full report (Agent Payment Protocol, FET on testnet) -------------
# The receiving wallet is an Agentverse secret; without it everything stays free.
FET_WALLET_ENV = "CROWDCAST_FET_WALLET"   # fetch1... address that receives payments (no private key)
PRICE_ENV = "CROWDCAST_PRICE_FET"         # e.g. "0.5"
DEFAULT_PRICE_FET = "0.5"
FET_DENOM = "atestfet"                    # stable-testnet; "afet" would be real FET on mainnet
PAYMENT_DEADLINE_S = 600

SOCIAL_PLATFORMS = {"instagram": "Instagram", "linkedin": "LinkedIn", "x": "X"}
SOCIAL_FORMATS = {
    "instagram": "Carousel or 15–30s reel; put the hook in the first slide or first 2 seconds.",
    "linkedin": "Text post with one image; hook in the first line, short paragraphs.",
    "x": "Single post under 280 characters; lead with the hook, one link at most.",
}
STOPWORDS = set("""a an and are as at be by for from get has have in into is it its of on or our so that the
their this to was we with you your every new just more most only than then them they very will""".split())
REWRITE_FAILED = "(Rewrite unavailable"

# button wording, for clicks that arrive as prose without our session id
LABEL_ACTIONS = [
    ("retest", r"\btest(?:ing)? the rewrite\b|\bretest\b"),
    ("regions", r"\bregion(?:al)? (?:details|breakdown|panels?)\b|\bregions\b"),
    ("setup", r"\bchange (?:the )?setup\b|\bsetup\b"),
    ("run", r"\brun (?:the )?test\b"),
    ("unlock", r"\bunlock\b"),
    ("variants", r"\bpreview both\b|\bvariants?\b"),
    ("social", r"\bsocial preview\b|\bpost preview\b"),
]
# questions about the agent itself (incl. ASI:One's "Tell me what you were created to do")
HELP_RE = re.compile(
    r"\b(what (can|do|does|did|are|were) (you|crowdcast)|who are you|what were you (created|made|built)"
    r"|(created|made|built) to do|tell me (about yourself|what you)|how (does|do) (this|you|it|crowdcast) work"
    r"|what is crowdcast|^(?:help|start|hello|hey|hi)\W*$)", re.I)
# our own replies coming back at us (e.g. relayed by a planner) must never become a new test
# ASI:One's own assistant answers our messages ("Got it — I'll keep an eye out ..."); that chatter
# reaches us as a chat message and must not be tested as a pitch
CHATTER_RE = re.compile(
    r"^(?:(?:got it|thanks|thank you|sounds good|noted|will do|understood|let me know"
    r"|here (?:are|is) the results|the results)\b|i'?ll |i will "
    r"|(?:great|ok(?:ay)?|perfect|awesome|sure|cool|nice)\s*[!.,—–-])", re.I)
ECHO_MARKERS = ("🧪 Testing", "📣 ", "🔬 Original vs rewrite", "🗺️ Regional panels", "ℹ️ ", "⏳ ",
                "Charts, objections and a rewrite are in the card", "Crowdcast results —", "Hi, I'm Crowdcast")

# chart colours (dark mode): diverging pair (love = blue, hate = red) around a
# neutral gray, on a dark surface; segments also carry direct labels + gaps
LOVE, IGNORE, HATE = "#3987e5", "#6e6d68", "#e66767"
CHART_BG, INK, INK_MUTED, GRID = "#1a1a19", "#f0efec", "#c3c2b7", "#2c2c2a"
QUICKCHART_URL = "https://quickchart.io/chart"


# ---- Shared message models (keep identical in every agent) -----------------
class CrowdRequest(Model):
    session: str
    content: str


class CrowdResult(Model):
    session: str
    reactions_json: str


class SimRequest(Model):
    session: str
    reactions_json: str
    size: int


class SimResult(Model):
    session: str
    stats_json: str


class RewriteRequest(Model):
    session: str
    content: str
    objections_json: str


class RewriteResult(Model):
    session: str
    rewrite: str


agent = Agent()
chat_proto = Protocol(spec=chat_protocol_spec)
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")


# ---- Helpers ---------------------------------------------------------------
def now():
    return datetime.now(timezone.utc)


def text_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=now(),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def card_message(text: str, card: dict, terminal: bool) -> ChatMessage:
    """Plain-text fallback first, then the card for ASI:One to render."""
    content = [TextContent(type="text", text=text)]
    if USE_CARDS:
        metadata = {
            "card_protocol_version": "1",
            "requires_card_interaction": "true",
            "card_kind": "custom",
            "card_payload": json.dumps(card),
            "preferred_drawer_width_px": "640",
        }
        if terminal:
            metadata["is_terminal"] = "true"
        content.append(MetadataContent(type="metadata", metadata=metadata))
    return ChatMessage(timestamp=now(), msg_id=uuid4(), content=content)


def parse_size(text: str) -> int:
    """Pick up a crowd size like 'crowd 100k' or 'audience 50k'. Default 10k."""
    m = re.search(r"(?:crowd|audience|impressions?|people)\D{0,12}(\d+)\s*k\b", text.lower())
    if not m:
        m = re.search(r"\b(\d+)\s*k\s*(?:crowd|audience|impressions?|people)", text.lower())
    if m:
        return max(1_000, min(int(m.group(1)) * 1000, MAX_SIZE))
    return DEFAULT_SIZE


def strip_mentions(text: str) -> str:
    return re.sub(r"^(?:\s*@[\w.-]+)+\s*", "", text)


def clean_content(text: str) -> str:
    """Strip @mentions, a leading ask like 'How will people react to this pitch:' and size hints."""
    t = strip_mentions(text)
    m = re.match(r"^([^:\n]{0,140}):\s*", t)
    if m and re.search(r"\b(react|reaction|think|feel|test|respond|rate|review|audience|crowd|people)\b", m.group(1), re.I):
        t = t[m.end():]
    t = re.sub(r"[\s,(]*\b(?:crowd|audience|impressions?|people)\s*(?:size\s*)?:?\s*\d+\s*k\b\)?\s*$", "", t, flags=re.I)
    t = re.sub(r"[\s,(]*\b\d+\s*k\s*(?:crowd|audience|impressions?|people)\b\)?\s*$", "", t, flags=re.I)
    t = t.strip().strip('"“”').strip()
    return t or text


def for_crowd(content: str, platform: str) -> str:
    """Tell the personas (and the rewriter) what kind of content they're looking at."""
    if platform in PLATFORMS and platform != "unspecified":
        return f"Format: {PLATFORMS[platform]}\n\n{content}"
    return content


def strip_format_line(text: str) -> str:
    return re.sub(r"^\s*Format:[^\n]*\n+", "", text).strip()


def merge_reactions(batches: list, seed: str) -> list:
    """Flatten the regional panels and shuffle, so quotes and objections aren't all from one region."""
    merged = [r for batch in batches for r in batch]
    random.Random(seed).shuffle(merged)
    return merged


def split_pct(counts: dict, n: int) -> tuple:
    """Whole-number love/ignore/hate percentages that add up to 100."""
    if not n:
        return 0, 0, 0
    love = round(100 * counts.get("love", 0) / n)
    hate = round(100 * counts.get("hate", 0) / n)
    return love, 100 - love - hate, hate


def summarise_regions(batches: list) -> list:
    """Raw panel results per regional crowd agent (no smoothing, no projection)."""
    out = []
    for region in CROWD_AGENTS:
        reactions = [r for b in batches if b["region"] == region for r in b["reactions"]]
        if not reactions:
            continue
        counts = {}
        for r in reactions:
            counts[r["reaction"]] = counts.get(r["reaction"], 0) + 1
        love, ignore, hate = split_pct(counts, len(reactions))
        answered = [r for r in reactions if r.get("reason") and r["reason"] != "(no response)"]
        out.append({
            "region": region, "label": REGION_LABELS[region], "n": len(reactions),
            "love_pct": love, "ignore_pct": ignore, "hate_pct": hate,
            "quotes": [f"{r['reason']} — {r['name']}, {r['age']}, {r['city']}" for r in answered[:2]],
            "objections": [r["objection"] for r in reactions if r.get("objection")][:2],
            "failed": len(answered) == 0,
        })
    return out


def rewrite_ok(rewrite: str) -> bool:
    return bool(rewrite) and not rewrite.startswith(REWRITE_FAILED)


def verdict(before: dict, after: dict) -> str:
    d_love = after["love_pct"] - before["love_pct"]
    d_hate = after["hate_pct"] - before["hate_pct"]
    if d_love >= NOISE_PTS and d_hate <= 0:
        return f"The rewrite tested better: +{d_love} pts love, {d_hate:+d} pts hate."
    if d_love <= -NOISE_PTS or d_hate >= NOISE_PTS:
        return f"The original tested better: rewrite is {d_love:+d} pts love, {d_hate:+d} pts hate."
    return (f"No clear difference ({d_love:+d} pts love, {d_hate:+d} pts hate). "
            f"Gaps under {NOISE_PTS} points are within normal run-to-run variation.")


# ---- Charts (QuickChart PNGs from real numbers) ----------------------------
def chart_url(title: str, rows: list, axis_label: str) -> str:
    """100% stacked horizontal bars, one row per (label, love, ignore, hate)."""
    def dataset(name, idx, colour, label_colour):
        return {  # zero -> null, so empty segments get no stray "0" label
            "label": name, "data": [r[idx] or None for r in rows], "backgroundColor": colour,
            "borderColor": CHART_BG, "borderWidth": 2, "barThickness": 20,
            "datalabels": {"color": label_colour},
        }

    config = {
        "type": "horizontalBar",
        "data": {
            "labels": [r[0] for r in rows],
            "datasets": [
                dataset("Love", 1, LOVE, "#ffffff"),
                dataset("Ignore", 2, IGNORE, "#ffffff"),
                dataset("Hate", 3, HATE, "#ffffff"),
            ],
        },
        "options": {
            "title": {"display": True, "text": title, "fontColor": INK, "fontSize": 14},
            "legend": {"position": "bottom", "labels": {"fontColor": INK_MUTED, "boxWidth": 12}},
            "scales": {
                "xAxes": [{
                    "stacked": True, "ticks": {"min": 0, "max": 100, "fontColor": INK_MUTED},
                    "gridLines": {"color": GRID},
                    "scaleLabel": {"display": True, "labelString": axis_label, "fontColor": INK_MUTED},
                }],
                "yAxes": [{"stacked": True, "ticks": {"fontColor": INK_MUTED}, "gridLines": {"display": False}}],
            },
            "plugins": {"datalabels": {"display": "auto", "font": {"size": 11}}},
        },
    }
    height = 130 + 40 * len(rows)
    query = {"w": 600, "h": height, "bkg": CHART_BG, "f": "png",
             "c": json.dumps(config, separators=(",", ":"))}
    return f"{QUICKCHART_URL}?{urlencode(query)}"


def segment_chart(stats: dict) -> str:
    rows = [(s["label"], s["love_pct"], s["ignore_pct"], s["hate_pct"]) for s in stats["segments"]]
    return chart_url("Projected reaction by audience segment", rows, "% of each segment")


def region_chart(regions: list) -> str:
    rows = [(f"{r['label']} ({r['n']})", r["love_pct"], r["ignore_pct"], r["hate_pct"]) for r in regions]
    return chart_url("Raw persona panels by region (personas per panel)", rows, "% of panel")


def compare_chart(before: dict, after: dict) -> str:
    rows = [("Original", before["love_pct"], before["ignore_pct"], before["hate_pct"]),
            ("Rewrite", after["love_pct"], after["ignore_pct"], after["hate_pct"])]
    return chart_url("Original vs rewrite (projected)", rows, "% of impressions")


def pct_line(x: dict) -> str:
    return f"{x['love_pct']}% love · {x['ignore_pct']}% ignore · {x['hate_pct']}% hate"


# ---- Plain-text reports (fallback when cards don't render) -----------------
def build_report(s: dict, stats: dict, rewrite: str) -> str:
    lines = [
        f"📣 Crowdcast results — {stats['size']:,} simulated impressions",
        f"Platform: {PLATFORMS.get(s.get('platform'), PLATFORMS['unspecified'])} · "
        f"Target: {REGION_LABELS.get(s.get('region'), REGION_LABELS['all'])}",
        "",
        f"❤️ Love {stats['love_pct']}%  ·  😐 Ignore {stats['ignore_pct']}%  ·  👎 Hate {stats['hate_pct']}%",
        f"🔁 {stats['shares']:,} shares → projected total reach {stats['total_reach']:,}",
        "",
        "Segments (best first):",
    ]
    for seg in stats["segments"]:
        lines.append(f"• {seg['label']}: {pct_line(seg)} ({seg['seen']:,} saw it)")
    if s.get("regions"):
        lines += ["", "Regional persona panels (raw, not projected):"]
        lines += [f"• {r['label']} ({r['n']} personas): {pct_line(r)}" for r in s["regions"]]
    if stats.get("objections"):
        lines += ["", "Top objections:"]
        lines += [f"{i}. {o}" for i, o in enumerate(stats["objections"], 1)]
    if stats.get("quotes"):
        lines += ["", "What the crowd said:"]
        lines += [f"“{q}”" for q in stats["quotes"]]
    lines += [
        "",
        "✍️ Rewritten version that should test better:",
        rewrite,
        "",
        f"Method: {stats['personas']} AI personas from {stats.get('crowd_agents', 1)} regional "
        f"crowd agents each reacted to your content; their reactions were weighted by "
        f"audience segment and statistically expanded to {stats['size']:,} impressions.",
    ]
    if rewrite_ok(rewrite):
        lines += ["", "Reply with this session to retest the rewrite: "
                      + json.dumps({"action": "retest", "session": s["id"]})]
    return "\n".join(lines)


def build_compare_report(before: dict, after: dict, original: str, rewrite: str) -> str:
    return "\n".join([
        "🔬 Original vs rewrite — each tested separately on the same crowd",
        f"Original: {pct_line(before)}",
        f"Rewrite:  {pct_line(after)}",
        "",
        verdict(before, after),
        "",
        "Original:", original, "", "Rewrite:", rewrite,
    ])


# ---- Cards ------------------------------------------------------------------
def text_el(value: str, style: str = "body") -> dict:
    return {"type": "text", "value": value, "style": style}


def heading_el(value: str, level: int = 3) -> dict:
    return {"type": "heading", "value": value, "level": level}


def button_el(label: str, action: str, session: str, primary: bool = False, **extra) -> dict:
    return {"type": "button", "label": label, "primary": primary,
            "action": {"selection": {"action": action, "session": session, **extra}}}


# ---- Payments: helpers ------------------------------------------------------
def fet_wallet() -> str:
    return os.environ.get(FET_WALLET_ENV, "").strip()


def payments_enabled() -> bool:
    return fet_wallet().startswith("fetch1")


def price_fet() -> str:
    price = os.environ.get(PRICE_ENV, "").strip() or DEFAULT_PRICE_FET
    try:
        return price if float(price) > 0 else DEFAULT_PRICE_FET
    except ValueError:
        return DEFAULT_PRICE_FET


def is_unlocked(s: dict) -> bool:
    return not payments_enabled() or bool(s.get("paid"))


def verify_fet_payment(tx_hash: str, recipient: str, amount_fet: str, sender_wallet: str = "") -> bool:
    """Check on the Fetch.ai stable-testnet ledger that tx_hash moved >= amount to our wallet."""
    from cosmpy.aerial.client import LedgerClient, NetworkConfig  # imported lazily: only paid flows need it

    tx = LedgerClient(NetworkConfig.fetchai_stable_testnet()).query_tx(tx_hash)
    if not tx.is_successful():
        return False
    need = int(round(float(amount_fet) * 10**18))
    transfers = []
    for events in [tx.events] + [getattr(log, "events", {}) for log in (getattr(tx, "logs", None) or [])]:
        found = (events or {}).get("transfer") if isinstance(events, dict) else None
        transfers += found if isinstance(found, list) else [found] if isinstance(found, dict) else []
    for event in transfers:
        amount = str(event.get("amount", ""))
        if (event.get("recipient") == recipient and amount.endswith(FET_DENOM)
                and (not sender_wallet or event.get("sender") == sender_wallet)
                and int(amount[: -len(FET_DENOM)] or 0) >= need):
            return True
    return False


# ---- Social previews --------------------------------------------------------
def hashtags(content: str, region: str) -> list:
    """Simple keyword hashtags from the user's own words (suggestions, not trend data)."""
    words = re.findall(r"[A-Za-z][A-Za-z']{4,}", content)
    counts = {}
    for w in words:
        if w.lower() not in STOPWORDS:
            counts[w.lower()] = counts.get(w.lower(), 0) + 1
    tags = [f"#{w}" for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))[:4]]
    if region in CROWD_AGENTS:
        tags.append(f"#{REGION_LABELS[region].replace(' ', '')}")
    return tags


def hook_of(content: str) -> str:
    first = re.split(r"(?<=[.!?])\s+|\n", content.strip(), maxsplit=1)[0]
    return first[:120]


def social_platform(s: dict, requested: str = None) -> str:
    if requested in SOCIAL_PLATFORMS:
        return requested
    return s.get("platform") if s.get("platform") in SOCIAL_PLATFORMS else "instagram"


def post_preview(platform: str, content: str, stats: dict, region: str, label: str = None) -> list:
    """Children for one platform-styled mock post (stays within the 8-level nesting limit)."""
    limit = 280 if platform == "x" else 2200
    body = content if len(content) <= limit else content[: limit - 1] + "…"
    rows = []
    if label:
        rows.append(heading_el(label))
    hook = hook_of(content)
    rest = body[len(hook):].strip() if body.startswith(hook) else body
    rows += [text_el(f"{SOCIAL_PLATFORMS[platform]} · @yourbrand", "muted"), text_el(hook, "emphasis")]
    if rest:   # one-sentence posts are all hook; don't print them twice
        rows.append(text_el(rest))
    rows += [
        text_el(" ".join(hashtags(content, region)), "muted"),
        {"type": "group", "direction": "row", "gap": 8, "children": [
            {"type": "badge", "label": f"❤️ {stats['love_pct']}% love", "variant": "success"},
            {"type": "badge", "label": f"👎 {stats['hate_pct']}% hate", "variant": "warning"},
        ]},
    ]
    if platform == "x" and len(content) > 280:
        rows.append(text_el(f"Too long for one X post ({len(content)} characters, limit 280).", "muted"))
    return rows


def social_card(s: dict, stats: dict, platform: str) -> dict:
    children = post_preview(platform, s["content"], stats, s.get("region"))
    children += [
        {"type": "divider"},
        text_el(f"Suggested format: {SOCIAL_FORMATS[platform]}", "muted"),
        text_el("Hashtags are keywords from your own text, not trend data. Nothing is posted anywhere.", "muted"),
        {"type": "group", "direction": "row", "gap": 8, "children": [
            button_el(name, "social", s["id"], primary=(key == platform), platform=key)
            for key, name in SOCIAL_PLATFORMS.items()
        ]},
    ]
    return {"root": {"type": "section", "title": f"{SOCIAL_PLATFORMS[platform]} preview",
                     "subtitle": "Your content as a post, with its crowd score", "children": children}}


def variants_card(parent: dict, before: dict, child: dict, after: dict, platform: str) -> dict:
    winner = verdict(before, after)
    children = [text_el(winner, "emphasis"), {"type": "divider"}]
    children += post_preview(platform, parent["content"], before, parent.get("region"), "Original")
    children += [{"type": "divider"}]
    children += post_preview(platform, child["content"], after, parent.get("region"), "Rewrite")
    children += [text_el("Nothing is posted anywhere; copy the version you prefer.", "muted")]
    return {"root": {"type": "section", "title": f"Original vs rewrite as {SOCIAL_PLATFORMS[platform]} posts",
                     "subtitle": "Each version tested separately on the same crowd", "children": children}}


def options(mapping: dict) -> list:
    return [{"value": k, "label": v} for k, v in mapping.items()]


def setup_card(session: str, has_content: bool) -> dict:
    children = []
    if not has_content:
        children.append({"type": "input", "name": "content", "kind": "text", "required": True,
                         "label": "Pitch, post or ad to test",
                         "placeholder": "Paste the text your audience would see"})
    children += [
        {"type": "input", "name": "platform", "kind": "select", "label": "Platform",
         "options": options(PLATFORMS)},
        {"type": "input", "name": "region", "kind": "select", "label": "Target region",
         "options": options(REGION_LABELS)},
        {"type": "input", "name": "size", "kind": "select", "label": "Crowd size",
         "options": [{"value": str(n), "label": f"{n:,} impressions"} for n in SIZES]},
        button_el("Run test", "run", session, primary=True),
    ]
    return {"root": {
        "type": "section", "title": "Test your content on a simulated crowd",
        "subtitle": f"{len(CROWD_AGENTS) * 18} AI personas across {len(CROWD_AGENTS)} regions of India",
        "children": children,
    }}


def results_card(s: dict, stats: dict, rewrite: str) -> dict:
    children = [
        {"type": "group", "direction": "row", "gap": 8, "children": [
            {"type": "badge", "label": f"❤️ {stats['love_pct']}% love", "variant": "success"},
            {"type": "badge", "label": f"😐 {stats['ignore_pct']}% ignore", "variant": "info"},
            {"type": "badge", "label": f"👎 {stats['hate_pct']}% hate", "variant": "warning"},
        ]},
        text_el(f"{stats['shares']:,} shares → projected reach {stats['total_reach']:,}", "muted"),
    ]
    target = next((r for r in s.get("regions", []) if r["region"] == s.get("region")), None)
    if target:
        children.append(text_el(f"Your target, {target['label']} ({target['n']} personas): {pct_line(target)}",
                                "emphasis"))
    children += [
        {"type": "divider"},
        heading_el("By audience segment"),
        {"type": "image", "src": segment_chart(stats), "alt": "Stacked bars of love, ignore and hate per audience segment"},
        text_el(" · ".join(f"{x['label']}: {x['love_pct']}/{x['ignore_pct']}/{x['hate_pct']}"
                           for x in stats["segments"]) + "  (love/ignore/hate %)", "muted"),
    ]
    full = is_unlocked(s)
    if s.get("regions") and full:
        children += [
            heading_el("By region"),
            {"type": "image", "src": region_chart(s["regions"]), "alt": "Stacked bars of love, ignore and hate per regional persona panel"},
            text_el(" · ".join(f"{r['label']}: {r['love_pct']}/{r['ignore_pct']}/{r['hate_pct']}"
                               for r in s["regions"]) + "  (raw panel %, love/ignore/hate)", "muted"),
        ]
    objections = stats.get("objections", []) if full else stats.get("objections", [])[:2]
    if objections:
        children += [heading_el("Top objections"), {"type": "list", "items": [
            {"children": [text_el(f"{i}. {o}")]} for i, o in enumerate(objections, 1)]}]
    if stats.get("quotes") and full:
        children += [heading_el("What the crowd said"), {"type": "list", "items": [
            {"children": [text_el(f"“{q}”", "muted")]} for q in stats["quotes"]]}]
    if full:
        children += [{"type": "divider"}, heading_el("Rewritten version"), text_el(rewrite[:1500])]
    else:
        children += [{"type": "divider"}, text_el(
            f"🔒 Full report: region chart and details, every objection, persona quotes, the rewrite and a "
            f"before/after retest. {price_fet()} testFET, paid from your ASI:One wallet.", "emphasis")]

    buttons = []
    if not full:
        buttons.append(button_el(f"Unlock full report ({price_fet()} testFET)", "unlock", s["id"], primary=True))
    elif rewrite_ok(rewrite):
        buttons.append(button_el("Test the rewrite", "retest", s["id"], primary=True))
    buttons.append(button_el("Social preview", "social", s["id"]))
    buttons.append(button_el("Change setup", "setup", s["id"]))
    if s.get("regions") and full:
        buttons.append(button_el("Region details", "regions", s["id"]))
    children += [
        {"type": "group", "direction": "row", "gap": 8, "children": buttons},
        text_el(f"Method: {stats['personas']} AI personas from {stats.get('crowd_agents', 1)} regional crowd "
                f"agents reacted; segment numbers are weighted and expanded to {stats['size']:,} "
                f"impressions, region numbers are the raw panels.", "muted"),
    ]
    return {"root": {
        "type": "section", "title": "Crowdcast results",
        "subtitle": f"{PLATFORMS.get(s.get('platform'), PLATFORMS['unspecified'])} · "
                    f"{stats['size']:,} simulated impressions from {stats['personas']} personas",
        "children": children,
    }}


def regions_card(s: dict) -> dict:
    items = []
    for r in s.get("regions", []):
        rows = [text_el(f"{r['label']} — {r['n']} personas", "emphasis"), text_el(pct_line(r))]
        if r["failed"]:
            rows.append(text_el("This panel didn't answer properly; its personas count as 'ignore'.", "muted"))
        rows += [text_el(f"“{q}”", "muted") for q in r["quotes"]]
        rows += [text_el(f"Objection: {o}", "muted") for o in r["objections"]]
        items.append({"children": rows})
    return {"root": {
        "type": "section", "title": "Regional persona panels",
        "subtitle": "Raw reactions per regional crowd agent (not projected)",
        "children": [{"type": "list", "items": items}],
    }}


def compare_card(before: dict, after: dict, original: str, rewrite: str, session: str = None) -> dict:
    d_love = after["love_pct"] - before["love_pct"]
    d_hate = after["hate_pct"] - before["hate_pct"]
    return {"root": {
        "type": "section", "title": "Original vs rewrite",
        "subtitle": "Each version tested separately on the same crowd",
        "children": [
            {"type": "group", "direction": "row", "gap": 8, "children": [
                {"type": "badge", "label": f"Love {d_love:+d} pts", "variant": "success" if d_love > 0 else "info"},
                {"type": "badge", "label": f"Hate {d_hate:+d} pts", "variant": "warning" if d_hate > 0 else "info"},
            ]},
            {"type": "image", "src": compare_chart(before, after), "alt": "Stacked bars comparing original and rewrite"},
            text_el(f"Original: {pct_line(before)}", "muted"),
            text_el(f"Rewrite: {pct_line(after)}", "muted"),
            text_el(verdict(before, after), "emphasis"),
            {"type": "divider"},
            heading_el("Original"), text_el(original[:1200]),
            heading_el("Rewrite"), text_el(rewrite[:1200]),
        ] + ([button_el("Preview both as posts", "variants", session, primary=True)] if session else []) + [
        ],
    }}


# ---- Card clicks ------------------------------------------------------------
def label_action(low: str):
    return next((a for a, pattern in LABEL_ACTIONS if re.search(pattern, low)), None)


def parse_action(text: str, latest: dict = None):
    """A card click: JSON selection (direct @mention), prose that names the session id,
    or short prose that only names the button (then the sender's latest session is used)."""
    body = strip_mentions(text).strip()
    candidates = [body]
    m = re.search(r"\{.*\}", body, re.S)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("action") in ACTIONS and data.get("session"):
            return data
    low = body.lower()
    session = re.search(r"\b[0-9a-f]{32}\b", body)
    action = label_action(low)
    if not action and session:
        action = next((a for a in ("retest", "regions", "setup", "run") if re.search(rf"\b{a}\b", low)), None)
    if not action:
        return None
    if session:
        data = {"action": action, "session": session.group(0)}
    elif len(body) < 300:
        latest = latest or {}
        data = {"action": action, "session": latest.get("setup" if action == "run" else "result")}
    else:
        return None
    platform = next((k for k in PLATFORMS if k != "unspecified" and re.search(rf"\b{k}\b", low)), None)
    region = next((k for k in REGION_LABELS if re.search(rf"\b{k}\b", low)), None)
    size = re.search(r"\b(10|50|100)\s*(?:k|,?000)\b", low)
    if platform:
        data["platform"] = platform
    if region:
        data["region"] = region
    if size:
        data["size"] = str(int(size.group(1)) * 1000)
    return data


def new_session(ctx: Context, user: str, **fields) -> str:
    session = uuid4().hex
    ctx.storage.set(session, {"id": session, "user": user, **fields})
    return session


def remember_latest(ctx: Context, user: str, kind: str, session: str):
    latest = ctx.storage.get(f"latest:{user}") or {}
    latest[kind] = session
    ctx.storage.set(f"latest:{user}", latest)


async def notice(ctx: Context, user: str, text: str):
    """Plain text, at most once per cooldown, so a blocked loop can't feed itself."""
    key = f"notice:{user}"
    if time.time() - (ctx.storage.get(key) or 0) < NOTICE_COOLDOWN_S:
        return
    ctx.storage.set(key, time.time())
    await ctx.send(user, text_message(f"ℹ️ {text}"))


def finish_running(ctx: Context, user: str, session: str):
    running = ctx.storage.get(f"running:{user}") or {}
    running.pop(session, None)
    ctx.storage.set(f"running:{user}", running)


async def start_test(ctx: Context, user: str, content: str, platform: str, region: str,
                     size: int, parent: str = None):
    """Start a crowd run unless this sender already has one going or has hit the rate limit."""
    t = time.time()
    running = {s: started for s, started in (ctx.storage.get(f"running:{user}") or {}).items()
               if t - started < CROWD_TIMEOUT_S + 120}
    starts = [x for x in (ctx.storage.get(f"starts:{user}") or []) if t - x < RATE_WINDOW_S]
    if len(running) >= MAX_RUNNING_PER_SENDER:
        ctx.logger.warning(f"blocked new test from {user}: {len(running)} already running")
        await notice(ctx, user, "A test is already running. I'll post the results here as soon as they're ready.")
        return None
    if len(starts) >= MAX_TESTS_PER_WINDOW:
        ctx.logger.warning(f"blocked new test from {user}: rate limit ({len(starts)} in {RATE_WINDOW_S}s)")
        await notice(ctx, user, "You've run a lot of tests in the last few minutes. Please wait a bit and try again.")
        return None

    session = new_session(
        ctx, user, content=content, platform=platform, region=region, size=size,
        kind="retest" if parent else "test", parent=parent,
        started=time.time(), pending=list(CROWD_ADDRESSES), batches=[], dispatched=False,
    )
    ctx.storage.set("active", (ctx.storage.get("active") or []) + [session])
    running[session] = t
    ctx.storage.set(f"running:{user}", running)
    ctx.storage.set(f"starts:{user}", starts + [t])
    ctx.logger.info(f"[{session}] new {'retest' if parent else 'test'} from {user}, "
                    f"asking {len(CROWD_ADDRESSES)} crowd agents")
    if SEND_PROGRESS:
        what = "the rewrite" if parent else "your content"
        await ctx.send(user, text_message(
            f"🧪 Testing {what} on {len(CROWD_AGENTS) * 18} personas across "
            f"{len(CROWD_AGENTS)} regional crowds… results in about a minute."))
    for address in CROWD_ADDRESSES:
        await ctx.send(address, CrowdRequest(session=session, content=for_crowd(content, platform)))
    return session


async def request_payment(ctx: Context, user: str, s: dict):
    """Seller side of the Agent Payment Protocol: ask the user's wallet for the full-report price."""
    if not payments_enabled():
        await notice(ctx, user, "Payments aren't set up yet, so the full report is already included.")
        return
    if s.get("paid"):
        await notice(ctx, user, "This report is already unlocked.")
        return
    if time.time() - s.get("payment_requested", 0) < 60:   # don't stack payment prompts on repeat clicks
        return
    s["payment_requested"] = time.time()
    ctx.storage.set(s["id"], s)
    ctx.logger.info(f"[{s['id']}] requesting {price_fet()} testFET")
    await ctx.send(user, RequestPayment(
        accepted_funds=[Funds(amount=price_fet(), currency="FET", payment_method="fet_direct")],
        recipient=fet_wallet(),
        deadline_seconds=PAYMENT_DEADLINE_S,
        reference=s["id"],
        description=f"Crowdcast full report ({price_fet()} testFET, Fetch.ai testnet)",
        metadata={"fet_network": "stable-testnet", "service": "crowdcast_full_report"},
    ))


@payment_proto.on_message(CommitPayment)
async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
    s = ctx.storage.get(msg.reference or "") or {}
    used = ctx.storage.get("used_tx") or []
    ok = False
    if (s.get("user") == sender and not s.get("paid") and msg.transaction_id not in used
            and msg.funds.currency == "FET" and msg.funds.payment_method == "fet_direct"
            and float(msg.funds.amount or 0) >= float(price_fet())):
        buyer = ""
        if isinstance(msg.metadata, dict):
            buyer = str(msg.metadata.get("buyer_fet_wallet") or msg.metadata.get("buyer_fet_address") or "")
        try:
            ok = verify_fet_payment(msg.transaction_id, fet_wallet(), price_fet(), buyer)
        except Exception as e:  # noqa: BLE001
            ctx.logger.warning(f"[{s.get('id')}] payment check failed: {e}")
    if not ok:
        ctx.logger.warning(f"payment not accepted for {msg.reference}: tx {msg.transaction_id}")
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id,
                                             reason="Payment could not be verified on the Fetch.ai testnet"))
        return
    ctx.storage.set("used_tx", (used + [msg.transaction_id])[-500:])
    s["paid"] = msg.transaction_id
    ctx.storage.set(s["id"], s)
    ctx.logger.info(f"[{s['id']}] payment verified: {msg.transaction_id}")
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    stats = json.loads(s["stats"])
    await ctx.send(sender, card_message(
        "ℹ️ Payment received. Here's your full report.", results_card(s, stats, s.get("rewrite", "")),
        terminal=False))


@payment_proto.on_message(RejectPayment)
async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
    ctx.logger.info(f"payment rejected by {sender}: {msg.reason}")


async def handle_action(ctx: Context, sender: str, action: dict):
    s = ctx.storage.get(action["session"]) if action.get("session") else None
    if not s or s.get("user") != sender:
        ctx.logger.warning(f"click '{action['action']}' for unknown session {action.get('session')}")
        await notice(ctx, sender, "That test isn't available any more. Paste your content again to start a new one.")
        return
    kind = action["action"]
    ctx.logger.info(f"[{s['id']}] click: {kind}")
    if kind in ("retest", "regions") and not is_unlocked(s):
        kind = "unlock"   # paid features: ask for payment instead

    if kind == "unlock":
        await request_payment(ctx, sender, s)
        return

    if kind == "social":
        if "stats" not in s:
            await notice(ctx, sender, "Run a test first; the preview shows your post with its crowd score.")
            return
        platform = social_platform(s, action.get("platform"))
        await ctx.send(sender, card_message(
            f"ℹ️ {SOCIAL_PLATFORMS[platform]} preview of your post.",
            social_card(s, json.loads(s["stats"]), platform), terminal=False))
        return

    if kind == "variants":
        parent = ctx.storage.get(s.get("parent") or "") or {}
        if s.get("kind") != "retest" or "stats" not in s or "stats" not in parent:
            await notice(ctx, sender, "Test the rewrite first, then preview both versions as posts.")
            return
        platform = social_platform(parent, action.get("platform"))
        await ctx.send(sender, card_message(
            f"ℹ️ Original and rewrite as {SOCIAL_PLATFORMS[platform]} posts.",
            variants_card(parent, json.loads(parent["stats"]), s, json.loads(s["stats"]), platform),
            terminal=True))
        return

    if kind == "run":
        content = (action.get("content") or s.get("content") or "").strip()
        if len(content) < 15:
            remember_latest(ctx, sender, "setup", s["id"])
            await ctx.send(sender, card_message(
                "ℹ️ Please add the pitch, post or ad you want to test (at least a sentence).",
                setup_card(s["id"], has_content=False), terminal=False))
            return
        platform = action.get("platform") if action.get("platform") in PLATFORMS else s.get("platform", "unspecified")
        region = action.get("region") if action.get("region") in REGION_LABELS else s.get("region", "all")
        try:
            size = max(1_000, min(int(action.get("size") or s.get("size") or DEFAULT_SIZE), MAX_SIZE))
        except (TypeError, ValueError):
            size = DEFAULT_SIZE
        await start_test(ctx, sender, clean_content(content), platform, region, size)

    elif kind == "setup":
        session = new_session(ctx, sender, content=s.get("content"), platform=s.get("platform", "unspecified"),
                              region=s.get("region", "all"), size=s.get("size", DEFAULT_SIZE), kind="setup")
        remember_latest(ctx, sender, "setup", session)
        await ctx.send(sender, card_message(
            "ℹ️ Pick a platform, target region and crowd size, then run the test again.",
            setup_card(session, has_content=bool(s.get("content"))), terminal=False))

    elif kind == "retest":
        if not rewrite_ok(s.get("rewrite", "")):
            await notice(ctx, sender, "There's no rewrite to test for that result yet.")
            return
        if s.get("retest"):  # one retest per result: repeated (or auto-)clicks must not start more runs
            ctx.logger.warning(f"[{s['id']}] ignored repeat retest click")
            await notice(ctx, sender, "That rewrite has already been tested; the before/after card is above.")
            return
        child = await start_test(ctx, sender, s["rewrite"], s.get("platform", "unspecified"),
                                 s.get("region", "all"), s.get("size", DEFAULT_SIZE), parent=s["id"])
        if child:
            s["retest"] = child
            ctx.storage.set(s["id"], s)

    elif kind == "regions":
        if not s.get("regions"):
            await ctx.send(sender, text_message("No regional breakdown is available for that result."))
            return
        text = "🗺️ Regional panels:\n" + "\n".join(f"{r['label']} ({r['n']} personas): {pct_line(r)}" for r in s["regions"])
        await ctx.send(sender, card_message(text, regions_card(s), terminal=True))


# ---- Chat protocol (ASI:One <-> orchestrator) ------------------------------
@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=now(), acknowledged_msg_id=msg.msg_id))

    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if not text:
        return  # e.g. StartSessionContent only
    ctx.logger.info(f"message from {sender}: {text[:LOG_PREVIEW]!r}")

    if any(marker in text for marker in ECHO_MARKERS):
        ctx.logger.warning("ignored: looks like one of our own replies")
        return
    if CHATTER_RE.search(strip_mentions(text)):
        ctx.logger.warning("ignored: conversational reply, not content to test")
        return

    action = parse_action(text, ctx.storage.get(f"latest:{sender}"))
    if action:
        await handle_action(ctx, sender, action)
        return

    body = strip_mentions(text)
    if len(body) < 15 or HELP_RE.search(body):
        session = new_session(ctx, sender, kind="setup", platform="unspecified", region="all", size=DEFAULT_SIZE)
        remember_latest(ctx, sender, "setup", session)
        await ctx.send(sender, card_message(
            "Hi, I'm Crowdcast 👋 Paste the startup pitch, Instagram post or ad you want to test, "
            "and I'll drop it into a simulated crowd. Add 'crowd 100k' to change the crowd size (default 10k).",
            setup_card(session, has_content=False), terminal=False))
        return

    await start_test(ctx, sender, clean_content(text), "unspecified", "all", parse_size(text))


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


# ---- Pipeline: crowds (parallel) -> simulation -> rewrite -> user ----------
REGION_OF = {address: region for region, address in CROWD_AGENTS.items()}


async def send_to_simulation(ctx: Context, session: str, s: dict):
    s["dispatched"] = True
    s["crowds"] = len(s["batches"])
    s["regions"] = summarise_regions(s["batches"])
    reactions = merge_reactions([b["reactions"] for b in s["batches"]], session)
    s["batches"] = []
    ctx.storage.set(session, s)
    await ctx.send(
        SIM_ADDRESS,
        SimRequest(session=session, reactions_json=json.dumps(reactions), size=s["size"]),
    )


@agent.on_message(model=CrowdResult)
async def on_crowd(ctx: Context, sender: str, msg: CrowdResult):
    s = ctx.storage.get(msg.session)
    if not s or s.get("dispatched") or sender not in s.get("pending", []):
        return
    s["pending"].remove(sender)
    s["batches"].append({"region": REGION_OF[sender], "reactions": json.loads(msg.reactions_json)})
    ctx.storage.set(msg.session, s)
    ctx.logger.info(
        f"[{msg.session}] crowd {len(s['batches'])}/{len(CROWD_ADDRESSES)} reacted"
    )
    if not s["pending"]:
        await send_to_simulation(ctx, msg.session, s)


@agent.on_interval(period=5.0)
async def check_timeouts(ctx: Context):
    """Don't let one slow or stopped crowd agent hold up the report."""
    due, keep = [], []
    for session in ctx.storage.get("active") or []:
        s = ctx.storage.get(session)
        if not s or s.get("dispatched"):
            continue
        if time.time() - s["started"] > CROWD_TIMEOUT_S:
            due.append((session, s))
        else:
            keep.append(session)
    ctx.storage.set("active", keep)
    for session, s in due:
        if s["batches"]:
            ctx.logger.warning(
                f"[{session}] timed out waiting for {len(s['pending'])} crowd agent(s); "
                f"going ahead with {len(s['batches'])}"
            )
            await send_to_simulation(ctx, session, s)
        else:
            s["dispatched"] = True
            ctx.storage.set(session, s)
            finish_running(ctx, s["user"], session)
            ctx.logger.warning(f"[{session}] no crowd agent answered")
            await ctx.send(
                s["user"],
                text_message("ℹ️ Sorry, the crowd didn't respond in time. Please try again in a minute."),
            )


@agent.on_message(model=SimResult)
async def on_sim(ctx: Context, sender: str, msg: SimResult):
    s = ctx.storage.get(msg.session)
    if not s:
        return
    stats = json.loads(msg.stats_json)
    stats["crowd_agents"] = s.get("crowds", 1)
    s["stats"] = json.dumps(stats)
    ctx.storage.set(msg.session, s)
    ctx.logger.info(f"[{msg.session}] simulation done")

    if s.get("kind") == "retest":
        finish_running(ctx, s["user"], msg.session)
        parent = ctx.storage.get(s["parent"]) or {}
        if "stats" not in parent:
            return
        before = json.loads(parent["stats"])
        ctx.logger.info(f"[{msg.session}] sending before/after to user")
        text = (f"🔬 Original vs rewrite: {verdict(before, stats)}" if USE_CARDS
                else build_compare_report(before, stats, parent["content"], s["content"]))
        await ctx.send(s["user"], card_message(
            text, compare_card(before, stats, parent["content"], s["content"], msg.session), terminal=False))
        return

    await ctx.send(
        REWRITE_ADDRESS,
        RewriteRequest(
            session=msg.session,
            content=for_crowd(s["content"], s.get("platform", "unspecified")),
            objections_json=json.dumps(stats.get("objections", [])),
        ),
    )


@agent.on_message(model=RewriteResult)
async def on_rewrite(ctx: Context, sender: str, msg: RewriteResult):
    s = ctx.storage.get(msg.session)
    if not s or "stats" not in s:
        return
    stats = json.loads(s["stats"])
    rewrite = strip_format_line(msg.rewrite)
    s["rewrite"] = rewrite
    ctx.storage.set(msg.session, s)
    finish_running(ctx, s["user"], msg.session)
    remember_latest(ctx, s["user"], "result", msg.session)
    ctx.logger.info(f"[{msg.session}] sending report to user")
    text = (f"📣 {stats['size']:,} simulated impressions: ❤️ {stats['love_pct']}% love · "
            f"😐 {stats['ignore_pct']}% ignore · 👎 {stats['hate_pct']}% hate. "
            f"Charts, objections and a rewrite are in the card below." if USE_CARDS
            else build_report(s, stats, rewrite))
    await ctx.send(s["user"], card_message(text, results_card(s, stats, rewrite), terminal=False))


agent.include(chat_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
