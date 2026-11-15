# Bottle waiting, composition bounds and endpoint volume calibration

> For agentic workers: use subagent-driven-development; preserve hardware communication and existing six-page UI.

**Goal:** Fix the two user-reproduced automatic-run defects and support per-source onsite endpoint volume calibration for manual mL dosing.

**Architecture:** Keep the existing continuation domain/service; constrain integer candidates before ranking and share waiting-state decisions. Add versioned source volume references independent of formal mass calibration, with a simple collection/save UI.

**Tech Stack:** Python unittest, existing HTTP controller and browser JavaScript, Windows desktop packaging.

**Spec:** User review supplied 2026-11-15 in this conversation. User already authorized implementation and GitHub publication.

## Global constraints
- No physical pump/valve motion during this software task. Fake hardware only.
- Research bounds are inclusive hard constraints, independent of target error tolerance.
- A blocked ready bottle must not end a run while another bottle is observing or awaiting review.
- Volume calibration uses each source's actual sample-endpoint delivery; preserve stock/batch, temperature, geometry, bottle scope and motion parameters. Do not assume mother-solution density is 1 g/mL.
- Single-dose proportional extrapolation is an estimate, not validated precision. Explicit dose range and repeated measurements remain visible.
- Keep formal mass experiments on their own g/step calibration. No invented GP, independent compositions or tie-line inversion.

## Tasks and checks
- [x] T1 root: reproduce waiting/quantization regressions; fix continuation_service.py and continuation_domain.py, add per-bottle rejection diagnostics and hard post-compile bounds checks; test delayed review can resume another bottle.
- [x] T2 backend: add source_volume_calibration.py, integrate controller/server/free_debug, persist immutable source-specific references and preview requested mL with calibration scope checks; tests for source/batch/bottle/geometry mismatch, changed motion, invalid readings, mass/density conversion and segmentation.
- [x] T3 UI: equipment calibration workflow and manual dosing provenance, browser regression; simple Chinese instructions on collecting at the sample outlet.
- [ ] T4 root/reviewer: independent review, full Python/optical/browser validation, docs and Windows release, publish to authorized repository after checks.

## Interface contract
GET /api/state includes source_volume_calibrations (list). POST /api/source-volume-calibration saves an operator record (no motion) and returns {ok,reference}. POST /api/source-volume-preview previews {source,sample,volume_ml}; /api/action volume/recipe reuses the same planner. Calibration collection uses existing stock_calibration action with explicit aspirate/chase/doses. Backend implementer finalizes detailed payload with UI implementer before edits.

## Review focus
- No waiting-state decision depends on whether failure happened before or after exact compilation.
- No out-of-range adopted point enters training/selection even under loose composition tolerance.
- Updating unrelated stock must not invalidate another source reference.
- A one-dose measurement must not claim repeatability or validated extrapolation.
- Old manual water reference stays explicitly unvalidated; invalid existing source calibration must not silently fall back to water.

## Rulings
- User's explicit requested fixes and prior implementation/upload instructions provide scope approval; no additional design-confirmation pause.
- Future GP and inverse tie-line research remain outside this release; the code reports current heuristic limits.
