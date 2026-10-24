# Crowdcast: pitch script

About 3 minutes spoken. Lines in *italics* are stage directions.

## 1. Hook (15 s)
"Every founder, creator and small brand posts blind. You write a pitch, an Instagram post or an ad, publish it, and only then find out nobody cared. Real audience testing is slow and expensive, so most content never gets tested at all."

## 2. What Crowdcast does (20 s)
"Crowdcast tests your content on a simulated Indian crowd before you post. You paste it into ASI:One, and in about a minute you see who loves it, who ignores it, who hates it and why, plus a rewrite that answers the objections."

## 3. Live demo (75 s)
*Say hi to `@crowdcast`, fill in the setup card (Instagram, South India), Run test.*

"While it runs: five regional crowd agents (north, south, west, east and central India) each hold 18 distinct personas, 90 in total, and all of them react to my post at the same time through the ASI:One LLM. A simulation agent weights those reactions by audience segment and projects them to 10,000 impressions."

*Results card appears.*

"Here's the split, the segments that liked it most, and the same panels by region. These are the real objections and quotes from the personas. The rewrite only rephrases my own facts. It isn't allowed to invent discounts or claims, and code checks that."

*Click Social preview, then Test the rewrite.*

"This is how it would look as an Instagram post. And we don't just claim the rewrite is better: we test it on the same crowd, and only call a winner if the gap is bigger than normal run-to-run noise."

## 4. Business implementation (35 s)
"Crowdcast runs entirely as agents on Agentverse, with ASI:One as the interface, so there's no app to install.

- **Freemium.** The quick test is free. The full report (regional breakdown, every objection, persona quotes, the rewrite and the before/after test) is unlocked with a small FET payment through Fetch.ai's Agent Payment Protocol, straight from the user's ASI:One wallet.
- **Who pays:** startup founders before investor or demo days, Instagram creators, small D2C brands and marketing agencies who test many variants a week.
- **Costs stay low:** one test is roughly 15 LLM calls, because we run 90 real persona reactions and project them statistically, rather than simulating every impression.
- **Later:** credit packs and agency plans for teams that test every campaign."

## 5. Future scope (25 s)
"Next we want to:
1. ground the crowd in **live local trends and news** for the target city, with sources shown,
2. write and test **regional-language** variants (Kannada, Hindi, Tamil…) without stereotypes,
3. go beyond India to more regions and niche audiences,
4. **connect social accounts** so a winning version can be scheduled, always with an explicit approval step,
5. **calibrate against real campaign results**, so the simulated crowd gets closer to real audiences over time."

## 6. Close (10 s)
"Crowdcast: test anything on a crowd before the real crowd sees it. Try `@crowdcast` on ASI:One."

---

**Keep it honest on stage:** the numbers come from simulated personas, not real people, and they vary by a few points between runs. Say "simulated" whenever you quote a percentage.
