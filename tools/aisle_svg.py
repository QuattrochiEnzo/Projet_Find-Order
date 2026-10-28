#!/usr/bin/env python3
from __future__ import annotations

"""
Generate a static SVG layout based on aisle_racks.txt (via WarehouseLoader).

Rules:
- Racks packed vertically (row_gap = 0) -> glued (no vertical spacing).
- Two columns per aisle separated by a small gap (corridor).
- Aisles are glued together (aisle_gap = 0).
- START/END:
    * default: START + END side-by-side above the warehouse
    * toy: START above, END below the whole warehouse

Add-on:
- Aisle direction arrows:
    * If help/aisle_directions.txt exists, use it
    * Otherwise fallback to "flip every other aisle" (even: down, odd: up)

NEW (Step 2.5):
- In-memory assignment state (SolutionState)
- Optional mock assignment to visualize occupied racks
- Color occupied racks by dominant circuit + show "+k" for number of products

Spec codes (help/aisle_directions.txt):
0 DOWN (Y decreasing), 1 UP (Y increasing), 2 LEFT, 3 RIGHT, 4 NO_DIRS
Note: SVG Y increases downward => UP(1) is drawn downward in SVG.
"""

import argparse
import colorsys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from warehouse_loader import WarehouseLoader, WarehouseInstance
from aisle_layout import build_aisle_layout, layout_bbox, resolve_instance_dir, LayoutCell
from solution_state import SolutionState


# -----------------------------
# Helpers
# -----------------------------

def _svg_escape(s: str) -> str:
    return (s.replace("&", "&amp;")                                                                       # escape XML ampersand
             .replace("<", "&lt;")                                                                        # escape XML <
             .replace(">", "&gt;")                                                                        # escape XML >
             .replace('"', "&quot;"))                                                                     # escape XML quotes


def _write_svg(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)                                                        # create output directory if needed
    path.write_text(content, encoding="utf-8")                                                            # write SVG as UTF-8 text


def read_aisle_directions(instance_dir: Path, n_aisles: int) -> Optional[List[int]]:
    """
    Read help/aisle_directions.txt if present, return None if missing.

    File format:
        <n_aisles>
        <dir_0>
        <dir_1>
        ...
    Lines starting with "..." are ignored (handy for comments / templates).
    """
    p = instance_dir / "help" / "aisle_directions.txt"                                                    # optional directions file
    if not p.exists():                                                                                    # tolerate missing helper file
        return None                                                                                        # fallback will be used
    lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()                                # read all lines
             if l.strip() and not l.startswith("...")]                                                     # drop empties + comment-like lines
    if not lines:                                                                                          # empty file -> treat as missing
        return None                                                                                        # fallback will be used
    if int(lines[0]) != n_aisles:                                                                          # validate header count
        raise ValueError(f"aisle_directions.txt n_aisles={lines[0]} != instance n_aisles={n_aisles}")      # keep message explicit
    dirs = [int(x) for x in lines[1:]]                                                                     # parse direction codes
    if len(dirs) != n_aisles:                                                                              # validate payload size
        raise ValueError(f"aisle_directions.txt has {len(dirs)} dirs, expected {n_aisles}")                # keep message explicit
    return dirs                                                                                            # return direction codes


def _compute_top_offset(cell_px: int, top_pad_px: int) -> int:
    # Reserve one rack row above the layout for START/END in default mode                                # keep header area constant
    return top_pad_px + cell_px + top_pad_px                                                               # pad + cell + pad


def _aisle_bbox(layout: Dict[int, LayoutCell], aisle_id: int, cell_px: int) -> Optional[Tuple[int, int, int, int]]:
    """
    Return (min_x, min_y, max_x, max_y) in layout coordinates (no top offsets applied).
    max_* includes one cell size (bbox is inclusive of the rendered square).
    """
    cells = [c for c in layout.values() if c.aisle_id == aisle_id]                                         # extract cells for this aisle
    if not cells:                                                                                          # guard against empty aisle
        return None                                                                                        # no bounding box
    min_x = min(c.x for c in cells)                                                                        # left boundary (layout coords)
    min_y = min(c.y for c in cells)                                                                        # top boundary (layout coords)
    max_x = max(c.x for c in cells) + cell_px                                                              # right boundary (inclusive)
    max_y = max(c.y for c in cells) + cell_px                                                              # bottom boundary (inclusive)
    return min_x, min_y, max_x, max_y                                                                      # bbox in layout coordinates


def _arrow_segment_for_dir(
    d: int,
    x_mid: int,
    y_mid: int,
    length: int,
) -> Optional[Tuple[int, int, int, int]]:
    """
    Return (x1,y1,x2,y2) for an arrow segment centered at (x_mid, y_mid).

    Direction codes (spec):
      0 DOWN (Y decreasing) -> in SVG this points UP (because SVG Y grows downward)
      1 UP   (Y increasing) -> in SVG this points DOWN
      2 LEFT (X decreasing)
      3 RIGHT(X increasing)
      4 NO_DIRS -> None
    Unknown codes are treated as "no arrow".
    """
    if d == 4:                                                                                             # explicit "no direction"
        return None                                                                                        # do not draw anything

    half = length // 2                                                                                     # half-length around midpoint

    if d == 0:      # Y decreasing -> SVG up
        return (x_mid, y_mid + half, x_mid, y_mid - half)                                                  # bottom to top
    if d == 1:      # Y increasing -> SVG down
        return (x_mid, y_mid - half, x_mid, y_mid + half)                                                  # top to bottom
    if d == 2:      # X decreasing -> left
        return (x_mid + half, y_mid, x_mid - half, y_mid)                                                  # right to left
    if d == 3:      # X increasing -> right
        return (x_mid - half, y_mid, x_mid + half, y_mid)                                                  # left to right

    return None                                                                                            # unknown => skip arrow


def _rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
    r, g, b = rgb                                                                                          # unpack floats in [0, 1]
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))                          # convert to CSS hex


# -----------------------------
# Color mapping (UPDATED)
# -----------------------------

# Readable categorical palette (Tableau/Matplotlib-like):
# - high contrast for small/medium number of circuits                                                     # prefer fixed colors for legibility
# - when circuits exceed palette size, we fallback to a golden-ratio hue sampling                          # avoid obvious collisions
PALETTE = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#7f7f7f",  # gray
    "#bcbd22",  # olive
    "#17becf",  # cyan
    "#aec7e8",  # light blue
    "#ffbb78",  # light orange
    "#98df8a",  # light green
    "#ff9896",  # light red
    "#c5b0d5",  # light purple
    "#c49c94",  # light brown
    "#f7b6d2",  # light pink
    "#c7c7c7",  # light gray
    "#dbdb8d",  # light olive
    "#9edae5",  # light cyan
]


def circuit_color(circuit_id: int, n_circuits: int) -> str:
    """
    Stable color selection per circuit.

    Strategy:
    - Use the fixed qualitative palette first (best readability / contrast).
    - If circuit_id exceeds the palette, use a golden-ratio hue progression in HSV.
      This keeps hues well-separated without maintaining a huge hard-coded palette.
    """
    if circuit_id < len(PALETTE):                                                                          # palette covers common cases
        return PALETTE[circuit_id]                                                                         # stable categorical color

    golden_ratio = 0.61803398875                                                                           # irrational step for hue sampling
    h = (circuit_id * golden_ratio) % 1.0                                                                  # spread hues quasi-uniformly
    s = 0.55                                                                                               # moderate saturation for readability
    v = 0.95                                                                                               # high value for bright fills
    return _rgb_to_hex(colorsys.hsv_to_rgb(h, s, v))                                                        # convert HSV -> RGB -> hex


def _get_product_circuit(instance: WarehouseInstance) -> List[int]:
    """
    Best-effort accessor: instances usually expose 'product_circuit' (list[int]).
    A legacy name 'product_circuits' is also supported for compatibility.
    """
    if hasattr(instance, "product_circuit"):                                                               # preferred field name
        return list(getattr(instance, "product_circuit"))                                                  # normalize to list
    if hasattr(instance, "product_circuits"):                                                              # legacy / alternate name
        return list(getattr(instance, "product_circuits"))                                                 # normalize to list
    raise AttributeError("Instance has no product_circuit field (expected by visualization).")             # explicit contract error


# -----------------------------
# SVG generation
# -----------------------------

def generate_svg(
    instance: WarehouseInstance,
    instance_dir: Path,
    instance_key: str,
    cell_px: int = 18,
    col_gap_px: int = 6,
    top_pad_px: int = 6,
    show_labels: bool = True,
    draw_directions: bool = True,
    state: Optional[SolutionState] = None,
    status_text: Optional[str] = None,
) -> str:
    """
    Build the SVG string for the given instance and (optional) assignment state.
    """
    layout: Dict[int, LayoutCell] = build_aisle_layout(                                                    # compute rack placement grid
        instance,
        cell_px=cell_px,
        col_gap_px=col_gap_px,
        row_gap_px=0,                                                                                      # glue racks vertically
        aisle_gap_px=0,                                                                                    # glue aisles horizontally
        flip_every_other_aisle=True,                                                                        # default traversal orientation
    )

    toy_mode = (instance_key == "toy")                                                                     # special START/END placement mode

    start_id = 0                                                                                           # START node id (by spec)
    end_id = int(instance.metadata["num_racks"]) - 1                                                       # END node id (by spec)
    n_aisles = int(instance.metadata["num_aisles"])                                                        # number of aisles
    n_circuits = int(instance.metadata.get("num_circuits", 1))                                             # circuits count (robust default)

    if state is None:                                                                                      # allow pure "layout only" rendering
        state = SolutionState.empty_from_instance(instance)                                                # empty assignment state

    product_circuit = _get_product_circuit(instance)                                                       # product -> circuit mapping

    w_racks, h_racks = layout_bbox(layout, cell_px)                                                        # bbox of rack layout (no START/END)

    top_offset = _compute_top_offset(cell_px, top_pad_px)                                                  # vertical shift for header zone

    label_font_px = max(9, cell_px // 2)                                                                   # scale label font with cell size

    extra_right = 0                                                                                        # optional margin for status text
    if status_text:
        extra_right = int(len(status_text) * label_font_px * 0.6) + 50                                     # rough text width estimate
        extra_right = max(extra_right, 260)                                                                # enforce comfortable minimum

    width = max(w_racks, 2 * cell_px + col_gap_px) + 2 * top_pad_px + extra_right                          # overall SVG width
    height = h_racks + top_offset + top_pad_px                                                             # overall SVG height (default)

    def rack_xy(c: LayoutCell) -> Tuple[int, int]:
        return top_pad_px + c.x, top_offset + c.y                                                          # apply margins + top offset

    if toy_mode:
        sx = top_pad_px + max(0, (w_racks - cell_px) // 2)                                                 # START centered above layout
        sy = top_pad_px                                                                                    # START in header zone

        ex = sx                                                                                             # END centered under layout
        ey = top_offset + h_racks + top_pad_px                                                             # END below racks

        height = ey + cell_px + top_pad_px                                                                 # extend height to fit END box
    else:
        total_pair_w = 2 * cell_px + col_gap_px                                                            # START+END pair width
        sx = top_pad_px + max(0, (w_racks - total_pair_w) // 2)                                            # pair centered above layout
        sy = top_pad_px                                                                                    # header zone y
        ex = sx + cell_px + col_gap_px                                                                     # END to the right of START
        ey = top_pad_px                                                                                    # same header zone y

    dirs = read_aisle_directions(instance_dir, n_aisles) if draw_directions else None                      # optional direction codes
    arrow_thickness = max(2, col_gap_px // 4)                                                              # scale arrow stroke with corridor
    arrow_len = arrow_thickness * 6                                                                        # arrow line length
    head_w = 2                                                                                             # arrowhead marker width
    head_h = 2                                                                                             # arrowhead marker height

    css = f"""
    .rack {{ fill: #e9ecef; stroke: #343a40; stroke-width: 1; }}
    .special {{ fill: #ffe066; }}
    .occupied {{ stroke: #212529; stroke-width: 1; }}
    .label {{ font-family: Arial, sans-serif; font-size: {max(9, cell_px//2)}px; fill: #212529;  pointer-events: none; }}
    .label-special {{ font-weight: bold; }}
    .arrow {{ stroke: #ffd43b; stroke-width: {arrow_thickness}; fill: none; }}
    .obstacle {{ fill: #495057; stroke: #212529; }}
    """                                                                                                    # embed minimal CSS for consistent rendering

    parts: List[str] = []                                                                                  # SVG chunks (string builder)
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '                      # SVG root element
        f'viewBox="0 0 {width} {height}">\n'
        f'<defs>\n'                                                                                        # defs section for markers
        f'  <marker id="arrowhead" markerWidth="{head_w}" markerHeight="{head_h}" '                        # arrowhead marker
        f'refX="{head_w - 1}" refY="{head_h / 2}" orient="auto">\n'
        f'    <polygon points="0 0, {head_w} {head_h/2}, 0 {head_h}" fill="#ffd43b" />\n'
        f'  </marker>\n'
        f'</defs>\n'
        f'<style>{css}</style>\n'                                                                          # inline CSS
    )

    def draw_special(rack_id: int, x: int, y: int, text: str) -> None:
        extra_w = 20                                                                                        # extra width for label readability
        special_w = cell_px + extra_w                                                                       # widened START/END box

        parts.append(
            f'<rect id="rack-{rack_id}" class="rack special" x="{x - 10}" y="{y}" '                        # draw widened rectangle
            f'width="{special_w}" height="{cell_px}" rx="2" ry="2"/>\n'
        )

        if show_labels:                                                                                     # optional label rendering
            tx = (x - 10) + special_w // 2                                                                  # center text horizontally
            ty = y + (cell_px * 2) // 3                                                                     # baseline ~ 2/3 height
            parts.append(
                f'<text class="label label-special" x="{tx}" y="{ty}" text-anchor="middle">{_svg_escape(text)}</text>\n'
            )                                                                                               # label START/END

    draw_special(start_id, sx, sy, "START")                                                                 # render START node
    draw_special(end_id, ex, ey, "END")                                                                     # render END node

    if status_text:
        tx = ex + cell_px + 20                                                                              # place status text to the right
        ty = ey + cell_px                                                                                   # align with header row
        parts.append(
            f'<text class="label" x="{tx}" y="{ty}" text-anchor="start">{_svg_escape(status_text)}</text>\n'
        )                                                                                                   # render status string

    if draw_directions:
        for aisle_id in range(n_aisles):                                                                    # draw one arrow per aisle
            bb = _aisle_bbox(layout, aisle_id, cell_px)                                                     # compute aisle bbox in layout coords
            if not bb:
                continue                                                                                    # skip empty aisle
            min_x, min_y, max_x, max_y = bb                                                                 # unpack bbox

            x_mid = top_pad_px + min_x + cell_px + (col_gap_px // 2)                                        # center of corridor between columns
            y_mid = top_offset + (min_y + max_y) // 2                                                       # vertical center of aisle bbox

            if dirs is not None:
                d = dirs[aisle_id]                                                                          # file-driven direction code
            else:
                d = 0 if (aisle_id % 2 == 1) else 1                                                         # fallback: alternate directions

            seg = _arrow_segment_for_dir(d, x_mid, y_mid, length=arrow_len)                                 # compute arrow segment
            if seg is None:
                continue                                                                                    # no arrow for this aisle
            x1, y1, x2, y2 = seg                                                                            # unpack segment endpoints
            parts.append(
                f'<line class="arrow" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#arrowhead)"/>\n'
            )                                                                                               # render arrow line

    for rack_id, c in layout.items():
        x, y = rack_xy(c)                                                                                   # rack top-left in SVG coords

        capacity = instance.rack_capacity[rack_id]                                                          # rack capacity (0 => obstacle)
        circuits = state.circuits_in_rack(rack_id, product_circuit)                                         # circuits present in this rack

        products = state.rack_contents.get(rack_id, [])                                                     # product list for tooltip
        if products:
            shown = products[:12]                                                                           # cap tooltip length for readability
            tip_items = [f"{p}(c{product_circuit[p]})" for p in shown]                                      # compact product+circuit display
            tip = f"rack {rack_id}\nproducts: " + ", ".join(tip_items)                                      # build tooltip text
            if len(products) > len(shown):
                tip += f"\n... +{len(products) - len(shown)} more"                                          # indicate truncated tooltip
        else:
            tip = f"rack {rack_id}\n(empty)"                                                                # tooltip for empty rack

        tip = _svg_escape(tip)                                                                              # escape tooltip for XML

        cls = "rack"                                                                                        # base class name
        style = ""                                                                                          # optional inline fill style

        if capacity == 0 and rack_id not in (start_id, end_id):                                             # obstacles: capacity 0 except START/END
            cls = "rack obstacle"                                                                           # obstacle styling
            parts.append(
                f'<rect id="rack-{rack_id}" class="{cls}" x="{x}" y="{y}" '                                 # obstacle square
                f'width="{cell_px}" height="{cell_px}" rx="1" ry="1">'
                f'<title>{tip}</title></rect>\n'
            )                                                                                               # tooltip embedded in rect

        else:
            if len(circuits) <= 1:                                                                          # empty or single-circuit fill
                fill = None                                                                                 # default fill uses CSS
                if len(circuits) == 1:
                    fill = circuit_color(circuits[0], n_circuits)                                           # stable color for that circuit
                    cls = "rack occupied"                                                                   # occupied border style
                style = f' style="fill:{fill};"' if fill else ""                                            # inline fill override

                parts.append(
                    f'<rect id="rack-{rack_id}" class="{cls}" x="{x}" y="{y}" '                             # rack square
                    f'width="{cell_px}" height="{cell_px}" rx="1" ry="1"{style}>'
                    f'<title>{tip}</title></rect>\n'
                )                                                                                           # tooltip embedded in rect

            else:
                cls = "rack occupied"                                                                       # multi-circuit border style

                parts.append(f'<g id="rack-{rack_id}">')                                                    # group for stripes + tooltip

                parts.append(
                    f'<rect class="{cls}" x="{x}" y="{y}" '                                                 # outer border rectangle
                    f'width="{cell_px}" height="{cell_px}" rx="1" ry="1"/>'
                )

                parts.append(f'<title>{tip}</title>')                                                       # group-level tooltip

                stripes = circuits[:3]                                                                      # limit to 3 stripes for readability
                m = len(stripes)                                                                            # number of stripes
                stripe_w = cell_px / m                                                                      # stripe width in px (float)

                for i, circ in enumerate(stripes):                                                          # draw each colored stripe
                    fx = x + int(round(i * stripe_w))                                                       # stripe left boundary
                    fw = int(round((i + 1) * stripe_w)) - int(round(i * stripe_w))                          # stripe width (integer pixels)
                    fill = circuit_color(circ, n_circuits)                                                  # circuit color
                    parts.append(
                        f'<rect x="{fx}" y="{y}" width="{fw}" height="{cell_px}" '                           # stripe rectangle
                        f'style="fill:{fill}; stroke:none;"/>'
                    )

                parts.append('</g>\n')                                                                      # close rack group

        if show_labels:
            tx = x + cell_px // 2                                                                           # label x centered in cell
            ty = y + (cell_px * 2) // 3                                                                     # label baseline position
            parts.append(
                f'<text class="label" x="{tx}" y="{ty}" text-anchor="middle">{rack_id}</text>\n'
            )                                                                                               # draw rack id label

    parts.append("</svg>\n")                                                                                # close SVG document
    return "".join(parts)                                                                                   # join chunks into final SVG string


# -----------------------------
# CLI
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()                                                                          # CLI argument parser
    ap.add_argument("--instance", required=True, help="Alias (toy/family/market/category) or path inside data/")  # instance selector
    ap.add_argument("--cell-px", type=int, default=18)                                                      # rack cell size in pixels
    ap.add_argument("--col-gap-px", type=int, default=20, help="Gap between the 2 columns of the same aisle (corridor).")  # corridor width
    ap.add_argument("--top-pad-px", type=int, default=6)                                                    # outer padding
    ap.add_argument("--no-labels", action="store_true", help="Do not draw rack_id labels.")                 # disable labels
    ap.add_argument("--no-directions", action="store_true", help="Disable aisle direction arrows.")        # disable arrows
    ap.add_argument("--out", default="", help="Output SVG path. Default: tools/out/<instance>.svg")        # output path override

    ap.add_argument("--mock-assign", type=int, default=0,
                    help="If >0, assign the first K products to racks cyclically (for visualization).")     # quick occupancy smoke-test
    ap.add_argument("--mock-rack-start", type=int, default=1,
                    help="First rack id used for mock assignment (avoid 0).")                               # avoid START by default
    ap.add_argument("--mock-rack-span", type=int, default=50,
                    help="Number of consecutive racks to use in mock assignment.")                          # wrap range for mock mapping

    args = ap.parse_args()                                                                                  # parse argv

    instance_dir = resolve_instance_dir(args.instance)                                                      # resolve alias/path to directory
    loader = WarehouseLoader(str(instance_dir))                                                             # build loader
    inst = loader.load_all()                                                                                # load instance data

    state = SolutionState.empty_from_instance(inst)                                                         # initialize empty assignment state

    if args.mock_assign > 0:
        start = max(1, args.mock_rack_start)                                                                # clamp start rack (avoid START)
        span = max(1, args.mock_rack_span)                                                                  # clamp span to positive
        for p in range(min(args.mock_assign, state.n_products)):                                            # assign first K products
            r = start + (p % span)                                                                          # cycle within [start, start+span)
            if r >= state.n_racks - 1:
                r = (state.n_racks - 2)                                                                     # avoid END rack index
            try:
                state.assign_product(p, r)                                                                  # apply mock assignment
            except ValueError:
                continue                                                                                    # skip invalid/full racks (small instances)

    svg = generate_svg(
        inst,
        instance_dir=instance_dir,
        instance_key=("toy" if args.instance == "toy" else args.instance),                                  # keep key consistent with loader aliases
        cell_px=args.cell_px,
        col_gap_px=args.col_gap_px,
        top_pad_px=args.top_pad_px,
        show_labels=not args.no_labels,
        draw_directions=not args.no_directions,
        state=state,
    )

    out_path = Path(args.out) if args.out else (Path(__file__).resolve().parent / "out" / f"{args.instance}.svg")  # default output location
    _write_svg(out_path, svg)                                                                               # write SVG to disk
    print(f"[ok] wrote: {out_path}")                                                                        # success log


if __name__ == "__main__":
    main()                                                                                                  # CLI entrypoint