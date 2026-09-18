---
type: Partition Pattern
title: Partition decisions before coordinates
description: Partition decisions before coordinates for conceptual single-floor planning.
tags: [always]
applies_to: [always]
priority: 92
status: draft
generated: {by: constructiq-authoring/1}
retrieved_on: '2026-09-18'
review_note: Source-informed synthesis; no architectural sign-off.
---
# Partition decisions before coordinates

ConstructIQ procedure:
1. Budget areas for bedrooms, compact bathrooms, kitchen and furnished public
   rooms; reserve circulation last. Use actual catalog minima and caps.
2. Group public rooms near the entrance, bedroom suites in a quieter zone,
   and service rooms close to dining. Compare at least two different trees.
3. For every x/y split, calculate BOTH child widths and lengths. Multiplying
   nested fractions is necessary: a fraction belongs to its parent rectangle.
4. Check each required shared wall after splitting. Diagonal corner contact
   is not adjacency. Bathroom or parking columns can accidentally sever access.
5. Recompute ALL hallway/alcove area and bedroom-only area balance.
6. If a room becomes a thin strip or a large leftover alcove, change grouping
   before repeatedly nudging the same fractions.

Try a public-middle band for side entrances, public-front with bedroom suites
behind, or a short side connector. These are patterns to adapt, not templates
valid for every brief. Reference JSON remains subject to Python validation.
Related: [Compact circulation](../principles/compact-circulation.md).

