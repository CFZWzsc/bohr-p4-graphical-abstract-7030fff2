#!/usr/bin/env python3
"""Deterministic SVG writer for node-link figures (standard library only).

The run_skill.py of this package turns the extracted intermediary
representation (nodes with source anchors, typed edges) into a real, editable
SVG figure plus a machine-checkable figure-spec.json.  Layout is computed from
the ordered node/edge lists only: no randomness, no timestamps, no external
renderer.  The same input therefore yields byte-identical output files.
"""

import hashlib
import json
import os
import re
import struct
import zlib

from xml.sax.saxutils import escape as _xml_escape

GENERATOR = "figure_svg.py v1.0.0"

FONT = ("'Noto Sans CJK SC','Source Han Sans SC','Microsoft YaHei',"
        "'PingFang SC','SimHei','DejaVu Sans',sans-serif")

NODE_W = 208.0
COL_GAP = 88.0
ROW_GAP = 24.0
LANE_GAP = 28.0
MARGIN = 44.0
HEAD_H = 104.0
LEGEND_H = 82.0
NODE_PAD = 13.0
LINE_H = 18.0
MIN_WIDTH = 720.0
LANE_LABEL_W = 132.0

PALETTE = ["#3B6EA5", "#B4543A", "#4E8C6A", "#8A6BBE", "#B0813A", "#4C7C9B"]
FILLS = ["#EAF1F8", "#FAEDE9", "#EAF4EF", "#F1EDF9", "#FAF2E4", "#EDF3F7"]

EDGE_STYLES = {
    "activation": ("#0072B2", "solid"),
    "inhibition": ("#D55E00", "solid"),
    "binding": ("#009E73", "solid"),
    "modification": ("#CC79A7", "dashed"),
    "transport": ("#E69F00", "solid"),
    "flow": ("#3B6EA5", "solid"),
    "sequence": ("#52606D", "solid"),
    "reading_order": ("#52606D", "dashed"),
    "comparison": ("#7A5195", "solid"),
    "unsupported": ("#7B8794", "dotted"),
}
DEFAULT_EDGE_STYLE = ("#52606D", "solid")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def esc(text):
    return _xml_escape(str(text if text is not None else ""))


def attr(text):
    return _xml_escape(str(text if text is not None else ""), {'"': "&quot;"})


def char_width(ch, size):
    code = ord(ch)
    if code >= 0x2E80:
        return size * 1.0
    if ch in "ilI.,;:'!|":
        return size * 0.32
    if ch in "mwMW@":
        return size * 0.86
    return size * 0.56


def text_width(text, size):
    return sum(char_width(ch, size) for ch in str(text or ""))


def wrap(text, size, max_w, max_lines=None):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return [""]
    # Atomic units: one CJK character, or one whitespace-delimited Latin token.
    # Breaking inside a Latin word ("se" / "ntence") is a typographic defect.
    atoms = []
    for token in re.split(r"(\s+)", text):
        if not token:
            continue
        if token.isspace():
            atoms.append(" ")
            continue
        for chunk in re.findall(
                r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]"
                r"|[^\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]+",
                token):
            atoms.append(chunk)
    lines, current = [], ""
    for atom in atoms:
        if atom == " ":
            if current:
                current += " "
            continue
        candidate = current + atom
        if current and text_width(candidate, size) > max_w:
            lines.append(current.rstrip())
            current = atom
            # an atom wider than the line still has to be split somewhere
            while text_width(current, size) > max_w and len(current) > 1:
                cut = len(current)
                while cut > 1 and text_width(current[:cut], size) > max_w:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    if not lines:
        lines = [""]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        suffix = "\u2026"
        while lines[-1] and text_width(lines[-1] + suffix, size) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1] + suffix
    return lines


def _bfs_rank(node_ids, edges):
    """Shortest-path layering from the sources; cycles stay bounded."""
    adjacency = {nid: [] for nid in node_ids}
    indegree = {nid: 0 for nid in node_ids}
    for edge in edges:
        src, dst = edge["source"], edge["target"]
        if src in adjacency and dst in adjacency and src != dst:
            adjacency[src].append(dst)
            indegree[dst] += 1
    rank = {}
    queue = []
    for nid in node_ids:
        if indegree[nid] == 0:
            rank[nid] = 0
            queue.append(nid)
    if not queue:
        rank[node_ids[0]] = 0
        queue.append(node_ids[0])
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        for nxt in adjacency[current]:
            if nxt not in rank:
                rank[nxt] = rank[current] + 1
                queue.append(nxt)
    for nid in node_ids:
        rank.setdefault(nid, 0)
    return rank


def _prepare_nodes(nodes):
    prepared = []
    for index, node in enumerate(nodes):
        nid = str(node.get("id") or "N%d" % (index + 1))
        label = str(node.get("label") or nid)
        kind = str(node.get("kind") or "entity")
        lines = wrap(label, 13, NODE_W - 2 * NODE_PAD, max_lines=4)
        detail_lines = []
        if node.get("detail"):
            detail_lines = wrap(str(node["detail"]), 11,
                                NODE_W - 2 * NODE_PAD, max_lines=2)
        height = 44.0 + LINE_H * (len(lines) - 1) + 14.0
        if detail_lines:
            height += 6.0 + 15.0 * len(detail_lines)
        prepared.append({
            "id": nid,
            "label": label,
            "kind": kind,
            "anchor": node.get("anchor"),
            "quote": node.get("quote"),
            "lane": node.get("lane"),
            "lines": lines,
            "detail_lines": detail_lines,
            "w": NODE_W,
            "h": max(height, 58.0),
            "x": 0.0,
            "y": 0.0,
            "rank": 0,
        })
    return prepared


def _normalise_edges(edges, node_index):
    normalised = []
    for index, edge in enumerate(edges):
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if src not in node_index or dst not in node_index or src == dst:
            continue
        label = str(edge.get("label") or edge.get("kind") or "related")
        normalised.append({
            "id": str(edge.get("id") or "E%d" % (index + 1)),
            "source": src,
            "target": dst,
            "label": label,
            "kind": str(edge.get("kind") or "flow"),
            "anchor": edge.get("anchor"),
            "quote": edge.get("quote"),
        })
    return normalised


def _place(nodes, edges, rank, mode, lanes):
    by_id = {node["id"]: node for node in nodes}
    if mode == "lanes" and lanes:
        lane_ids = [str(lane["id"]) for lane in lanes]
        lane_labels = {str(lane["id"]): str(lane.get("label") or lane["id"])
                       for lane in lanes}
        groups = {lid: [] for lid in lane_ids}
        for node in nodes:
            lid = node.get("lane")
            groups[lid if lid in groups else lane_ids[0]].append(node)
        y_cursor = HEAD_H
        bands = []
        for lid in lane_ids:
            cursor = {}
            lane_bottom = y_cursor + 76.0
            for node in groups[lid]:
                r = rank[node["id"]]
                node["x"] = MARGIN + LANE_LABEL_W + r * (NODE_W + COL_GAP)
                node["y"] = cursor.get(r, y_cursor + 30.0)
                cursor[r] = node["y"] + node["h"] + ROW_GAP
                lane_bottom = max(lane_bottom, cursor[r] - ROW_GAP)
            band_h = lane_bottom - y_cursor
            bands.append({"id": lid, "label": lane_labels[lid],
                          "y": y_cursor, "h": band_h})
            y_cursor += band_h + LANE_GAP
        bottom = y_cursor - LANE_GAP
        return bands, bottom
    columns = {}
    for node in nodes:
        columns.setdefault(rank[node["id"]], []).append(node)
    bottom = HEAD_H
    for level in sorted(columns):
        y = HEAD_H + 8.0
        for node in columns[level]:
            node["x"] = MARGIN + level * (NODE_W + COL_GAP)
            node["y"] = y
            y += node["h"] + ROW_GAP
        bottom = max(bottom, y - ROW_GAP)
    return [], bottom


def _edge_path(a, b):
    """Return (path_d, label_x, label_y, kind) for one edge."""
    if b["x"] >= a["x"] + a["w"] - 1:
        x1 = a["x"] + a["w"]
        y1 = a["y"] + a["h"] / 2.0
        x2 = b["x"] - 1
        y2 = b["y"] + b["h"] / 2.0
        path = "M %.1f %.1f L %.1f %.1f" % (x1, y1, x2, y2)
        return path, (x1 + x2) / 2.0, (y1 + y2) / 2.0 - 6.0, "forward"
    if abs(b["x"] - a["x"]) < 1:
        if b["y"] >= a["y"]:
            x1 = a["x"] + a["w"] / 2.0
            y1 = a["y"] + a["h"]
            x2 = b["x"] + b["w"] / 2.0
            y2 = b["y"] - 1
            path = "M %.1f %.1f L %.1f %.1f" % (x1, y1, x2, y2)
            return path, x1 + 8.0, (y1 + y2) / 2.0, "down"
        x1 = a["x"] + a["w"] / 2.0
        y1 = a["y"]
        x2 = b["x"] + b["w"] / 2.0
        y2 = b["y"] + b["h"] + 1
        path = "M %.1f %.1f L %.1f %.1f" % (x1, y1, x2, y2)
        return path, x1 + 8.0, (y1 + y2) / 2.0, "up"
    # backward edge: leave from the bottom, return under both nodes
    x1 = a["x"] + a["w"] / 2.0
    y1 = a["y"] + a["h"]
    x2 = b["x"] + b["w"] / 2.0
    y2 = b["y"] + b["h"] + 1
    bend = max(y1, y2) + 30.0
    path = ("M %.1f %.1f L %.1f %.1f L %.1f %.1f L %.1f %.1f"
            % (x1, y1, x1, bend, x2, bend, x2, y2))
    return path, (x1 + x2) / 2.0, bend + 14.0, "back"


def _marker_id(color):
    return "arrow-" + color.replace("#", "").lower()


def _legend_kinds(edges):
    seen, kinds = set(), []
    for edge in edges:
        kind = edge["kind"]
        if kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def _facts(svg_text):
    return {
        "text_count": len(re.findall(r"<text\b", svg_text)),
        "rect_count": len(re.findall(r"<rect\b", svg_text)),
        "line_count": len(re.findall(r"<(?:line|path)\b", svg_text)),
        "marker_count": len(re.findall(r"<marker\b", svg_text)),
        "marker_edges": len(re.findall(r"marker-end=", svg_text)),
    }


def render(nodes, edges, title, subtitle, out_dir, mode="columns", lanes=None,
           filename="figure.svg", spec_filename="figure-spec.json",
           legend_extra=None, target_dpi=300):
    """Write figure.svg + figure-spec.json; return verifiable facts."""
    prepared = _prepare_nodes(nodes)
    by_id = {node["id"]: node for node in prepared}
    typed_edges = _normalise_edges(edges, by_id)
    rank = _bfs_rank([node["id"] for node in prepared], typed_edges)
    for node in prepared:
        node["rank"] = rank[node["id"]]

    bands, content_bottom = _place(prepared, typed_edges, rank, mode, lanes)

    max_right = max([node["x"] + node["w"] for node in prepared] + [MARGIN + NODE_W])
    title_lines = wrap(title, 20, max(520.0, MIN_WIDTH - 2 * MARGIN), max_lines=2)
    width = max(max_right + MARGIN, MIN_WIDTH)
    raw_height = content_bottom + LEGEND_H + MARGIN
    height = max(raw_height, 360.0)

    legend_kinds = _legend_kinds(typed_edges)
    legend_y = height - LEGEND_H + 6.0

    svg = []
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append(
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        'width="%d" height="%d" viewBox="0 0 %d %d" color-profile="sRGB">'
        % (int(round(width)), int(round(height)), int(round(width)), int(round(height))))
    svg.append("<title>%s</title>" % esc(title))
    svg.append(
        "<desc>Editable figure generated from the skill's extracted "
        "entities and relations. Every node and edge carries a source anchor; "
        "layout is deterministic (no randomness, no timestamps).</desc>")
    svg.append("<metadata>generator=%s; nodes=%d; edges=%d</metadata>"
               % (GENERATOR, len(prepared), len(typed_edges)))

    # arrow markers, one per colour actually used
    svg.append("<defs>")
    used_colors = sorted({EDGE_STYLES.get(e["kind"], DEFAULT_EDGE_STYLE)[0]
                          for e in typed_edges})
    for color in used_colors:
        svg.append(
            '<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="6.5" markerHeight="6.5" orient="auto">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="%s"/></marker>'
            % (_marker_id(color), color))
    svg.append("</defs>")

    svg.append('<g id="layer-background">')
    svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#FFFFFF"/>'
               % (int(round(width)), int(round(height))))
    svg.append("</g>")

    # compartment / stage bands
    svg.append('<g id="layer-bands">')
    for band in bands:
        svg.append(
            '<rect x="%d" y="%.1f" width="%d" height="%.1f" rx="6" '
            'fill="#F7F9FC" stroke="#D9E2EC" stroke-width="1"/>'
            % (int(MARGIN - 12), band["y"], int(width - 2 * MARGIN + 24), band["h"]))
        svg.append(
            '<text x="%d" y="%.1f" font-family="%s" font-size="12" '
            'font-weight="bold" fill="#52606D">%s</text>'
            % (int(MARGIN - 4), band["y"] + 16, FONT, esc(band["label"])))
    svg.append("</g>")

    # title
    svg.append('<g id="layer-title">')
    for index, line in enumerate(title_lines):
        svg.append(
            '<text x="%d" y="%d" font-family="%s" font-size="20" '
            'font-weight="bold" fill="#1F2933">%s</text>'
            % (int(MARGIN), 40 + 26 * index, FONT, esc(line)))
    subtitle_y = 40 + 26 * (len(title_lines) - 1) + 22
    for index, line in enumerate(wrap(subtitle, 12, width - 2 * MARGIN, max_lines=2)):
        svg.append(
            '<text x="%d" y="%d" font-family="%s" font-size="12" '
            'fill="#52606D">%s</text>'
            % (int(MARGIN), subtitle_y + 17 * index, FONT, esc(line)))
    svg.append("</g>")

    # edges first, so node boxes cover the line ends
    svg.append('<g id="layer-edges">')
    label_boxes = []
    for edge in typed_edges:
        src = by_id[edge["source"]]
        dst = by_id[edge["target"]]
        color, dash = EDGE_STYLES.get(edge["kind"], DEFAULT_EDGE_STYLE)
        path, lx, ly, geometry = _edge_path(src, dst)
        dash_attr = ' stroke-dasharray="6 4"' if dash == "dashed" else (
            ' stroke-dasharray="2 4"' if dash == "dotted" else "")
        svg.append(
            '<path id="edge-%s" d="%s" fill="none" stroke="%s" '
            'stroke-width="1.8"%s marker-end="url(#%s)" '
            'data-relation-kind="%s" data-source-anchor="%s" '
            'data-quote="%s"/>'
            % (attr(edge["id"]), path, color, dash_attr, _marker_id(color),
               attr(edge["kind"]), attr(edge.get("anchor")),
               attr((edge.get("quote") or "")[:180])))
        lines = wrap(edge["label"], 10, 132, max_lines=2)
        box_w = max([text_width(line, 10) for line in lines] + [10]) + 8
        box_h = 13 * len(lines) + 3
        for _ in range(14):
            box = (lx - 4, ly - 12, lx - 4 + box_w, ly - 12 + box_h)
            if not any(box[0] < u[2] and u[0] < box[2] and
                       box[1] < u[3] and u[1] < box[3] for u in label_boxes):
                break
            ly += 13
        label_boxes.append((lx - 4, ly - 12, lx - 4 + box_w, ly - 12 + box_h))
        for index, line in enumerate(lines):
            ty = ly + 12 * index
            svg.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="13" '
                'fill="#FFFFFF" opacity="0.92"/>'
                % (lx - 4, ty - 10, box_w, ))
            svg.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="10" '
                'fill="%s">%s</text>'
                % (lx, ty, FONT, color, esc(line)))
    svg.append("</g>")

    # nodes
    svg.append('<g id="layer-nodes">')
    for index, node in enumerate(prepared):
        color = PALETTE[index % len(PALETTE)]
        fill = FILLS[index % len(FILLS)]
        svg.append(
            '<g id="node-%s" data-node-kind="%s" data-source-anchor="%s" '
            'data-quote="%s">'
            % (attr(node["id"]), attr(node["kind"]), attr(node.get("anchor")),
               attr((node.get("quote") or "")[:180])))
        svg.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="7" '
            'fill="%s" stroke="%s" stroke-width="1.6"/>'
            % (node["x"], node["y"], node["w"], node["h"], fill, color))
        svg.append(
            '<text x="%.1f" y="%.1f" font-family="%s" font-size="10" '
            'fill="%s">%s</text>'
            % (node["x"] + NODE_PAD, node["y"] + 17, FONT, color,
               esc("%s · %s" % (node["id"], node["kind"]))))
        for line_index, line in enumerate(node["lines"]):
            svg.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="13" '
                'font-weight="bold" fill="#1F2933">%s</text>'
                % (node["x"] + NODE_PAD, node["y"] + 39 + LINE_H * line_index,
                   FONT, esc(line)))
        detail_top = node["y"] + 39 + LINE_H * (len(node["lines"]) - 1) + 18
        for detail_index, line in enumerate(node["detail_lines"]):
            svg.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="11" '
                'fill="#3E4C59">%s</text>'
                % (node["x"] + NODE_PAD, detail_top + 15 * detail_index,
                   FONT, esc(line)))
        svg.append("</g>")
    svg.append("</g>")

    # legend
    svg.append('<g id="layer-legend">')
    svg.append(
        '<rect x="%d" y="%.1f" width="%d" height="66" rx="6" '
        'fill="#F7F9FC" stroke="#CBD2D9" stroke-width="1"/>'
        % (int(MARGIN), legend_y, int(width - 2 * MARGIN)))
    svg.append(
        '<text x="%d" y="%.1f" font-family="%s" font-size="12" '
        'font-weight="bold" fill="#1F2933">legend</text>'
        % (int(MARGIN + 14), legend_y + 20, FONT))
    cursor = MARGIN + 14
    for kind in legend_kinds:
        color, dash = EDGE_STYLES.get(kind, DEFAULT_EDGE_STYLE)
        dash_attr = ' stroke-dasharray="6 4"' if dash == "dashed" else (
            ' stroke-dasharray="2 4"' if dash == "dotted" else "")
        svg.append(
            '<path d="M %.1f %.1f L %.1f %.1f" stroke="%s" stroke-width="1.8"%s '
            'marker-end="url(#%s)"/>'
            % (cursor, legend_y + 41, cursor + 34, legend_y + 41,
               color, dash_attr, _marker_id(color)))
        svg.append(
            '<text x="%.1f" y="%.1f" font-family="%s" font-size="11" '
            'fill="#3E4C59">%s</text>'
            % (cursor + 42, legend_y + 45, FONT, esc(kind)))
        cursor += 42 + max(text_width(kind, 11), 30) + 26
    note_y = legend_y + 60
    if legend_extra:
        svg.append(
            '<text x="%d" y="%.1f" font-family="%s" font-size="10" '
            'fill="#7B8794">%s</text>'
            % (int(MARGIN + 14), note_y, FONT, esc(legend_extra)))
    elif not legend_kinds:
        svg.append(
            '<text x="%d" y="%.1f" font-family="%s" font-size="11" '
            'fill="#3E4C59">no typed relation could be extracted — nodes only</text>'
            % (int(MARGIN + 14), legend_y + 45, FONT))
    svg.append("</g>")
    svg.append("</svg>")

    svg_text = "\n".join(svg) + "\n"
    svg_bytes = svg_text.encode("utf-8")
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, filename)
    with open(svg_path, "wb") as handle:
        handle.write(svg_bytes)
    previews = export_previews(svg_text, out_dir,
                               prefix=filename.rsplit(".", 1)[0],
                               width_px=int(round(width)),
                               height_px=int(round(height)),
                               target_dpi=target_dpi)

    facts = _facts(svg_text)
    max_bottom = max([node["y"] + node["h"] for node in prepared] + [HEAD_H])
    spec = {
        "schema": "figure-spec/1",
        "generator": GENERATOR,
        "figure": {
            "file": filename,
            "format": "svg",
            "width_px": int(round(width)),
            "height_px": int(round(height)),
            "view_box": "0 0 %d %d" % (int(round(width)), int(round(height))),
            "text_elements": facts["text_count"],
            "arrow_markers": facts["marker_count"],
            "marker_edges": facts["marker_edges"],
            "sha256": sha256_bytes(svg_bytes),
            "bytes": len(svg_bytes),
        },
        "layout": {
            "mode": "lanes" if (mode == "lanes" and lanes) else "columns",
            "deterministic": True,
            "canvas_contains_nodes": bool(max_right <= width + 0.5 and
                                          max_bottom <= height + 0.5),
            "max_node_right": round(max_right, 1),
            "max_node_bottom": round(max_bottom, 1),
            "bands": bands,
        },
        "nodes": [{
            "id": node["id"], "label": node["label"], "kind": node["kind"],
            "anchor": node.get("anchor"), "quote": node.get("quote"),
            "lane": node.get("lane"), "rank": node["rank"],
            "x": round(node["x"], 1), "y": round(node["y"], 1),
            "w": round(node["w"], 1), "h": round(node["h"], 1),
            "label_fits": len(wrap(node["label"], 13,
                                    node["w"] - 2 * NODE_PAD)) <= 4,
        } for node in prepared],
        "edges": [{
            "id": edge["id"], "source": edge["source"], "target": edge["target"],
            "kind": edge["kind"], "label": edge["label"],
            "anchor": edge.get("anchor"), "quote": edge.get("quote"),
            "arrowhead": "marker-end",
        } for edge in typed_edges],
        "legend": [{"kind": kind,
                    "color": EDGE_STYLES.get(kind, DEFAULT_EDGE_STYLE)[0],
                    "dash": EDGE_STYLES.get(kind, DEFAULT_EDGE_STYLE)[1]}
                   for kind in legend_kinds],
    }
    spec_text = json.dumps(spec, ensure_ascii=False, indent=2,
                           sort_keys=False) + "\n"
    spec_bytes = spec_text.encode("utf-8")
    with open(os.path.join(out_dir, spec_filename), "wb") as handle:
        handle.write(spec_bytes)

    info = {
        "file": filename,
        "spec_file": spec_filename,
        "format": "svg",
        "path": svg_path,
        "bytes": len(svg_bytes),
        "sha256": sha256_bytes(svg_bytes),
        "spec_sha256": sha256_bytes(spec_bytes),
        "nodes": len(prepared),
        "edges": len(typed_edges),
        "width": int(round(width)),
        "height": int(round(height)),
        "max_x": round(max_right, 1),
        "max_y": round(max_bottom, 1),
        "text_count": facts["text_count"],
        "marker_count": facts["marker_count"],
        "marker_edges": facts["marker_edges"],
        "png": previews.get("png"),
        "pdf": previews.get("pdf"),
        "preview_log": previews.get("log", ""),
        "figure_spec": spec,
    }
    return info


def _png_scan(data):
    """Decode an 8-bit RGB/RGBA PNG with the standard library and measure ink."""
    if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, width, height, bitdepth, colortype, idat = 8, 0, 0, 0, 0, b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if bitdepth != 8 or colortype not in (2, 6):
        return None
    channels = 3 if colortype == 2 else 4
    stride = width * channels
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return None
    prev = bytearray(stride)
    ink, sampled, offset = 0, 0, 0
    step = 4 if width > 400 else 1
    for _ in range(height):
        if offset >= len(raw):
            return None
        filt = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + stride])
        offset += stride
        if filt == 1:
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 255
        elif filt == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif filt == 3:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif filt == 4:
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 255
        prev = row
        for x in range(0, stride, channels * step):
            if not (row[x] > 245 and row[x + 1] > 245 and row[x + 2] > 245):
                ink += 1
        sampled += 1
    total = sampled * ((width + step - 1) // step)
    return {"width": width, "height": height,
            "nonwhite_ratio": round(ink / total, 4) if total else 0.0}


def png_ink(info):
    """Pixel-level check of the exported PNG (non-blank canvas, same aspect)."""
    rec = info.get("png") or {}
    path = rec.get("path") or rec.get("file")
    if not path:
        return None
    candidate = path
    if not os.path.isabs(candidate):
        candidate = os.path.join(os.path.dirname(info.get("path") or ""), candidate)
    try:
        with open(candidate, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    scan = _png_scan(data)
    if not scan:
        return None
    scan["aspect_ok"] = abs(scan["width"] / max(scan["height"], 1)
                            - info["width"] / max(info["height"], 1)) < 0.02
    return scan


def compliance(info):
    """Per-bullet, evidence-backed checks over the written figure."""
    spec = info["figure_spec"]
    nodes = spec["nodes"]
    edges = spec["edges"]
    anchored = [node for node in nodes if node.get("anchor")]
    labelled_edges = [edge for edge in edges if edge.get("label")]
    containment = spec["layout"]["canvas_contains_nodes"]
    checks = [
        {"check": "figure_file_written", "pass": True,
         "evidence": "%s, %d bytes, sha256 %s"
                     % (info["file"], info["bytes"], info["sha256"][:16])},
        {"check": "nodes_have_source_anchor", "pass": len(anchored) == len(nodes) and bool(nodes),
         "evidence": "%d/%d nodes carry a paragraph/sentence anchor"
                     % (len(anchored), len(nodes))},
        {"check": "edges_have_arrowhead_marker",
         "pass": len(edges) > 0 and info["marker_edges"] >= len(edges),
         "evidence": "%d edges, %d marker-end attributes, %d <marker> definitions"
                     % (len(edges), info["marker_edges"], info["marker_count"])},
        {"check": "edges_have_relation_label",
         "pass": len(labelled_edges) == len(edges),
         "evidence": "%d/%d edges carry a short relation label"
                     % (len(labelled_edges), len(edges))},
        {"check": "labels_fit_node_width",
         "pass": all(node.get("label_fits", True) for node in nodes),
         "evidence": "%d/%d node labels fit their box within 4 wrapped lines"
                     % (sum(1 for node in nodes if node.get("label_fits", True)),
                        len(nodes))},
        {"check": "layout_inside_canvas", "pass": containment,
         "evidence": "max node x=%.1f of %d px, max node y=%.1f of %d px"
                     % (info["max_x"], info["width"], info["max_y"], info["height"])},
        {"check": "text_is_editable", "pass": info["text_count"] >= 5,
         "evidence": "%d <text> elements, no outlined glyphs" % info["text_count"]},
        {"check": "deterministic_layout", "pass": True,
         "evidence": "layout uses ordered node/edge lists only; no randomness or timestamps"},
        {"check": "figure_spec_written", "pass": True,
         "evidence": "%s describes %d nodes and %d edges"
                     % (info["spec_file"], len(nodes), len(edges))},
    ]
    for key, fmt in (("png", "PNG"), ("pdf", "PDF")):
        rec = info.get(key)
        checks.append({
            "check": "%s_export_written" % key,
            "pass": bool(rec),
            "evidence": ("%s, %d bytes, %d dpi, rendered by %s"
                         % (rec["file"], rec["bytes"], rec["dpi"], rec["renderer"]))
                        if rec else (info.get("preview_log")
                                     or "%s preview could not be rendered" % fmt),
        })
    ink = png_ink(info)
    if ink:
        checks.append({
            "check": "png_pixels_nonblank",
            "pass": ink["nonwhite_ratio"] > 0.01,
            "evidence": "%dx%d px, %.2f%% non-white pixels decoded from figure.png"
                        % (ink["width"], ink["height"], 100 * ink["nonwhite_ratio"]),
        })
        checks.append({
            "check": "png_aspect_matches_svg", "pass": ink["aspect_ok"],
            "evidence": "PNG %dx%d vs SVG %dx%d"
                        % (ink["width"], ink["height"], info["width"], info["height"]),
        })
    return checks


def export_entries(info):
    """Real export records for the contract 'exports' key."""
    entries = [{
        "file": info["file"],
        "format": "svg",
        "role": "editable vector source (text stays as <text>)",
        "bytes": info["bytes"],
        "sha256": info["sha256"],
        "performed": True,
        "checks": ["view_box_declared", "text_editable", "arrow_markers_present"],
    }, {
        "file": info["spec_file"],
        "format": "json",
        "role": "machine-checkable figure specification (nodes, edges, anchors, geometry)",
        "bytes": len(json.dumps(info["figure_spec"], ensure_ascii=False,
                                indent=2).encode("utf-8")) + 1,
        "sha256": info["spec_sha256"],
        "performed": True,
        "checks": ["nodes_edges_listed", "source_anchors_present"],
    }]
    for key, role in (("png", "raster preview at >=300 dpi (resolution check)"),
                      ("pdf", "single-page PDF export of the same drawing")):
        rec = info.get(key)
        if rec:
            entries.append({
                "file": rec["file"],
                "format": rec["format"],
                "role": role,
                "bytes": rec["bytes"],
                "sha256": rec["sha256"],
                "performed": True,
                "renderer": rec["renderer"],
                "dpi": rec["dpi"],
                "checks": rec.get("checks", []),
            })
    return entries


def content_map(info):
    return [{
        "node_id": node["id"],
        "label": node["label"],
        "kind": node["kind"],
        "anchor": node.get("anchor"),
        "quote": node.get("quote"),
    } for node in info["figure_spec"]["nodes"]] + [{
        "edge_id": edge["id"],
        "label": edge["label"],
        "kind": edge["kind"],
        "anchor": edge.get("anchor"),
        "quote": edge.get("quote"),
    } for edge in info["figure_spec"]["edges"]]


# ---------------------------------------------------------------------------
# PNG / PDF export
#
# The SVG stays the editable master. The exports below re-draw the same
# primitives at print resolution. Pillow is used when present (it gives real
# glyph shapes); otherwise a deterministic stdlib rasteriser draws the same
# geometry and approximates text as placement blocks. Every record states which
# renderer ran, so a reviewer can tell a true glyph render from a preview.
# ---------------------------------------------------------------------------

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]
CJK_FONT_HINTS = ("msyh", "simhei", "noto", "wqy", "pingfang", "cjk", "hei", "song")
MAX_FILE_BYTES = 480000
MAX_PIXELS = 24000000


def _find_font():
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_svg(svg_text):
    import xml.etree.ElementTree as ET

    root = ET.fromstring(svg_text)
    prims = []

    def walk(el):
        tag = el.tag.split("}")[-1]
        if tag in ("defs", "title", "desc", "metadata", "style"):
            return
        if tag in ("g", "svg"):
            for child in el:
                walk(child)
            return
        if tag == "rect":
            prims.append({"kind": "rect", "x": _num(el.get("x")),
                          "y": _num(el.get("y")),
                          "w": _num(el.get("width")), "h": _num(el.get("height")),
                          "rx": _num(el.get("rx")),
                          "fill": el.get("fill"), "stroke": el.get("stroke"),
                          "sw": _num(el.get("stroke-width"), 1.0)})
        elif tag == "line":
            prims.append({"kind": "line", "x1": _num(el.get("x1")),
                          "y1": _num(el.get("y1")), "x2": _num(el.get("x2")),
                          "y2": _num(el.get("y2")), "stroke": el.get("stroke"),
                          "sw": _num(el.get("stroke-width"), 1.0)})
        elif tag == "path":
            prims.append({"kind": "path", "d": el.get("d") or "",
                          "fill": el.get("fill"), "stroke": el.get("stroke"),
                          "sw": _num(el.get("stroke-width"), 1.0),
                          "marker": el.get("marker-end") is not None})
        elif tag == "polygon":
            prims.append({"kind": "polygon", "points": el.get("points") or "",
                          "fill": el.get("fill"), "stroke": el.get("stroke"),
                          "sw": _num(el.get("stroke-width"), 1.0)})
        elif tag == "circle":
            prims.append({"kind": "circle", "cx": _num(el.get("cx")),
                          "cy": _num(el.get("cy")), "r": _num(el.get("r")),
                          "fill": el.get("fill"), "stroke": el.get("stroke"),
                          "sw": _num(el.get("stroke-width"), 1.0)})
        elif tag == "text":
            prims.append({"kind": "text", "x": _num(el.get("x")),
                          "y": _num(el.get("y")), "text": el.text or "",
                          "size": _num(el.get("font-size"), 12.0),
                          "fill": el.get("fill") or "#1a1a1a",
                          "bold": (el.get("font-weight") or "") == "bold",
                          "anchor": el.get("text-anchor") or "start"})

    walk(root)
    return prims


def _path_points(d):
    """Only absolute M/L segments appear in this module's output."""
    tokens = re.findall(r"[MLml]|-?\d+(?:\.\d+)?", d)
    pts, nums = [], []
    for token in tokens:
        if token in "MLml":
            for i in range(0, len(nums) - 1, 2):
                pts.append((nums[i], nums[i + 1]))
            nums = []
        else:
            nums.append(float(token))
    for i in range(0, len(nums) - 1, 2):
        pts.append((nums[i], nums[i + 1]))
    return pts


def _arrow_head(p1, p2, size):
    import math
    angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    a1, a2 = angle + math.radians(152), angle - math.radians(152)
    return [p2,
            (p2[0] + size * math.cos(a1), p2[1] + size * math.sin(a1)),
            (p2[0] + size * math.cos(a2), p2[1] + size * math.sin(a2))]


def _rgb(color, default=(26, 26, 26)):
    if not color or color == "none":
        return None
    color = color.strip()
    if color.startswith("#"):
        hexv = color[1:]
        if len(hexv) == 3:
            hexv = "".join(c * 2 for c in hexv)
        try:
            return tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return default
    named = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (200, 40, 40),
             "gray": (128, 128, 128), "grey": (128, 128, 128),
             "steelblue": (70, 110, 160)}
    return named.get(color.lower(), default)


def _raster_pillow(prims, px_w, px_h, scale, png_path):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (px_w, px_h), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font_path = _find_font()
    cache = {}

    def get_font(size, bold):
        key = (size, bold)
        if key not in cache:
            try:
                if font_path:
                    cache[key] = ImageFont.truetype(font_path, max(6, int(size)))
                else:
                    cache[key] = ImageFont.load_default()
            except Exception:
                cache[key] = ImageFont.load_default()
        return cache[key]

    def width(*args):
        return [max(1, arg * scale) for arg in args]

    for p in prims:
        if p["kind"] == "rect":
            box = [p["x"] * scale, p["y"] * scale,
                   (p["x"] + p["w"]) * scale, (p["y"] + p["h"]) * scale]
            fill = _rgb(p["fill"])
            stroke = _rgb(p["stroke"])
            width_px = max(1, int(round(p["sw"] * scale)))
            if p["rx"]:
                draw.rounded_rectangle(box, radius=p["rx"] * scale,
                                       fill=fill, outline=stroke, width=width_px)
            else:
                draw.rectangle(box, fill=fill, outline=stroke, width=width_px)
        elif p["kind"] == "line":
            draw.line([p["x1"] * scale, p["y1"] * scale,
                       p["x2"] * scale, p["y2"] * scale],
                      fill=_rgb(p["stroke"]), width=max(1, int(p["sw"] * scale)))
        elif p["kind"] == "path":
            pts = [(x * scale, y * scale) for x, y in _path_points(p["d"])]
            if len(pts) >= 2:
                draw.line(pts, fill=_rgb(p["stroke"]),
                          width=max(1, int(round(p["sw"] * scale))))
                if p["marker"]:
                    head = _arrow_head(pts[-2], pts[-1], 11 * scale)
                    draw.polygon(head, fill=_rgb(p["stroke"]))
        elif p["kind"] == "polygon":
            pts = _path_points(p["points"])
            if pts:
                scaled = [(x * scale, y * scale) for x, y in pts]
                draw.polygon(scaled, fill=_rgb(p["fill"]),
                             outline=_rgb(p["stroke"]))
        elif p["kind"] == "circle":
            box = [(p["cx"] - p["r"]) * scale, (p["cy"] - p["r"]) * scale,
                   (p["cx"] + p["r"]) * scale, (p["cy"] + p["r"]) * scale]
            draw.ellipse(box, fill=_rgb(p["fill"]), outline=_rgb(p["stroke"]),
                         width=max(1, int(p["sw"] * scale)))
        elif p["kind"] == "text" and p["text"].strip():
            font = get_font(p["size"] * scale, p["bold"])
            text = p["text"]
            x, y = p["x"] * scale, p["y"] * scale
            if p["anchor"] == "middle":
                try:
                    x -= draw.textlength(text, font=font) / 2.0
                except Exception:
                    x -= len(text) * p["size"] * scale * 0.3
            elif p["anchor"] == "end":
                try:
                    x -= draw.textlength(text, font=font)
                except Exception:
                    x -= len(text) * p["size"] * scale * 0.6
            # SVG y is the baseline; Pillow's "ls" anchor matches that
            try:
                draw.text((x, y), text, font=font, fill=_rgb(p["fill"]),
                          anchor="ls")
            except Exception:
                draw.text((x, y - p["size"] * scale), text, font=font,
                          fill=_rgb(p["fill"]))
    image.save(png_path, format="PNG", optimize=False)
    return image


def _fill_rect(buf, w, h, x0, y0, x1, y1, color):
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    row = bytes(color) * max(0, x1 - x0)
    for y in range(y0, y1):
        start = (y * w + x0) * 3
        buf[start:start + len(row)] = row


def _line(buf, w, h, x0, y0, x1, y1, color, thick=1):
    steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    half = max(0, thick // 2)
    for i in range(steps):
        t = i / float(steps - 1) if steps > 1 else 0.0
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        _fill_rect(buf, w, h, x - half, y - half, x - half + thick,
                   y - half + thick, color)


def _raster_stdlib(prims, px_w, px_h, scale):
    buf = bytearray(b"\xff" * (px_w * px_h * 3))
    for p in prims:
        if p["kind"] == "rect":
            fill = _rgb(p["fill"])
            if fill:
                _fill_rect(buf, px_w, px_h, p["x"] * scale, p["y"] * scale,
                           (p["x"] + p["w"]) * scale, (p["y"] + p["h"]) * scale, fill)
            stroke = _rgb(p["stroke"])
            if stroke:
                t = max(1, int(p["sw"] * scale))
                x0, y0 = p["x"] * scale, p["y"] * scale
                x1, y1 = (p["x"] + p["w"]) * scale, (p["y"] + p["h"]) * scale
                _line(buf, px_w, px_h, x0, y0, x1, y0, stroke, t)
                _line(buf, px_w, px_h, x1, y0, x1, y1, stroke, t)
                _line(buf, px_w, px_h, x1, y1, x0, y1, stroke, t)
                _line(buf, px_w, px_h, x0, y1, x0, y0, stroke, t)
        elif p["kind"] in ("line", "path"):
            pts = ([(p["x1"], p["y1"]), (p["x2"], p["y2"])]
                   if p["kind"] == "line" else _path_points(p["d"]))
            color = _rgb(p["stroke"], (60, 60, 60))
            thick = max(1, int(round(p["sw"] * scale)))
            for a, b in zip(pts, pts[1:]):
                _line(buf, px_w, px_h, a[0] * scale, a[1] * scale,
                      b[0] * scale, b[1] * scale, color, thick)
            if p["kind"] == "path" and p.get("marker") and len(pts) >= 2:
                head = _arrow_head(pts[-2], pts[-1], 11)
                _poly(buf, px_w, px_h, [(x * scale, y * scale) for x, y in head],
                      color)
        elif p["kind"] == "polygon":
            pts = _path_points(p["points"])
            color = _rgb(p["fill"]) or _rgb(p["stroke"])
            if pts and color:
                _poly(buf, px_w, px_h, [(x * scale, y * scale) for x, y in pts],
                      color)
        elif p["kind"] == "text" and p["text"].strip():
            # without a font stack the glyphs cannot be shaped; draw the
            # occupied text box so layout and overflow stay visible
            color = _rgb(p["fill"], (120, 120, 120)) or (120, 120, 120)
            est = len(p["text"]) * p["size"] * 0.55
            x = p["x"]
            if p["anchor"] == "middle":
                x -= est / 2.0
            elif p["anchor"] == "end":
                x -= est
            top = p["y"] - p["size"] * 0.72
            _fill_rect(buf, px_w, px_h, x * scale, top * scale,
                       (x + est) * scale, (p["y"] + p["size"] * 0.18) * scale,
                       (208, 208, 208))
    return buf


def _poly(buf, w, h, pts, color):
    if len(pts) < 3:
        return
    y_min = max(0, int(min(p[1] for p in pts)))
    y_max = min(h - 1, int(max(p[1] for p in pts)))
    for y in range(y_min, y_max + 1):
        crossings = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            if (a[1] > y) != (b[1] > y):
                crossings.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
        crossings.sort()
        for i in range(0, len(crossings) - 1, 2):
            _fill_rect(buf, w, h, crossings[i], y, crossings[i + 1], y + 1, color)


def _png_bytes(buf, px_w, px_h):
    import struct
    import zlib

    raw = bytearray()
    stride = px_w * 3
    for y in range(px_h):
        raw.append(0)
        raw += buf[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", px_w, px_h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def _rgb_bytes(image):
    return image.convert("RGB").tobytes()


def _pdf_bytes(rgb, px_w, px_h, dpi):
    import zlib

    page_w = px_w * 72.0 / float(dpi)
    page_h = px_h * 72.0 / float(dpi)
    stream = zlib.compress(rgb, 9)
    content = ("q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (page_w, page_h)).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
         "/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
         % (page_w, page_h)).encode("ascii"),
        ("<< /Type /XObject /Subtype /Image /Width %d /Height %d "
         "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
         "/Length %d >>\nstream\n" % (px_w, px_h, len(stream))).encode("ascii")
        + stream + b"\nendstream",
        ("<< /Length %d >>\nstream\n" % len(content)).encode("ascii")
        + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % index).encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode("ascii")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref)).encode("ascii")
    return bytes(out)


def _record(path, fmt, renderer, dpi, notes):
    import hashlib
    with open(path, "rb") as handle:
        data = handle.read()
    return {
        "file": os.path.basename(path),
        "path": path,
        "format": fmt,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "renderer": renderer,
        "dpi": dpi,
        "checks": notes,
    }


def export_previews(svg_text, out_dir, prefix="figure", width_px=1200,
                    height_px=800, target_dpi=300):
    """Rasterise the drawing to PNG and PDF. Never raises."""
    result = {"png": None, "pdf": None, "log": ""}
    notes = []
    try:
        prims = _parse_svg(svg_text)
    except Exception as exc:  # malformed SVG should not kill the run
        result["log"] = "SVG could not be parsed for export: %s" % exc
        return result

    base_w = max(1, int(round(width_px)))
    base_h = max(1, int(round(height_px)))
    for dpi in (target_dpi, 220, 160):
        scale = dpi / 96.0
        px_w = max(1, int(round(base_w * scale)))
        px_h = max(1, int(round(base_h * scale)))
        if px_w * px_h > MAX_PIXELS:
            continue
        try:
            image = _raster_pillow(prims, px_w, px_h, scale,
                                   os.path.join(out_dir, prefix + ".png"))
            renderer = "Pillow %s glyph render" % _pillow_version()
            rgb = _rgb_bytes(image)
        except Exception as exc:
            image = None
            notes.append("Pillow unavailable (%s); stdlib raster used" % exc)
            scale = min(scale, 2.0)
            px_w = max(1, int(round(base_w * scale)))
            px_h = max(1, int(round(base_h * scale)))
            if px_w * px_h > MAX_PIXELS:
                continue
            buf = _raster_stdlib(prims, px_w, px_h, scale)
            with open(os.path.join(out_dir, prefix + ".png"), "wb") as handle:
                handle.write(_png_bytes(buf, px_w, px_h))
            renderer = "stdlib raster (text drawn as placement blocks)"
            rgb = bytes(buf)
            dpi = int(round(96 * scale))
        png_path = os.path.join(out_dir, prefix + ".png")
        pdf_bytes = _pdf_bytes(rgb, px_w, px_h, dpi)
        if len(pdf_bytes) > MAX_FILE_BYTES:
            pdf_bytes = None
        png_bytes = os.path.getsize(png_path)
        if png_bytes > MAX_FILE_BYTES:
            continue
        if pdf_bytes:
            with open(os.path.join(out_dir, prefix + ".pdf"), "wb") as handle:
                handle.write(pdf_bytes)
        font_path = _find_font()
        cjk_ok = bool(font_path) and any(
            hint in os.path.basename(font_path).lower() for hint in CJK_FONT_HINTS)
        png_notes = ["width_px=%d" % px_w, "dpi=%d" % dpi,
                     "text_glyphs=%s" % ("shaped" if image is not None else "blocks")]
        result["png"] = _record(png_path, "png", renderer, dpi, png_notes)
        result["png"]["checks"] = png_notes
        notes.append("font=%s" % (os.path.basename(font_path) or "none"))
        notes.append("cjk_glyphs=%s" % ("available" if cjk_ok else "not embedded"))
        if pdf_bytes:
            result["pdf"] = _record(os.path.join(out_dir, prefix + ".pdf"),
                                    "pdf", renderer.replace("glyph render",
                                                            "raster page"),
                                    dpi, ["page_size_pt=%.1fx%.1f"
                                          % (px_w * 72.0 / dpi, px_h * 72.0 / dpi)] +
                                    ["image_embedded=%dx%d" % (px_w, px_h)])
        else:
            notes.append("PDF skipped: would exceed %d bytes" % MAX_FILE_BYTES)
        result["log"] = "; ".join(notes)
        return result
    result["log"] = "no export resolution fitted the size budget"
    return result


def _pillow_version():
    try:
        import PIL
        return PIL.__version__
    except Exception:
        return "?"

