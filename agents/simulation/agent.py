"""
Crowdcast — SIMULATION agent (hosted on Agentverse).

Pure Python, no LLM. Takes the real persona reactions and statistically
expands them to N impressions:
  1. split N impressions across audience segments by weight
  2. each segment's love/ignore/hate split = its personas' reactions
     (smoothed so 1-2 personas don't give 0% / 100%)
  3. people who love it AND would share create a second wave of reach
On stage: "a weighted statistical expansion of real persona responses".
"""

import json
from collections import Counter

from uagents import Agent, Context, Model


# ---- Shared message models (keep identical in every agent) -----------------
class SimRequest(Model):
    session: str
    reactions_json: str
    size: int


class SimResult(Model):
    session: str
    stats_json: str


agent = Agent()

# share of the audience that sees the content first (sums to 1.0)
SEGMENTS = {
    "students": {"label": "Students (18-22)", "weight": 0.30},
    "young_professionals": {"label": "Young professionals (25-32)", "weight": 0.28},
    "parents": {"label": "Parents (35-50)", "weight": 0.15},
    "founders": {"label": "Founders & business owners", "weight": 0.10},
    "creators": {"label": "Creators", "weight": 0.10},
    "seniors": {"label": "Seniors (60+)", "weight": 0.07},
}
SHARE_RATE_IF_WILLING = 0.12   # of lovers who said they'd share, how many actually do
REACH_PER_SHARE = 9            # new people reached per share (second wave)
SMOOTHING = 0.5                # Laplace-style smoothing per reaction bucket


def simulate(reactions: list, size: int) -> dict:
    by_seg = {}
    for r in reactions:
        by_seg.setdefault(r["segment"], []).append(r)

    present = {k: v for k, v in SEGMENTS.items() if k in by_seg}
    total_w = sum(v["weight"] for v in present.values()) or 1.0

    segments, totals, shares = [], Counter(), 0
    for key, meta in present.items():
        people = by_seg[key]
        n = len(people)
        counts = Counter(p["reaction"] for p in people)
        seen = round(size * meta["weight"] / total_w)
        dist = {
            r: (counts.get(r, 0) + SMOOTHING) / (n + 3 * SMOOTHING)
            for r in ("love", "ignore", "hate")
        }
        seg_counts = {r: round(seen * dist[r]) for r in dist}
        willing = sum(1 for p in people if p["reaction"] == "love" and p["would_share"])
        willing_frac = willing / max(counts.get("love", 0), 1)
        seg_shares = round(seg_counts["love"] * willing_frac * SHARE_RATE_IF_WILLING)

        totals.update(seg_counts)
        shares += seg_shares
        segments.append({
            "label": meta["label"],
            "seen": seen,
            "love_pct": round(100 * dist["love"]),
            "ignore_pct": round(100 * dist["ignore"]),
            "hate_pct": round(100 * dist["hate"]),
        })

    segments.sort(key=lambda s: (s["love_pct"], -s["hate_pct"]), reverse=True)
    seen_total = sum(totals.values()) or 1

    # objections: haters first, then ignorers; dedupe, keep top 5
    objections, seen_obj = [], set()
    for bucket in ("hate", "ignore", "love"):
        for p in reactions:
            o = p.get("objection", "").strip()
            if p["reaction"] == bucket and o and o.lower() not in seen_obj:
                seen_obj.add(o.lower())
                objections.append(o)
    quotes = [
        f"{p['reason']} — {p['name']}, {p['age']}, {p['city']}"
        for p in reactions if p.get("reason") and p["reason"] != "(no response)"
    ][:3]

    return {
        "size": size,
        "personas": len(reactions),
        "love_pct": round(100 * totals["love"] / seen_total),
        "ignore_pct": round(100 * totals["ignore"] / seen_total),
        "hate_pct": round(100 * totals["hate"] / seen_total),
        "shares": shares,
        "total_reach": size + shares * REACH_PER_SHARE,
        "segments": segments,
        "objections": objections[:5],
        "quotes": quotes,
    }


@agent.on_message(model=SimRequest)
async def on_request(ctx: Context, sender: str, msg: SimRequest):
    reactions = json.loads(msg.reactions_json)
    stats = simulate(reactions, msg.size)
    ctx.logger.info(f"[{msg.session}] simulated {msg.size} impressions")
    await ctx.send(sender, SimResult(session=msg.session, stats_json=json.dumps(stats)))


if __name__ == "__main__":
    agent.run()
