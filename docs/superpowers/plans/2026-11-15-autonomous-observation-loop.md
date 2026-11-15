# Automatic formulation and observation loop implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent domain, observation and UI tasks, with final integrated review. User has requested implementation and GitHub upload; no further design approval is required.

**Goal:** Complete reachable-target selection → cumulative addition → calibrated integer commands → recorded execution → bounded observation → next reachable experiment.

**Architecture:** Retain the existing six-page UI, shared serial controller, continuation solver, frozen calibrations and persistent scheduler. Add a pure observation policy and integrate its decisions into the existing sample lifecycle. Existing human-review runs retain their behavior; new runs can explicitly select automatic optical candidates, with all evidence and limitations recorded.

**Tech stack:** Python standard library, SQLite, existing JavaScript optical algorithm, unittest, Node and Playwright.

**Spec:** User's 2026-11-15 specification in this chat; extends the implemented 2026-11-14 continuation plan.

## Global constraints

- Automatic bottles 1–5; bottle 6 remains manual. A project contains one salt system.
- No physical pump/valve commands during development; do not restart or disconnect the user's running controller.
- Preserve the prior 30 rpm / 2000 step manual chase adjustment and its historical calibration provenance.
- No invented density, calibration, measured composition, empty bottle or phase equilibrium.
- Mass and steps remain separate: integer-command completion is not a measured delivery.
- Stop must bypass failing storage; plans must persist before dispatch; restart never resumes movement.
- Existing manual-review runs default to human decisions. Automatic mode is explicit, uses evidence-backed optical candidates, and never converts unknown to single phase.
- Source changes are uploaded to the existing GitHub repository after regression and independent review. Preserve real local data outside commits.

## Review focus

1. Two adjacent images from different sample generations/ROI mappings must not satisfy stability.
2. Camera loss or absent analysis must terminate or pause finitely without dosing again.
3. Rounded doses must satisfy initial imaging amount, reserved capacity and per-source material budgets.
4. A late capture, duplicate callback or repeated start must not create duplicate actions or resurrect an interrupted bottle.
5. Automatic candidate labels must retain machine provenance; human correction overrides them; no-interface alone never becomes a single-phase training label.

## Tasks

- [x] Domain: extend `continuation_domain.py` with `min_initial_mass_g`, `reserve_mass_g`, `source_mass_remaining_g`; return theoretical preview even when capacity is missing while keeping `executable=False`; apply constraints after rounding. Tests cover mass conservation, dilution/concentration examples and capacity/step boundaries.
- [x] Pure observation: create `observation_policy.py` and tests. Public functions `validate_observation_policy(values)` and `evaluate_observation(sample, project, policy)`. Policy fields: `mode` (`human`/`automatic`), `max_auto_retries` (0–10), `retry_interval_s` (1–3600), `stable_frames` (2–5), `min_frame_spacing_s` (1–3600), `max_interface_shift_fraction` (0–0.2), `min_phase_fraction` (0.01–0.45), `single_phase_validated` (bool), `single_phase_evidence` (text). Evaluation returns status (`pending`/`candidate`/`unknown`), classification, reason_codes, evidence_frame_ids, phase_height_fraction and method; only current-generation, same-mapping, in-window quality frames qualify. Automatic single phase additionally requires explicit validated observation criterion/evidence.
- [x] UI: expose run observation policy, initial mass floor and reserve; show automatic observation/retry/unknown reasons and origin of labels; keep all six pages and manual controls. Add focused browser test.
- [x] Scheduler: freeze run policy; call bounded observation tick before selecting next liquid action; persist per-generation results and capped retries, automatically adopt eligible optical candidates only in automatic mode. Human review remains authoritative. Uncertain/failed/missing photographs end unknown with reasons; other bottles continue. Freeze composition and pause liquid dispatch after faults or unknown common-path state.
- [x] Selection: score only executable adopted compositions; freeze phase-data regime and reasons; favor fresh bottles initially; preserve source budget reservations and actual used figures as estimates. Exhaustion reasons must distinguish finite-search limits, capacity, calibration and observation unknowns.
- [x] Export: include per-source theoretical/quantized additions, observation evidence, action events and frozen budgets/policies; optional independent tie-line analysis input/export must not infer phase compositions from photographs.
- [ ] Regression/release: run targeted and full Python/optical/UI tests, independent review, bump software version, update instructions and release workflow, commit/push and verify remote checks. Publish Windows bundle if build and smoke checks pass; state physical-validation limits.

## Implementation ledger

- Baseline: `17c2100`; prior uncommitted chase-default changes are authorized and must be included.
- Existing implementation already covers cumulative inventory, minimum theoretical mass, bounded neighboring integer search, serialized motion, idempotency, manual-review gating, history and versioned exports. Extend these rather than replacing them.
- Ruling: default human review is retained from the user's previous explicit choice; automatic mode is available for this new specification. Unvalidated no-interface output stays unknown unless an explicit validated single-phase observation criterion is supplied.
- Ruling: browser camera remains the current acquisition path. Closed browser/absent heartbeat is recorded as missed observation; the backend bounds retries and ends unknown rather than fabricating images.

- Verification: 479 Python tests pass; 27 optical tests pass; eight browser scenarios pass. Synthetic PNG/real HTTP/fake hardware test completes two rounds without human labels. Independent review re-ran 99 targeted tests; all findings fixed and covered by regressions.
- Release: source commit followed by Windows build/smoke and GitHub publication; live 8765 controller is not restarted or disconnected.
