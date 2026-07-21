---
name: weather-skill-authoring
description: The playbook for writing a weather skill on the weather_skills_core @weather_skill decorator. Covers the envelope contract, the declaration surface for all five skill classes (transform, fetcher, streaming fetcher, plot, no-artifact), the date grammar, provenance and caching, units rules, error handling, credentials, versioning, and the PEP 723 script layout. Use when creating a new skill, converting an existing one onto the decorator, or reviewing a skill for conformance.
---

# weather-skill-authoring

How to write a weather skill. A skill is a directory `skills/<name>/` holding a
**SKILL.md manifest** and a single-file **`scripts/<name>.py`** script whose CLI,
input reading, envelope validation, provenance, caching, and output writing are
owned by the `@weather_skill` decorator from `weather_skills_core`. The script
body holds only domain logic.

## Read these first

- `ENVELOPE.md` (forecasting-skills) — the artifact contract: envelope shapes,
  the `weather_skills_history` schema, CF compliance, write rules.
- `CONVENTIONS.md` (forecasting-skills) — canonical CLI flag names and the
  relative-or-absolute date grammar. A flag that does the same thing on
  different skills has the same name; match the table.
- `CONTRIBUTING.md` (forecasting-skills) — the publish model and the
  version-bump workflow.

## The five skill classes

| Class | Declaration shape | Function returns |
| --- | --- | --- |
| Transform | `input_type` + zarr `output_type` | a Dataset |
| Fetcher | no `input_type`, zarr `output_type`, `source=` | a Dataset |
| Streaming fetcher | fetcher + `streaming=True` | a generator of per-period Datasets |
| Plot | `input_type` + `output_type="png"` | a matplotlib Figure |
| No-artifact | no `output_type` | anything (ignored) |

## The envelope contract

Every zarr input and output is a weather-skills envelope: a CF-compliant Zarr
store plus the `weather_skills_history` provenance attr. Shapes:

- `gridded` — `latitude`/`longitude` dims (aliases accepted on input) with a
  `time` dim;
- `forecast` — a `step` (lead time) dim plus a scalar `time` coord for the
  init date;
- `station` — a `station_id` dim with 1-D `latitude(station_id)` /
  `longitude(station_id)` coords and a `time` dim.

Declare each input's shape in `input_type` (use `any` to opt out of shape
validation); the decorator validates on open and exits 2 with a message naming
the offending dim. Outputs are written `consolidated=True`, missing data is
NaN (never a sentinel), and per-variable `encoding` is not part of the
contract — the decorator clears it on write.

## Declaring a skill

The script is a PEP 723 single file. Skeleton:

```python
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""One-line summary; becomes the argparse description."""

from weather_skills_core import weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.1.0"


@weather_skill("my-skill", _SKILL_VERSION, ...)
def my_skill(ds, ...):
    """Docstring shown as the CLI description."""
    ...


if __name__ == "__main__":
    my_skill()
```

Declaration surface (all keyword-only after `name`, `version`):

- `input_type` — `None`, one type, or a comma string / list with one type per
  input. Inputs arrive as `--input`/`-i` (repeated for several), or via
  `input_names=["forecast", "mclimate"]` for dedicated flags, or
  `variadic_input=True` for two-or-more `--input` repeats (the function then
  receives one list of datasets).
- `output_type` — `None`, a zarr envelope type, or `"png"`.
- Standard flags, enabled by toggles and passed as keyword arguments:
  `start_time`/`end_time` (`--start`/`--end`), `date` (`--date`), `bbox`
  (`"required"` or `"optional"`; the function receives a parsed
  `(N, W, S, E)` tuple), `variable` (`"single"` or `"repeat"`), `workers`
  (pass the default int), `title`, `dims`, `time_dim`.
- `extra_args` — dest name to a bare type (`int`; `bool` makes a store-true
  flag), a constraint set (`{int, range(0, 2)}` derives `choices`), or an
  argparse-keyword dict (supports `positional`, `flag`, `aliases`, `repeat`).
- Hooks and cache behavior: `latest_resolver`, `source`, `streaming`,
  `hash_input`, `completeness_probe`, `validate_args`, `normalize_args`,
  `exclude_args`, `reference_args`, `history_labels`, `write_encoding`,
  `append_dim`, `savefig_kwargs`.

The function receives the opened input dataset(s) positionally, then the
resolved parameters as keyword arguments. Raise
`weather_skills_core.UsageError` for usage/validation failures (exit 2) and
`weather_skills_core.DataError` for data-availability or hard failures
(exit 1). Never call `sys.exit` from the body.

Defer heavy imports (`xarray`, `numpy`, plotting, client libraries) into the
function body so `--help` and cache hits stay cheap; `weather_skills_core`
itself defers them.

### Worked example: transform

```python
@weather_skill(
    "clip-region", _SKILL_VERSION,
    input_type="gridded", output_type="gridded",
    bbox="required", dims=True,
    hash_input=False,  # cheap cache check; hash computed only on a miss
)
def clip_region(ds, bbox, dims):
    """Spatially subset a gridded weather-skills envelope Zarr."""
    from weather_skills_core.envelope import bbox_subset, detect_spatial_dims

    lat_dim, lon_dim = detect_spatial_dims(ds, dims)
    return bbox_subset(ds, bbox, lat_dim=lat_dim, lon_dim=lon_dim)
```

The decorator writes the returned Dataset: it carries the first input's attrs
forward, stamps the provenance chain, clears encodings, and replaces the
output store atomically enough for the cache contract. Do not open or write
zarr yourself.

### Worked example: fetcher with a `latest` resolver

```python
def _latest(args):
    """Newest date with available data. One bounded discovery call."""
    import xarray as xr
    ...
    return newest_date  # a datetime.date


def _store_is_complete(out):
    """Corner-read probe: True when a candidate cache hit actually reads back."""
    import xarray as xr
    ...


@weather_skill(
    "oisst-fetch", _SKILL_VERSION,
    output_type="gridded", source="oisst",
    start_time=True, end_time=True, bbox="optional",
    latest_resolver=_latest,
    completeness_probe=_store_is_complete,
)
def fetch(start_time, end_time, bbox):
    """Fetch daily SST and write a weather-skills envelope Zarr."""
    import xarray as xr
    ...
    return ds
```

`start_time`/`end_time` arrive as resolved `datetime.date` objects. The
resolver runs lazily and at most once, only when a token references `latest`;
an all-absolute invocation performs zero network before the cache check.

### Worked example: streaming fetcher

```python
from weather_skills_core import EntryOverride


def _set_write_encoding(ds):
    """Controlled write encodings, applied after the decorator's encoding clear."""
    import numpy as np

    ds["time"].encoding.update(units="days since 1970-01-01 00:00:00", calendar="standard")
    ds["sst"].encoding["_FillValue"] = np.float32("nan")


@weather_skill(
    "oisst-fetch", _SKILL_VERSION,
    output_type="gridded", source="oisst",
    start_time=True, end_time=True, bbox="optional",
    streaming=True, append_dim="time",
    write_encoding=_set_write_encoding,
)
def fetch(start_time, end_time, bbox):
    """Fetch daily SST, one period per yield, bounded memory."""
    days = plan_days(start_time, end_time)
    if days and days[-1] != end_time:
        # Trailing days not yet published: record the effective window.
        yield EntryOverride({"end": days[-1].isoformat()})
    for day in days:
        yield fetch_one_day(day, bbox)
```

Yield one Dataset per period. The decorator writes the first with
`mode="w"` and appends the rest along `append_dim`, re-stamping provenance on
every append, and removes a partial store on any mid-stream failure. Yield an
`EntryOverride` (before or between datasets) to rewrite the recorded args;
the last stamp is the one that persists.

### Worked example: plot

```python
@weather_skill(
    "plot-compare", _SKILL_VERSION,
    input_type=["any", "any"], output_type="png",
    history_labels=["a", "b"], title=True,
    savefig_kwargs={"bbox_inches": "tight"},
)
def plot_compare(ds_a, ds_b, title):
    """Render two inputs as stacked heatmap rows."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2)
    ...
    return fig
```

Return the Figure; the decorator saves it with each input branch's full
history embedded in the PNG metadata (`weather_skills_history` for a single
input; `weather_skills_history_<label>` per declared label otherwise, plus a
`Software` key). Plot skills have no cache: they always render.

### Worked example: no-artifact

```python
@weather_skill(
    "resolve-region", _SKILL_VERSION,
    extra_args={"code": {"positional": True, "metavar": "CODE"}, "geojson": str},
)
def resolve_region(code, geojson):
    """Resolve an ISO 3166-1 alpha-3 country code to an N/W/S/E bbox."""
    print("12.0/33.9/-4.7/41.9")  # stdout is load-bearing: callers consume it
```

No provenance, no cache, no output flag — the decorator provides the CLI and
the version epilog. Keep stdout limited to the machine-consumed result; all
diagnostics go to stderr.

## The date grammar, from the author's side

You never parse date tokens. Declare `start_time`/`end_time` (or `date`) and
the decorator applies the full grammar from CONVENTIONS.md: absolute
`YYYY-MM-DD`, `now`/`today`, `latest`, `now/latest-N{d,w}` offsets with the
36525-day cap, inclusive endpoints, and the duration idiom (`latest-3w ..
latest` is exactly 21 days inclusive of `latest`). Malformed tokens, month or
year units, future offsets, and reversed ranges exit 2 before any network
call; relative resolutions print a stderr line with the resolved dates, the
day count, and the boundary reason. Your only obligation is the
`latest_resolver` callable for sources that support `latest` — one bounded
discovery call returning a `datetime.date`.

## Provenance and caching

The decorator computes the provenance entry — skill, version, the recorded
args, and the input reference(s) — **before** your function runs; on a cache
hit it returns without calling you or touching the store. What you control:

- The recorded args are the argparse namespace minus input/output path
  strings, with resolved absolute dates (never relative tokens) and
  `--workers` excluded. Use `normalize_args` to canonicalize (sort a repeated
  `--variable`, coerce types) so flag order cannot cause spurious misses, and
  `exclude_args` for any other pure-concurrency or presentation knob.
- `hash_input=False` defers the input content hash until after a cheap cache
  check (the stamped entry still carries the hash). Keep the default when a
  modified same-named input must force a recompute.
- `reference_args` names arg dests holding secondary reference stores
  (a reference grid, a distribution reference); their content hashes enter
  the cache key as `reference_inputs`.
- `completeness_probe` guards fetcher hits against a truncated prior store:
  a cheap corner-element read, not a metadata check.
- `validate_args` runs before the cache check — an invalid argument must
  never report a cache hit.

Everything else — chain append on the first input's trunk, per-branch
histories for multi-input entries, legacy attribute migration, the
`weather_skills_source` stamp, PNG metadata keys — the decorator does for you.

## Units

Units are the single most error-prone surface. For any skill that produces or
relabels data variables:

- **Pass the source's units through verbatim by default.**
- **Remap only** when the source value is a valid unit spelled in a form
  udunits will not accept — relabel to the conformant spelling of the *same*
  unit. Never remap a unit that already parses.
- **Never convert numeric values** to land in a different unit. The one
  principled exception is a documented integer storage encoding with no unit
  of its own (e.g. "tenths of a mm"); declare it as a value conversion.
- Validate every output data-variable unit with a real udunits check
  (`cf_units.Unit(...)`); a missing or empty unit is invalid — drop the
  variable with a note or fail, never write `units=None`.
- `standard_name` must match the unit family; verify the exact string against
  the current CF standard-name table before stamping it, and omit it when no
  verified entry cleanly applies (that is CF-valid).

Unit *conversion* is its own skill (`unit-convert`); do not fold conversions
into fetchers or transforms.

## The source-to-output transform declaration

In a fetcher, declare every divergence between the raw source and the written
output in one labeled comment block near the top of the script: every unit
remap (with the same-unit-made-to-comply reason), variable rename, value
conversion, and standard_name/long_name assignment. Pass-through is the
unstated default; a reader must be able to reconstruct the entire
source-to-output delta from the block alone.

## Errors: reactive, never proactive

The user decides what to fetch or compute. Never refuse a request because it
looks big: no pre-flight size estimates, no cell-count thresholds, no
"large/slow" warnings. (A *required* `--bbox` for a source whose global query
is genuinely unbounded is a missing-argument error, not a size guard.)

Handle real failures reactively with one-line, actionable messages that tell
the calling agent what to change, classified where the remedies differ:

- provider-rejected-oversized — "reduce `--bbox` / shorten the window;
  retrying unchanged will not help";
- availability (outside the served range, not yet published) — distinct from
  transport;
- transport (network/timeout) — distinct from availability;
- auth — see Credentials.

Raise `UsageError`/`DataError` with the message; never let a known failure
mode reach the user as a raw traceback.

## Credentials

For a credentialed source: read the credential from the environment with a
presence check and exit with a clear "set `<ENV_VAR>`" message when unset;
hand the value straight to the auth library or an HTTP header; never print,
log, or echo it anywhere, including in error messages. Classify auth failures
(HTTP 401/403, login-library errors) into a one-line actionable message
without echoing the key; a per-item auth failure mid-run is fatal and
surfaced, not silently dropped. Declare the required env var in the SKILL.md
frontmatter metadata so the runner knows it is needed.

## What the decorator does for you

Do not re-implement these in a skill body:

- CLI construction, the `--bbox` negative-north argv rewrite, the
  `skill version:` epilog, exit-code mapping.
- Input open, envelope validation, the input/output overlap guard.
- Date-grammar parsing, `latest` memoization, the resolved-dates stderr line.
- The cache key, the cache-hit short-circuit, cache-completeness probing.
- Provenance: entry construction, chain append, multi-input branch histories,
  legacy attribute migration, PNG metadata.
- Writing: encoding clear (set controlled write encodings via
  `write_encoding`, which runs after the clear), `consolidated=True`,
  streaming first-write/append ordering, partial-store rollback on failure.

## Versioning

`_SKILL_VERSION` sits at the top of the script and is passed to the decorator
so it lands in the epilog and every provenance entry. CI owns it: the
version-bump workflow updates `_SKILL_VERSION` and the SKILL.md
`metadata.version` in lockstep on merge, and a consistency check fails the PR
when they disagree. Never edit either by hand, and keep the constant's
one-line assignment shape so the bump tooling's regex continues to match.

## Script and lockfile layout

- One file: `skills/<name>/scripts/<name>.py`, runnable with
  `uv run --script`.
- Dependencies go in the PEP 723 inline header, including
  `weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core`.
  No `uv add`, no shared helper module in the skills repo.
- Each script has a sibling `<name>.py.lock`, regenerated with
  `uv lock --script` when the inline dependencies change.

## Where tests live

Skill behavior is tested in the weather-skills-core repo — the grammar,
envelope, provenance, and decorator suites — never in forecasting-skills.
Do not add unit tests, a `tests/` directory, doctests, self-test modes, or
CI test steps to a skills repo; its check surface is ruff, inline-dep
validation, and one `--help` invocation per script. If a change seems to need
a test to be correct, add the test to weather-skills-core (extending the core
if the behavior belongs there) or raise it with the maintainer.

## SKILL.md (the skill's own docs)

- Describe **current behavior** only — no "previously", "used to", or
  "no longer".
- Examples use realistic, bounded selections, with no narration about why the
  example was chosen; state the real cost model once in a performance note
  and let the examples be examples.
- Document the reactive error catalog and, for a credentialed source, the
  missing/wrong-key behavior; keep the runner's required-env metadata block.

## Creation checklist

Before calling a skill done, confirm:

- [ ] The declaration matches CONVENTIONS.md flag names exactly; new concepts
      are added to that file in the same PR.
- [ ] The body holds domain logic only — nothing from "What the decorator
      does for you" is re-implemented.
- [ ] Heavy imports are deferred into the function body; `--help` runs
      without them.
- [ ] Failures raise `UsageError`/`DataError` with one-line actionable
      messages, classified by remedy; no proactive size guard anywhere.
- [ ] Units: verbatim pass-through or a declared same-unit compliance remap,
      udunits-validated; fetchers carry the source-to-output transform block.
- [ ] (Credentialed) no credential value is ever printed or echoed; auth
      failures classified; required env declared in frontmatter metadata.
- [ ] `write_encoding` sets any controlled time units/calendar and
      `_FillValue`; nothing else touches `.encoding`.
- [ ] Cache declaration is deliberate: `hash_input`, `normalize_args`,
      `exclude_args`, `reference_args`, `completeness_probe` each considered.
- [ ] `_SKILL_VERSION` untouched by hand; PEP 723 header carries the core git
      dependency; `<name>.py.lock` present.
- [ ] No tests in the skills repo; new behavior is covered in
      weather-skills-core.
- [ ] SKILL.md: current-behavior only, bounded examples, reactive-error
      catalog documented.

## Updating this playbook

This is a living document. When the skill paradigm shifts — a new declaration
parameter, a refined units case, a different error classification — update
the relevant section here in the same change that establishes it, so the next
skill inherits the lesson. Each rule reads as a current-behavior statement,
not a history of how it changed.
