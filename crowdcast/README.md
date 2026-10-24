# Crowdcast — test anything on a simulated crowd

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Built for **Fetch-A-Thon 3.0** (Fetch.ai Innovation Lab, Presidency University, Bengaluru).

## Problem
Founders, creators and marketers post pitches, reels and ads blind. Real audience testing is slow and expensive, so most content ships without ever being tested.

## What it does
In **ASI:One**, paste a startup pitch, Instagram post or ad and ask how people will react. Crowdcast drops it into a virtual crowd and returns:

- % of people who **love / ignore / hate** it across 10,000 (up to 100,000) simulated impressions
- which **audience segments** react best
- the **top objections**, plus real quotes from persona agents
- a **rewritten version** that answers those objections

The whole workflow runs inside an ASI:One conversation, with results shown as an **ASI:One interactive card**.

## Target users
Startup founders, student hackathon teams, Instagram creators, small D2C brands and marketers who want feedback before they post.

## How the "crowd" works (honest method)
1. A panel of distinct LLM persona agents (age, city, interests, mood) each genuinely react to the content via the ASI:One LLM.
2. The simulation agent weights those reactions by audience segment and **statistically expands** them to N impressions, then applies a simple share model (people who love it and would share it create a second wave of reach).

The 10k/100k numbers are a weighted statistical expansion of real persona responses, not 100k separate LLM calls.

## Architecture
```
User (ASI:One chat)
   │  Agent Chat Protocol
   ▼
Orchestrator ──► Crowd agent      (persona reactions, ASI:One LLM)
     │       ──► Simulation agent (weighted expansion + share model)
     │       ──► Rewrite agent    (improved version, ASI:One LLM)
     ▼
Report + interactive card back to the user
```

## Agents (all hosted on Agentverse)
| Agent | Address | Agentverse profile |
|---|---|---|
| crowdcast-orchestrator | `agent1q...` | link |
| crowdcast-crowd | `agent1q...` | link |
| crowdcast-simulation | `agent1q...` | link |
| crowdcast-rewrite | `agent1q...` | link |

## Try it
1. Open [ASI:One](https://asi1.ai/chat) and mention the orchestrator agent.
2. Paste your content, e.g. *"How will people react to this Instagram post: …"*
3. Optional: add `crowd 100k` to change the crowd size (default 10k).

Shared demo chat: _link_ · Demo video: _link_

## Run / deploy it yourself
1. Create 4 hosted agents on [Agentverse](https://agentverse.ai) (Agents → + Launch an Agent → Blank).
2. Paste each file from `agents/<name>/agent.py` into its agent's Build tab.
3. Add the secret `ASI1_API_KEY` (from [asi1.ai](https://asi1.ai)) to the crowd and rewrite agents.
4. Paste the crowd, simulation and rewrite addresses into the top of `agents/orchestrator/agent.py`.
5. Start all 4 agents, then chat with the orchestrator.

Extra resources: ASI:One API key, Agentverse account. No other services needed.

## Team
- Agent lead, orchestrator & integration
- LLM prompts (crowd + rewrite)
- Agentverse deployment & hosting
- Pitch, presentation & README
