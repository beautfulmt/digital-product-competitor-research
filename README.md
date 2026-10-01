# Digital Product Competitor Research

一个通用 Codex skill，用移动或桌面安装包、网页、截图等材料研究具体数字产品。支持单品全景、专题分析、横向比较和后续版本监控。默认交付留白清晰的单文件 HTML。

## 安装

从 Gitee 安装：

```bash
git clone https://gitee.com/beautifulmt/digital-product-competitor-research.git ~/.codex/skills/digital-product-competitor-research
```

也可从 GitHub 安装：

```bash
git clone https://github.com/beautfulmt/digital-product-competitor-research.git ~/.codex/skills/digital-product-competitor-research
```

## 使用

在 Codex 中调用 `$digital-product-competitor-research`，提供产品材料和想解决的问题。例如：

> 用 $digital-product-competitor-research 分析这两个 IPA，重点比较用户完成核心任务的路径和商业模式，生成一份 HTML 报告。

若暂时没有明确目的，skill 会做全景拆解。报告完成后也可以继续用自然语言要求增改章节、比较维度或视觉呈现。

本地安装包扫描使用 Python 3.10 或更新版本。`apktool`、`jadx`、`hdiutil`、`7z` 等工具按当前系统与包格式选用；缺少工具时会按可用材料继续分析并说明范围。

仓库只包含 skill、参考说明和脚本。安装包、账号信息、扫描过程文件与报告不放入仓库。
