#!/usr/bin/env python3
"""Graphical abstract composer (standard library only).

Classifies source sentences into background / method / result / conclusion,
binds every panel claim to its evidence sentence, derives flow arrows and a
mini-chart from any table, checks style budgets, and writes a reproducible
graphical-abstract specification to /app/output/result.json.  Offline and
deterministic; no rendered image or unsupported claim is fabricated.
"""

import argparse
import hashlib
import json
import os
import re
import sys

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
SKILL_NAME = "graphical-abstract-composer"
SKILL_VERSION = "1.0.0"
CHALLENGE_ID = "graphical-abstract-7030fff2"
MAX_TEXT = 400000
SKIP_KEYS = {"output_contract", "scoring", "tags", "meta", "case_id",
             "challenge_id", "skill", "contract_notes"}

PANEL_ROLES = ["background", "method", "result", "conclusion"]
ROLE_LABELS = {"background": "问题与背景", "method": "方法与设计",
               "result": "关键结果", "conclusion": "结论与意义"}
CUE_WORDS = {
    "background": ("背景", "问题", "挑战", "瓶颈", "需求", "重要性", "难以", "缺乏",
                   "challenge", "problem", "need", "bottleneck", "remains"),
    "method": ("方法", "设计", "构建", "合成", "制备", "开发", "提出", "采用", "流程",
               "we developed", "we designed", "we propose", "method", "approach"),
    "result": ("结果", "发现", "显示", "表明", "提高", "降低", "达到", "实现", "显著",
               "results", "we found", "showed", "increased", "reduced", "achieved"),
    "conclusion": ("结论", "意义", "应用", "前景", "综上", "因此", "可用于", "推广",
                   "conclusion", "implication", "in summary", "future", "enable"),
}
NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|倍|mg|g|mL|L|h|小时|分钟|天|周|°C|nm|μm|um|mM|μM|uM|nM|kDa|Da|个|例|名|人)")
STOPWORDS = {"the", "and", "for", "with", "from", "that", "this", "these", "those",
             "into", "using", "used", "were", "was", "are", "is", "be", "of", "in",
             "on", "to", "by", "as", "at", "an", "or", "we", "our", "their", "its"}


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_text_file(path):
    try:
        if os.path.isfile(path) and os.path.getsize(path) <= 4 * 1024 * 1024:
            with open(path, "r", encoding="utf-8-sig") as handle:
                return handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    return None


def collect_material(case, base_dir):
    parts, unreadable = [], []

    def walk(node, label):
        if isinstance(node, str):
            stripped = node.strip()
            if len(stripped) >= 30:
                parts.append({"label": label, "text": stripped, "path": None})
                return
            if stripped and not stripped.startswith(("{", "[")):
                for candidate in (stripped, os.path.join(base_dir, stripped)):
                    text = read_text_file(candidate)
                    if text is not None:
                        parts.append({"label": label, "text": text, "path": candidate})
                        return
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in SKIP_KEYS or key in ("id", "name", "title", "type", "kind"):
                    continue
                walk(value, "%s.%s" % (label, key))
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                walk(value, "%s[%d]" % (label, idx))

    for key, value in case.items():
        if key in SKIP_KEYS:
            continue
        walk(value, key)
    parts.sort(key=lambda item: (-len(item["text"]), item["label"]))
    return parts, unreadable


def task_text(base_dir):
    for name in ("task.md", "TASK.md", "task.txt"):
        path = os.path.join(base_dir, name)
        text = read_text_file(path)
        if text:
            return text, path
    return "", None


def split_sentences(text):
    sentences = []
    for chunk in re.split(r"(?<=[。；;！!？?])|(?<=\.)\s+|\n{2,}", text):
        sentence = norm(chunk)
        if 12 <= len(sentence) <= 320:
            sentences.append(sentence)
    return sentences


def classify_sentence(sentence):
    lowered = sentence.lower()
    best_role, best_score = None, 0
    for role in PANEL_ROLES:
        score = sum(1 for cue in CUE_WORDS[role] if cue.lower() in lowered)
        if score > 0 and score >= best_score:
            best_role, best_score = role, score
    return best_role


def pick_panels(case, sentences):
    explicit = case.get("panels")
    panels = []
    if isinstance(explicit, list) and explicit:
        for idx, entry in enumerate(explicit):
            if isinstance(entry, str):
                panels.append({"role": PANEL_ROLES[idx % 4] if idx < 4 else "extra",
                               "message": entry.strip(), "visual": None,
                               "evidence_sentence": None, "source": "panels"})
            elif isinstance(entry, dict):
                panels.append({
                    "role": str(entry.get("role") or entry.get("type") or
                                (PANEL_ROLES[idx] if idx < 4 else "extra")),
                    "message": entry.get("message") or entry.get("text") or entry.get("claim"),
                    "visual": entry.get("visual") or entry.get("visual_type"),
                    "evidence_sentence": entry.get("evidence") or entry.get("evidence_sentence"),
                    "source": "panels",
                })
        if panels:
            return panels

    buckets = {role: [] for role in PANEL_ROLES}
    for idx, sentence in enumerate(sentences):
        role = classify_sentence(sentence)
        if role:
            buckets[role].append((idx, sentence))
    for role in PANEL_ROLES:
        candidates = buckets[role]
        if candidates:
            idx, sentence = candidates[0]
            panels.append({"role": role, "message": sentence[:220],
                           "visual": {"background": "concept_icon", "method": "workflow",
                                      "result": "mini_chart", "conclusion": "impact_icon"}[role],
                           "evidence_sentence": sentence, "evidence_index": idx,
                           "source": "sentence_classifier"})
        else:
            panels.append({"role": role, "message": None,
                           "visual": {"background": "concept_icon", "method": "workflow",
                                      "result": "mini_chart", "conclusion": "impact_icon"}[role],
                           "evidence_sentence": None, "evidence_index": None,
                           "source": "missing"})
    return panels


def extract_entities(text):
    names = []
    for match in re.finditer(
            r"([\u4e00-\u9fffA-Za-z0-9\-/+]{2,18}?"
            r"(?:蛋白|基因|受体|因子|材料|颗粒|催化剂|电极|膜|细胞|通路|分子|复合物))", text):
        names.append(match.group(1))
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9\-]{2,15})\b", text):
        if match.group(1).lower() not in STOPWORDS:
            names.append(match.group(1))
    seen, result = set(), []
    for name in names:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        result.append(name)
    return result[:24]


def find_table(case, parts):
    def from_node(node):
        if isinstance(node, dict):
            columns = node.get("columns") or node.get("header")
            rows = node.get("rows") or node.get("data") or node.get("records")
            if isinstance(columns, list) and isinstance(rows, list) and rows:
                records = []
                for row in rows:
                    if isinstance(row, dict):
                        records.append(row)
                    elif isinstance(row, list):
                        records.append(dict(zip([str(c) for c in columns], row)))
                if records:
                    return {"columns": [str(c) for c in columns], "records": records}
        if isinstance(node, list) and node and all(isinstance(entry, dict) for entry in node):
            columns = []
            for entry in node:
                for key in entry:
                    if str(key) not in columns:
                        columns.append(str(key))
            return {"columns": columns, "records": node}
        return None

    for key in ("tables", "table", "data", "dataset", "results"):
        found = from_node(case.get(key))
        if found:
            return found
    for part in parts:
        lines = [line for line in part["text"].splitlines() if line.strip()]
        if len(lines) >= 3 and all("," in line or "\t" in line for line in lines[:3]):
            separator = "\t" if "\t" in lines[0] else ","
            rows = [[cell.strip() for cell in line.split(separator)] for line in lines]
            width = len(rows[0])
            if width >= 2 and all(len(row) == width for row in rows[:5]):
                columns = rows[0]
                records = [dict(zip(columns, row)) for row in rows[1:]]
                return {"columns": columns, "records": records}
    return None


def parse_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("%", "")
        if re.match(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)$", cleaned):
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def build_mini_chart(table):
    if not table:
        return None
    numeric_columns = []
    for column in table["columns"]:
        values = [parse_number(record.get(column)) for record in table["records"]]
        if sum(1 for value in values if value is not None) >= 2:
            numeric_columns.append((column, values))
    if not numeric_columns:
        return None
    category_column = next((column for column in table["columns"]
                            if column not in [name for name, _ in numeric_columns]), None)
    name, values = numeric_columns[0]
    clean = [value for value in values if value is not None]
    low, high = min(clean), max(clean)
    span = (high - low) or 1.0
    points = [{"category": norm(table["records"][idx].get(category_column)) if category_column else str(idx + 1),
               "value": values[idx],
               "normalized": round((values[idx] - low) / span, 4)}
              for idx in range(len(values)) if values[idx] is not None]
    return {"type": "sparkline_or_bar", "series": name,
            "unit": (re.search(r"[\(\[]([^\)\]]+)[\)\]]", name).group(1)
                     if re.search(r"[\(\[]([^\)\]]+)[\)\]]", name) else None),
            "points": points, "source": "table", "rendered": False}


def contract_fields(contract):
    fields, unsupported = [], []
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            return list(contract["properties"].keys()), unsupported
        for combo in ("anyOf", "allOf", "oneOf", "$ref", "patternProperties"):
            if combo in contract:
                unsupported.append(combo)
        nested = contract.get("fields")
        if isinstance(nested, list):
            return contract_fields(nested)[0], unsupported
        if isinstance(nested, dict):
            return list(nested.keys()), unsupported
        reserved = {"type", "required", "optional", "additionalProperties", "title",
                    "description", "version", "schema", "$schema"}
        return [key for key in contract if key not in reserved and not str(key).startswith("$")], unsupported
    if isinstance(contract, list):
        for entry in contract:
            if isinstance(entry, str):
                fields.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("field") or entry.get("key")
                if name:
                    fields.append(str(name))
        return fields, unsupported
    return fields, unsupported


ALIASES = {
    "status": "status", "graphic_spec": "graphic_spec", "graphical_abstract": "graphic_spec",
    "panels": "panels", "flow": "flow", "legend": "legend",
    "style_checks": "style_checks", "consistency_checks": "style_checks",
    "caption": "caption", "alt_text": "alt_text", "export_plan": "export_plan",
    "export_artifact": "export_plan", "editable_source": "editable_source",
    "gaps": "gaps", "uncertainty": "uncertainty", "evidence": "evidence",
    "stats": "stats", "limitations": "limitations",
}


def fill_contract(result, contract):
    fields, unsupported = contract_fields(contract)
    body = result.get("result") if isinstance(result.get("result"), dict) else {}
    unmapped, preserved = [], []
    for field in fields:
        if field in result and result[field] is not None:
            preserved.append(field)
            continue
        key = ALIASES.get(field.lower(), field.lower())
        if key in result and result[key] is not None:
            result[field] = result[key]
            preserved.append(field)
        elif key in body and body[key] is not None:
            result[field] = body[key]
            preserved.append(field)
        else:
            result[field] = None
            unmapped.append(field)
    result["contract_notes"] = {
        "declared_fields": fields, "preserved_fields": preserved,
        "unmapped_fields": unmapped, "unsupported_features": unsupported,
        "note": "Unsupported or absent fields are null; nothing is fabricated.",
    }
    if result.get("status") == "success" and (unmapped or unsupported):
        result["status"] = "partial"


def write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_export_plan(case):
    requested = case.get("export") or case.get("target_format") or case.get("export_spec")
    formats = []
    if isinstance(requested, str):
        formats = [part.strip().lower() for part in re.split(r"[,/;]", requested) if part.strip()]
    if not formats:
        formats = ["svg", "pdf", "png"]
    rules = {
        "svg": ["text_editable", "panels_as_groups", "arrows_editable"],
        "pdf": ["fonts_embedded", "vector_preserved", "cmyk_or_rgb_declared"],
        "png": ["width_at_least_1200_px", "dpi_at_least_300", "no_text_clipping"],
        "tiff": ["dpi_at_least_300", "lossless_compression"],
    }
    return [{"format": fmt, "required_checks": rules.get(fmt, ["resolution_declared"]),
             "performed": False,
             "reason": "no renderer invoked; checks are a reproducible plan"} for fmt in formats]


def main():
    parser = argparse.ArgumentParser(description="Graphical abstract composer")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args()

    result = {
        "case_id": None,
        "challenge_id": CHALLENGE_ID,
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
        "status": "failed",
        "result": {
            "graphic_spec": {"canvas": {}, "panels": [], "flow": [], "legend": [],
                             "mini_chart": None, "reading_order": []},
            "panels": [], "flow": [], "legend": [], "style_checks": [],
            "caption": None, "alt_text": None, "export_plan": [],
            "editable_source": None, "gaps": [],
        },
        "evidence": [], "uncertainty": [], "errors": [], "stats": {},
        "limitations": [
            "Sentence-to-panel classification uses cue words and may misassign borderline claims.",
            "The output is a layout and content specification, not a rendered image.",
        ],
    }

    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case root is not an object")
    except (OSError, ValueError) as exc:
        result["errors"].append({"stage": "read_case", "type": type(exc).__name__,
                                 "detail": "case.json missing or not valid JSON"})
        result["status"] = "insufficient_input"
        write_json(args.out, result)
        return 2

    base_dir = os.path.dirname(os.path.abspath(args.case))
    result["case_id"] = case.get("case_id") or case.get("id")
    contract = case.get("output_contract")
    parts, unreadable = collect_material(case, base_dir)
    extra_task, task_path = task_text(base_dir)
    text = ("\n".join(part["text"] for part in parts) + "\n" + extra_task)[:MAX_TEXT]

    if not text.strip():
        result["status"] = "insufficient_input"
        result["uncertainty"].append({"item": "material", "detail": "no readable abstract or sections"})
        fill_contract(result, contract)
        write_json(args.out, result)
        return 0

    sentences = split_sentences(text)
    panels = pick_panels(case, sentences)
    table = find_table(case, parts)
    mini_chart = build_mini_chart(table)
    entities = extract_entities(text)

    style = case.get("style") if isinstance(case.get("style"), dict) else {}
    canvas = case.get("canvas") if isinstance(case.get("canvas"), dict) else {}
    width_px = int(canvas.get("width_px") or style.get("width_px") or 1200)
    height_px = int(canvas.get("height_px") or style.get("height_px") or 600)
    min_font = float(style.get("min_font_pt") or 8)
    max_colors = int(style.get("max_colors") or 3)
    max_words = int(style.get("max_words_per_panel") or 18)
    palette = ["#0072B2", "#D55E00", "#009E73"][:max_colors]

    checks = []
    for panel in panels:
        if not panel.get("message"):
            checks.append({"check": "panel_missing", "role": panel["role"]})
            continue
        if not panel.get("evidence_sentence"):
            checks.append({"check": "claim_without_evidence", "role": panel["role"]})
        words = len(re.findall(r"[A-Za-z0-9\-]+|[\u4e00-\u9fff]", panel["message"]))
        if words > max_words * 3:
            checks.append({"check": "panel_word_budget_exceeded", "role": panel["role"],
                           "estimated_tokens": words, "budget": max_words * 3})

    present_roles = {panel["role"] for panel in panels if panel.get("message")}
    for role in PANEL_ROLES:
        if role not in present_roles and not any(
                check["check"] == "panel_missing" and check["role"] == role for check in checks):
            checks.append({"check": "panel_missing", "role": role})

    number_checks = []
    if table:
        numeric_values = set()
        for record in table["records"]:
            for column in table["columns"]:
                value = parse_number(record.get(column))
                if value is not None:
                    numeric_values.add(round(value, 6))
        for match in NUMBER_RE.finditer(text):
            value = round(float(match.group(1)), 6)
            if numeric_values and value not in numeric_values:
                number_checks.append({"check": "number_not_in_table", "number": match.group(1),
                                      "unit": match.group(2),
                                      "context": norm(text[max(0, match.start() - 24):match.end() + 8])})
    else:
        for match in NUMBER_RE.finditer(text):
            number_checks.append({"check": "number_without_table", "number": match.group(1),
                                  "unit": match.group(2)})
    checks.extend(number_checks[:20])

    if not mini_chart and any(panel["role"] == "result" and panel.get("message") for panel in panels):
        checks.append({"check": "result_panel_without_data", "detail": "no table for result panel"})

    flow = []
    explicit_flow = case.get("flow")
    if isinstance(explicit_flow, list):
        for idx, entry in enumerate(explicit_flow):
            if isinstance(entry, dict) and entry.get("source") and entry.get("target"):
                flow.append({"id": "F%d" % (idx + 1), "source": str(entry["source"]),
                             "target": str(entry["target"]), "kind": "process"})
    if not flow and len(entities) >= 2:
        for idx in range(min(3, len(entities) - 1)):
            flow.append({"id": "F%d" % (idx + 1), "source": entities[idx],
                         "target": entities[idx + 1], "kind": "inferred_order",
                         "evidence": "entity mention order, not a stated causal link"})
    if not flow:
        checks.append({"check": "flow_missing", "detail": "no explicit or inferable panel-to-panel flow"})
    else:
        for idx, arrow in enumerate(flow):
            if arrow.get("kind") == "inferred_order":
                checks.append({"check": "inferred_flow_arrow", "arrow": arrow["id"],
                               "detail": "order-only inference; confirm before publication"})

    if len(panels) < 4:
        checks.append({"check": "panel_count_below_four", "count": len(panels)})
    if not any(panel.get("role") == "conclusion" and panel.get("message") for panel in panels):
        checks.append({"check": "conclusion_unsupported", "detail": "no conclusion sentence found"})

    legend = [{"key": panel["role"], "label": ROLE_LABELS.get(panel["role"], panel["role"]),
               "color": palette[idx % len(palette)]}
              for idx, panel in enumerate(panels)]

    caption_parts = [panel["message"] for panel in panels
                     if panel.get("message") and panel["role"] in ("method", "result", "conclusion")]
    caption = None
    if caption_parts:
        caption = "；".join(part[:90].rstrip("。；;.") for part in caption_parts[:3]) + "。"

    alt_text = None
    if panels:
        alt_text = "图形摘要，共 %d 个面板，阅读顺序：%s。" % (
            len(panels), "、".join(ROLE_LABELS.get(panel["role"], panel["role"]) for panel in panels))

    graphic_spec = {
        "canvas": {"width_px": width_px, "height_px": height_px, "dpi": 300,
                   "background": "#FFFFFF", "palette": palette},
        "panels": [{"id": "P%d" % (idx + 1), "role": panel["role"],
                    "label": ROLE_LABELS.get(panel["role"], panel["role"]),
                    "message": panel.get("message"),
                    "visual": panel.get("visual"),
                    "evidence_sentence": panel.get("evidence_sentence"),
                    "evidence_index": panel.get("evidence_index"),
                    "word_budget": max_words,
                    "bbox_ratio": [idx, 0, 1, 1] if len(panels) == 4 else None}
                   for idx, panel in enumerate(panels)],
        "flow": flow,
        "legend": legend,
        "mini_chart": mini_chart,
        "reading_order": [panel["role"] for panel in panels],
        "style_rules": {"min_font_pt": min_font, "max_colors": max_colors,
                        "max_words_per_panel": max_words,
                        "arrow_between_panels": True, "alt_text_required": True},
        "caption": caption,
    }

    style_checks = [
        {"check": "canvas_width", "value": width_px, "pass": width_px >= 1200},
        {"check": "color_count", "value": max_colors, "pass": max_colors <= 3},
        {"check": "font_size", "value": min_font, "pass": min_font >= 8},
        {"check": "resolution", "value": 300, "unit": "dpi", "pass": True, "rendered": False},
    ]

    gaps = [{"type": "missing_panel", "role": check["role"]}
            for check in checks if check["check"] == "panel_missing"]
    if not mini_chart:
        gaps.append({"type": "no_quantitative_panel", "detail": "no table or numeric series found"})
    if not flow:
        gaps.append({"type": "no_flow"})

    source_register = [
        {"label": part["label"], "path": None if not part["path"] else os.path.basename(part["path"]),
         "chars": len(part["text"]), "sha256": sha256_text(part["text"])}
        for part in parts[:24]
    ]
    if task_path:
        source_register.append({"label": "task.md", "path": os.path.basename(task_path),
                                "chars": len(extra_task), "sha256": sha256_text(extra_task)})

    result["result"].update({
        "graphic_spec": graphic_spec,
        "panels": graphic_spec["panels"],
        "flow": flow,
        "legend": legend,
        "style_checks": style_checks + checks,
        "caption": caption,
        "alt_text": alt_text,
        "export_plan": build_export_plan(case),
        "editable_source": {"target_file": "graphical_abstract.svg",
                            "panel_groups": ["P%d" % (idx + 1) for idx in range(len(panels))],
                            "produced": False,
                            "reason": "emitted as a reproducible specification, not a fabricated render"},
        "source_register": source_register,
        "gaps": gaps,
    })
    result["evidence"] = [
        {"type": "panel_evidence", "panel": "P%d" % (idx + 1), "role": panel["role"],
         "sentence": (panel.get("evidence_sentence") or "")[:240]}
        for idx, panel in enumerate(panels) if panel.get("evidence_sentence")
    ]
    result["evidence"].append({"type": "sentences_considered", "count": len(sentences)})
    for entry in unreadable:
        result["uncertainty"].append({"item": "file", "detail": "unreadable_or_missing",
                                      "path": os.path.basename(entry["path"])})
    if number_checks:
        result["uncertainty"].append({"item": "numbers",
                                      "detail": "text numbers could not all be matched to the table",
                                      "count": len(number_checks)})
    if any(arrow.get("kind") == "inferred_order" for arrow in flow):
        result["uncertainty"].append({"item": "flow", "detail": "arrow order inferred from mentions"})
    if not table:
        result["uncertainty"].append({"item": "data", "detail": "no table found for the result panel"})

    result["stats"] = {
        "sentences": len(sentences),
        "panels": len(panels),
        "panels_with_evidence": sum(1 for panel in panels if panel.get("evidence_sentence")),
        "flow_arrows": len(flow),
        "entities": len(entities),
        "mini_chart_points": len(mini_chart["points"]) if mini_chart else 0,
        "checks": len(style_checks) + len(checks),
        "number_issues": len(number_checks),
    }

    supported = sum(1 for panel in panels if panel.get("message") and panel.get("evidence_sentence"))
    if supported >= 3 and mini_chart:
        result["status"] = "success"
    elif supported >= 2:
        result["status"] = "partial"
    else:
        result["status"] = "insufficient_input"
    if any(check["check"] in ("number_not_in_table", "inferred_flow_arrow") for check in checks):
        result["status"] = "partial"

    fill_contract(result, contract)
    write_json(args.out, result)
    if result["status"] == "failed":
        return 2
    if result.get("contract_notes", {}).get("unmapped_fields"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
