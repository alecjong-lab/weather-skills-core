"""Provenance chain handling for weather-skill artifacts.

``weather_skills_history`` is a JSON-encoded append-only array of entries
(oldest first). Each entry has ``skill``, ``version``, ``args``, and ``input``.
"""

import hashlib
import html
import json
import re
import sys
from pathlib import Path

HISTORY_ATTR = "weather_skills_history"
SOURCE_ATTR = "weather_skills_source"
DEFAULT_SOFTWARE = "forecasting-skills"
OFFICIAL_MARK_TEXT = "weather-skills"

_EXIF_USER_COMMENT = 0x9286  # EXIF UserComment tag
_HTML_META_RE = re.compile(
    rf'<meta\s+name=["\']{re.escape(HISTORY_ATTR)}["\']\s+content=["\'](.*?)["\']\s*/?>',
    re.IGNORECASE | re.DOTALL,
)


def hash_zarr(zarr_path: Path) -> str:
    """Stable sha256 of a zarr directory's relative paths + file bytes."""
    zarr_path = Path(zarr_path)
    h = hashlib.sha256()
    for p in sorted(zarr_path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(zarr_path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def parse_chain(raw: str) -> list:
    """Strictly parse ``weather_skills_history`` JSON into a list."""
    try:
        chain = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("value is not valid JSON") from None
    if not isinstance(chain, list):
        raise ValueError("value is not a JSON array")  # noqa: TRY004
    return chain


def coerce_chain(raw: str, label: str) -> list | None:
    """Lenient parse for render paths; warns and returns None if malformed."""
    try:
        chain = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        chain = None
    if not isinstance(chain, list):
        print(
            f"ignoring malformed weather_skills_history on {label}; "
            "run `provenance --check` for details",
            file=sys.stderr,
        )
        return None
    return chain


_ENTRY_KNOWN_KEYS = {"skill", "version", "args", "input"}
_INPUT_ITEM_KNOWN_KEYS = {"basename", "hash", "history"}


def _validate_input(value, loc: str, violations: list, notes: list) -> None:
    if value is None:
        return

    def _check_item(item, item_loc: str) -> None:
        if not isinstance(item, dict):
            violations.append(f"{item_loc}: input entry is not an object")
            return
        if "basename" not in item:
            violations.append(f"{item_loc}: missing required key 'basename'")
        elif not isinstance(item["basename"], str):
            violations.append(f"{item_loc}.basename: must be a string")
        if "hash" not in item:
            violations.append(f"{item_loc}: missing required key 'hash'")
        elif not isinstance(item["hash"], str):
            violations.append(f"{item_loc}.hash: must be a string")
        if "history" in item:
            _validate_chain(item["history"], f"{item_loc}.history", violations, notes)
        for key in item:
            if key not in _INPUT_ITEM_KNOWN_KEYS:
                notes.append(f"{item_loc}: unknown key {key!r}")

    if isinstance(value, list):
        for j, item in enumerate(value):
            _check_item(item, f"{loc}[{j}]")
        return
    if isinstance(value, dict):
        _check_item(value, loc)
        return
    violations.append(f"{loc}: must be null, an object, or an array of objects")


def _validate_chain(chain, loc: str, violations: list, notes: list) -> None:
    if not isinstance(chain, list):
        violations.append(f"{loc}: value is not a JSON array")
        return
    for i, entry in enumerate(chain):
        eloc = f"{loc}[{i}]"
        if not isinstance(entry, dict):
            violations.append(f"{eloc}: entry is not an object")
            continue
        if "skill" not in entry:
            violations.append(f"{eloc}: missing required key 'skill'")
        elif not isinstance(entry["skill"], str):
            violations.append(f"{eloc}.skill: must be a string")
        elif not entry["skill"]:
            violations.append(f"{eloc}.skill: must be a non-empty string")
        if "version" not in entry:
            violations.append(f"{eloc}: missing required key 'version'")
        elif not isinstance(entry["version"], str):
            violations.append(f"{eloc}.version: must be a string")
        if "args" not in entry:
            violations.append(f"{eloc}: missing required key 'args'")
        elif not isinstance(entry["args"], dict):
            violations.append(f"{eloc}.args: must be an object")
        if "input" not in entry:
            violations.append(f"{eloc}: missing required key 'input'")
        else:
            _validate_input(entry["input"], f"{eloc}.input", violations, notes)
        for key in entry:
            if key not in _ENTRY_KNOWN_KEYS:
                notes.append(f"{eloc}: unknown key {key!r}")


def validate_chain(chain, loc: str) -> tuple[list, list]:
    """Validate a parsed history chain. Returns ``(violations, notes)``."""
    violations: list = []
    notes: list = []
    _validate_chain(chain, loc, violations, notes)
    return violations, notes


def chain_is_intact(history) -> bool:
    """True if ``history`` is a non-empty schema-valid provenance chain."""
    if not isinstance(history, list) or not history:
        return False
    violations, _notes = validate_chain(history, HISTORY_ATTR)
    return not violations


def _load_mark_font(size: int):
    """Prefer a readable TrueType font; fall back to PIL's default bitmap font."""
    from PIL import ImageFont

    candidates = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_official_mark(img):
    """Composite a translucent bottom-right ``weather-skills`` pill onto ``img``.

    No-ops (returns a copy) when the image is too small to hold the mark.
    """
    from PIL import Image, ImageDraw

    w, h = img.size
    if min(w, h) < 64:
        return img.copy()

    # Work in RGBA so the pill can be translucent over any mode.
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    margin = max(4, int(min(w, h) * 0.02))
    font_size = max(10, min(18, int(w * 0.022)))
    font = _load_mark_font(font_size)
    pad_x, pad_y = max(4, font_size // 2), max(2, font_size // 3)

    text = OFFICIAL_MARK_TEXT
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_w, box_h = tw + 2 * pad_x, th + 2 * pad_y
    if box_w + 2 * margin > w or box_h + 2 * margin > h:
        return img.copy()

    x1 = w - margin
    y1 = h - margin
    x0, y0 = x1 - box_w, y1 - box_h
    radius = max(2, box_h // 2)
    # Dark translucent pill (~75% opaque) with white label.
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(20, 24, 28, 192))
    draw.text((x0 + pad_x, y0 + pad_y - bbox[1]), text, fill=(255, 255, 255, 255), font=font)

    marked = Image.alpha_composite(base, overlay)
    if img.mode == "RGBA":
        return marked
    if img.mode == "RGB":
        return marked.convert("RGB")
    return marked.convert(img.mode)


def load_history(zarr_path: Path) -> list:
    """Read a zarr store's history chain; empty on miss or malformation."""
    zarr_path = Path(zarr_path)
    try:
        import xarray as xr

        with xr.open_zarr(zarr_path, consolidated=False) as ds:
            raw = ds.attrs.get(HISTORY_ATTR)
    except (OSError, KeyError, ValueError):
        return []
    if not raw:
        return []
    parsed = coerce_chain(raw, str(zarr_path))
    return [] if parsed is None else parsed


def hash_file(path: Path) -> str:
    """Sha256 of a single file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_ref(path: Path, *, include_hash: bool = True) -> dict:
    """Single-input ``input`` value: ``{basename[, hash]}``."""
    path = Path(path)
    ref = {"basename": path.name}
    if include_hash:
        ref["hash"] = hash_zarr(path)
    return ref


def multi_input_ref(paths, histories) -> list:
    """Multi-input ``input`` value: per-input ``{basename, hash, history}``."""
    paths = [Path(p) for p in paths]
    return [
        {"basename": p.name, "hash": hash_zarr(p), "history": h}
        for p, h in zip(paths, histories, strict=True)
    ]


def build_entry(skill: str, version: str, args: dict, input) -> dict:
    """Assemble a provenance entry."""
    return {"skill": skill, "version": version, "args": args, "input": input}


def stamp_zarr(ds, history: list, *, source: str | None = None) -> None:
    """Stamp history (and optional source) on a dataset; clear encodings."""
    ds.attrs[HISTORY_ATTR] = json.dumps(history, sort_keys=True)
    if source is not None:
        ds.attrs[SOURCE_ATTR] = source
    for v in ds.variables:
        ds[v].encoding = {}


def restamp_zarr(zarr_path: Path, history: list) -> None:
    """Rewrite history on an already-written zarr store in place."""
    import zarr

    group = zarr.open_group(str(zarr_path), mode="r+", use_consolidated=False)
    group.attrs[HISTORY_ATTR] = json.dumps(history, sort_keys=True)
    zarr.consolidate_metadata(str(zarr_path))


def stamp_figure(path: Path, history: list, *, software: str = DEFAULT_SOFTWARE) -> None:
    """Embed ``weather_skills_history`` into a PNG, JPEG, or HTML file.

    When the chain is intact (non-empty and schema-valid), also draw a small
    official ``weather-skills`` corner mark on PNG/JPEG pixels. HTML gets
    metadata only.
    """
    from weather_skills_core.errors import SkillError

    path = Path(path)
    payload = json.dumps(history, sort_keys=True)
    suffix = path.suffix.lower()
    mark = chain_is_intact(history)

    if suffix == ".png":
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        with Image.open(path) as img:
            out = _draw_official_mark(img) if mark else img.copy()
            info = PngInfo()
            for key, value in img.info.items():
                if isinstance(value, str) and key not in (HISTORY_ATTR, "Software"):
                    info.add_text(key, value)
            info.add_text(HISTORY_ATTR, payload)
            info.add_text("Software", software)
            out.save(path, pnginfo=info)
        return

    if suffix in (".jpg", ".jpeg"):
        from PIL import Image

        with Image.open(path) as img:
            out = _draw_official_mark(img) if mark else img.copy()
            if out.mode not in ("RGB", "L"):
                out = out.convert("RGB")
            exif = img.getexif()
            # ASCII UserComment: 8-byte charset header + payload
            exif[_EXIF_USER_COMMENT] = b"ASCII\x00\x00\x00" + payload.encode("ascii")
            out.save(path, exif=exif, quality=95)
        return

    if suffix in (".html", ".htm"):
        text = path.read_text(encoding="utf-8")
        meta = f'<meta name="{HISTORY_ATTR}" content="{html.escape(payload, quote=True)}">'
        if _HTML_META_RE.search(text):
            text = _HTML_META_RE.sub(meta, text, count=1)
        elif re.search(r"<head[^>]*>", text, re.IGNORECASE):
            text = re.sub(r"(<head[^>]*>)", rf"\1\n{meta}", text, count=1, flags=re.IGNORECASE)
        else:
            text = meta + "\n" + text
        path.write_text(text, encoding="utf-8")
        return

    raise SkillError(
        f"unsupported figure type {suffix!r} for {path}; expected .png, .jpg/.jpeg, or .html/.htm"
    )


def load_figure_history(path: Path) -> list | None:
    """Read history from a stamped figure file, or None if absent."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".png":
        from PIL import Image

        with Image.open(path) as img:
            raw = img.info.get(HISTORY_ATTR)
        return coerce_chain(raw, path.name) if raw else None

    if suffix in (".jpg", ".jpeg"):
        from PIL import Image

        with Image.open(path) as img:
            raw = img.getexif().get(_EXIF_USER_COMMENT)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            if raw.startswith(b"ASCII\x00\x00\x00"):
                raw = raw[8:].decode("ascii")
            else:
                raw = raw.decode("utf-8", errors="replace")
        return coerce_chain(raw, path.name)

    if suffix in (".html", ".htm"):
        text = path.read_text(encoding="utf-8")
        m = _HTML_META_RE.search(text)
        if not m:
            return None
        return coerce_chain(html.unescape(m.group(1)), path.name)

    return None
