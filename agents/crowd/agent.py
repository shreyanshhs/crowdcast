"""
Crowdcast — CROWD agent (hosted on Agentverse).

One regional persona panel. The same code runs as 5 hosted agents
(north / south / west / east / central India); the orchestrator asks all
of them in parallel and merges the answers. Each persona is a distinct
audience member (age, city, interests, mood), generated from a fixed
seed so a region's panel is the same on every run. The ASI:One LLM
role-plays every persona and returns a structured reaction. Personas are
batched into a few LLM calls to stay fast and inside rate limits.

Secrets needed on Agentverse (Build tab -> Agent Secrets):
  ASI1_API_KEY  - your ASI:One API key
  CROWD_REGION  - north | south | west | east | central
"""

import json
import os
import random
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

REGIONS = {
    "north": {
        "cities": ["Delhi", "Gurugram", "Noida", "Lucknow", "Jaipur", "Chandigarh", "Dehradun"],
        "names": ["Aarav", "Ishita", "Kabir", "Simran", "Rohit", "Neha", "Harpreet", "Ankit", "Pooja",
                  "Vikram", "Meenal", "Sahil", "Tanya", "Yash", "Ritu", "Gurpreet", "Aditi", "Manish"],
    },
    "south": {
        "cities": ["Bengaluru", "Chennai", "Hyderabad", "Kochi", "Mysuru", "Coimbatore", "Visakhapatnam"],
        "names": ["Ananya", "Karthik", "Divya", "Arjun", "Lakshmi", "Rahul", "Sneha", "Vignesh", "Priya",
                  "Suresh", "Meera", "Naveen", "Kavya", "Farhan", "Deepa", "Aditya", "Revathi", "Sai"],
    },
    "west": {
        "cities": ["Mumbai", "Pune", "Ahmedabad", "Surat", "Goa", "Nashik", "Vadodara"],
        "names": ["Riya", "Omkar", "Krisha", "Parth", "Mitali", "Jay", "Siddharth", "Hetal", "Rohan",
                  "Tanvi", "Dhruv", "Shruti", "Nikhil", "Isha", "Mihir", "Fatima", "Kunal", "Zoya"],
    },
    "east": {
        "cities": ["Kolkata", "Bhubaneswar", "Patna", "Guwahati", "Ranchi", "Siliguri", "Shillong"],
        "names": ["Arpita", "Sourav", "Debolina", "Abhishek", "Priyanka", "Moumita", "Subham", "Ankita",
                  "Bikash", "Rupam", "Pallavi", "Imran", "Tania", "Arindam", "Nikita", "Ritwik", "Barsha", "Sayan"],
    },
    "central": {
        "cities": ["Bhopal", "Indore", "Nagpur", "Raipur", "Jabalpur", "Gwalior", "Kanpur"],
        "names": ["Aman", "Shreya", "Harsh", "Nidhi", "Ravi", "Kritika", "Ayush", "Saurabh", "Muskan",
                  "Deepak", "Anjali", "Vivek", "Sakshi", "Gaurav", "Komal", "Rajat", "Swati", "Tushar"],
    },
}

# segment ids must match SEGMENTS in the simulation agent; counts roughly follow its weights
SEGMENT_MIX = [
    ("students", 5, (18, 22), ["coding", "cricket", "gaming", "memes", "Instagram reels", "anime", "fashion", "exam prep", "college fests"]),
    ("young_professionals", 5, (24, 32), ["startups", "personal finance", "fitness", "travel", "cafes", "productivity", "crypto", "OTT shows", "side hustles"]),
    ("parents", 3, (35, 50), ["kids' education", "health", "budgeting", "news", "family trips", "home cooking", "cricket"]),
    ("founders", 2, (28, 48), ["SaaS", "growth hacking", "VC news", "D2C brands", "operations", "hiring", "unit economics"]),
    ("creators", 2, (19, 30), ["YouTube", "music production", "brand deals", "photography", "short-form video", "streetwear"]),
    ("seniors", 1, (60, 72), ["devotional music", "cooking", "grandkids", "morning walks", "TV news", "gardening"]),
]
MOODS = [
    "curious but broke", "short attention span", "trend-driven", "busy and sceptical", "ad-fatigued",
    "early adopter", "careful with money", "distrusts new apps", "looking for an edge", "ROI-focused",
    "prefers simple language", "wants authenticity", "easily excited", "privacy-conscious",
    "value-for-money hunter", "loyal to brands they know",
]


def build_personas(region: str) -> list:
    """Deterministic panel for one region: same seed -> same 18 people every run."""
    rng = random.Random(f"crowdcast-{region}")
    pool = REGIONS[region]
    names = rng.sample(pool["names"], sum(n for _, n, _, _ in SEGMENT_MIX))
    personas = []
    for segment, count, (lo, hi), interests in SEGMENT_MIX:
        for _ in range(count):
            personas.append({
                "id": len(personas) + 1,
                "name": names[len(personas)],
                "age": rng.randint(lo, hi),
                "city": rng.choice(pool["cities"]),
                "segment": segment,
                "interests": ", ".join(rng.sample(interests, 3)),
                "mood": rng.choice(MOODS),
            })
    return personas


REGION = os.environ.get("CROWD_REGION", "").strip().lower()
if REGION not in REGIONS:
    REGION = "south"
PERSONAS = build_personas(REGION)

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


@agent.on_event("startup")
async def on_startup(ctx: Context):
    if os.environ.get("CROWD_REGION", "").strip().lower() != REGION:
        ctx.logger.warning(f"CROWD_REGION secret missing or unknown; falling back to '{REGION}'")
    ctx.logger.info(f"region {REGION}: {len(PERSONAS)} personas")


@agent.on_message(model=CrowdRequest)
async def on_request(ctx: Context, sender: str, msg: CrowdRequest):
    ctx.logger.info(f"[{msg.session}] {REGION}: {len(PERSONAS)} personas reacting")
    reactions = []
    for i in range(0, len(PERSONAS), BATCH_SIZE):
        reactions += react(msg.content, PERSONAS[i : i + BATCH_SIZE], ctx)
    await ctx.send(sender, CrowdResult(session=msg.session, reactions_json=json.dumps(reactions)))


if __name__ == "__main__":
    agent.run()
