"""AST extraction of a skill script's declaration surface.

The extractor reads the ``@weather_skill`` call, the ``_SKILL_VERSION``
constant, and the PEP 723 inline-metadata block from the script's source text.
The script is parsed, never imported: extraction works without the script's
dependencies installed and runs none of its code.

Literal declaration values are evaluated; a non-literal value (a name, an
f-string, a call) is recorded as dynamic and excluded from shape comparison,
with a note on the declaration.
"""

import ast
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: Sentinel for a declaration value that is not a literal in the source.
DYNAMIC = "<dynamic>"

# The PEP 723 inline-metadata block grammar (the regular expression given by
# the specification, anchored to the "script" block type by the extractor).
_PEP723_RE = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")

_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize_requirement_name(requirement: str) -> str | None:
    """The normalized project name of one PEP 508 requirement string, or None."""
    m = _REQUIREMENT_NAME_RE.match(requirement)
    if m is None:
        return None
    return re.sub(r"[-_.]+", "-", m.group(1)).lower()


@dataclass(frozen=True)
class ArgShape:
    """The comparable CLI shape of one declared ``Argument`` entry."""

    dest: str
    flags: tuple[str, ...] = ()
    positional: bool = False
    arity: str = "single"  # "single" | "append" | "store_true"
    nargs: object = None
    type_name: str | None = None  # None: the raw CLI string
    choices: tuple | None = None
    required: bool = False
    dynamic_keys: tuple[str, ...] = ()  # dict-spec keys whose values are not literals
    dynamic: bool = False  # the whole spec is not a recognized literal form

    @property
    def primary_flag(self) -> str | None:
        return self.flags[0] if self.flags else None

    @property
    def identity(self) -> str:
        """The name rules match across skills: the primary flag, or the dest."""
        return self.primary_flag or self.dest


@dataclass
class SkillDeclaration:
    """Everything extraction learned about one skill script."""

    skill_dir: Path
    script: Path
    name: str | None = None
    error: str | None = None  # set: the script could not be analyzed at all
    arguments: dict[str, ArgShape] = field(default_factory=dict)
    arguments_dynamic: bool = False  # the declared-flag set is not statically knowable
    input_names: list[str] | None = None
    has_input: bool = False
    input_arity: str = "single"
    has_output: bool = False
    version_constant: bool = False
    version_passed: bool = False
    pep723_deps: list[str] | None = None  # None: no parseable script block
    notes: list[str] = field(default_factory=list)

    # Back-compat aliases used by older rule helpers during migration.
    @property
    def extra_args(self) -> dict[str, ArgShape]:
        return self.arguments

    @property
    def extra_args_dynamic(self) -> bool:
        return self.arguments_dynamic

    @property
    def toggles(self) -> dict:
        return {}

    @property
    def display_name(self) -> str:
        return self.name or self.skill_dir.name

    @property
    def key(self) -> str:
        try:
            return str(self.script.relative_to(self.skill_dir.parent))
        except ValueError:
            return str(self.script)

    def toggle_enabled(self, keyword: str) -> bool:
        return False


def _literal(node):
    """The node's literal value, or DYNAMIC when it is not a plain literal."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return DYNAMIC


def _find_decorator_calls(
    tree: ast.Module,
) -> list[tuple[str, ast.Call, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every ``@weather_skill(...)`` application, as ``(name, call, func)`` in source order."""
    calls: list[tuple[str, ast.Call, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            if func_name == "weather_skill":
                calls.append((node.name, dec, node))
    calls.sort(key=lambda pair: pair[1].lineno)
    return calls


def _dest_from_option_strings(flags: tuple[str, ...], kwargs: dict) -> str:
    if isinstance(kwargs.get("dest"), str):
        return kwargs["dest"]
    flag = next((f for f in flags if f.startswith("--")), flags[0] if flags else "arg")
    return flag.lstrip("-").replace("-", "_")


def _shape_from_add_argument(option_strings, kwargs_node, notes: list[str]) -> ArgShape | None:
    """Build an ArgShape from Argument option strings + kwargs AST nodes."""
    flags_val = _literal(option_strings) if not isinstance(option_strings, list | tuple) else None
    if isinstance(option_strings, ast.Tuple | ast.List):
        flags = []
        for elt in option_strings.elts:
            v = _literal(elt)
            if not isinstance(v, str):
                notes.append("arguments: a non-literal option string was skipped")
                return None
            flags.append(v)
        flags = tuple(flags)
    elif isinstance(flags_val, tuple | list) and all(isinstance(f, str) for f in flags_val):
        flags = tuple(flags_val)
    else:
        notes.append("arguments: option_strings is not a literal sequence of strings")
        return None

    if kwargs_node is None:
        kwargs_node = ast.Dict(keys=[], values=[])
    if not isinstance(kwargs_node, ast.Dict):
        notes.append(f"arguments {flags[0]!r}: kwargs is not a literal dict; recorded as dynamic")
        dest = _dest_from_option_strings(flags, {})
        return ArgShape(dest=dest, flags=flags, dynamic=True)

    spec = {}
    dynamic_keys = []
    for key_node, value_node in zip(kwargs_node.keys, kwargs_node.values, strict=True):
        key = _literal(key_node) if key_node is not None else DYNAMIC
        if key is DYNAMIC or not isinstance(key, str):
            notes.append(f"arguments {flags[0]!r}: a non-literal kwargs key was skipped")
            continue
        if key == "type":
            if isinstance(value_node, ast.Name):
                spec["type"] = value_node.id
            else:
                dynamic_keys.append(key)
            continue
        value = _literal(value_node)
        if value is DYNAMIC:
            dynamic_keys.append(key)
            continue
        spec[key] = value

    positional = bool(flags) and not any(f.startswith("-") for f in flags)
    if positional:
        flag_tuple = ()
        dest = spec.get("dest") or (flags[0] if flags else "arg")
    else:
        flag_tuple = flags
        dest = _dest_from_option_strings(flags, spec)

    if spec.get("action") == "store_true":
        arity = "store_true"
    elif spec.get("action") == "append":
        arity = "append"
    else:
        arity = "single"
    choices = spec.get("choices")
    if choices is not None and isinstance(choices, list | tuple | set):
        choices = tuple(choices)
    elif choices is not None:
        notes.append(f"arguments {dest!r}: 'choices' is not a list; ignored")
        choices = None
    if dynamic_keys:
        notes.append(
            f"arguments {dest!r}: non-literal value(s) for "
            f"{', '.join(sorted(dynamic_keys))} recorded as dynamic"
        )
    return ArgShape(
        dest=dest,
        flags=flag_tuple,
        positional=positional,
        arity=arity,
        nargs=spec.get("nargs"),
        type_name=spec.get("type"),
        choices=choices,
        required=bool(spec.get("required", False)),
        dynamic_keys=tuple(sorted(dynamic_keys)),
    )


def _shape_from_argument_call(call: ast.Call, notes: list[str]) -> ArgShape | None:
    """Build an ArgShape from one ``weather_skill.argument(...)`` / ``Argument(...)`` call."""
    if not call.args:
        notes.append("arguments: argument() has no option strings; skipped")
        return None
    # Positional option strings: argument("--foo", "-f", help="...")
    option_nodes = list(call.args)
    if call.keywords:
        keys = []
        values = []
        for kw in call.keywords:
            if kw.arg is None:
                notes.append("arguments: argument(**kwargs) is dynamic")
                return None
            keys.append(ast.Constant(value=kw.arg))
            values.append(kw.value)
        kwargs_node = ast.Dict(keys=keys, values=values)
    else:
        kwargs_node = ast.Dict(keys=[], values=[])
    return _shape_from_add_argument(
        ast.Tuple(elts=option_nodes, ctx=ast.Load()),
        kwargs_node,
        notes,
    )


def _is_argument_decorator_call(dec: ast.AST) -> bool:
    """True for ``@weather_skill.argument(...)`` (or ``@*.argument(...)``)."""
    if not isinstance(dec, ast.Call):
        return False
    func = dec.func
    return isinstance(func, ast.Attribute) and func.attr == "argument"


def _extract_stacked_arguments(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef, notes: list[str]
) -> tuple[dict[str, ArgShape], bool]:
    """Extract ``@weather_skill.argument(...)`` decorators in source order."""
    shapes: dict[str, ArgShape] = {}
    dynamic = False
    found = False
    for dec in func_node.decorator_list:
        if not _is_argument_decorator_call(dec):
            continue
        found = True
        assert isinstance(dec, ast.Call)
        shape = _shape_from_argument_call(dec, notes)
        if shape is None:
            dynamic = True
            continue
        shapes[shape.dest] = shape
    if not found:
        return {}, False
    return shapes, dynamic


def _extract_arguments_list(node, notes: list[str]) -> tuple[dict[str, ArgShape], bool]:
    """Legacy: extract ``arguments=[Argument(...), ...]`` if still present."""
    if node is None:
        return {}, False
    notes.append(
        "arguments= list is deprecated; use stacked @weather_skill.argument(...) decorators"
    )
    if not isinstance(node, ast.List | ast.Tuple):
        notes.append(
            "arguments is not a literal list; the declared-flag set is unknown, so the "
            "SKILL.md reverse check is suppressed for this script"
        )
        return {}, True
    shapes = {}
    dynamic = False
    for i, elt in enumerate(node.elts):
        if not isinstance(elt, ast.Call):
            notes.append(f"arguments[{i}]: expected Argument(...); skipped")
            dynamic = True
            continue
        func = elt.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name not in ("Argument", "argument"):
            notes.append(f"arguments[{i}]: expected Argument(...); got {name!r}")
            dynamic = True
            continue
        shape = _shape_from_argument_call(elt, notes)
        if shape is None:
            dynamic = True
            continue
        shapes[shape.dest] = shape
    return shapes, dynamic


def _extract_pep723_deps(source: str, notes: list[str]) -> list[str] | None:
    blocks = [m for m in _PEP723_RE.finditer(source) if m.group("type") == "script"]
    if not blocks:
        return None
    if len(blocks) > 1:
        notes.append(
            f"{len(blocks)} PEP 723 script blocks found; a script must have at most one. "
            "Analyzing the first."
        )
    content_lines = []
    for line in blocks[0].group("content").splitlines():
        content_lines.append(line[2:] if line.startswith("# ") else line[1:])
    try:
        metadata = tomllib.loads("\n".join(content_lines))
    except tomllib.TOMLDecodeError as exc:
        notes.append(f"PEP 723 script block is not valid TOML ({exc})")
        return None
    deps = metadata.get("dependencies", [])
    if not isinstance(deps, list):
        notes.append("PEP 723 script block has a non-list dependencies value")
        return None
    return [d for d in deps if isinstance(d, str)]


def extract_script(script: Path, skill_dir: Path) -> SkillDeclaration:
    """Extract one script's declaration surface. Never imports the script."""
    decl = SkillDeclaration(skill_dir=skill_dir, script=script)
    try:
        source = script.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        decl.error = f"script is not valid UTF-8 ({exc})"
        return decl
    except OSError as exc:
        decl.error = f"could not read the script ({exc})"
        return decl
    try:
        tree = ast.parse(source, filename=str(script))
    except SyntaxError as exc:
        decl.error = f"script does not parse (line {exc.lineno}: {exc.msg})"
        return decl

    decl.pep723_deps = _extract_pep723_deps(source, decl.notes)

    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "_SKILL_VERSION":
                decl.version_constant = True

    calls = _find_decorator_calls(tree)
    if not calls:
        decl.error = "no @weather_skill decorator call found"
        return decl
    if len(calls) > 1:
        skipped = ", ".join(func_name for func_name, _, _ in calls[1:])
        decl.notes.append(
            f"{len(calls)} @weather_skill functions in the script; only the first "
            f"({calls[0][0]}) is analyzed; skipped: {skipped}"
        )
    _func_name, call, func_node = calls[0]

    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    if any(kw.arg is None for kw in call.keywords):
        decl.notes.append("declaration spreads **kwargs; those keywords are not analyzed")

    name_node = keywords.get("name")
    if name_node is None and call.args:
        name_node = call.args[0]
        decl.notes.append("pass name= as a keyword; positional name is deprecated")
    if name_node is not None:
        name = _literal(name_node)
        if isinstance(name, str):
            decl.name = name
        else:
            decl.notes.append("skill name is not a string literal; using the directory name")

    version_node = keywords.get("version")
    if version_node is None and len(call.args) > 1:
        version_node = call.args[1]
        decl.notes.append("pass version= as a keyword; positional version is deprecated")
    decl.version_passed = isinstance(version_node, ast.Name) and version_node.id == "_SKILL_VERSION"

    inputs_val = _literal(keywords["inputs"]) if "inputs" in keywords else None
    if inputs_val is DYNAMIC:
        decl.has_input = True
        decl.notes.append("inputs is not a literal; input arity unknown")
    elif isinstance(inputs_val, list | tuple):
        decl.has_input = len(inputs_val) > 0
        # A single "type+" entry is variadic (append); multiple fixed slots also append.
        if len(inputs_val) == 1 and isinstance(inputs_val[0], str) and inputs_val[0].endswith("+") or len(inputs_val) > 1:
            decl.input_arity = "append"
        else:
            decl.input_arity = "single"
    elif inputs_val is not None:
        decl.notes.append(
            f"inputs is a {type(inputs_val).__name__} literal, not a sequence; input arity unknown"
        )

    outputs_val = _literal(keywords["outputs"]) if "outputs" in keywords else None
    if outputs_val is DYNAMIC:
        decl.has_output = True
        decl.notes.append("outputs is not a literal; treated as artifact-writing")
    elif isinstance(outputs_val, list | tuple):
        decl.has_output = len(outputs_val) > 0
    else:
        decl.has_output = outputs_val is not None

    stacked, stacked_dynamic = _extract_stacked_arguments(func_node, decl.notes)
    if "arguments" in keywords:
        legacy, legacy_dynamic = _extract_arguments_list(keywords["arguments"], decl.notes)
        # Prefer stacked when both present; merge stacked over legacy.
        decl.arguments = {**legacy, **stacked}
        decl.arguments_dynamic = stacked_dynamic or legacy_dynamic
    else:
        decl.arguments = stacked
        decl.arguments_dynamic = stacked_dynamic
    return decl


def extract_skill(skill_dir: Path) -> list[SkillDeclaration]:
    """Extract every decorated declaration in a skill directory.

    Reads each ``scripts/*.py``; scripts without a decorator call are helper
    scripts and are ignored when at least one decorated script exists. When no
    script yields a declaration, the per-script error results are returned so
    each carries its analysis failure.
    """
    scripts_dir = skill_dir / "scripts"
    # os.scandir raises on an unlistable directory; Path.glob would suppress
    # the PermissionError and misreport it as "no scripts/*.py found".
    try:
        if scripts_dir.is_dir():
            with os.scandir(scripts_dir) as entries:
                scripts = sorted(
                    Path(entry.path)
                    for entry in entries
                    if entry.name.endswith(".py") and not entry.name.startswith(".")
                )
        else:
            scripts = []
    except OSError as exc:
        return [
            SkillDeclaration(
                skill_dir=skill_dir,
                script=scripts_dir,
                error=f"could not list the scripts directory ({exc})",
            )
        ]
    if not scripts:
        return [
            SkillDeclaration(
                skill_dir=skill_dir,
                script=scripts_dir,
                error="no scripts/*.py found",
            )
        ]
    results = [extract_script(script, skill_dir) for script in scripts]
    ok = [r for r in results if r.error is None]
    return ok if ok else results
