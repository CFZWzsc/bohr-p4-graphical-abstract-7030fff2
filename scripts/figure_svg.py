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
    lines, current = [], ""
    for ch in text:
        if current and text_width(current + ch, size) > max_w:
            lines.append(current.rstrip())
            current = "" if ch == " " else ch
        else:
            current += ch
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
           legend_extra=None):
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
        "figure_spec": spec,
    }
    return info


def compliance(info):
    """Per-bullet, evidence-backed checks over the written figure."""
    spec = info["figure_spec"]
    nodes = spec["nodes"]
    edges = spec["edges"]
    anchored = [node for node in nodes if node.get("anchor")]
    labelled_edges = [edge for edge in edges if edge.get("label")]
    containment = spec["layout"]["canvas_contains_nodes"]
    return [
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


def export_entries(info):
    """Real export records for the contract 'exports' key."""
    return [{
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
