---
name: graphical-abstract-composer
description: Use when a paper's problem, method, key result and implication must become a single-panel graphical abstract with claims tied to evidence and style constraints checked.
---

# 图形摘要与科学视觉传达

## 用途与适用条件

把论文或项目材料压缩成一张图形摘要：问题背景、方法、关键结果、结论意义四类面板，配流程箭头、关键实体、迷你数据图、图例与一句话图注，并检查每个面板的主张是否有来源句、数字是否与数据表一致、阅读顺序是否唯一、版式约束是否可执行。

适用条件：`/input/case.json`（或 `--case`）含摘要、引言、方法、结果、结论段落，或面板/图表清单，要求生成图形摘要、信息图或单图概览。典型键：`panels`、`abstract`、`sections`、`key_results`、`tables`、`style`/`canvas`。

不适用：不替代论文正文，不做统计检验，不把没有来源的数字、倍数或结论写进图里；不伪造最终渲染图像。

## 输入

- `/input/case.json`：主输入。
- `/input/task.md`：存在时补充目标尺寸、颜色限制与读者定位。
- 引用的本地文本/JSON/CSV 会被实际读取；缺失登记到 `errors`/`uncertainty`。
- 仅标准库；不联网、不写 `/input`。

## 处理流程

1. **读入**：加载 case.json、task.md、引用文件，记录来源哈希。
2. **句子分类**：按线索词把句子分到 background / method / result / conclusion；保留原句作为证据。
3. **面板构建**：优先读显式 `panels`；缺失时按四类各取代表句，生成面板消息、视觉类型（示意/流程/迷你图）与证据引用。
4. **关键实体与流程**：抽取实体与动词关系，生成从左到右的流程箭头；无关系时不硬连。
5. **数据编码**：从表格抽取一条数值系列生成迷你图规格；数字与表格不一致时标出。
6. **图注与标题**：用一句话概括“做了什么、发现了什么、意味着什么”，不夸大。
7. **版式约束**：检查画布尺寸、分辨率、最小字号、颜色数、面板字数、阅读顺序、箭头完整性。
8. **导出计划**：登记格式与检查项，`performed=false`。
9. **契约落盘**：保留 `case_id` 与全部 `output_contract` 字段。

## 评分维度对应

- **实际效果**：四类信息完整、主张准确、数字与来源一致。
- **完整性**：面板、实体、流程、数据图、图例、图注、导出齐备。
- **可核验性**：每个面板与数字都能回到来源句或表格行。
- **创新增益**：主张—证据绑定、字数与颜色预算、阅读顺序检查。
- **可靠体验**：缺面板、无证据、数字冲突时显式降级。

## 输出契约

`/app/output/result.json` 顶层保留 `case_id`、`output_contract` 全部字段、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`、`skill`、`challenge_id`、`contract_notes`。未支持字段写 `null` 并记录原因。

主要交付段：`result.graphic_spec.panels`、`result.graphic_spec.flow`、`result.graphic_spec.legend`、`result.style_checks`、`result.caption`。

## 异常处理

| 情况 | 处理 | 输出表现 |
|---|---|---|
| case.json 缺失/非法 | 结构化失败 | 退出码 `2`，`status=insufficient_input`/`failed` |
| 缺少某类面板 | 不编造结论 | `panel_missing`，面板消息 `null` |
| 主张无来源句 | 保留但标未支撑 | `claim_without_evidence` |
| 数字与表格不一致 | 保留两值 | `number_mismatch`，不擅自改数 |
| 无实体关系 | 不画箭头 | `flow` 为空，`uncertainty` 说明 |
| 字数/颜色/字号超限 | 不静默压缩 | `style_violation` 列出项 |
| 无表格 | 不生成迷你图 | `mini_chart` 为 `null` |
| 渲染工具缺失 | 只给导出计划 | `export_plan[].performed=false` |

## 限制

句子分类基于线索词，可能把结果句误判为方法句；图形摘要的视觉隐喻与美学判断不在自动校验范围内。输出是结构规格，不是最终像素。

## 最小调用示例（示例，非官方案例结果）

```bash
python scripts/run_skill.py --case /input/case.json --out /app/output/result.json
```

示例输入（示意）：四段式摘要 + 一张两列小表。示例输出（示意）：`status="success"`，四个 panel 各带 `evidence_sentence`，迷你图来自表 1。仅为示例，不代表官方固定案例结果。
