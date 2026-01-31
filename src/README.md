This directory contains the full analytical pipeline for the
**HR Network-Based Bottleneck Diagnostics Project**.

## Main File

- `pipeline.py`  
  End-to-end pipeline covering:
  ETL → Network Construction → Macro Diagnostics → Simulation →
  Micro Role Signals → HR-Readable Actions

## Design Principles

- Start from **organizational structure**, not individual ranking
- Treat network metrics as **diagnostic signals**, not labels
- Use **simulation** to validate risk, not just describe it
- Translate analytics into **actionable HR insights**

## Entry Point

Run from repository root:

```bash
python src/pipeline.py

