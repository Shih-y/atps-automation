# Optional manual recipe rinse and purge

Goal: single- and multi-bottle mL recipes offer a cleaning checkbox; all manual mL source chases use 30 native turns at 60 rpm. Existing volume ratios remain estimates and old calibration motion records are preserved.

User-specified design: selecting cleaning performs the existing 1.5 mL water rinse toward waste followed by air from port16 toward waste. Deselecting skips both. Multi-bottle selection applies after each bottle, never between sources. Logical bottle mapping remains unchanged.

Implementation decisions: keep `rinse_after` as the Boolean request field, default true for recipes and false for standalone volume dosing. Batch top-level `rinse_after` applies to every row. Use one finite port16 purge of 30 native turns / 60 rpm / 46 s timeout after rinse; no sensor is present, so record `air_unverified`, never verified dry. 91 cm of 3.2 mm ID tube holds about7.32 mL; 30 turns at the old water reference corresponds to about27.46 mL water displacement, not a gas calibration or proof of drying. Retain manual purge for visible remaining water. No prerequisite prompts.

Formal automatic mass experiments and saved calibration evidence keep their frozen parameters. Manual mL plans override chase only, retain old reference motion in the snapshot and explicitly flag changed conditions requiring delivery revalidation. Source collection defaults switch to 30 turns/60 rpm while remaining editable.

Tasks (inline execution, final independent review):
- [x] Add failing fake-hardware tests for enabled/disabled cleanup, order/direction, batch propagation, stop during purge, all sources' chase and preservation of old calibration evidence.
- [x] Implement shared manual chase settings; recipe cleanup order and batch option; accurate progress/state.
- [x] Add checkboxes and previews to existing cards; freeze the cleaning choice on ambiguous batch retries. Update source calibration defaults.
- [x] Update intentional old-method test expectations, run full Python and relevant browser regressions, review whole change.
- [x] Update version/docs. Publish and update the idle live service after this verified commit; reconnect read-only and open right. Do not run real dosing or cleaning.

Verification ledger: 7 new backend cases (6 observed failing on old implementation, existing no-clean behavior retained) now pass; 555 full Python tests pass; five browser suites pass (manual batch28, current console35, integrated15, source volume19, six-page experiment regression). Independent review found no P1/P2. Minor historical rinse-only progress wording corrected and covered by browser test. Browser test waits for rendered idle state before starting its next request. Earlier sandbox HTTP denial resolved by rerunning isolated tests with local-network permission; no production test bypasses introduced. Live update waits for user-started active recipe to finish; no change to running actions.
