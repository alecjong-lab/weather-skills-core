"""Ontology for the weather-skills standard dataset.

Dims and types skills declare on ``inputs=``/``outputs=``, plus checks that a
Zarr matches those requirements.
"""

from __future__ import annotations

from dataclasses import dataclass

from weather_skills_core.errors import UsageError

# --- dimension vocabulary -------------------------------------------------

SPACE = "space"
TIME = "time"
INIT_TIME = "init_time"
PREDICTION_TIMEDELTA = "prediction_timedelta"
MEMBER = "member"
DAY_OF_YEAR = "day_of_year"
POINT_ID = "point_id"
X = "x"
Y = "y"

# Ontology dims skills declare in inputs=/outputs=
DIMS = frozenset(
    {SPACE, TIME, INIT_TIME, PREDICTION_TIMEDELTA, MEMBER, DAY_OF_YEAR, POINT_ID, X, Y}
)

# Types → required ontology dims (AND)
TYPE_DIMS: dict[str, frozenset[str]] = {
    "observations": frozenset({SPACE, TIME}),
    "forecast": frozenset({SPACE, INIT_TIME, PREDICTION_TIMEDELTA}),
    "ensemble_forecast": frozenset({SPACE, INIT_TIME, PREDICTION_TIMEDELTA, MEMBER}),
    "point_obs": frozenset({POINT_ID, TIME}),
}

# Type name synonyms → primary key in TYPE_DIMS
TYPE_ALIASES: dict[str, str] = {
    "obs": "observations",
    "analysis": "observations",
    "retrieval": "observations",
    "field": "observations",
    "data": "observations",
    "station": "point_obs",
}

# Dataset / CF names → preferred name. space = preferred lat + lon (not one key).
ALIASES: dict[str, str] = {
    "lat": "lat",
    "latitude": "lat",
    "y": "lat",
    "lon": "lon",
    "longitude": "lon",
    "x": "lon",
    "time": "time",
    "init_time": "init_time",
    "prediction_timedelta": "prediction_timedelta",
    "step": "prediction_timedelta",
    "lead_time": "prediction_timedelta",
    "member": "member",
    "number": "member",
    "realization": "member",
    "point_id": "point_id",
    "station_id": "point_id",
    "day_of_year": "day_of_year",
    "doy": "day_of_year",
}

# Non-dim I/O kinds
UNSTRUCTURED = "unstructured"
FIGURE = "figure"
ANY = "any"


def names_for(preferred: str) -> tuple[str, ...]:
    """Dataset names that map to ``preferred`` in ``ALIASES`` (preferred first)."""
    others = sorted(k for k, v in ALIASES.items() if v == preferred and k != preferred)
    return (preferred, *others)


@dataclass(frozen=True)
class IoSpec:
    """What one ``--input`` / ``--output`` path is allowed to be.

    Built from one entry in ``@weather_skill(inputs=..., outputs=...)`` by
    :func:`normalize_slot`. The decorator uses it for CLI help and to check
    opened Zarrs via :func:`validate_input`.

    Examples (author shorthand → meaning)::

        "forecast"                     # Zarr with forecast dims (see TYPE_DIMS)
        "time"                         # Zarr that has a time dimension
        ("space", "member")            # Zarr that has *both* (tuple = AND)
        ["observations", "forecast"]   # Zarr that matches *either* (list = OR)
        "any"                          # any Zarr; skip dimension checks
        "unstructured"                 # not Zarr; skill gets a Path
        "figure"                       # output image path only
        "any+"                         # one or more matching inputs (``+``)

    Fields:
        kind: ``"zarr"``, ``"unstructured"``, or ``"figure"``.
        alternatives: For Zarr, each item is a set of ontology dims that must
            all be present. The dataset may match any one item. ``None`` means
            no dim check (``"any"``) or a non-Zarr kind.
        variadic: ``True`` if the author wrote a trailing ``+`` (inputs only;
            that entry must be the only ``inputs=`` entry).
        label: Text for help and errors (e.g. ``"forecast"``, ``"any+"``).
    """

    kind: str  # "zarr" | "unstructured" | "figure"
    alternatives: tuple[frozenset[str], ...] | None  # None = any (no dim check)
    variadic: bool = False
    label: str = ""


def expand_atom(atom: str) -> frozenset[str]:
    """Map a type/dim name to required ontology dims (``TYPE_ALIASES`` → ``TYPE_DIMS``, else ``DIMS``/``ALIASES``)."""
    primary = TYPE_ALIASES.get(atom, atom)
    if primary in TYPE_DIMS:
        return TYPE_DIMS[primary]
    if atom in DIMS:
        return frozenset({atom})
    dim = ALIASES.get(atom, atom)  # dataset-name synonyms: doy → day_of_year, …
    if dim in DIMS:
        return frozenset({dim})
    raise ValueError(
        f"unknown IO atom {atom!r}; expected a dimension {sorted(DIMS)}, "
        f"a type {sorted(TYPE_DIMS)}, or any/unstructured/figure"
    )


def and_group(spec) -> frozenset[str]:
    """Flatten a str or AND-tuple into one required dim-set."""
    if isinstance(spec, str):
        return expand_atom(spec)
    if isinstance(spec, tuple):
        if not spec:
            return frozenset()
        out: set[str] = set()
        for part in spec:
            if isinstance(part, (list, tuple)) and not isinstance(part, str):
                if isinstance(part, list):
                    raise ValueError(f"OR lists cannot appear inside an AND tuple; got {part!r}")
                out |= and_group(part)
            elif isinstance(part, str):
                out |= expand_atom(part)
            else:
                raise ValueError(f"invalid AND-group entry {part!r}")
        return frozenset(out)
    raise ValueError(f"expected str or tuple for AND-group, got {type(spec).__name__}")


def parse_alternatives(spec) -> tuple[frozenset[str], ...]:
    """Parse a Zarr IO entry body into OR-alternatives: str / tuple=AND / list=OR → :attr:`IoSpec.alternatives`."""
    if isinstance(spec, str):
        return (expand_atom(spec),)
    if isinstance(spec, tuple):
        return (and_group(spec),)
    if isinstance(spec, list):
        if not spec:
            raise ValueError("OR list must not be empty")
        alts: list[frozenset[str]] = []
        for item in spec:
            if isinstance(item, list):
                raise ValueError(f"nested OR lists are not allowed; got {item!r}")
            alts.extend(parse_alternatives(item))
        return tuple(alts)
    raise ValueError(f"invalid IO entry {spec!r}; expected str, list (OR), or tuple (AND)")


def normalize_slot(raw, *, allow_variadic: bool = False, for_input: bool = True) -> IoSpec:
    """Parse one ``inputs=``/``outputs=`` entry into a :class:`IoSpec` (kinds, ``+``, then dim spec)."""
    variadic = False
    label = repr(raw)
    if isinstance(raw, str) and raw.endswith("+"):
        if not allow_variadic:
            raise ValueError(f"outputs do not support variadic '+'; got {raw!r}")
        variadic = True
        raw = raw[:-1]
        label = raw + "+"

    if isinstance(raw, str):
        if raw == ANY:
            return IoSpec(kind="zarr", alternatives=None, variadic=variadic, label=label or ANY)
        if raw == UNSTRUCTURED:
            return IoSpec(kind="unstructured", alternatives=None, variadic=variadic, label=raw)
        if raw == FIGURE:
            if for_input:
                raise ValueError("figure is output-only")
            return IoSpec(kind="figure", alternatives=None, variadic=False, label=raw)

    alternatives = parse_alternatives(raw)
    label = label if isinstance(raw, str) else repr(raw)
    return IoSpec(kind="zarr", alternatives=alternatives, variadic=variadic, label=label)


def normalize_io_list(
    raw_slots, *, allow_variadic: bool, for_input: bool
) -> tuple[list[IoSpec], bool]:
    """Parse ``inputs=``/``outputs=`` → ``(slots, is_variadic)``; ``+`` must be the sole entry."""
    slots = list(raw_slots or [])
    if not slots:
        return [], False
    parsed = [normalize_slot(s, allow_variadic=allow_variadic, for_input=for_input) for s in slots]
    if any(s.variadic for s in parsed):
        if len(parsed) != 1 or not parsed[0].variadic:
            raise ValueError(
                "variadic IO must be a single entry like 'any+' or 'time+'; "
                "cannot mix fixed and variadic slots"
            )
        return parsed, True
    return parsed, False


def has_dim(ds, dim: str) -> bool:
    """True if ``ds`` has the ontology dim (ALIASES / CF / special space rules)."""
    if dim == SPACE:
        try:
            detect_spatial_dims(ds)
            return True
        except UsageError:
            return False
    if dim == TIME:
        try:
            name = detect_time_dim(ds)
            return name in ds.dims
        except UsageError:
            return False
    if dim == INIT_TIME:
        if "init_time" in ds.dims or "init_time" in ds.coords:
            return True
        # Classic forecast: scalar time coord + lead-time dim
        if "time" in ds.coords and "time" not in ds.dims and ds["time"].ndim == 0:
            return any(ALIASES.get(n) == PREDICTION_TIMEDELTA for n in ds.dims)
        return False
    if dim == PREDICTION_TIMEDELTA:
        return any(ALIASES.get(n) == PREDICTION_TIMEDELTA for n in ds.dims)
    if dim == MEMBER:
        return any(ALIASES.get(n) == MEMBER for n in ds.dims)
    if dim == DAY_OF_YEAR:
        return any(ALIASES.get(n) == DAY_OF_YEAR for n in ds.dims)
    if dim == POINT_ID:
        point = next((n for n in names_for(POINT_ID) if n in ds.dims), None)
        if point is None:
            return False
        lat = next(
            (n for n in names_for("lat") if n in ds.coords and tuple(ds[n].dims) == (point,)), None
        )
        lon = next(
            (n for n in names_for("lon") if n in ds.coords and tuple(ds[n].dims) == (point,)), None
        )
        return lat is not None and lon is not None
    if dim == X:
        return "x" in ds.dims
    if dim == Y:
        return "y" in ds.dims
    return False


def missing_dims(
    ds,
    required: frozenset[str],
    *,
    dims: str | None = None,
    time_dim: str | None = None,
) -> list[str]:
    missing = []
    for d in sorted(required):
        if d == SPACE and dims is not None:
            try:
                detect_spatial_dims(ds, dims)
                continue
            except UsageError:
                missing.append(d)
                continue
        if d == TIME and time_dim is not None:
            try:
                detect_time_dim(ds, time_dim)
                continue
            except UsageError:
                missing.append(d)
                continue
        if not has_dim(ds, d):
            missing.append(d)
    return missing


def validate_dims(
    ds,
    alternatives: tuple[frozenset[str], ...] | None,
    name: str,
    *,
    dims: str | None = None,
    time_dim: str | None = None,
) -> frozenset[str]:
    """Return the first matching dim-set, or raise; ``alternatives is None`` = unconstrained."""
    if alternatives is None:
        return frozenset()
    for alt in alternatives:
        if not missing_dims(ds, alt, dims=dims, time_dim=time_dim):
            return alt
    parts = []
    for alt in alternatives:
        miss = missing_dims(ds, alt, dims=dims, time_dim=time_dim)
        parts.append(f"{{{', '.join(sorted(alt))}}} missing {miss}")
    raise UsageError(
        f"{name} does not satisfy required dimensions ({' OR '.join(parts)}); dims={list(ds.dims)}"
    )


def detect_type(ds) -> str:
    """Classify ``ds`` as a primary key in ``TYPE_DIMS`` (or ``ensemble_forecast``)."""
    if has_dim(ds, POINT_ID):
        return "point_obs"
    if has_dim(ds, PREDICTION_TIMEDELTA) and has_dim(ds, INIT_TIME):
        if has_dim(ds, MEMBER):
            return "ensemble_forecast"
        return "forecast"
    return "observations"


def validate_input(
    ds, allowed, name: str, *, dims: str | None = None, time_dim: str | None = None
) -> str:
    """Check ``ds`` against an IoSpec or IO declaration; return ``detect_type``."""
    if isinstance(allowed, IoSpec):
        if allowed.kind != "zarr":
            return allowed.kind
        validate_dims(ds, allowed.alternatives, name, dims=dims, time_dim=time_dim)
        return detect_type(ds)

    # String / list / tuple slot body (including legacy "data")
    if isinstance(allowed, (str, list, tuple)):
        if (
            isinstance(allowed, list)
            and allowed
            and all(
                isinstance(x, str)
                and x
                in (
                    "data",
                    "forecast",
                    "station",
                    "point_obs",
                    "observations",
                    "any",
                )
                for x in allowed
            )
        ):
            mapped = [
                ("observations" if x == "data" else "point_obs" if x == "station" else x)
                for x in allowed
            ]
            slot = normalize_slot(mapped if len(mapped) > 1 else mapped[0], for_input=True)
        else:
            body = (
                "observations"
                if allowed == "data"
                else "point_obs"
                if allowed == "station"
                else allowed
            )
            slot = normalize_slot(body, for_input=True)
        if slot.kind != "zarr":
            return slot.kind
        validate_dims(ds, slot.alternatives, name, dims=dims, time_dim=time_dim)
        return detect_type(ds)

    raise ValueError(f"invalid validate_input allowed={allowed!r}")


def detect_spatial_dims(ds, override: str | None = None) -> tuple:
    """Find lat/lon dim names: optional ``LAT,LON`` override, then CF, then ALIASES."""
    if override:
        parts = [s.strip() for s in override.split(",")]
        if len(parts) != 2:
            raise UsageError("--dims must be two comma-separated names: LAT,LON.")
        lat_dim, lon_dim = parts
        if lat_dim not in ds.dims or lon_dim not in ds.dims:
            raise UsageError(f"--dims names not in dataset dims {list(ds.dims)}")
        return lat_dim, lon_dim
    try:
        import cf_xarray  # noqa: F401 -- registers the .cf accessor

        return ds.cf["latitude"].name, ds.cf["longitude"].name
    except KeyError:
        pass
    lat_dim = next((n for n in names_for("lat") if n in ds.dims), None)
    lon_dim = next((n for n in names_for("lon") if n in ds.dims), None)
    if lat_dim is None or lon_dim is None:
        raise UsageError(
            f"could not identify lat/lon coords via CF metadata or name "
            f"heuristics in {list(ds.coords)}. Pass --dims to override."
        )
    return lat_dim, lon_dim


def detect_time_dim(ds, override: str | None = None) -> str:
    """Find the time dim: optional name override, then CF, then literal ``time``."""
    if override:
        if override not in ds.dims:
            raise UsageError(f"--time-dim {override!r} not in dataset dims {list(ds.dims)}")
        return override
    try:
        import cf_xarray  # noqa: F401 -- registers the .cf accessor

        name = ds.cf["time"].name
        if name in ds.dims:
            return name
    except KeyError:
        pass
    if "time" in ds.dims:
        return "time"
    raise UsageError(
        f"could not identify a time dim via CF metadata or name heuristics in "
        f"{list(ds.dims)}. Pass --time-dim to override."
    )
