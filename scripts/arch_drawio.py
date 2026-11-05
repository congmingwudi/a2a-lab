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

The console reads the manifest: a stale level opens on its mermaid, not its
drawing, so a visitor is never shown a picture of the old shape by default.

    uv run python scripts/arch_drawio.py status
    uv run python scripts/arch_drawio.py build L4 && uv run python scripts/arch_drawio.py stamp L4
"""

from __future__ import annotations

import argparse
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
    return " | ".join(_norm(v) for v in values if v)


def missing_labels(mermaid: str, drawio: Path) -> list[str]:
    """Mermaid labels the drawing does not contain. Compared segment by segment
    (a `<br/>` line each), because a drawing may legitimately re-flow a
    two-line label into a bold title and a muted detail line."""
    text = drawio_text(drawio)
    flat = text.replace(" | ", " ")
    missing = []
    for label in mermaid_labels(mermaid):
        if label in flat:
            continue
        segs = [_norm(s) for s in re.split(r"<br\s*/?>", label) if _norm(s)]
        if segs and all(s in flat for s in segs):
            continue
        missing.append(label)
    return missing


def _manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def _levels() -> dict[str, str]:
    doc = parse((ROOT / DOC_PATH).read_text(encoding="utf-8"))
    return {lv["id"]: lv["mermaid"] for lv in doc["levels"] if lv["mermaid"]}


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
    ARCH_SVG_DIR.mkdir(parents=True, exist_ok=True)
    sources = sorted(DRAWIO_SRC_DIR.glob("*.drawio"))
    wanted = {p.stem: p for p in sources if not ids or p.stem in ids}
    unknown = set(ids) - set(wanted)
    if unknown:
        sys.exit(f"no docs/architecture/<id>.drawio for: {', '.join(sorted(unknown))}")
    for lid, src in wanted.items():
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.svg"
            subprocess.run(
                [cli, "-x", "-f", "svg", "--theme", "light", "-b", "10", "-o", str(out), str(src)],
                check=True,
                capture_output=True,
            )
            raw = out.read_text(encoding="utf-8")
        dest = ARCH_SVG_DIR / f"{lid}.svg"
        dest.write_text(_slim(raw), encoding="utf-8")
        print(
            f"{lid}: {src.relative_to(ROOT)} → {dest.relative_to(ROOT)} "
            f"({len(raw) // 1024} KB → {dest.stat().st_size // 1024} KB)"
        )


def stamp(ids: list[str]) -> None:
    levels = _levels()
    man = _manifest()
    for lid in ids:
        if lid not in levels:
            sys.exit(f"{lid}: no such level with a mermaid diagram in {DOC_PATH}")
        if not (DRAWIO_SRC_DIR / f"{lid}.drawio").exists():
            sys.exit(f"{lid}: docs/architecture/{lid}.drawio does not exist")
        miss = missing_labels(levels[lid], DRAWIO_SRC_DIR / f"{lid}.drawio")
        if miss:
            sys.exit(
                f"{lid}: refusing to stamp — the drawing lacks {len(miss)} mermaid "
                f"label(s): {miss[:5]}"
            )
        man[lid] = {"mermaid_sha256": mermaid_digest(levels[lid])}
        print(f"{lid}: stamped against mermaid {man[lid]['mermaid_sha256'][:12]}")
    MANIFEST_PATH.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")


def status() -> list[dict]:
    levels = _levels()
    man = _manifest()
    rows = []
    for lid, src in levels.items():
        drawio = DRAWIO_SRC_DIR / f"{lid}.drawio"
        svg = ARCH_SVG_DIR / f"{lid}.svg"
        row = {"level": lid, "drawn": drawio.exists(), "built": svg.exists()}
        if drawio.exists():
            stamped = (man.get(lid) or {}).get("mermaid_sha256")
            row["stale"] = stamped != mermaid_digest(src)
            row["missing_labels"] = missing_labels(src, drawio)
            row["svg_older_than_source"] = (
                not svg.exists() or svg.stat().st_mtime < drawio.stat().st_mtime
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
                    print(f"{r['level']:6} not drawn (console shows mermaid only)")
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
                print(f"{r['level']:6} {'; '.join(flags) or 'ok'}")
        if args.cmd == "check" and any(
            r["drawn"] and (r["stale"] or r["missing_labels"] or r["svg_older_than_source"])
            for r in rows
        ):
            sys.exit(1)


if __name__ == "__main__":
    main()
