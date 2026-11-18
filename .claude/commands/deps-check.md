---
description: Report outdated dependencies — read-only
---

You are running the `/deps-check` skill. READ-ONLY. Do not upgrade anything —
that's `/deps-update`. This only reports.

## 1. List outdated dependencies

Run the outdated-dependency commands for both ecosystems inside their
containers:

```bash
docker compose run --rm app uv tree --outdated
docker compose run --rm web npm outdated
```

Parse the output into package / current version / latest version.

## 2. Group by category

Split into:

- **Runtime deps** — what ships in the built artifact.
- **Dev deps** — test/lint/build-only tooling.

## 3. Flag major bumps that need a pin change

For any dependency whose latest version crosses a pin boundary your manifest
enforces (e.g. current pin is `<2.0` and latest is `2.x`), flag it separately
as **"needs design review"** — a major bump is a decision, not an update.

## 4. Base image age, if applicable

If a `Dockerfile` exists, read its `FROM` lines and report the pinned tags plus
how long it's been since those lines last changed:

```bash
git log -1 --format=%cI -- Dockerfile
```

## Output

Table: `package | current | latest | category | action`.

Actions:

- `safe-update` — within the existing pin, `/deps-update` can pick it up.
- `review` — new major, or otherwise flagged; needs a human decision.
- `defer` — minor lag, no urgency.

End with one summary line: "X safe, Y need review, Z deferred."
