# Architecture drawings — the draw.io style contract (D113)

`docs/architecture/<level>.drawio` are hand-placed draw.io twins of the mermaid
diagrams in `plan/09-deployment-map.md`. **The mermaid is the source of facts; the
drawing is a second rendering of it.** The console's Architecture section opens each
level on its drawing, with a Mermaid tab next to it. If the mermaid changed after the
drawing was stamped, the level opens on Mermaid instead (see `scripts/arch_drawio.py`).

## Facts first

- Keep every node, edge, edge label and subgraph title from the level's mermaid fence.
  Don't add facts and don't drop any. Keep `D<n>` refs in labels; the console turns
  them into chips.
- `arch_drawio.py stamp` refuses a drawing that is missing a mermaid label. Labels are
  compared after unescaping and lowercasing, one `<br/>` line at a time, so a two-line
  mermaid label may become a bold title plus a muted detail line.
- No account ids, profile names or other environment identifiers, the same as anywhere
  else in this repo.

## Visual language

- White canvas, no grid. Helvetica: 12px nodes, 11px edge labels, 14px bold container
  titles.
- One rounded container per cloud or boundary
  (`rounded=1;arcSize=4;verticalAlign=top;align=left;spacingLeft=10;fontStyle=1`),
  coloured by owner:

  | Owner | stroke | fill | |
  |---|---|---|---|
  | AWS | `#ED7100` | `#FFF7EE` | |
  | Salesforce | `#0176D3` | `#EEF6FD` | |
  | GCP / Vertex | `#34A853` | `#EFF8F1` | |
  | Azure / Foundry | `#0078D4` | `#EEF3FB` | `dashed=1` (tells it apart from Salesforce) |
  | Anthropic | `#C15F3C` | `#FBF1EC` | |
  | Heroku | `#6762A6` | `#F3F2FA` | |
  | Cloudflare | `#F38020` | `#FFF4EA` | `dashed=1` |
  | Operator / local / other | `#666666` | `#F5F5F5` | |

  A sub-boundary inside a cloud (an ALB, an API Gateway) uses the cloud's hue, fill
  `#FFFFFF` and `dashed=1`.
- Nodes: `rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=<container hue>`.
  Make the first label line bold (`<b>name</b><br>`) and put the detail in
  `<font color="#555555">`. Datastores use `shape=cylinder3;boundedLbl=1;size=8`.
- Provider icons (32–40px) are seasoning, not structure. Look up shape names with the
  drawio skill's `find-shapes.js` rather than guessing them, and skip an icon if it
  clutters the drawing.
- Edges: `edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#444444;endArrow=block;endFill=1;fontSize=11;labelBackgroundColor=#FFFFFF`.
  Mermaid `-.->` becomes `dashed=1`, `<-->` becomes `startArrow=block`, and `==>`
  becomes `strokeWidth=2`.
- Well-formed XML: no comments, escape `& < > "` inside attributes, unique ids, and
  every edge carries `<mxGeometry relative="1" as="geometry"/>`.

## The loop

```sh
# edit docs/architecture/L4.drawio (by hand in draw.io Desktop, or as XML)
drawio -x -f png -s 1 -b 10 -o /tmp/L4.png docs/architecture/L4.drawio  # LOOK at it
uv run python scripts/arch_drawio.py build L4    # → src/console/static/arch/L4.svg
uv run python scripts/arch_drawio.py stamp L4    # record: matches the CURRENT mermaid
uv run python scripts/arch_drawio.py check       # exit 1 if any level is stale
```

Look at the PNG before you stamp. Fix overlapping nodes, clipped labels and edges that
cross nodes, then look again. A drawing is done when it reads better than the mermaid
at a glance. `/architecture-sweep` runs this loop for every level `status` reports as
stale.

## Console figures (D114)

The same contract covers every other mermaid figure in the console. These are the
`*_DIAGRAM` constants in `src/console/static/index.html` (the Details-pane figures)
and the insight-chip diagrams in `config/diagrams.yaml`. A figure's drawing is
`docs/diagrams/<id>.drawio`. It builds to `src/console/static/diagrams/<id>.svg`, and
its stamp lives in `src/console/static/diagrams/manifest.json`. The id is the
diagrams.yaml `id`, or for a constant its name kebab-cased without `_DIAGRAM`
(`COST_SENTINEL_DIAGRAM` → `cost-sentinel`). `src/console/figures.py` extracts both
sources. The browser hashes the mermaid it is about to render, so editing a constant
or a diagrams.yaml entry turns its drawing stale with no server round trip, and the
figure opens on Mermaid until it is redrawn and stamped. The loop is the same, with
the figure id in place of the level:

```sh
drawio -x -f png -s 1 -b 10 -o /tmp/cost-sentinel.png docs/diagrams/cost-sentinel.drawio
uv run python scripts/arch_drawio.py build cost-sentinel && \
  uv run python scripts/arch_drawio.py stamp cost-sentinel
```

A figure constant must be a literal template. A real `${...}` interpolation is refused,
because its value is only known at run time and no stamp could match it.
