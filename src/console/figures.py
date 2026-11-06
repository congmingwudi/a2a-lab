"""The console's non-architecture figures and their draw.io twins (D114, extends D113).

Two places hold a console figure's mermaid, and each stays its SOURCE:

  * `src/console/static/index.html` — a `const <NAME>_DIAGRAM = `...`;` template
    literal per Details-pane figure. Figure id: the name, kebab-cased, without the
    `_DIAGRAM` suffix (`COST_SENTINEL_DIAGRAM` → `cost-sentinel`).
  * `config/diagrams.yaml` — the insight-chip diagrams, by their own `id`.

A drawing is `docs/diagrams/<id>.drawio`, built to `static/diagrams/<id>.svg`.
`static/diagrams/manifest.json` records, per id, the digest of the mermaid the
drawing was last checked against (and the built SVG's digest, for cache-busting).
The browser hashes the mermaid it is about to render and compares: a mismatch
means the drawing is stale, and the figure opens on its mermaid instead — the
same honesty rule D113 applies to the plan/09 levels.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
INDEX_HTML = _REPO / "src" / "console" / "static" / "index.html"
DIAGRAMS_YAML = _REPO / "config" / "diagrams.yaml"
FIG_SRC_DIR = _REPO / "docs" / "diagrams"
FIG_SVG_DIR = _REPO / "src" / "console" / "static" / "diagrams"
FIG_MANIFEST_PATH = FIG_SVG_DIR / "manifest.json"

_CONST_RE = re.compile(r"^const ([A-Z][A-Z0-9_]*)_DIAGRAM = `(.*?)`;", re.S | re.M)
# A template literal's escapes, as the browser evaluates them.
_TPL_ESC_RE = re.compile(r"\\([\\`$])")


def const_id(name: str) -> str:
    """`COST_SENTINEL` (the part before `_DIAGRAM`) → `cost-sentinel`."""
    return name.lower().replace("_", "-")


def _template_value(body: str, name: str) -> str:
    # An UNescaped ${ would be a real interpolation — its value is only known at
    # run time, so a digest over the source could never match the browser's.
    if re.search(r"(?<!\\)\$\{", body):
        raise ValueError(f"{name}_DIAGRAM interpolates (${{...}}); figures must be literal")
    return _TPL_ESC_RE.sub(r"\1", body)


def console_figures(html: str | None = None) -> dict[str, str]:
    """{figure id: mermaid} for every `*_DIAGRAM` constant in index.html."""
    html = INDEX_HTML.read_text(encoding="utf-8") if html is None else html
    return {const_id(m.group(1)): _template_value(m.group(2), m.group(1))
            for m in _CONST_RE.finditer(html)}  # fmt: skip


def insight_figures(path: Path = DIAGRAMS_YAML) -> dict[str, str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {d["id"]: d.get("mermaid") or "" for d in doc.get("diagrams") or [] if d.get("id")}


def all_figures() -> dict[str, str]:
    """Every console figure by id. Ids are one namespace — a collision would let
    one figure's drawing stand in for another's, so it is refused."""
    con, ins = console_figures(), insight_figures()
    clash = set(con) & set(ins)
    if clash:
        raise ValueError(f"figure id used by both index.html and diagrams.yaml: {sorted(clash)}")
    return {**con, **ins}
