# 单文件 HTML 内容结构

默认使用 `scripts/report.py render content.json state.json output.html`。只为当前研究有内容的模块写 `sections`，脚本不会产生空模块。页面采用米白纸张、深色文字、单一强调色、宽留白和明确的字号层级；适配桌面和手机，支持目录跳转与打印。若用户有视觉参考，可调整模板 CSS。

全景和横向报告应有详细功能正文，按 [功能模块详细拆解](feature-breakdown.md) 展开。可以让每个产品或重要模块各占一个 `section`，在其 `blocks` 中组合子功能表、主要流程、规则状态说明和产品判断。摘要只提炼重点，功能正文保留具体用法。

子功能表可采用“子功能、用户怎么用、产出结果、关键规则”四列；复杂流程再用 `flow` 展开，避免把全部细节挤进一张超宽表。共享规则集中写，模块差异单独展开。

`content.json` 示例：

```json
{
  "title": "一款产品的竞品拆解",
  "subtitle": "让读者一眼看懂产品如何解决核心问题",
  "eyebrow": "产品研究 · 2026-09-29",
  "meta": [{"label": "平台", "value": "iOS / Web"}, {"label": "版本", "value": "1.0"}],
  "lead": "一句话说明最重要的发现及其适用条件。",
  "takeaways": [
    {"title": "关键发现", "body": "用直白的话说明用户价值和产品机制。"}
  ],
  "sections": [
    {
      "id": "journey",
      "label": "核心路径",
      "title": "用户如何完成任务",
      "intro": "简要交代场景。",
      "blocks": [
        {"type": "flow", "steps": [{"title": "进入", "body": "入口和触发条件"}, {"title": "完成", "body": "结果反馈和下一动作"}]},
        {"type": "visuals", "items": [{"src": "data:image/png;base64,...", "alt": "产品图标", "caption": "用于辨别同名产品"}]},
        {"type": "cards", "items": [{"title": "机制", "body": "为什么这样设计"}]},
        {"type": "text", "title": "判断", "body": "事实、解释和适用条件。"},
        {"type": "table", "columns": ["维度", "旧版", "新版"], "rows": [["入口", "导航页", "首页"]]}
      ]
    }
  ],
  "footnote": "必要的研究范围说明，控制在两三句话。"
}
```

报告正文不展示材料出处、技术文件路径和核查过程。需要帮助读者理解产品的图片时，将选定图片以 data URL 内嵌到 HTML，避免另交图片目录。

`visuals` 仅接收完整的 PNG、JPEG、WebP data URL。只有图片帮助区分产品或解释用户路径时才使用；报告要保持留白，不堆素材。

`state.json` 是工作数据，不直接展示。由 `report.py init-state inventory.json state.json` 初始化后，补全 `modules` 与 `features`。模块记录稳定的 `id`、`name`；子功能记录稳定的 `id`、`name`、`status` 和所属 `module_id`。建议的状态值：`observed`、`implemented`、`candidate`。流程、规则、状态和权益等按 [快照记录](feature-breakdown.md#快照记录) 保存。旧报告缺少模块地图时，先按已有功能与文件比较，并建立新版模块基线。

横向报告中，每个产品保持独立的 `state.json`，用 `report.py merge-states merged.json product-a.json product-b.json` 合并后交给 `render`。功能 `id` 应按稳定的用户能力命名，不随资源文件名变化。`diff` 接受包含多个产品的旧 HTML，并从中匹配新版产品的 `id` 与平台。

只有网页、截图等材料时，可以手工建立同结构的 `state.json`：`schema_version: 1`，`product` 至少含稳定 `id`、`name`、`platform`、`version`，`manifest` 为空数组，`features` 记录已确认的能力。网页产品的 `id` 可用规范化主域名。这样后续仍能比较两次观察结果。

内容筛选规则：摘要保留最重要的 3 到 5 条；长表只用于读者需要逐项比较的维度；技术细节只在会改变产品判断时出现。语言要简单，不用“赋能”“闭环”“抓手”等词替代具体机制。
