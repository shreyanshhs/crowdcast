"""
Crowdcast — REWRITE agent (hosted on Agentverse).

Takes the original content + the crowd's top objections and asks the
ASI:One LLM for an improved version that answers those objections.

The rewrite may only rephrase and reorder facts that are already in the
original. A checker compares the two: new numbers, guarantees, promotions,
superlatives and quality claims are rejected, the LLM gets one retry with
the violations listed, and if it still adds claims we return "unavailable"
rather than put invented facts in front of the user.

Secret needed on Agentverse (Build tab -> Agent Secrets): ASI1_API_KEY
"""

import json
import os
import re

import requests
from uagents import Agent, Context, Model

ASI1_URL = "https://api.asi1.ai/v1/chat/completions"
MODEL = "asi1"
UNAVAILABLE = "(Rewrite unavailable: I couldn't improve this without adding claims that aren't in your original.)"

SYSTEM_PROMPT = (
    "You are a careful copywriter for Indian audiences. You may only rephrase, reorder, shorten "
    "and clarify facts that are already in the user's original content. Never invent claims, "
    "statistics, numbers, prices, guarantees ('or it's free', money-back), promotions or discounts, "
    "awards, rankings or superlatives ('best', 'most-loved', '#1'), certifications, quality or "
    "hygiene claims ('100% original', 'sanitised', 'premium'), testimonials or partnerships. "
    "If an objection can only be answered with a new fact, do not invent it: address it with "
    "clearer wording of existing facts, or leave it."
)

# words that signal a claim; flagged only when they are not already in the original
CLAIM_WORDS = [
    "free", "guarantee", "guaranteed", "money-back", "refund", "best", "most", "top-rated", "#1",
    "number one", "award", "award-winning", "certified", "official", "authentic", "genuine",
    "original", "premium", "luxury", "sanitised", "sanitized", "hygienic", "cheapest", "lowest",
    "fastest", "trusted", "famous", "leading", "exclusive", "limited", "hand-checked", "checked",
    "cleaned", "verified", "quality", "100%",
]


# ---- Shared message models (keep identical in every agent) -----------------
class RewriteRequest(Model):
    session: str
    content: str
    objections_json: str


class RewriteResult(Model):
    session: str
    rewrite: str


agent = Agent()


def _numbers(text: str) -> set:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?%?", text)}


def _has(word: str, text: str) -> bool:
    if not word[0].isalnum() or not word[-1].isalnum():
        return word in text
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w])", text) is not None


def unsupported_claims(original: str, rewritten: str) -> list:
    """Claims in the rewrite that the original doesn't support (numbers and claim words)."""
    o, r = original.lower(), rewritten.lower()
    found = [f"number {n}" for n in sorted(_numbers(r) - _numbers(o))]
    found += [f"'{w}'" for w in CLAIM_WORDS if _has(w, r) and not _has(w, o)]
    return found


def ask_llm(prompt: str) -> str:
    r = requests.post(
        ASI1_URL,
        headers={
            "Authorization": f"Bearer {os.environ.get('ASI1_API_KEY', '')}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def rewrite(content: str, objections: list, ctx: Context = None) -> str:
    obj = "\n".join(f"- {o}" for o in objections) or "- (none given)"
    prompt = (
        f"ORIGINAL CONTENT:\n\"\"\"\n{content}\n\"\"\"\n\n"
        f"TOP AUDIENCE OBJECTIONS:\n{obj}\n\n"
        "Rewrite the content so it answers these objections as well as the existing facts allow. "
        "Keep the same format and roughly the same length, keep the core message, and use only "
        "facts that are in the original. Return ONLY the rewritten content."
    )
    text = ask_llm(prompt)
    problems = unsupported_claims(content, text)
    if not problems:
        return text
    if ctx:
        ctx.logger.warning(f"rewrite added claims {problems}; retrying")
    text = ask_llm(
        prompt + "\n\nYour previous draft added things that are NOT in the original: "
        + ", ".join(problems) + ". Remove every one of them and do not add any other new facts."
    )
    problems = unsupported_claims(content, text)
    if problems:
        if ctx:
            ctx.logger.warning(f"rewrite still added claims {problems}; returning unavailable")
        return UNAVAILABLE
    return text


@agent.on_message(model=RewriteRequest)
async def on_request(ctx: Context, sender: str, msg: RewriteRequest):
    try:
        text = rewrite(msg.content, json.loads(msg.objections_json), ctx)
    except Exception as e:  # noqa: BLE001
        ctx.logger.warning(f"[{msg.session}] rewrite failed: {e}")
        text = "(Rewrite unavailable right now — try again in a minute.)"
    await ctx.send(sender, RewriteResult(session=msg.session, rewrite=text))


if __name__ == "__main__":
    agent.run()
