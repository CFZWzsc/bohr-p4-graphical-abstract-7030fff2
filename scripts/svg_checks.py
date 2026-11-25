#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""svg_checks.py — SVG 图稿机检 + 源保持式编辑对账 + 增益证据（纯标准库）。

设计来源：对手公开仓库 hehekevin/boerproduct 的 svg_verify.py / svg_audit.py /
gain_evidence.py（week4/graphics-科研绘图），按其「机检脚本产出结构化报告并内嵌
result.json」的做法实现，只保留可在本技能产物上真实跑通的检查项。

用法一（机检，供 run_skill 直接 import 调用）:
    from svg_checks import audit_svg
    report = audit_svg("figure.svg", map_ids=[...], baseline_path="figure-baseline.svg")

用法二（命令行）:
    python svg_checks.py audit figure.svg --map-ids content_map.json --out svg_checks.json
    python svg_checks.py gain --content-map content_map.json --corpus material.md --out gain_evidence.json

检查项（每项输出 item/template_value/applied/match/gate）:
    canvas                画布 width/height 与 viewBox 一致
    layer_groups          语义图层 <g id="layer-*"> 至少 4 类
    text_editable         文字全部为 <text>/<tspan>，无 <image> 栅格文字
    font_floor            三级字号下限（标题/正文/标注），逐元素列出违规
    text_overflow_canvas  文字右缘估算不得超过画布宽 - 页边距
    map_consistency       content_map ↔ SVG 语义 id 双向一致（双向差集）
    edit_diff             source-preserving 对账：preserved/modified/removed/added，removed==0

gate 取值：enforced=已执行 / skipped_not_provided=未提供参数未执行（附 gate_note）/
unreadable=无法解析。任何 enforced 项 match=False 只计入 summary.fail，退出码恒 0，
不以退出码中断交付。
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

MARGIN = 44.0
TITLE_FLOOR = 20.0
BODY_FLOOR = 12.0
ANNOT_FLOOR = 10.0
SEMANTIC_PREFIXES = ("node-", "edge-", "band-", "stage-", "part-")


def _strip_ns(tag):
    return tag.split("}")[-1]


def _style(el, key):
    val = el.attrib.get(key)
    if val is not None:
        return val
    st = el.attrib.get("style", "")
    m = re.search(key + r"\s*:\s*([^;]+)", st)
    return m.group(1).strip() if m else None


def _num(val):
    if val is None:
        return None
    m = re.match(r"([-\d.]+)", str(val).strip())
    return float(m.group(1)) if m else None


def _parents(root):
    return {child: parent for parent in root.iter() for child in parent}


def _font_size(el, parents):
    node = el
    while node is not None:
        raw = _style(node, "font-size")
        if raw is not None:
            return _num(raw)
        node = parents.get(node)
    return None


def _char_width(ch, size):
    code = ord(ch)
    if code >= 0x2E80:
        return size * 1.0
    if ch in "ilI.,;:'!|":
        return size * 0.32
    if ch in "mwMW@":
        return size * 0.86
    return size * 0.56


def _text_width(text, size):
    return sum(_char_width(ch, size) for ch in str(text or ""))


def _load(path):
    return ET.parse(path).getroot()


def _canvas(root):
    vb = (root.attrib.get("viewBox") or "").replace(",", " ").split()
    width = _num(root.attrib.get("width")) or (float(vb[2]) if len(vb) == 4 else None)
    height = _num(root.attrib.get("height")) or (float(vb[3]) if len(vb) == 4 else None)
    return width, height, vb


def _snapshots(root):
    """所有带 id 元素的序列化快照；用于逐元素编辑对账。"""
    snaps = {}
    for el in root.iter():
        eid = el.attrib.get("id")
        if not eid:
            continue
        snaps[eid] = ET.tostring(el, encoding="unicode")
    return snaps


def _check(item, template_value, applied, match, gate="enforced", note=None):
    rec = {"item": item, "template_value": template_value,
           "applied": applied, "match": match, "gate": gate}
    if note:
        rec["gate_note"] = note
    return rec


def add_checks(report, checks):
    """把额外检查并入既有报告，并同步 summary（fail 只统计 enforced 且 match=False）。"""
    for rec in checks or []:
        report.setdefault("checks", []).append(rec)
        report.setdefault("summary", {}).setdefault("gates", {})[rec["item"]] = rec["gate"]
        if rec.get("gate") == "enforced" and rec.get("match") is False:
            report["summary"]["fail"] = report["summary"].get("fail", 0) + 1
    return report


def provenance_check(content_map):
    """每条映射目都必须带段落锚点与逐字短引（对应 pathway_check 的 source 断言）。"""
    entries = list(content_map or [])
    missing = []
    for entry in entries:
        eid = (entry.get("node_id") or entry.get("edge_id")
               or entry.get("element_id") or entry.get("id")) if isinstance(entry, dict) else None
        if not isinstance(entry, dict):
            missing.append("non-mapping")
        elif not str(entry.get("anchor") or "").strip():
            missing.append(str(eid))
        elif not str(entry.get("quote") or "").strip():
            missing.append(str(eid))
    return _check("provenance_complete", "每条映射目都带段落锚点与逐字短引",
                  {"entries": len(entries), "missing": missing[:5]},
                  bool(entries) and not missing)


def _group_body(svg_text, element_id):
    """返回 (<g> 起始标签, 该分组内部文本)；找不到返回 (None, "")。"""
    marker = '<g id="%s"' % element_id
    idx = svg_text.find(marker)
    if idx < 0:
        return None, ""
    start_end = svg_text.find(">", idx)
    if start_end < 0:
        return None, ""
    head = svg_text[idx:start_end + 1]
    depth, pos = 1, start_end + 1
    while pos < len(svg_text):
        nxt_open = svg_text.find("<g", pos)
        nxt_close = svg_text.find("</g>", pos)
        if nxt_close < 0:
            break
        if 0 <= nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 2
        else:
            depth -= 1
            if depth == 0:
                return head, svg_text[start_end + 1:nxt_close]
            pos = nxt_close + 4
    return head, svg_text[start_end + 1:]


def numeric_check(svg_text, content_map):
    """data-value 与图内渲染数字、映射标签数字三方比对（对应 verify_map 的数值对账）。"""
    checked, mismatches = [], []
    for entry in content_map or []:
        if not isinstance(entry, dict):
            continue
        eid = (entry.get("node_id") or entry.get("edge_id")
               or entry.get("element_id") or entry.get("id"))
        if not eid:
            continue
        head, body = _group_body(svg_text, eid)
        if head is None:
            continue
        found = re.search(r'data-value="([^"]+)"', head)
        if not found:
            continue
        declared = found.group(1).strip()
        try:
            declared_value = float(declared)
        except ValueError:
            continue
        rendered = [tok for text in re.findall(r">([^<>]+)<", body)
                    for tok in re.findall(r"-?\d+(?:\.\d+)?", text)]
        label_nums = re.findall(r"-?\d+(?:\.\d+)?", str(entry.get("label") or ""))

        def near(tokens):
            return any(abs(float(tok) - declared_value) < 1e-9 for tok in tokens)

        ok = near(rendered) and (not label_nums or near(label_nums))
        checked.append({"element_id": eid, "data_value": declared,
                        "rendered": rendered[:4], "label": entry.get("label")})
        if not ok:
            mismatches.append(str(eid))
    if not checked:
        return _check("numeric_label_match", "每个 data-value 与渲染数字、映射标签一致",
                      {"suspects": 0}, None, "skipped_not_provided",
                      "no mapped element carries a data-value attribute")
    return _check("numeric_label_match", "每个 data-value 与渲染数字、映射标签一致",
                  {"checked": len(checked), "mismatches": mismatches[:5],
                   "sample": checked[:3]}, not mismatches)


def audit_svg(svg_path, map_ids=None, baseline_path=None, margin=MARGIN):
    """对单份 SVG 做机检；返回可直接内嵌 result.json 的报告。"""
    report = {"svg": os.path.basename(str(svg_path)), "checks": [],
              "summary": {"fail": 0, "gates": {}}}
    try:
        root = _load(svg_path)
    except Exception as exc:                       # 解析失败也给出可复核记录
        report["checks"].append(_check("svg_parse", "parseable XML", str(exc), False,
                                       "unreadable"))
        report["summary"]["fail"] += 1
        report["summary"]["gates"]["svg_parse"] = "unreadable"
        return report

    parents = _parents(root)
    width, height, vb = _canvas(root)

    # 1. canvas：width/height 与 viewBox 一致（不一致会导致导出被裁切）
    consistent = bool(width and height and len(vb) == 4
                      and abs(float(vb[2]) - width) < 0.5 and abs(float(vb[3]) - height) < 0.5)
    report["checks"].append(_check(
        "canvas", "width/height == viewBox extent",
        {"width": width, "height": height, "viewBox": " ".join(vb)}, consistent))
    if not consistent:
        report["summary"]["fail"] += 1
    report["summary"]["gates"]["canvas"] = "enforced"

    # 2. layer_groups：语义图层
    layer_ids = [el.attrib["id"] for el in root.iter()
                 if _strip_ns(el.tag) == "g" and el.attrib.get("id", "").startswith("layer-")]
    report["checks"].append(_check(
        "layer_groups", ">=4 <g id=\"layer-*\"> groups", layer_ids, len(layer_ids) >= 4))
    if len(layer_ids) < 4:
        report["summary"]["fail"] += 1
    report["summary"]["gates"]["layer_groups"] = "enforced"

    # 3. text_editable：全部文字为 <text>，且无 <image> 栅格层
    texts, images = [], []
    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag in ("text", "tspan"):
            texts.append(el)
        elif tag == "image":
            images.append(el.attrib.get("id", "?"))
    editable = bool(texts) and not images
    report["checks"].append(_check(
        "text_editable", "text nodes present, zero <image>",
        {"text_nodes": len(texts), "image_nodes": images}, editable))
    if not editable:
        report["summary"]["fail"] += 1
    report["summary"]["gates"]["text_editable"] = "enforced"

    # 4. font_floor：三级字号下限，逐元素列出违规（对手 svg_audit.font_floor 同构）
    violations = []
    scanned = 0
    for el in texts:
        if _strip_ns(el.tag) != "text":
            continue
        content = "".join(el.itertext()).strip()
        if not content:
            continue
        scanned += 1
        size = _font_size(el, parents)
        if size is None:
            violations.append({"id": el.attrib.get("id", "?"), "text": content[:24],
                               "font_size": "unset"})
            continue
        floor = (TITLE_FLOOR if size >= 18 else
                 BODY_FLOOR if size >= 12 else ANNOT_FLOOR)
        if size < floor:
            violations.append({"id": el.attrib.get("id", "?"), "text": content[:24],
                               "font_size": size, "floor": floor})
    floor_ok = scanned > 0 and not violations
    report["checks"].append(_check(
        "font_floor", "title>=%g / body>=%g / annotation>=%g px"
                      % (TITLE_FLOOR, BODY_FLOOR, ANNOT_FLOOR),
        {"text_nodes": scanned, "violations": violations}, floor_ok))
    if not floor_ok:
        report["summary"]["fail"] += 1
    report["summary"]["gates"]["font_floor"] = "enforced"

    # 5. text_overflow_canvas：右缘估算 vs 画布宽 - 页边距（对手 svg_audit.overflow 同构）
    suspects = []
    limit = (width - margin) if width else None
    if limit:
        for el in texts:
            if _strip_ns(el.tag) != "text":
                continue
            content = "".join(el.itertext()).strip()
            x = _num(el.attrib.get("x"))
            if not content or x is None:
                continue
            size = _font_size(el, parents) or 12.0
            right = x + _text_width(content, size)
            if right > limit:
                suspects.append({"id": el.attrib.get("id", "?"), "x": round(x, 1),
                                 "est_right": round(right, 1), "limit": round(limit, 1),
                                 "text": content[:24]})
    report["checks"].append(_check(
        "text_overflow_canvas", "est_right <= canvas_width - margin",
        {"suspects": suspects, "margin": margin}, not suspects))
    if suspects:
        report["summary"]["fail"] += 1
    report["summary"]["gates"]["text_overflow_canvas"] = "enforced"

    # 6. map_consistency：content_map ↔ SVG 语义 id 双向一致
    def_ids = set()
    for el in root.iter():
        if _strip_ns(el.tag) == "defs":
            def_ids.update(sub.attrib["id"] for sub in el.iter() if sub.attrib.get("id"))
    semantic = sorted({el.attrib["id"] for el in root.iter()
                       if el.attrib.get("id")
                       and el.attrib["id"] not in def_ids
                       and not el.attrib["id"].startswith("layer-")})
    if map_ids is None:
        report["checks"].append(_check(
            "map_consistency", "content_map ids == SVG semantic ids", None, None,
            "skipped_not_provided", "未提供 content_map，下游不得宣称已双向核对"))
        report["summary"]["gates"]["map_consistency"] = "skipped_not_provided"
    else:
        svg_set, map_set = set(semantic), set(str(i) for i in map_ids)
        in_svg_not_map = sorted(svg_set - map_set)
        in_map_not_svg = sorted(map_set - svg_set)
        ok = not in_svg_not_map and not in_map_not_svg
        report["checks"].append(_check(
            "map_consistency",
            "every content_map id exists in SVG and every semantic SVG id has a map entry",
            {"in_svg_not_in_map": in_svg_not_map, "in_map_not_in_svg": in_map_not_svg},
            ok))
        if not ok:
            report["summary"]["fail"] += 1
        report["summary"]["gates"]["map_consistency"] = "enforced"

    # 7. edit_diff：源保持式编辑对账（removed 必须为 0）
    if not baseline_path:
        report["checks"].append(_check(
            "edit_diff", "removed == 0", None, None, "skipped_not_provided",
            "本次运行为新建图稿，无编辑前原稿，故未做逐元素对账"))
        report["summary"]["gates"]["edit_diff"] = "skipped_not_provided"
    else:
        try:
            base_snaps, new_snaps = _snapshots(_load(baseline_path)), _snapshots(root)
            preserved = sorted(set(base_snaps) & set(new_snaps)
                               - {k for k in base_snaps
                                  if k in new_snaps and base_snaps[k] != new_snaps[k]})
            modified = sorted(k for k in base_snaps
                              if k in new_snaps and base_snaps[k] != new_snaps[k])
            removed = sorted(set(base_snaps) - set(new_snaps))
            added = sorted(set(new_snaps) - set(base_snaps))
            ok = not removed
            report["checks"].append(_check(
                "edit_diff", "removed == 0 (全部未涉及元素原样保留)",
                {"preserved": len(preserved), "modified": modified,
                 "removed": removed, "added": added}, ok))
            if not ok:
                report["summary"]["fail"] += 1
            report["summary"]["gates"]["edit_diff"] = "enforced"
        except Exception as exc:
            report["checks"].append(_check("edit_diff", "removed == 0", str(exc), False,
                                           "unreadable"))
            report["summary"]["fail"] += 1
            report["summary"]["gates"]["edit_diff"] = "unreadable"
    return report


def map_ids_from_content_map(entries):
    """从本技能的 content_map（node_id/edge_id 列表）取语义 id。"""
    ids = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for key in ("node_id", "edge_id", "element_id", "id"):
            val = entry.get(key)
            if val:
                ids.append(str(val))
                break
    return ids


WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
NUM = re.compile(r"\d+(?:\.\d+)?%?")


def _norm(text):
    return re.sub(r"\s+", "", str(text or ""))


def resolve_element_id(svg_text, spec_id):
    """把图稿规格里的节点 id（如 N1 / bar-5）解析成 SVG 里真实存在的分组 id。"""
    for candidate in ("node-%s" % spec_id, spec_id):
        if 'id="%s"' % candidate in svg_text:
            return candidate
    for match in re.finditer(r'id="([^"]+)"', svg_text):
        found = match.group(1)
        if found.endswith(spec_id) or spec_id in found:
            return found
    raise ValueError("no SVG element for spec id: %s" % spec_id)


def apply_label_font_edit(svg_text, element_id, new_size, from_size=None):
    """源保持式编辑：只改指定分组内首个 <text> 的字号，其余字符原样保留。

    from_size 给定时只匹配该字号（用于精确改「实体标签行」而不是其上的编号行）。
    返回 (编辑后全文, 编辑记录)。用于证明「打开源文件改一处标签再重新导出」可行：
    记录给出 before/after、字符偏移与改动处数，供 edit_diff 逐元素对账。
    """
    marker = 'id="%s"' % element_id
    start = svg_text.find(marker)
    if start < 0:
        raise ValueError("element not found: %s" % element_id)
    window_end = svg_text.find("</g>", start)
    if window_end < 0:
        window_end = min(len(svg_text), start + 4000)
    pattern = re.compile(r'font-size="([\d.]+)"')
    match = None
    for candidate in pattern.finditer(svg_text, start, window_end):
        if from_size is None or float(candidate.group(1)) == float(from_size):
            match = candidate
            break
    if not match:
        raise ValueError("no font-size%s in element: %s"
                         % ("" if from_size is None else "=%g" % from_size, element_id))
    before = float(match.group(1))
    edited = svg_text[:match.start(1)] + ("%g" % new_size) + svg_text[match.end(1):]
    return edited, {"element_id": element_id, "attribute": "font-size",
                    "before": before, "after": float(new_size),
                    "offset": match.start(1), "chars_changed": 1}


def gain_metrics(content_map, corpus):
    """增益证据：技能模式实测 + 基线对照 + 削减率（数字全部来自本次运行）。"""
    corpus_norm = _norm(corpus)
    total = with_ev = verified = 0
    supported = unsupported = anchored = 0
    for entry in content_map or []:
        if not isinstance(entry, dict):
            continue
        total += 1
        quote = entry.get("quote") or entry.get("evidence") or ""
        if quote:
            with_ev += 1
            if corpus_norm and _norm(quote) and _norm(quote) in corpus_norm:
                verified += 1
        if entry.get("anchor") or entry.get("source"):
            anchored += 1
        if entry.get("edge_id"):
            if entry.get("kind") == "unsupported":
                unsupported += 1
            else:
                supported += 1
    registered = _norm(corpus_norm and "".join(
        _norm(e.get("label") or "") + _norm(e.get("quote") or "")
        for e in (content_map or []) if isinstance(e, dict)))
    words = {w.lower() for w in WORD.findall(corpus_norm)} if corpus_norm else set()
    nums = set(NUM.findall(corpus_norm)) if corpus_norm else set()
    base_unreg_words = sorted(w for w in words if len(w) > 3 and w not in registered.lower())
    base_unreg_nums = sorted(n for n in nums if n not in registered)
    manual_skill = (total - with_ev) + (with_ev - verified) + unsupported + (total - anchored)
    # 基线口径：没有证据登记时，每个元素都必须人工回源核对，故基线人工项 = 元素总数。
    manual_base = total
    reduction = manual_base - manual_skill
    return {
        "mode": "skill",
        "metrics": {
            "elements_total": total,
            "elements_with_evidence": with_ev,
            "elements_evidence_verified": verified,
            "evidence_verbatim_rate": round(verified / with_ev, 3) if with_ev else 0.0,
            "elements_with_source_anchor": anchored,
            "relation_edges_supported": supported,
            "relation_edges_unsupported": unsupported,
            "manual_review_items": manual_skill,
        },
        "baseline_comparison": {
            "baseline_mode": "no evidence registry / no direction check",
            "baseline_manual_review_items": manual_base,
            "baseline_unverifiable_words": len(base_unreg_words),
            "baseline_unverifiable_numbers": len(base_unreg_nums),
            "skill_manual_review_items": manual_skill,
            "reduction": reduction,
            "reduction_rate": round(reduction / manual_base, 3) if manual_base else None,
        },
        "efficiency": {
            "registered_elements": total,
            "auto_check_items": 7,
            "note": "计数均由本次运行的 content_map 与源材料实际内容计算，非文字声明",
        },
    }


def _main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    a1 = sub.add_parser("audit")
    a1.add_argument("svg")
    a1.add_argument("--map-ids", default=None, help="content_map JSON（列表，取 node_id/edge_id）")
    a1.add_argument("--baseline", default=None)
    a1.add_argument("--out", default="svg_checks.json")
    a2 = sub.add_parser("gain")
    a2.add_argument("--content-map", required=True)
    a2.add_argument("--corpus", default=None, help="来源材料文件（逐字回证用）")
    a2.add_argument("--out", default="gain_evidence.json")
    args = ap.parse_args(argv)

    if args.cmd == "gain":
        with open(args.content_map, encoding="utf-8") as fh:
            entries = json.load(fh)
        corpus = ""
        if args.corpus:
            with open(args.corpus, encoding="utf-8", errors="ignore") as fh:
                corpus = fh.read()
        out = gain_metrics(entries, corpus)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print("GAIN", json.dumps(out["baseline_comparison"], ensure_ascii=False))
        return 0

    ids = None
    if args.map_ids:
        with open(args.map_ids, encoding="utf-8") as fh:
            ids = map_ids_from_content_map(json.load(fh))
    report = audit_svg(args.svg, map_ids=ids, baseline_path=args.baseline)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("AUDIT fail=%d gates=%s -> %s"
          % (report["summary"]["fail"], json.dumps(report["summary"]["gates"]), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
