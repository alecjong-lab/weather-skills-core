#!/usr/bin/env python3
"""Rebuild the bundled Natural Earth countries GeoJSON.

Source: Natural Earth 1:110m admin-0
https://github.com/nvkelso/natural-earth-vector

Writes ``src/weather_skills_core/data/countries.geojson`` — polygons plus
``iso3``, ``name``, ``continent``, ``region_un``, ``subregion``, ``region_wb``.
``resolve-region`` groups those features at runtime (``East Africa``, …).

ISO3 is ``ISO_A3``, else ``ISO_A3_EH``, else ``ADM0_A3``. Natural Earth uses
``-99`` for some disputed / unassigned codes; those fall through. Kosovo
``KOS`` is stored as ``XKX``.

From the repo root:

    uv run python tools/build_countries.py
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

_NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_110m_admin_0_countries.geojson"
)
_OUT = Path(__file__).resolve().parents[1] / "src/weather_skills_core/data/countries.geojson"


def iso3(props: dict) -> str | None:
    for key in ("ISO_A3", "ISO_A3_EH", "ADM0_A3"):
        val = props.get(key)
        if isinstance(val, str) and len(val) == 3 and val.isalpha():
            return "XKX" if val.upper() == "KOS" else val.upper()
    return None


def main() -> None:
    req = urllib.request.Request(_NE_URL, headers={"User-Agent": "weather-skills-core-dev"})
    with urllib.request.urlopen(req, timeout=60) as response:
        ne = json.loads(response.read().decode())

    features = []
    skipped = []
    for src in ne["features"]:
        props = src["properties"]
        code = iso3(props)
        name = props.get("NAME") or props.get("ADMIN")
        if code is None or not name:
            skipped.append(name or props.get("ADMIN") or "?")
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "iso3": code,
                    "name": name,
                    "continent": props.get("CONTINENT") or None,
                    "region_un": props.get("REGION_UN") or None,
                    "subregion": props.get("SUBREGION") or None,
                    "region_wb": props.get("REGION_WB") or None,
                },
                "geometry": src["geometry"],
            }
        )

    if skipped:
        raise SystemExit(f"no ISO3/name for {skipped}")

    features.sort(key=lambda f: f["properties"]["iso3"])
    payload = {
        "type": "FeatureCollection",
        "_source": "Natural Earth 110m admin-0 (nvkelso/natural-earth-vector)",
        "features": features,
    }
    _OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"wrote {_OUT} ({len(features)} countries)")


if __name__ == "__main__":
    main()
