#!/usr/bin/env python3
"""Rebuild country_regions.json from Natural Earth 110m admin-0.

Join keys match the bundled countries.geojson iso3 values (ISO_A3, else
ISO_A3_EH / ADM0_A3; Kosovo ADM0_A3 KOS → XKX).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

_NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_110m_admin_0_countries.geojson"
)
_ADM0_PATCH = {"KOS": "XKX"}
_ROOT = Path(__file__).resolve().parents[1]
_COUNTRIES = _ROOT / "src/weather_skills_core/data/countries.geojson"
_OUT = _ROOT / "src/weather_skills_core/data/country_regions.json"


def _iso_of(props: dict, our_iso3: set[str]) -> str | None:
    for key in ("ISO_A3", "ISO_A3_EH", "ADM0_A3"):
        val = props.get(key)
        if isinstance(val, str) and len(val) == 3 and val.isalpha():
            iso = _ADM0_PATCH.get(val.upper(), val.upper())
            if iso in our_iso3:
                return iso
    adm = props.get("ADM0_A3")
    if isinstance(adm, str):
        iso = _ADM0_PATCH.get(adm.upper(), adm.upper())
        if iso in our_iso3:
            return iso
    return None


def main() -> None:
    req = urllib.request.Request(_NE_URL, headers={"User-Agent": "weather-skills-core-dev"})
    with urllib.request.urlopen(req, timeout=60) as response:
        ne = json.loads(response.read().decode())
    ours = json.loads(_COUNTRIES.read_text(encoding="utf-8"))
    our_iso3 = {feature["properties"]["iso3"] for feature in ours["features"]}

    attrs: dict[str, dict] = {}
    for feature in ne["features"]:
        props = feature["properties"]
        iso = _iso_of(props, our_iso3)
        if iso is None:
            continue
        attrs[iso] = {
            "continent": props.get("CONTINENT") or None,
            "region_un": props.get("REGION_UN") or None,
            "subregion": props.get("SUBREGION") or None,
            "region_wb": props.get("REGION_WB") or None,
        }

    missing = sorted(our_iso3 - set(attrs))
    if missing:
        raise SystemExit(f"no Natural Earth region attrs for {missing}")

    payload = {
        "_source": "Natural Earth 110m admin-0 (nvkelso/natural-earth-vector geojson)",
        "countries": {key: attrs[key] for key in sorted(attrs)},
    }
    _OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_OUT} ({len(attrs)} countries)")


if __name__ == "__main__":
    main()
