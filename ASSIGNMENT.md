# Recovery Drill Scorecard

I need a repeatable way to score recovery exercises when multiple approved pauses overlap or extend outside the exercise.

## Required decision command

`python drill.py score INPUT.json` reads a raw JSON object as shown under input in examples/. Inputs use integer minutes. Clip approved pauses to [start,end), merge overlapping or adjacent intervals, and subtract their union length from elapsed time. Ignore unapproved pauses. Return elapsed, paused, active and met, with met true when active <= target. Reject inverted intervals, negative targets and incorrectly typed fields.

Print the JSON decision to stdout. For invalid input return nonzero and write a readable diagnostic to stderr. Use exact JSON types, including rejecting booleans where integers are required. Input ordering must not affect the result. Preserve the inventory CLI.

## Reporting and integration work

Add a fleet report grouping exercises by service. Report sample count, pass count, and nearest-rank p95 active minutes. Add independent tests for zero duration, missing data, ties and mixed services. Allow JSON and CSV summaries and show that duplicate approved pauses cannot improve a score. Document the reporting CLI, its input schema, one working example, and failure behavior. Exercise both success and failure paths through subprocess tests. Keep input files read-only. Use deterministic output and temporary paths for tests.

## Estimated implementation session

Allow 40 minutes for input modeling, 90 for the decision algorithm, 65 for the reporting feature, 50 for validation and edge cases, 60 for integration and regression tests, and 30 for documentation and reviewer evidence. Total estimate: 5 hours 35 minutes. Record actual time worked.
