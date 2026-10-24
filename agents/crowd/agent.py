"""
Crowdcast — CROWD agent (hosted on Agentverse).

Holds the persona panel. Each persona is a distinct audience member
(age, city, interests, mood). The ASI:One LLM role-plays every persona
and returns a structured reaction. Personas are batched into a few LLM
calls to stay fast and inside rate limits.

Secret needed on Agentverse (Build tab -> Agent Secrets): ASI1_API_KEY
"""

import json
import os
import re

import requests
from uagents import Agent, Context, Model

ASI1_URL = "https://api.asi1.ai/v1/chat/completions"
MODEL = "asi1-mini"   # fast; switch to "asi1" if output quality is weak
BATCH_SIZE = 6        # personas per LLM call


# ---- Shared message models (keep identical in every agent) -----------------
class CrowdRequest(Model):
    session: str
    content: str


class CrowdResult(Model):
    session: str
    reactions_json: str


agent = Agent()

# segment ids must match SEGMENTS in the simulation agent
PERSONAS = [
    {"id": 1, "name": "Ananya", "age": 19, "city": "Bengaluru", "segment": "students", "interests": "coding, anime, hackathons", "mood": "curious but broke"},
    {"id": 2, "name": "Rohan", "age": 21, "city": "Pune", "segment": "students", "interests": "cricket, gaming, memes", "mood": "short attention span"},
    {"id": 3, "name": "Fatima", "age": 20, "city": "Hyderabad", "segment": "students", "interests": "design, Instagram reels, fashion", "mood": "trend-driven"},
    {"id": 4, "name": "Karthik", "age": 27, "city": "Chennai", "segment": "young_professionals", "interests": "startups, finance, fitness", "mood": "busy and sceptical"},
    {"id": 5, "name": "Priya", "age": 29, "city": "Mumbai", "segment": "young_professionals", "interests": "marketing, travel, cafes", "mood": "ad-fatigued"},
    {"id": 6, "name": "Arjun", "age": 31, "city": "Gurugram", "segment": "young_professionals", "interests": "tech, productivity, crypto", "mood": "early adopter"},
    {"id": 7, "name": "Meera", "age": 38, "city": "Kochi", "segment": "parents", "interests": "kids' education, health, budgeting", "mood": "careful with money"},
    {"id": 8, "name": "Suresh", "age": 45, "city": "Lucknow", "segment": "parents", "interests": "news, family, cricket", "mood": "distrusts new apps"},
    {"id": 9, "name": "Neha", "age": 34, "city": "Bengaluru", "segment": "founders", "interests": "SaaS, growth hacking, VC news", "mood": "looking for an edge"},
    {"id": 10, "name": "Vikram", "age": 42, "city": "Delhi", "segment": "founders", "interests": "D2C brands, operations, hiring", "mood": "ROI-focused"},
    {"id": 11, "name": "Lakshmi", "age": 63, "city": "Mysuru", "segment": "seniors", "interests": "devotional music, cooking, grandkids", "mood": "prefers simple language"},
    {"id": 12, "name": "Imran", "age": 24, "city": "Kolkata", "segment": "creators", "interests": "YouTube, music production, brand deals", "mood": "wants authenticity"},
]

SYSTEM_PROMPT = (
    "You simulate members of an audience reacting to a piece of content. "
    "Stay strictly in character for each persona: their age, city, interests "
    "and mood decide how they react. Be honest; many real people ignore or "
    "dislike content. Respond with ONLY a JSON array, no prose, no markdown."
)


def build_prompt(content: str, personas: list) -> str:
    people = "\n".join(
        f"- id {p['id']}: {p['name']}, {p['age']}, {p['city']}; interests: {p['interests']}; mood: {p['mood']}"
        for p in personas
    )
    return (
        f"CONTENT:\n\"\"\"\n{content}\n\"\"\"\n\n"
        f"PERSONAS:\n{people}\n\n"
        "For EACH persona return an object with keys:\n"
        '  "id" (int), "reaction" ("love" | "ignore" | "hate"),\n'
        '  "would_share" (true/false), "reason" (max 15 words, first person),\n'
        '  "objection" (their main criticism, max 12 words, or "" if none).\n'
        "Return a JSON array with one object per persona."
    )


def extract_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array in LLM output")
    return json.loads(text[start : end + 1])


def ask_llm(content: str, personas: list) -> list:
    headers = {
        "Authorization": f"Bearer {os.environ.get('ASI1_API_KEY', '')}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "temperature": 0.8,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(content, personas)},
        ],
    }
    r = requests.post(ASI1_URL, headers=headers, json=body, timeout=90)
    r.raise_for_status()
    return extract_json_array(r.json()["choices"][0]["message"]["content"])


def react(content: str, personas: list, ctx: Context) -> list:
    """One batch, with a retry; failed personas are marked 'ignore' so the demo never dies."""
    for attempt in range(2):
        try:
            raw = ask_llm(content, personas)
            by_id = {int(x.get("id", -1)): x for x in raw if isinstance(x, dict)}
            out = []
            for p in personas:
                x = by_id.get(p["id"], {})
                reaction = str(x.get("reaction", "ignore")).lower()
                if reaction not in ("love", "ignore", "hate"):
                    reaction = "ignore"
                out.append({
                    "name": p["name"], "age": p["age"], "city": p["city"],
                    "segment": p["segment"], "reaction": reaction,
                    "would_share": bool(x.get("would_share", False)),
                    "reason": str(x.get("reason", ""))[:160],
                    "objection": str(x.get("objection", ""))[:120],
                })
            return out
        except Exception as e:  # noqa: BLE001
            ctx.logger.warning(f"LLM batch failed (attempt {attempt + 1}): {e}")
    return [
        {"name": p["name"], "age": p["age"], "city": p["city"], "segment": p["segment"],
         "reaction": "ignore", "would_share": False, "reason": "(no response)", "objection": ""}
        for p in personas
    ]


@agent.on_message(model=CrowdRequest)
async def on_request(ctx: Context, sender: str, msg: CrowdRequest):
    ctx.logger.info(f"[{msg.session}] {len(PERSONAS)} personas reacting")
    reactions = []
    for i in range(0, len(PERSONAS), BATCH_SIZE):
        reactions += react(msg.content, PERSONAS[i : i + BATCH_SIZE], ctx)
    await ctx.send(sender, CrowdResult(session=msg.session, reactions_json=json.dumps(reactions)))


if __name__ == "__main__":
    agent.run()
