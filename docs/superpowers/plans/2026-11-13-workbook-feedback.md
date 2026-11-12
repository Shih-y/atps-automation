# Workbook feedback implementation plan

> For agentic workers: execute the assigned independent component with tests first; root reviews integration and publishes only after verification.

Goal: implement the user's 2026-11-13 workbook recommendations in the real-device program and GitHub repository.

Architecture: retain the verified serial transport and stop-with-connection behavior. Add a single-bottle recipe controller, replace the crowded default UI with a compact workflow, and implement local browser image analysis with explicit uncertainty and optional manual ROIs.

Tech stack: existing Python standard-library HTTP controller, vanilla browser JavaScript/Canvas, pyserial. No new runtime packages or simulated user mode.

Source specification: the submitted workbook adds recommendations at 操作步骤与微调!B121:B131 and 指定毫升换算!A12,A29. No numeric override cells changed. The cached 37.7 value changing to 37.700000000000003 is not a parameter change.

## Constraints and decisions

- No live pump/valve movement, camera activation or serial disconnect for implementation tests. Use the isolated fake service only.
- Keep CH340 discovery, manual valve/CW/CCW/rpm/integer turns, one-click wash and air purge, source refill and end drainage, optional endpoint calibration, and immediate stop retaining connection.
- Remove legacy water recipe and redundant debug workflows from the default UI. Normal recipe delivery must not call discard/reset/wash.
- Recipe accepts one target and an ordered list of source/volume entries. Validate the entire request before motion, use saved reference initially 45 steps/0.15 mL, aspirate/chase each component. The user's subsequent reply explicitly requests automatic final mixing: mix once into the target after all components, then purge once to waste.
- The user confirmed more portions above 1.6 mL are allowed; each nominal portion must remain at most 0.8 mL. Preserve total integer steps when partitioning. This does not establish actual physical recovery or retained slug volume.
- Buttons have no historical-readiness or observation locks. Invalid input and concurrent serial conflicts must be clearly reported; no automatic queued duplicate clicks.
- Camera opens only on explicit click. Unmirrored capture, bottle numbering right-to-left 1–5. Automated detection must not invent five bottles in an empty image; manual ROI correction stays available.
- Phase inference is an unvalidated optical heuristic, not established experimental accuracy. Save the original frame, ROIs, method/settings and results separately from manual labels. Low contrast, glare, bubbles, unstable frames or insufficient bottle detection must remain uncertain.

## Components and contracts

1. Backend: recipe/calibration changes in free_debug.py/controller.py/bench_calibration.py/tube_workflows.py with Python isolated tests. Action `recipe` payload `{sample,items:[{source,volume_ml}],mix_after:true,purge_after:true,mixing:{steps:20,rpm:5,timeout_s:10}}`. `volume` delegates to the same planning rules for one source. Snapshot `recipe_run` stores plan, command progress, actual_ml:null and cancellation. Default UI explicitly supplies all calibration pulse parameters.
2. UI: replace static/index.html and add static/console.js, static/console.css. Keep old scripts only as historical compatibility assets, not loaded by the default page. Bind existing APIs plus recipe and reference update. Do not disable action buttons based on readiness; render clear asynchronous results and current pump/valve telemetry. Camera and inference integration use the contract below.
3. Vision: static/phase_analysis.js exposes `PhaseAnalysis.analyzeFrame(imageData, options)` and CommonJS export for tests. options may include `rois` and `previous`; result `{rois:[{sample,x,y,width,height}], bottles:[{sample,classification,reason,interface_y,...}], detection:{...}, method,validated:false}`. classifications `single`,`two_phase`,`uncertain`; `single` means optical no-interface candidate only. Use synthetic test images for algorithm checks, never label them real validation.
4. Root integration: wire static assets and APIs, verify archival schema and calibration persistence, package updated source/documentation without restarting or hot-swapping a live server, run all unit and new browser tests, review diff, commit and push main, verify GitHub CI. `/api/imaging/begin` creates offline image batches; explicit partial-ROI archival saves raw frames with unassigned targets, never invented bottle labels.

## Review focus and checks

- Entire recipe is invalid if any later component has a bad route/volume; assert zero initial moves.
- 0.8/0.801/1.0/1.6/2.0 mL and integer-step rounding must conserve total steps and report requested versus estimated volumes.
- Stop during a multi-component recipe must prevent all remaining moves and keep the serial handle only after confirmed stopping.
- Recalibration must survive restart and update recipe plans without inventing repeatability.
- Empty/one/missing bottle frames, reversed numbering, glare, low contrast, bubbles and unstable repeated frames must not be asserted confidently as phase separation.
- Main UI must load without JS errors, camera remains off, manual controls and batch recipe are independently usable, 16 purge occurs once, and phone layout remains readable.

## Delivery

- Add a dated implementation/limitation note and current operation table; historical documents remain explicitly historical.
- Publish verified code and documentation to Shih-y/atps-automation main without force update.
