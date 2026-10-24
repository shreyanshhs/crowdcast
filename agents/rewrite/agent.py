"""
Crowdcast — REWRITE agent (hosted on Agentverse).

Takes the original content + the crowd's top objections and asks the
ASI:One LLM for an improved version that answers those objections.

Secret needed on Agentverse (Build tab -> Agent Secrets): ASI1_API_KEY
"""

import json
import os

import requests
from uagents import Agent, Context, Model

ASI1_URL = "https://api.asi1.ai/v1/chat/completions"
MODEL = "asi1"


# ---- Shared message models (keep identical in every agent) -----------------
class RewriteRequest(Model):
    session: str
    content: str
    objections_json: str


class RewriteResult(Model):
    session: str
    rewrite: str


agent = Agent()


def rewrite(content: str, objections: list) -> str:
    obj = "\n".join(f"- {o}" for o in objections) or "- (none given)"
    prompt = (
        f"ORIGINAL CONTENT:\n\"\"\"\n{content}\n\"\"\"\n\n"
        f"TOP AUDIENCE OBJECTIONS:\n{obj}\n\n"
        "Rewrite the content so it answers these objections and would get more "
        "people to love and share it. Keep the same format and roughly the same "
        "length, keep the core message. Return ONLY the rewritten content."
    )
    r = requests.post(
        ASI1_URL,
        headers={
            "Authorization": f"Bearer {os.environ.get('ASI1_API_KEY', '')}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "temperature": 0.7,
            "messages": [
                {"role": "system", "content": "You are an expert copywriter for Indian audiences."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


@agent.on_message(model=RewriteRequest)
async def on_request(ctx: Context, sender: str, msg: RewriteRequest):
    try:
        text = rewrite(msg.content, json.loads(msg.objections_json))
    except Exception as e:  # noqa: BLE001
        ctx.logger.warning(f"[{msg.session}] rewrite failed: {e}")
        text = "(Rewrite unavailable right now — try again in a minute.)"
    await ctx.send(sender, RewriteResult(session=msg.session, rewrite=text))


if __name__ == "__main__":
    agent.run()
