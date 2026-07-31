---
name: clean-skill
description: Lint fixture; a fully conformant skill.
metadata:
  version: "0.1.0"
---

# clean-skill

Lint fixture with a conformant declaration.

## Usage

```
uv run scripts/clean_skill.py --input in.zarr --output out.zarr
```

### Arguments
- `--input`, `-i` — input Zarr.
- `--output`, `-o` — output Zarr.
- `--bbox` — optional N/W/S/E spatial subset.
- `--smoothing` — smoothing window width in grid cells.
