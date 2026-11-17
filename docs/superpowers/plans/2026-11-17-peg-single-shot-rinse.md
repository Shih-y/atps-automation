# PEG correction and single-shot manual recipes

User-authorized method, 2026-11-17: nominal PEG 1 mL delivered 0.42 mL; scale the prior 300-step command by 1/0.42. PEG chase is 30 native turns at 60 rpm. Each source is delivered in one finite aspiration/chase, without the former 0.8 mL splitting. Combined recipes mix once and draw 1.5 mL water from port 15 toward C/waste; do not purge port 16. User confirmed the rinse destination is waste.

Scope: manual mL delivery and combined recipes. Preserve separate mass experiment calibration snapshots, explicit manual actions, and historical collection records. A later applicable source endpoint calibration supersedes the PEG observation; do not multiply it again. Existing finite command/time and calibration applicability limits remain; requests beyond them fail before motion instead of silently splitting.

## Implementation

1. Add regression tests for the 714-step PEG 1 mL plan, fixed 60 rpm/30-turn chase, no effect on salt/water, precedence of later source calibration, and doses above 0.8 mL with exactly one command pair.
2. Replace manual splitting with a single rounded step total; reject unsupported single-shot ranges/timeouts before any motion. Keep original g/step experiment plans unchanged. Record the user observation and changed-chase validation limit in every PEG plan.
3. Add a port-15 waste rinse to combined recipes after mixing, using the C-end bulk water reference (0.915333 mL/turn): 1.5 mL -> 328 estimated steps. Freeze reference and request/estimate in the plan. No rinse between sources; standalone volume delivery has no automatic cleanup. Record wet common tubing, not a dry/reset state. Validate rinse before starting the recipe.
4. Update UI preview, progress, method text and operator instructions. Preserve the current six-page architecture and independent pump/valve controls. Test runtime commands with fake hardware only.
5. Run affected and full regression tests plus browser checks. Review diff, commit, synchronize GitHub. Apply to the local service only when idle; restore its existing connection without starting liquid movement.

## Review focus

- Original PEG reference is fixed at 300 steps per nominal mL, not recalculated from later water edits; no double correction after source calibration.
- 1.5 mL is nominal rinse water drawn, not a proven C-end recovered volume or complete flush of the 7.319 mL common tube. Residual water can affect the next sample; no automatic empty-state claim.
- No port-16 movement for a combined manual recipe, including old clients sending purge_after=true. Explicit purge buttons and mass experiment methods retain their separate meanings.
- Interrupted aspiration/chase/rinse never replays the recipe; complete-action counts are not measured delivery.
- A malformed reference or later invalid item fails the whole plan before physical movement.

## Validation

Python unittest discovery (full and targeted); optical Node tests; current console, integrated console and source-volume browser checks. Live service preview only, no experimental dosing. Physical recovery at the new PEG chase speed and residual-water effects remain unverified.

## Added user requirement: bottle wiring

User specified bottle 1..5 -> physical valve ports 1,3,4,6,7. Store an explicit logical-bottle mapping; apply it at sample-directed motion, while raw manual valve actions retain physical-port semantics. Image/sample/project IDs remain logical bottle 1..5. Port 6 is now bottle 4; port 7 bottle 5. Do not invent a sixth bottle connection. Include mapping in calibration compatibility, preserve old records, reject stale compiled plans rather than route them silently. Display both bottle and physical port in relevant selectors and monitor; permit explicit manual selection of physical port 7. Configure existing runtime only when idle, backed up, without starting motion. Preserve missing-map legacy configuration semantics for old data and tests; publish current mapping in shipped defaults. Add regressions for all five routes, final mixing, source calibration, manual physical-port non-remapping, invalid/duplicate mapping, and old calibration invalidation.

Native implementation of PEG task is complete pending full validation. Delegate the independent routing extension and review it before integration; parent completes documentation and regression updates. Final push/restart waits for both changes.

Final verification: 535 Python tests passed, 27 optical tests passed, all nine browser suites passed. PEG review found no P1/P2 issues; routing independent review found one malformed-input rejection issue, fixed with a regression test and confirmed closed before integration. No physical delivery tests or new EXE build in this update.
