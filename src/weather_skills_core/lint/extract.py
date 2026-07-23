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

_TOGGLE_KEYWORDS = (
    "start_time",
    "end_time",
    "date",
    "bbox",
    "variable",
    "workers",
    "title",
    "dims",
    "time_dim",
)

_BARE_TYPE_NAMES = {"int", "float", "str", "bool"}

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
    """The comparable CLI shape of one declared ``extra_args`` entry."""

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
    toggles: dict = field(default_factory=dict)  # toggle keyword -> literal value or DYNAMIC
    extra_args: dict[str, ArgShape] = field(default_factory=dict)
    input_names: list[str] | None = None
    has_input: bool = False
    input_arity: str = "single"
    has_output: bool = False
    version_constant: bool = False
    version_passed: bool = False
    pep723_deps: list[str] | None = None  # None: no parseable script block
    notes: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or self.skill_dir.name

    def toggle_enabled(self, keyword: str) -> bool:
        """True when a standard toggle keyword is declared with a non-off value.

        A dynamic value counts as enabled: the keyword is present and none of
        the off spellings (absent, ``False``, ``None``) are written literally.
        """
        if keyword not in self.toggles:
            return False
        value = self.toggles[keyword]
        if value is DYNAMIC:
            return True
        return value is not False and value is not None


def _literal(node):
    """The node's literal value, or DYNAMIC when it is not a plain literal."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return DYNAMIC


def _find_decorator_call(tree: ast.Module) -> ast.Call | None:
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
                return dec
    return None


def _default_flag(dest: str) -> str:
    return "--" + dest.replace("_", "-")


def _shape_from_dict_spec(dest: str, node: ast.Dict, notes: list[str]) -> ArgShape:
    spec = {}
    dynamic_keys = []
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        key = _literal(key_node) if key_node is not None else DYNAMIC
        if key is DYNAMIC or not isinstance(key, str):
            notes.append(f"extra_args {dest!r}: a non-literal spec key was skipped")
            continue
        if key == "type":
            # A type value is a callable (``int``, ``float``), never a literal;
            # a plain name is the recognized form.
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

    positional = bool(spec.get("positional", False))
    if positional:
        flags = ()
    else:
        flags = (spec.get("flag", _default_flag(dest)), *spec.get("aliases", ()))
    if spec.get("action") == "store_true":
        arity = "store_true"
    elif spec.get("repeat", False) or spec.get("action") == "append":
        arity = "append"
    else:
        arity = "single"
    choices = spec.get("choices")
    if choices is not None:
        choices = tuple(choices)
    if dynamic_keys:
        notes.append(
            f"extra_args {dest!r}: non-literal value(s) for "
            f"{', '.join(sorted(dynamic_keys))} recorded as dynamic and skipped "
            "from shape comparison"
        )
    return ArgShape(
        dest=dest,
        flags=flags,
        positional=positional,
        arity=arity,
        nargs=spec.get("nargs"),
        type_name=spec.get("type"),
        choices=choices,
        required=bool(spec.get("required", False)),
        dynamic_keys=tuple(sorted(dynamic_keys)),
    )


def _shape_from_set_spec(dest: str, node: ast.Set, notes: list[str]) -> ArgShape:
    type_name = None
    choices = None
    arity = "single"
    dynamic_keys = []
    for element in node.elts:
        if isinstance(element, ast.Name) and element.id in _BARE_TYPE_NAMES:
            if element.id == "bool":
                arity = "store_true"
            else:
                type_name = element.id
        elif (
            isinstance(element, ast.Call)
            and isinstance(element.func, ast.Name)
            and element.func.id == "range"
        ):
            args = [_literal(a) for a in element.args]
            if DYNAMIC in args:
                dynamic_keys.append("choices")
            else:
                choices = tuple(range(*args))
        else:
            value = _literal(element)
            if value is DYNAMIC:
                dynamic_keys.append("choices")
            elif isinstance(value, tuple | list):
                choices = tuple(value)
    if dynamic_keys:
        notes.append(
            f"extra_args {dest!r}: non-literal constraint-set element(s) recorded "
            "as dynamic and skipped from shape comparison"
        )
    return ArgShape(
        dest=dest,
        flags=(_default_flag(dest),),
        arity=arity,
        type_name=type_name,
        choices=choices,
        dynamic_keys=tuple(sorted(set(dynamic_keys))),
    )


def _shape_from_spec(dest: str, node, notes: list[str]) -> ArgShape:
    if isinstance(node, ast.Dict):
        return _shape_from_dict_spec(dest, node, notes)
    if isinstance(node, ast.Set):
        return _shape_from_set_spec(dest, node, notes)
    if isinstance(node, ast.Name) and node.id in _BARE_TYPE_NAMES:
        if node.id == "bool":
            return ArgShape(dest=dest, flags=(_default_flag(dest),), arity="store_true")
        return ArgShape(dest=dest, flags=(_default_flag(dest),), type_name=node.id)
    value = _literal(node)
    if isinstance(value, tuple | list):
        return ArgShape(dest=dest, flags=(_default_flag(dest),), choices=tuple(value))
    notes.append(
        f"extra_args {dest!r}: spec is not a recognized literal form; recorded as "
        "dynamic and skipped from shape comparison"
    )
    return ArgShape(dest=dest, flags=(_default_flag(dest),), dynamic=True)


def _extract_extra_args(node, notes: list[str]) -> dict[str, ArgShape]:
    if node is None:
        return {}
    if not isinstance(node, ast.Dict):
        notes.append(
            "extra_args is not a literal dict; its entries are recorded as dynamic and skipped"
        )
        return {}
    shapes = {}
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        dest = _literal(key_node) if key_node is not None else DYNAMIC
        if dest is DYNAMIC or not isinstance(dest, str):
            notes.append("extra_args: a non-literal dest name was skipped")
            continue
        shapes[dest] = _shape_from_spec(dest, value_node, notes)
    return shapes


def _extract_pep723_deps(source: str, notes: list[str]) -> list[str] | None:
    blocks = [m for m in _PEP723_RE.finditer(source) if m.group("type") == "script"]
    if not blocks:
        return None
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

    call = _find_decorator_call(tree)
    if call is None:
        decl.error = "no @weather_skill decorator call found"
        return decl

    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    if any(kw.arg is None for kw in call.keywords):
        decl.notes.append("declaration spreads **kwargs; those keywords are not analyzed")

    name_node = call.args[0] if call.args else keywords.get("name")
    if name_node is not None:
        name = _literal(name_node)
        if isinstance(name, str):
            decl.name = name
        else:
            decl.notes.append("skill name is not a string literal; using the directory name")

    version_node = call.args[1] if len(call.args) > 1 else keywords.get("version")
    decl.version_passed = isinstance(version_node, ast.Name) and version_node.id == "_SKILL_VERSION"

    for keyword in _TOGGLE_KEYWORDS:
        if keyword in keywords:
            decl.toggles[keyword] = _literal(keywords[keyword])

    input_type = _literal(keywords["input_type"]) if "input_type" in keywords else None
    if input_type is DYNAMIC:
        decl.has_input = True
        decl.notes.append("input_type is not a literal; input arity unknown")
    elif input_type is not None:
        decl.has_input = True
        n_inputs = len(input_type.split(",")) if isinstance(input_type, str) else len(input_type)
        variadic = _literal(keywords["variadic_input"]) if "variadic_input" in keywords else False
        decl.input_arity = "append" if (variadic is True or n_inputs > 1) else "single"

    if "input_names" in keywords:
        input_names = _literal(keywords["input_names"])
        if isinstance(input_names, list | tuple):
            decl.input_names = [str(n) for n in input_names]
        else:
            decl.notes.append("input_names is not a literal list; dedicated input flags unknown")

    output_type = _literal(keywords["output_type"]) if "output_type" in keywords else None
    if output_type is DYNAMIC:
        decl.has_output = True
        decl.notes.append("output_type is not a literal; treated as artifact-writing")
    else:
        decl.has_output = output_type is not None

    decl.extra_args = _extract_extra_args(keywords.get("extra_args"), decl.notes)
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
