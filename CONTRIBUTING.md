# Contributing

Keep changes tied to the experiment question. A feature should improve the
policy comparison, reproducibility, evidence quality, or clarity; otherwise it
is probably outside this repository's intentionally small scope.

Before opening a pull request:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests
```

Include the problem, the reason for the change, and evidence that it preserves
determinism and paired-policy fairness.
