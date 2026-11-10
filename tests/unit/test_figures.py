"""Console figures and their draw.io twins (D114, extending D113).

The browser decides whether a figure's drawing is stale by hashing the mermaid it
renders and comparing with the stamp manifest — so the server-side extractor
(src/console/figures.py, which the stamp is computed from) must see EXACTLY the
string the browser evaluates, and the browser's id map must name every figure.
"""

from __future__ import annotations

import json
import re

from console import figures
from console.architecture import mermaid_digest


def test_every_diagram_constant_is_extracted_with_a_kebab_id():
    figs = figures.console_figures()
    assert "cost-sentinel" in figs and "brief-delivery" in figs
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", k) for k in figs)
    assert all(v.lstrip().startswith("flowchart") for v in figs.values())


def test_template_escapes_are_resolved_as_the_browser_would():
    html = "const X_DIAGRAM = `a \\${VAR} \\` \\\\ b`;\n"
    assert figures.console_figures(html) == {"x": "a ${VAR} ` \\ b"}
    # INFRA_DIAGRAM really carries an escaped ${VAR}
    assert "${VAR}" in figures.console_figures()["infra"]


def test_a_real_interpolation_is_refused():
    import pytest

    with pytest.raises(ValueError, match="interpolates"):
        figures.console_figures("const X_DIAGRAM = `a ${b}`;\n")


def test_the_browser_key_map_names_every_console_figure():
    html = figures.INDEX_HTML.read_text(encoding="utf-8")
    block = html[html.index("figKeyMap = new Map([") : html.index("]);", html.index("figKeyMap"))]
    pairs = dict(re.findall(r"\[([A-Z0-9_]+)_DIAGRAM, \"([a-z0-9-]+)\"\]", block))
    for const, key in pairs.items():
        assert figures.const_id(const) == key, const
    assert set(pairs.values()) == set(figures.console_figures())


def test_figure_ids_do_not_collide_across_sources():
    assert len(figures.all_figures()) == len(figures.console_figures()) + len(
        figures.insight_figures()
    )


def test_manifest_names_only_real_figures_with_built_svgs():
    if not figures.FIG_MANIFEST_PATH.exists():
        return
    man = json.loads(figures.FIG_MANIFEST_PATH.read_text())
    known = figures.all_figures()
    for fid, entry in man.items():
        assert fid in known, f"manifest entry for unknown figure {fid}"
        assert (figures.FIG_SVG_DIR / f"{fid}.svg").exists(), fid
        assert (figures.FIG_SRC_DIR / f"{fid}.drawio").exists(), fid
        assert set(entry) <= {"mermaid_sha256", "svg_sha256"}


def test_digest_matches_what_the_browser_hashes():
    # The browser hashes src.trim(); mermaid_digest strips — same bytes.
    src = figures.console_figures()["brief-delivery"]
    import hashlib

    assert mermaid_digest(src) == hashlib.sha256(src.strip().encode("utf-8")).hexdigest()


def test_a_label_containing_a_pipe_can_be_matched(tmp_path):
    # drawio_text once joined cells with " | " and missing_labels then erased
    # every " | " — so a mermaid label like `managed | sdk backend`
    # (claude-pair-detail) could never be found, and stamp always refused it.
    import sys

    sys.path.insert(0, str(figures._REPO / "scripts"))
    from arch_drawio import missing_labels

    drawing = tmp_path / "x.drawio"
    drawing.write_text(
        '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="2" parent="1" vertex="1" '
        'value="&lt;b&gt;ClaudeAdapter&lt;/b&gt;&lt;br&gt;managed | sdk backend"/>'
        "</root></mxGraphModel>"
    )
    mermaid = 'flowchart LR\n  A["ClaudeAdapter<br/>managed | sdk backend"]\n'
    assert missing_labels(mermaid, drawing) == []
