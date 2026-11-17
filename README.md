# Recovery Drill Scorecard

I need a repeatable way to score recovery exercises when multiple approved pauses overlap or extend outside the exercise.

The starter inventories synthetic records. Implement the decision command and reporting workflow described in [ASSIGNMENT.md](ASSIGNMENT.md). Use Python 3.10 or newer with the standard library.

```sh
python drill.py inventory examples/duration-6/target-0-second-pause-1.json
python -m unittest discover -s tests -v
python acceptance.py --inventory
```

Example files contain input and expected results. The acceptance runner passes only the input to your implementation.
