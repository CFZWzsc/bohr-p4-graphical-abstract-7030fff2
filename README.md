# Runtime contract

Bohrium Playground Period 4 submission — challenge `graphical-abstract-7030fff2` (track: `scientific-graphics`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `graphical-abstract-composer`
- Summary: Use when a paper's problem, method, key result and implication must become a single-panel graphical abstract with claims tied to evidence and style constraints checked.

## Contents

| File |
|------|
| `SKILL.md` |
| `examples/demo-case/case.json` |
| `examples/demo-case/material.md` |
| `examples/demo-case/retention.csv` |
| `examples/demo-case/task.md` |
| `scripts/figure_svg.py` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
