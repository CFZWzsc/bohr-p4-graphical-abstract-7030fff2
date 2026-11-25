# Graphical Abstract Skill

把论文摘要与分节文本压缩成四块图形式图形摘要（背景 / 方法 / 结果 / 结论），
同时导出 300 dpi 的 `figure.png` 与 `figure.pdf`。Python 标准库实现，离线、确定性。

## 使用

```bash
python scripts/run_skill.py --case examples/demo-case/case.json \
    --out /app/output/result.json --work /app/work
```

- 输入：`case.json`（`case_id`、`output_contract`、材料路径）、`task.md`、摘要/正文（可含 CSV 结果表）。
- 输出：`result.json`（契约键 `case_id`/`status`/`editable_source`/`export_artifact`/
  `content_map`/`compliance_checks`/`exceptions`）、`figure.svg`、`figure-spec.json`、
  `figure.png`、`figure.pdf`。

## 设计要点

- **四块结构**：面板角色固定为 background/method/result/conclusion，每块绑定来源句子
  （`content_map` 记录 panel → 句子），箭头只表达声明的阅读顺序，不虚构因果。
- **不编造**：面板文字取原文句子，数值只来自结果表；缺失的面板写 `uncertainty` 而不是补写。
- **可编辑**：`layer-panels` / `layer-arrows` / `legend` 分层，文字全部 `<text>`。
- **可核验**：`compliance_checks` 覆盖面板锚点、箭头标记、图例、画布包含、PNG 非空白像素。
- **确定性**：单遍布局，无随机数与时间戳，双跑逐字节一致。

## 验证

```bash
python scripts/run_skill.py --case examples/demo-case/case.json --out /tmp/r1/result.json --work /tmp/r1
python scripts/run_skill.py --case examples/demo-case/case.json --out /tmp/r2/result.json --work /tmp/r2
# 两次产物 sha256 应完全一致
```
