# ConstructIQ: ten-home live QA report

Functional score: **60.0/100**. Layout-quality proxy: **30.0/100**.
Combined tested-scope score (70% functional, 30% quality): **51.0/100**.

Real Ollama Cloud calls through the local FastAPI server; no mocked generation or requirement extraction.
Each successful case exercises generation, a design-changing viewer API edit, wall paint, stale-version rejection, and undo.
The final saved home is restored to its original layout by undo; revision history preserves the edit.

| Home | Generation | Edit | Paint | Undo | Hallway % | Kitchen/dining adjacent | Balanced bedrooms | Functional |
|---|---|---|---|---|---|---|---|---|
| 1 | Pass | Pass | Pass | Pass | 0.0 | Pass | Pass | 100/100 |
| 2 | Pass | Pass | Pass | Pass | 9.62 | Pass | Fail / blocked | 100/100 |
| 3 | Pass | Pass | Pass | Pass | 21.09 | Pass | Fail / blocked | 100/100 |
| 4 | Pass | Pass | Pass | Pass | 6.56 | Pass | Pass | 100/100 |
| 5 | Pass | Pass | Pass | Pass | 15.25 | Fail / blocked | Fail / blocked | 100/100 |
| 6 | Pass | Pass | Pass | Pass | 16.3 | Fail / blocked | Fail / blocked | 100/100 |

## Scoring and limitations

Functional weights per home: requirements capture 15, valid generation/current 3D revision 25, asset delivery 10, successful requested edit 20, paint persistence 10, exact undo restoration 10, stale-version protection 10. Blocked downstream checks receive zero.
Requirements capture checks dimensions, counts, entrance, bathroom assignments, dining and requested extras. Kitchen/dining placement is measured separately from geometry rather than trusting the extracted text.
Quality proxy has three equally weighted checks: dedicated hallway area at most 15%, kitchen and dining sharing the validator-required wall span, and largest/smallest bedroom area ratio at most 1.25. These are product benchmark criteria, not an architectural assessment.
Missing layouts receive zero quality points. Compound failures remain visible; successful undo does not erase failed edit evidence.
Browser automation reported no available browser. WebGL rendering, camera behavior in a real browser, touch/mobile behavior and visual 3D walkthroughs were NOT verified. Assets, embedded scene geometry and Node interaction tests are distinct evidence.
The first attempted server run failed on Windows console Unicode logging before planning. The benchmark server was restarted with PYTHONIOENCODING=utf-8 and Python -X utf8; application logging code was not changed.
The parking-removal check in the original runner compared must_have_rooms literally with []; this report corrects that check to ignore base living/dining/kitchen entries while still requiring the requested extra to be removed. Raw events are retained.

## Verification

Embedded current scene matches saved layout: 6/10.
Offline Python suite: 85 passed. JavaScript walking/painting suite: 14 passed.

## Changes from testing

- Fallback compares feasible depth/orientation choices and keeps the one with least dedicated hallway area.
- Kitchen/dining remain adjacent when the public band also contains a common-bathroom column.
- A compatible validated reference can replace an oversized-circulation fallback when it uses less hallway space.
- Base living/dining/kitchen entries no longer prevent reference matching.
- Wall-paint test fixture includes the existing hex input; added valid/invalid custom-color checks.

## Before/after

Initial complete run: 5/10 generated; functional 57.5/100; quality proxy 10.0/100.
Retest: 6/10 generated; functional 60.0/100; quality proxy 30.0/100.
These are separate live model runs; model variability also contributes to differences. The added deterministic regression tests isolate the fixed failures.

## Saved test homes

- Home 1: `qa-20260917-145058-home-01-e0cab3d50a` — [open 3D viewer](http://127.0.0.1:8000/sessions/qa-20260917-145058-home-01-e0cab3d50a/assets/qa-20260917-145058-home-01-e0cab3d50a_3d.html)
- Home 2: `qa-20260917-145058-home-02-c5f9e6d717` — [open 3D viewer](http://127.0.0.1:8000/sessions/qa-20260917-145058-home-02-c5f9e6d717/assets/qa-20260917-145058-home-02-c5f9e6d717_3d.html)
- Home 3: `qa-20260917-145058-home-03-3d26613c25` — [open 3D viewer](http://127.0.0.1:8000/sessions/qa-20260917-145058-home-03-3d26613c25/assets/qa-20260917-145058-home-03-3d26613c25_3d.html)
- Home 4: `qa-20260917-145058-home-04-b3cd58b7b7` — [open 3D viewer](http://127.0.0.1:8000/sessions/qa-20260917-145058-home-04-b3cd58b7b7/assets/qa-20260917-145058-home-04-b3cd58b7b7_3d.html)
- Home 5: `qa-20260917-145058-home-05-8882d400b7` — [open 3D viewer](http://127.0.0.1:8000/sessions/qa-20260917-145058-home-05-8882d400b7/assets/qa-20260917-145058-home-05-8882d400b7_3d.html)
- Home 6: `qa-20260917-145058-home-06-c5bcce6169` — [open 3D viewer](http://127.0.0.1:8000/sessions/qa-20260917-145058-home-06-c5bcce6169/assets/qa-20260917-145058-home-06-c5bcce6169_3d.html)
