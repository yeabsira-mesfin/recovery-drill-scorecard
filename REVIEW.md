# Task description

I need a repeatable way to score recovery exercises when multiple approved pauses overlap or extend outside the exercise. The existing Python application inventories records. Implement `python drill.py score INPUT.json` as specified in ASSIGNMENT.md. Add a fleet report grouping exercises by service. Report sample count, pass count, and nearest-rank p95 active minutes. Add independent tests for zero duration, missing data, ties and mixed services. Allow JSON and CSV summaries and show that duplicate approved pauses cannot improve a score.

## How to judge completion

Run `python -m unittest discover -s tests -v` and `python acceptance.py` from the repository root. All existing tests, new tests and 72 decision cases should pass. Inspect drill.py, acceptance.py, tests/, README.md and any new implementation modules. Exercise the documented reporting command with a fresh synthetic example, reverse the input records, and compare the outputs. Feed invalid types and verify a nonzero exit, a stderr diagnostic and unchanged input. Check the reporting feature independently of the example matrix.

## Other notes for reviewers

Use synthetic data and temporary files. The expected values belong only to the acceptance runner. Check the algorithm on unseen inputs and record the actual commands and results.

## Final questionnaire

Copy the new live compare problem ID from the platform. Enter actual active hours (minutes divided by 60). Primary language: Python. Task type: feature implementation and regression testing. Source: a new MIT-licensed starter prepared with Codex. Use the sections above for task description, how to judge completion and reviewer notes. Record actual outcome, completion evidence, changed files, commands, turn count, flag count and observed flag categories. Include exact quotes or turn references when explaining a flag. Leave run-specific fields open until the session ends.
