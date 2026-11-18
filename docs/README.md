<!--
Documentation index. Keep this in sync whenever a doc is added, renamed, or
moved — it is the map, and a wrong map is worse than none. See Practices
Playbook Pillar III.
-->

# Documentation index

This tree is split by the question each half answers. `design/` answers
**what and why** — the domain, the roles, the decisions — in the vocabulary of
the problem, not the framework. `development/` answers **how** — architecture,
setup, testing, daily workflow — and is free to be as tool-specific as it needs
to be.

**`design/` must stay implementation-agnostic.** Design survives
re-platforming; framework-specific prose does not. If a sentence in `design/`
names a language, library, or tool, it belongs in `development/` instead, or
in a parenthetical example at most.

## What goes where

| Location | Answers | Contains |
| --- | --- | --- |
| `design/Philosophy.md` | Why does this project exist? | The one-sentence guiding principle; the stakeholder contract shape; the design failure test |
| `design/Stakeholders/` | Who does this serve? | Role contracts for [`Admin.md`](design/Stakeholders/Admin.md) and [`Viewer.md`](design/Stakeholders/Viewer.md) |
| `design/Timelapse-Modes-and-Storage.md` | How do capture, storage, and playback modes behave? | Capture contract, original + preview dual storage, hybrid player + ephemeral exports, Modes 1–3 |
| `development/Architecture.md` | How is the system built? | Container topology, FastAPI/SQLite/FFmpeg/React stack, credential encryption, `/data` layout |
| `development/Getting-Started.md` | How do I run this locally? | Prerequisites, `.env` bootstrap, `just dev`, commit hooks, resetting |
| `development/Testing.md` | How do we verify changes? | Three test tiers, acceptance-first workflow, guardian suites, a11y gate |
| `decisions/README.md` | What was decided, and where? | Discoverability index for inline `(decision YYYY-MM-DD, ...)` stamps |
| `archive/README.md` | What used to be true? | Completed one-time plans, kept for rationale, stripped of authority |
| `Implementation-Plan.md` | What's next, and what shipped when? | Forward-looking phases + an append-only progress log |
| `Practices-Playbook.md` | What practices does this repo follow, and why? | The idealized-form reference this repository implements |

## New here?

1. Read `Practices-Playbook.md` once, end to end — it explains the reasoning
   behind every convention below.
2. Pick a reading order below, depending on whether you're here to understand
   the product or to start shipping.
3. Skim `Implementation-Plan.md` for current phase and status.
4. Check `decisions/README.md` for anything load-bearing you're about to
   revisit — a past decision may already answer your question.
5. When in doubt about where a new doc belongs, ask: does this describe the
   problem (→ `design/`) or the toolchain (→ `development/`)?

## Reading order: for product understanding

1. `design/Philosophy.md` — the guiding principle and the stakeholder contract
   shape.
2. `design/Stakeholders/Admin.md` and `design/Stakeholders/Viewer.md` — the two
   stakeholder contracts.
3. `design/Timelapse-Modes-and-Storage.md` — capture, dual-tier storage, hybrid
   playback, and Modes 1–3.
4. `Implementation-Plan.md` — what's actually shipped versus planned.

## Reading order: for engineering understanding

1. `development/Architecture.md` — stack, encryption, and `/data` volume layout.
2. `development/Getting-Started.md` — clone to running stack.
3. `development/Testing.md` — test tiers and guardian suites.
4. `../CLAUDE.md` at the repository root — the agent-facing map and
   conventions.
