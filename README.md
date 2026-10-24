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
1. Five regional crowd agents (north, south, west, east, central India) each hold an 18-person panel of distinct personas (age, city, interests, mood); all 90 genuinely react to the content via the ASI:One LLM, in parallel.
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
| crowdcast-orchestrator | `agent1q26xmt9hz654pcx0h5uhqr5ccg5syvd6d3gqsrhynmzetm72j0wlgdrdsj9` | [profile](https://agentverse.ai/agents/details/agent1q26xmt9hz654pcx0h5uhqr5ccg5syvd6d3gqsrhynmzetm72j0wlgdrdsj9/profile) |
| crowdcast-crowd-north | `agent1qw6elpnrg0wqsw59unxnw5ymynypt9wh0g8n47jf5v7250vxwsn872e5saf` | [profile](https://agentverse.ai/agents/details/agent1qw6elpnrg0wqsw59unxnw5ymynypt9wh0g8n47jf5v7250vxwsn872e5saf/profile) |
| crowdcast-crowd-south | `agent1qddxa9d59mzv6ggdmff8d0kd2c5hssdv2w3ufx5kd0uaaxec6tvrzj8u6xh` | [profile](https://agentverse.ai/agents/details/agent1qddxa9d59mzv6ggdmff8d0kd2c5hssdv2w3ufx5kd0uaaxec6tvrzj8u6xh/profile) |
| crowdcast-crowd-west | `agent1q0yyadpxdy50xrfa35r6jltp5mla7pspfa6aqu83e4pwvrtlmepqgpv09dy` | [profile](https://agentverse.ai/agents/details/agent1q0yyadpxdy50xrfa35r6jltp5mla7pspfa6aqu83e4pwvrtlmepqgpv09dy/profile) |
| crowdcast-crowd-east | `agent1q2vtvps6krssghyx7vctv3np6py4022jn5njs4pcdl6hpykjs6y3g97zdng` | [profile](https://agentverse.ai/agents/details/agent1q2vtvps6krssghyx7vctv3np6py4022jn5njs4pcdl6hpykjs6y3g97zdng/profile) |
| crowdcast-crowd-central | `agent1qf98p6vzlwdd3ze43x4q2e3ghptcxyje53khlp60qe64c8a3rfdnwq9yj9d` | [profile](https://agentverse.ai/agents/details/agent1qf98p6vzlwdd3ze43x4q2e3ghptcxyje53khlp60qe64c8a3rfdnwq9yj9d/profile) |
| crowdcast-simulation | `agent1qv4tvuw3sklnf2wuufq7dq6pnkqt8mq9kq03kyyjl0w9p9n44d4xvr6p0h2` | [profile](https://agentverse.ai/agents/details/agent1qv4tvuw3sklnf2wuufq7dq6pnkqt8mq9kq03kyyjl0w9p9n44d4xvr6p0h2/profile) |
| crowdcast-rewrite | `agent1qtumj924vkyrkzw6smrlkrg5ss0tqeevv34l4ehuhg88uttnxa7aq3wwrdl` | [profile](https://agentverse.ai/agents/details/agent1qtumj924vkyrkzw6smrlkrg5ss0tqeevv34l4ehuhg88uttnxa7aq3wwrdl/profile) |

## Try it
1. Open [ASI:One](https://asi1.ai/chat) and mention the orchestrator agent, `@crowdcast`.
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
