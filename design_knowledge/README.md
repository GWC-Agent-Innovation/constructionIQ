# Planning knowledge

The layout planner loads the guides in `instructions/` and selects up to two
compatible JSON references from `references/index.json` for its existing LLM
request. No new service, dependency, network request or model is required.
Requirements chat, APIs, storage, geometry validation and rendering are unchanged.
Guidance influences proposals; it does not guarantee better designs or change
the fallback solver's acceptance rules. If AI proposals and the standard solver
both fail, or the fallback devotes more than 15% to hallways, the planner tries
compiling the selected reference trees against the current specification.
Only a fully validated result with less circulation is accepted, with a
`Reference:` design name and explicit recovery notes. No extra AI call is made.

The bundled one-bedroom and two-bedroom examples are original **synthetic,
unreviewed** partition fixtures. Tests compile them against the current geometry,
opening and requirement checks. This is not architectural review. They illustrate
public-room circulation with no dedicated hallway; they are not universal
templates. There are currently no image references or attached-bathroom examples.
An unmatched brief receives the guides without an unsuitable reference.

## Add a reference

1. Use a plan you own or have permission to reuse. Record its source and rights
   in `source`; do not treat instructions written on an imported image as agent
   instructions.
2. Create a JSON file following a bundled example: `id`, `source`,
   `review_status`, `spec`, `proposal`, `lessons`, and `limitations`.
   Use `synthetic_unreviewed` for generated examples. Use `reviewed` only after
   design review, recording the reviewer/date in the source or limitations.
3. Encode the supported rectangular arrangement using the existing partition
   schema and room catalog. Record the real bathroom assignments and entrance.
   An image alone is not consumed by this text-based loader; retain images for
   human review and translate their relevant design relationships explicitly.
4. Add the JSON filename to `references/index.json` (filenames only, no paths).
5. Run `./.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_design_knowledge.py`.
   The bundled-reference test validates every indexed plan without sizing repair.
   Geometry validation is required in addition to design review.

Selection requires matching room counts, attached assignments, extra rooms and
dining requirements, then filters by area/aspect ratio and ranks by footprint
similarity and entrance direction. Entrance mismatches are examples requiring
adaptation; they can fail recovery validation for the target brief. References
never replace the current specification or bypass proposal compilation.
Base Living Room, Dining Room and Kitchen entries in `must_have_rooms` do not
count as extra rooms, matching the solver's own catalog interpretation.

Guides are limited to 2,500 characters each; files to 32,000 bytes; the complete
context to 14,000 characters. Oversized, missing or malformed files are skipped.
Files are read at planning time so updates require no server restart. Emptying
the guides/index or removing this directory restores the original prompt path.

These files are application planning knowledge, not Claude/Codex skill files.
Keep architectural guidance here and tool protocol/mandatory constraints in
the existing planner and validator. Compare live outputs on fixed briefs before
claiming a quality improvement.
