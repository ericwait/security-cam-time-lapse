> Status: adopted | Audience: anyone designing a feature or a stakeholder doc |
> See also: [Stakeholders/Admin.md](Stakeholders/Admin.md),
> [Stakeholders/Viewer.md](Stakeholders/Viewer.md),
> [Timelapse-Modes-and-Storage.md](Timelapse-Modes-and-Storage.md)

# Philosophy

## The guiding principle

> Preserve full-resolution visual history automatically and make any slice of
> time effortlessly playable, without asking viewers to wait on video encoding
> or operators to babysit storage.

Every section below, every stakeholder document, and every feature that gets
built traces back to this sentence. When a decision cannot be defended against
it, revisit the decision rather than skipping the check.

## The stakeholder contract

Every role in this system — every document under `Stakeholders/` — fills the
same three-part contract:

1. **Domain of awareness** — what this role must stay aware of to do their
   part. Not everything in the system; only what bears on their decisions.
2. **Data this role keeps current** — what this role is the source of truth
   for. If nobody keeps a field current, question why it exists.
3. **Value this role receives** — what this role gets back for the awareness
   and upkeep asked of them. An unbalanced contract is a design defect, not a
   tradeoff to shrug off.

## The design failure test

> If the system asks more of a stakeholder than it gives back — forcing a
> viewer to wait on batch video encoding just to explore a date range, or
> forcing an admin to hand-edit configuration files on disk to manage a camera
> — the design has failed.

Apply this test whenever a feature adds an obligation (a field to fill, a step
to complete, an alert to read) to any role. A feature that serves no
stakeholder's contract — that asks without giving, or gives to nobody in
particular — is questioned by default, not built by default.

## Stakeholder summary matrix

| Role | Expected awareness | Expected action | Not expected to |
| --- | --- | --- | --- |
| [Admin](Stakeholders/Admin.md) | Camera connectivity, capture health, disk capacity, who should have access | Manage cameras, credentials, capture intervals, user accounts, and system settings in the Web UI | Hand-edit config files after deployment, manually prune render caches, or re-encode videos by hand |
| [Viewer](Stakeholders/Viewer.md) | Which camera and date/time window they want to watch | Log in, browse any enabled camera, scrub dynamic timelapses, inspect full-res frames, and export video files | Configure cameras, manage storage or caches, or understand stream protocols |
