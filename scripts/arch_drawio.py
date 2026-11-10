"""The hand-drawn draw.io twins of the plan/09 architecture diagrams (D113).

plan/09-deployment-map.md stays the SOURCE: each `## L<n>` level's mermaid fence
is the fact base, and the console parses it on every request. The draw.io
drawings are a second rendering of the same facts with a hand-placed layout, so
they can drift — the mermaid gets edited when the deployment changes, and a
drawing does not redraw itself. This script is what keeps that honest:

  build  [LEVEL ...]   docs/architecture/<id>.drawio → src/console/static/arch/<id>.svg
                       (light theme, text fallbacks stripped — see _slim)
  stamp  LEVEL ...     record that <id>.drawio now matches the level's CURRENT
                       mermaid. Run it only after redrawing (or checking) the level.
  status [--json]      per level: drawn? stale (mermaid changed since the stamp)?
                       labels in the mermaid that the drawing does not contain?
  check                status, exiting 1 if any drawn level is stale or missing labels

Every verb covers the console's other figures too (D114): the `*_DIAGRAM`
constants in src/console/static/index.html and the insight diagrams in
config/diagrams.yaml, by figure id (`cost-sentinel`, `platform-map`, ...). Their
drawings live in docs/diagrams/, build to src/console/static/diagrams/, and
stamp into that directory's manifest — which the BROWSER checks, by hashing the
mermaid it is about to render.

The console reads the manifest: a stale level opens on its mermaid, not its
drawing, so a visitor is never shown a picture of the old shape by default.

    uv run python scripts/arch_drawio.py status
    uv run python scripts/arch_drawio.py build L4 && uv run python scripts/arch_drawio.py stamp L4
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from console.architecture import (  # noqa: E402
    ARCH_SVG_DIR,
    DOC_PATH,
    DRAWIO_SRC_DIR,
    MANIFEST_PATH,
    mermaid_digest,
    parse,
)
from console.figures import (  # noqa: E402
    FIG_MANIFEST_PATH,
    FIG_SRC_DIR,
    FIG_SVG_DIR,
    all_figures,
)

# A quoted mermaid label: node `ID["..."]`, `ID(["..."])`, `ID[("...")]`,
# `ID{"..."}`, a subgraph title `subgraph X["..."]`, or an edge `|"..."|`.
_NODE_LABEL_RE = re.compile(r"\b[\w-]+\s*[\[\(\{]+[/\\]?\"([^\"]*)\"")
_EDGE_LABEL_RE = re.compile(r"\|\"?([^|\"]+)\"?\|")
_TAG_RE = re.compile(r"<[^>]+>")


def _norm(text: str) -> str:
    text = html.unescape(html.unescape(text))
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def mermaid_labels(src: str) -> list[str]:
    labels = _NODE_LABEL_RE.findall(src) + _EDGE_LABEL_RE.findall(src)
    out = []
    for raw in labels:
        n = _norm(raw)
        if n and n not in out:
            out.append(n)
    return out


def drawio_text(path: Path) -> str:
    """Every cell's label, normalised and joined — what a reader can see."""
    tree = ET.parse(path)
    values = [c.get("value", "") for c in tree.iter("mxCell")]
    values += [c.get("label", "") for c in tree.iter("object")]
    # Newline-joined: _norm collapses every whitespace run, so no label can
    # contain one — unlike " | ", which a label may (`managed | sdk backend`).
    return "\n".join(_norm(v) for v in values if v)


def missing_labels(mermaid: str, drawio: Path) -> list[str]:
    """Mermaid labels the drawing does not contain. Compared segment by segment
    (a `<br/>` line each), because a drawing may legitimately re-flow a
    two-line label into a bold title and a muted detail line."""
    text = drawio_text(drawio)
    flat = text.replace("\n", " ")
    missing = []
    for label in mermaid_labels(mermaid):
        if label in flat:
            continue
        segs = [_norm(s) for s in re.split(r"<br\s*/?>", label) if _norm(s)]
        if segs and all(s in flat for s in segs):
            continue
        missing.append(label)
    return missing


class Target:
    """One drawable diagram: a plan/09 level, or a console figure (D114)."""

    def __init__(self, tid: str, mermaid: str, src_dir: Path, svg_dir: Path, manifest: Path):
        self.id, self.mermaid = tid, mermaid
        self.drawio = src_dir / f"{tid}.drawio"
        self.svg = svg_dir / f"{tid}.svg"
        self.manifest = manifest


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _manifest() -> dict:
    return _load(MANIFEST_PATH)


def _levels() -> dict[str, str]:
    doc = parse((ROOT / DOC_PATH).read_text(encoding="utf-8"))
    return {lv["id"]: lv["mermaid"] for lv in doc["levels"] if lv["mermaid"]}


def _targets() -> dict[str, Target]:
    out = {lid: Target(lid, m, DRAWIO_SRC_DIR, ARCH_SVG_DIR, MANIFEST_PATH)
           for lid, m in _levels().items()}  # fmt: skip
    for fid, m in all_figures().items():
        if fid in out:
            sys.exit(f"figure id {fid} collides with a plan/09 level id")
        out[fid] = Target(fid, m, FIG_SRC_DIR, FIG_SVG_DIR, FIG_MANIFEST_PATH)
    return out


def _write_manifest(path: Path, man: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")


def _drawio_cli() -> str:
    for cand in (
        shutil.which("drawio"),
        "/Applications/draw.io.app/Contents/MacOS/draw.io",
        str(Path.home() / "Applications/draw.io.app/Contents/MacOS/draw.io"),
    ):
        if cand and Path(cand).exists():
            return cand
    sys.exit("draw.io desktop CLI not found — install draw.io Desktop (brew install --cask drawio)")


def _slim(svg: str) -> str:
    """Make a draw.io SVG fit to inline into the console.

    The CLI rasterises every HTML label to a base64 PNG as a <switch> fallback
    for renderers without foreignObject — ~90% of the file (a 0.7 MB SVG has
    ~22 KB of drawing). Every browser the console supports renders
    foreignObject, so the fallback is dropped. The XML prolog, DOCTYPE and
    comment go too: the console injects this as innerHTML, where they are noise.
    """
    # Only the fallback — the <image> that closes a <switch>. An icon cell is an
    # <image> too, and must survive.
    svg = re.sub(r"<image\b[^>]*/>(?=</switch>)", "", svg)
    svg = re.sub(r"<text\b[^>]*>[^<]*</text>(?=</switch>)", "", svg)
    svg = re.sub(r"^\s*<\?xml[^>]*>\s*", "", svg)
    svg = re.sub(r"<!--.*?-->\s*", "", svg, flags=re.S)
    svg = re.sub(r"<!DOCTYPE[^>]*>\s*", "", svg)
    return svg.strip() + "\n"


def build(ids: list[str]) -> None:
    cli = _drawio_cli()
    targets = _targets()
    drawn = {t.id: t for t in targets.values() if t.drawio.exists()}
    unknown = set(ids) - set(drawn)
    if unknown:
        sys.exit(f"no .drawio source (docs/architecture or docs/diagrams) for: "
                 f"{', '.join(sorted(unknown))}")  # fmt: skip
    for tid, t in drawn.items():
        if ids and tid not in ids:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.svg"
            subprocess.run(
                [cli, "-x", "-f", "svg", "--theme", "light", "-b", "10", "-o", str(out),
                 str(t.drawio)],
                check=True,
                capture_output=True,
            )  # fmt: skip
            raw = out.read_text(encoding="utf-8")
        t.svg.parent.mkdir(parents=True, exist_ok=True)
        t.svg.write_text(_slim(raw), encoding="utf-8")
        print(
            f"{tid}: {t.drawio.relative_to(ROOT)} → {t.svg.relative_to(ROOT)} "
            f"({len(raw) // 1024} KB → {t.svg.stat().st_size // 1024} KB)"
        )


def stamp(ids: list[str]) -> None:
    targets = _targets()
    for tid in ids:
        t = targets.get(tid)
        if t is None or not t.mermaid:
            sys.exit(f"{tid}: no such level ({DOC_PATH}) or console figure with a mermaid diagram")
        if not t.drawio.exists():
            sys.exit(f"{tid}: {t.drawio.relative_to(ROOT)} does not exist")
        miss = missing_labels(t.mermaid, t.drawio)
        if miss:
            sys.exit(
                f"{tid}: refusing to stamp — the drawing lacks {len(miss)} mermaid "
                f"label(s): {miss[:5]}"
            )
        man = _load(t.manifest)
        entry = man.setdefault(tid, {})
        entry["mermaid_sha256"] = mermaid_digest(t.mermaid)
        if t.manifest == FIG_MANIFEST_PATH:
            # The browser fetches figure SVGs by URL with no server in between, so
            # the manifest carries the content hash that cache-busts them. Written
            # here, not in `build`: builds run in parallel (the sweep), stamps run
            # once, serially — the manifest is read-modify-write.
            if not t.svg.exists():
                sys.exit(f"{tid}: build it first — {t.svg.relative_to(ROOT)} does not exist")
            entry["svg_sha256"] = hashlib.sha256(t.svg.read_bytes()).hexdigest()
        _write_manifest(t.manifest, man)
        print(f"{tid}: stamped against mermaid {man[tid]['mermaid_sha256'][:12]}")


def status() -> list[dict]:
    rows = []
    mans: dict[Path, dict] = {}
    for tid, t in _targets().items():
        kind = "level" if t.manifest == MANIFEST_PATH else "figure"
        row = {"level": tid, "kind": kind, "drawn": t.drawio.exists(), "built": t.svg.exists()}
        if t.drawio.exists():
            man = mans.setdefault(t.manifest, _load(t.manifest))
            stamped = (man.get(tid) or {}).get("mermaid_sha256")
            row["stale"] = stamped != mermaid_digest(t.mermaid)
            row["missing_labels"] = missing_labels(t.mermaid, t.drawio)
            row["svg_older_than_source"] = (
                not t.svg.exists() or t.svg.stat().st_mtime < t.drawio.stat().st_mtime
            )
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("levels", nargs="*")
    s = sub.add_parser("stamp")
    s.add_argument("levels", nargs="+")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    sub.add_parser("check")
    args = ap.parse_args()

    if args.cmd == "build":
        build(args.levels)
    elif args.cmd == "stamp":
        stamp(args.levels)
    else:
        rows = status()
        if getattr(args, "json", False):
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                if not r["drawn"]:
                    print(f"{r['level']:22} not drawn (console shows mermaid only)")
                    continue
                flags = []
                if r["stale"]:
                    flags.append("STALE: mermaid changed since the drawing was stamped")
                if r["missing_labels"]:
                    flags.append(
                        f"missing {len(r['missing_labels'])} label(s): {r['missing_labels'][:3]}"
                    )
                if r["svg_older_than_source"]:
                    flags.append("svg not rebuilt since the .drawio changed")
                print(f"{r['level']:22} {'; '.join(flags) or 'ok'}")
        if args.cmd == "check" and any(
            r["drawn"] and (r["stale"] or r["missing_labels"] or r["svg_older_than_source"])
            for r in rows
        ):
            sys.exit(1)


if __name__ == "__main__":
    main()
