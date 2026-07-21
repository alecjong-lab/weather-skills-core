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
| Transform | `input_type` + zarr `output_type` (or `"same"`) | a Dataset |
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
"""Module docstring: what the script is. Not read by the decorator."""

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

The **function docstring** is the `--help` description: the decorator builds
the parser with `description=fn.__doc__` and never reads the module
docstring. When a skill's `--help` description is its full module docstring
(the standalone-script pattern of `description=__doc__`), that full text must
live in the function docstring — a shortened function docstring shortens
`--help`.

Declaration surface (all keyword-only after `name`, `version`):

- `input_type` — `None`, one type, or a comma string / list with one type per
  input. Inputs arrive as `--input`/`-i` (repeated for several), or via
  `input_names=["forecast", "mclimate"]` for dedicated flags, or
  `variadic_input=True` for two-or-more `--input` repeats (the function then
  receives one list of datasets).
- `output_type` — `None`, a zarr envelope type, `"same"`, or `"png"`.
  `"same"` declares a shape-preserving transform: the output is whatever
  envelope type the input carries. Use it (instead of hard-coding one zarr
  type) when `input_type` admits several shapes (`"gridded|forecast"`,
  `"any"`) and the skill preserves whichever came in. It requires at least
  one declared zarr input and writes through the zarr path exactly like an
  explicit zarr type.
- Standard flags, enabled by toggles and passed as keyword arguments:
  `start_time`/`end_time` (`--start`/`--end`), `date` (`--date`), `bbox`
  (`"required"` or `"optional"`; the function receives a parsed
  `(N, W, S, E)` tuple), `variable` (`"single"` or `"repeat"`), `workers`
  (pass the default int), `title`, `dims`, `time_dim`. Standard toggles
  carry fixed, decorator-owned help text; a per-flag help string can only
  be set on `extra_args` entries (the dict spec's `help` key).
- `extra_args` — dest name to a bare type (`int`; `bool` makes a store-true
  flag), a constraint set (`{int, range(0, 2)}` derives `choices`), or an
  argparse-keyword dict (supports `positional`, `flag`, `aliases`, `repeat`,
  and any argparse keyword such as `help`).
- `mutex_groups` — named groups of mutually exclusive `extra_args` (see
  below).
- `input_paths=True` — the function also receives an `input_paths` keyword
  argument: the CLI-given input path(s) as a list of `pathlib.Path`, in
  input order. Use it for diagnostics and messages that name the inputs; the
  datasets still arrive positionally, and the paths never enter the recorded
  provenance args. This is the supported way to learn an input's path — do
  not fish it out of `ds.encoding`.
- Hooks and cache behavior: `latest_resolver`, `source`, `streaming`,
  `cache`, `hash_input`, `completeness_probe`, `validate_args`,
  `normalize_args`, `exclude_args`, `reference_args`, `history_labels`,
  `write_encoding`, `append_dim`, `savefig_kwargs`, `cache_hit_label`.

### Mutually exclusive groups

`mutex_groups` maps a group name to a sequence of `extra_args` dests (an
optional group) or to `{"args": (...), "required": True}`. The decorator
builds a real argparse mutually exclusive group per entry, so usage renders
the `(--a | --b)` bracketing and argparse enforces at-most-one (exactly-one
when required):

```python
@weather_skill(
    "downscale", _SKILL_VERSION,
    input_type="gridded", output_type="gridded",
    extra_args={
        "factor": {"type": float, "aliases": ["-f"]},
        "target_resolution": {"type": float},
        "reference_grid": {},
    },
    mutex_groups={
        "target": {"args": ("factor", "target_resolution", "reference_grid"),
                   "required": True},
    },
)
```

Members must be non-positional `extra_args` entries that do not set their own
`required` (requiredness belongs to the group); a dest may belong to at most
one group, and a group needs at least two members. Declare groups here —
never assemble them by reaching into `wrapper.parser._actions` after
decoration.

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
    cache_hit_label="clip",  # cache-hit line reads "skipping clip."
)
def clip_region(ds, bbox, dims):
    """Spatially subset a gridded weather-skills envelope Zarr."""
    from weather_skills_core.envelope import bbox_subset, detect_spatial_dims

    lat_dim, lon_dim = detect_spatial_dims(ds, dims)
    return bbox_subset(ds, bbox, lat_dim=lat_dim, lon_dim=lon_dim)
```

A typed `input_type="gridded"` composes with `dims=True`: when the caller
passes `--dims LAT,LON`, input validation checks that the overridden names
exist on the dataset instead of running CF/heuristic detection, so an input
with nonstandard dim names validates and reaches the body (the same holds
for `--time-dim`). Overrides participate only in typed validation; an input
declared `any` skips all shape checks.

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
- `cache=False` removes the cache check entirely: the function runs and the
  output is rewritten on every invocation, with the provenance entry still
  built and stamped. Declare it when a meaningful cache key does not exist
  or the recompute is cheaper than the check; it is valid only on zarr
  output types (PNG and no-artifact skills have no cache to disable).
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

### Raw-string parsers and the schema validator

A skill that reads `weather_skills_history` values itself (a provenance
inspector reading zarr attrs or PNG tEXt keys) uses the functions exported by
`weather_skills_core.provenance` instead of reimplementing them:

- `parse_chain(raw)` — strict: returns the chain list, or raises
  `ValueError` with the message `"value is not valid JSON"` or
  `"value is not a JSON array"` (schema checkers such as
  `provenance --check` record the raised message as a violation).
- `coerce_chain(raw, label)` — lenient: returns the chain list, or `None`
  for a value that is not a JSON array, after a one-line stderr warning
  naming `label` (the artifact basename or key being read) and pointing at
  `provenance --check`. A valid array passes through unchanged, even when
  its entries are imperfect.
- `validate_chain(chain, loc)` — validates a parsed chain against the entry
  schema and returns `(violations, notes)`, both lists of location-prefixed
  strings rooted at `loc`. Violations cover a non-array chain, non-object
  entries, and missing or mistyped required entry keys
  (`skill`/`version`/`args`/`input`), recursing into a multi-input entry's
  nested per-branch `history`; unknown/extra keys land in `notes` and do not
  fail validation.

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

### Unprefixed failures

The decorator prints a raised `UsageError`/`DataError` as `Error: <message>`.
Raising with `prefix=False` prints exactly the given message, with no
`Error: ` prefix; the exit code is unchanged (2 for `UsageError`, 1 for
`DataError`):

```python
raise DataError(f"Body too long: {over} characters over the limit.", prefix=False)
```

Two surfaces legitimately need this — and both still raise instead of
calling `sys.exit` (the never-`sys.exit`-from-the-body rule holds):

- exit-code-as-product programs, where the exit code is the skill's result
  and the printed line is a report rather than an error (`provenance
  --check` exits 0/1/2 for valid/absent/invalid);
- machine-consumed retry signals, where a caller parses the stderr text
  verbatim (submit-feedback's over-budget retry contract: stderr starts
  `Body too long: ...`).

Everything else keeps the default prefix.

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

## Decorator-owned stderr lines

These lines are printed by the decorator; a skill body never re-prints its
own version of any of them:

- the resolved-dates line for relative date tokens
  (`resolved "now-1w".."now" -> ... (7 days; ...)`);
- `Cache hit: <output> already matches requested params; skipping <label>.`
  — `<label>` defaults to the skill name; set `cache_hit_label` to change
  the word (e.g. `cache_hit_label="clip"`);
- `Wrote: <output> (<detail>)` — the default detail is the output's sizes
  for a standard zarr skill, `<append_dim>=<total>` for a streaming skill,
  and nothing for a PNG skill. To add or replace detail, return
  `weather_skills_core.WroteSummary("...")` alongside the output (a tuple:
  `return ds, WroteSummary("variable 'precip' -> 'rain'")`; combinable with
  an `EntryOverride`), or yield it from a streaming generator. The text is
  appended after the default detail unless `replace=True`;
- the opaque-input warnings (`no upstream weather_skills_history ...`), the
  incomplete-store re-fetch note, the partial-store removal note, and the
  malformed-history note.

Everything else the body wants to say goes to stderr under its own wording
(stdout stays reserved for load-bearing results).

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
- The core library declares `cftime` (its zarr reads decode model calendars
  such as `360_day`/`noleap`) plus `xarray`/`zarr`/`numpy`/`cf-xarray` — but
  NOT `pandas`, `pint`, or `matplotlib`. The inline header must keep every
  package the script body itself imports; do not drop a dependency on the
  assumption that core carries it.
- A repo-side dependency guard that scans script bodies for `open_zarr`
  calls (a `check_cftime_deps`-style check) cannot see the reads the
  decorator performs on the script's behalf, so it will not flag a missing
  `cftime`; core's own `cftime` dependency is what covers calendar decoding
  on those reads.
- Each script has a sibling `<name>.py.lock`, regenerated with
  `uv lock --script` when the inline dependencies change. Bootstrap window:
  until the weather-skills-core repo is pushed to the git URL above, `uv
  lock --script` cannot resolve the core dependency and lock regeneration is
  impossible — defer the regeneration and record it as a follow-up; never
  skip it silently or hand-edit a `.py.lock`.

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
- [ ] Cache declaration is deliberate: `cache`, `hash_input`,
      `normalize_args`, `exclude_args`, `reference_args`,
      `completeness_probe` each considered.
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
