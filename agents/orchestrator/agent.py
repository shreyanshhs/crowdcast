"""
Crowdcast — ORCHESTRATOR agent (hosted on Agentverse).

This is the only agent users talk to in ASI:One. It:
  1. receives the content (pitch / post / ad) through the Agent Chat Protocol
  2. sends it to the Crowd agent      -> real persona reactions (ASI:One LLM)
  3. sends reactions to Simulation     -> weighted expansion to N impressions
  4. sends objections to Rewrite agent -> improved version (ASI:One LLM)
  5. replies to the user with a text report + an ASI:One interactive card

Hosted-agent rules: no global state (use ctx.storage), no custom seed,
message model classes must be IDENTICAL in every agent file.
"""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)

# ---- Paste the addresses of your other 3 hosted agents here ---------------
CROWD_ADDRESS = "agent1q_REPLACE_WITH_CROWD_AGENT_ADDRESS"
SIM_ADDRESS = "agent1q_REPLACE_WITH_SIMULATION_AGENT_ADDRESS"
REWRITE_ADDRESS = "agent1q_REPLACE_WITH_REWRITE_AGENT_ADDRESS"

DEFAULT_SIZE = 10_000
MAX_SIZE = 100_000
USE_CARDS = True  # set False if the card ever causes trouble


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


# ---- Helpers ---------------------------------------------------------------
def now():
    return datetime.now(timezone.utc)


def text_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=now(),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def parse_size(text: str) -> int:
    """Pick up a crowd size like 'crowd 100k' or 'audience 50k'. Default 10k."""
    m = re.search(r"(?:crowd|audience|impressions?|people)\D{0,12}(\d+)\s*k\b", text.lower())
    if not m:
        m = re.search(r"\b(\d+)\s*k\s*(?:crowd|audience|impressions?|people)", text.lower())
    if m:
        return max(1_000, min(int(m.group(1)) * 1000, MAX_SIZE))
    return DEFAULT_SIZE


def build_report(stats: dict, rewrite: str) -> str:
    lines = [
        f"📣 Crowdcast results — {stats['size']:,} simulated impressions",
        "",
        f"❤️ Love {stats['love_pct']}%  ·  😐 Ignore {stats['ignore_pct']}%  ·  👎 Hate {stats['hate_pct']}%",
        f"🔁 {stats['shares']:,} shares → projected total reach {stats['total_reach']:,}",
        "",
        "Segments (best first):",
    ]
    for s in stats["segments"]:
        lines.append(
            f"• {s['label']}: {s['love_pct']}% love, {s['ignore_pct']}% ignore, "
            f"{s['hate_pct']}% hate ({s['seen']:,} saw it)"
        )
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
        f"Method: {stats['personas']} AI persona agents each reacted to your content; "
        f"their reactions were weighted by audience segment and statistically "
        f"expanded to {stats['size']:,} impressions.",
    ]
    return "\n".join(lines)


def build_card(stats: dict, rewrite: str) -> dict:
    """ASI:One custom interactive card (informational, no input needed)."""
    seg_items = [
        {
            "children": [
                {"type": "text", "value": s["label"], "style": "emphasis"},
                {
                    "type": "text",
                    "value": f"{s['love_pct']}% love · {s['ignore_pct']}% ignore · "
                    f"{s['hate_pct']}% hate · {s['seen']:,} saw it",
                    "style": "muted",
                },
            ]
        }
        for s in stats["segments"]
    ]
    objection_items = [
        {"children": [{"type": "text", "value": f"{i}. {o}"}]}
        for i, o in enumerate(stats.get("objections", []), 1)
    ]
    children = [
        {
            "type": "group",
            "direction": "row",
            "gap": 8,
            "children": [
                {"type": "badge", "label": f"❤️ {stats['love_pct']}% love", "variant": "success"},
                {"type": "badge", "label": f"😐 {stats['ignore_pct']}% ignore", "variant": "info"},
                {"type": "badge", "label": f"👎 {stats['hate_pct']}% hate", "variant": "warning"},
            ],
        },
        {
            "type": "text",
            "value": f"{stats['shares']:,} shares → projected reach {stats['total_reach']:,}",
            "style": "muted",
        },
        {"type": "divider"},
        {"type": "heading", "value": "Segments", "level": 3},
        {"type": "list", "items": seg_items},
    ]
    if objection_items:
        children += [
            {"type": "heading", "value": "Top objections", "level": 3},
            {"type": "list", "items": objection_items},
        ]
    children += [
        {"type": "divider"},
        {"type": "heading", "value": "Rewritten version", "level": 3},
        {"type": "text", "value": rewrite[:1500]},
    ]
    return {
        "root": {
            "type": "section",
            "title": "Crowdcast results",
            "subtitle": f"{stats['size']:,} simulated impressions from {stats['personas']} persona agents",
            "children": children,
        }
    }


def report_message(stats: dict, rewrite: str) -> ChatMessage:
    content = [TextContent(type="text", text=build_report(stats, rewrite))]
    if USE_CARDS:
        content.append(
            MetadataContent(
                type="metadata",
                metadata={
                    "card_protocol_version": "1",
                    "requires_card_interaction": "true",
                    "is_terminal": "true",
                    "card_kind": "custom",
                    "card_payload": json.dumps(build_card(stats, rewrite)),
                    "preferred_drawer_width_px": "600",
                },
            )
        )
    return ChatMessage(timestamp=now(), msg_id=uuid4(), content=content)


# ---- Chat protocol (ASI:One <-> orchestrator) ------------------------------
@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=now(), acknowledged_msg_id=msg.msg_id))

    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if not text:
        return  # e.g. StartSessionContent only

    if len(text) < 15:
        await ctx.send(
            sender,
            text_message(
                "Hi, I'm Crowdcast 👋 Paste the startup pitch, Instagram post or ad you "
                "want to test, and I'll drop it into a simulated crowd. "
                "Add 'crowd 100k' to change the crowd size (default 10k)."
            ),
        )
        return

    session = uuid4().hex
    ctx.storage.set(session, {"user": sender, "content": text, "size": parse_size(text)})
    ctx.logger.info(f"[{session}] new test from {sender}")
    await ctx.send(CROWD_ADDRESS, CrowdRequest(session=session, content=text))


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


# ---- Pipeline: crowd -> simulation -> rewrite -> user ----------------------
@agent.on_message(model=CrowdResult)
async def on_crowd(ctx: Context, sender: str, msg: CrowdResult):
    s = ctx.storage.get(msg.session)
    if not s:
        return
    ctx.logger.info(f"[{msg.session}] crowd reacted")
    await ctx.send(
        SIM_ADDRESS,
        SimRequest(session=msg.session, reactions_json=msg.reactions_json, size=s["size"]),
    )


@agent.on_message(model=SimResult)
async def on_sim(ctx: Context, sender: str, msg: SimResult):
    s = ctx.storage.get(msg.session)
    if not s:
        return
    s["stats"] = msg.stats_json
    ctx.storage.set(msg.session, s)
    stats = json.loads(msg.stats_json)
    ctx.logger.info(f"[{msg.session}] simulation done")
    await ctx.send(
        REWRITE_ADDRESS,
        RewriteRequest(
            session=msg.session,
            content=s["content"],
            objections_json=json.dumps(stats.get("objections", [])),
        ),
    )


@agent.on_message(model=RewriteResult)
async def on_rewrite(ctx: Context, sender: str, msg: RewriteResult):
    s = ctx.storage.get(msg.session)
    if not s or "stats" not in s:
        return
    stats = json.loads(s["stats"])
    ctx.logger.info(f"[{msg.session}] sending report to user")
    await ctx.send(s["user"], report_message(stats, msg.rewrite))


agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
