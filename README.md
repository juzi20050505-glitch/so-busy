# AI 短剧制作工作流

把剧本、实际配音和真实资产转成能执行、能接续、能交付的视频提示词。保留人物目的、直接回应、完整动作和最小有效返工；补齐网页执行与交付的衔接。

**当前本地版本为 0.2.0（2026-11-18）。** 制作由一个主控衔接导演稿、网页和交付。代码负责核对正式稿、汇总跨集进度和记录实际结果。新版已做离线验证，真实剧梦卡片和成片效果待实测。

## 从哪里开始

- AI 执行入口：[AGENTS.md](AGENTS.md)。
- 制作顺序：[workflow.md](workflow.md)。
- 提示词写法：[prompt-writing.md](prompt-writing.md)。
- 网页执行、失败恢复与交付：[execution.md](execution.md)。
- 多集开工、断点续做与工具调用：[批次操作](batch-run.md)。
- GitHub 与 Notion 的分工：[evidence-policy.md](evidence-policy.md)。
- 项目启动与交付模板：[project.template.md](project.template.md)、[delivery.template.md](delivery.template.md)。模板由 AI 按已有材料填写，用户不填内部表。

首次使用只需给材料目录、目标集数及本次要求。有采用稿就从采用阶段接着做；制作页链接和旧授权不自动变成新一轮付费生成许可。

## 开始制作

在 Codex 打开本目录，给出剧名、集数、材料目录和执行范围。网页任务附上剧梦项目入口。比如“按本目录工作流做第 1 至 3 集，材料在……，这次只填稿，我自己生成”。主控从已有材料整理执行包和批次清单。

只有提示词任务时，直接交完整正文和真实资产索引。多集网页任务按已就绪的段推进。后续说“继续原批次”时，主控从每集原回执恢复，优先核查结果未知的原卡。

## 验证数据仍在 Notion

[现有笔记入口](https://app.notion.com/p/3e0c10df3d5f816dbbc5c25514491648)与[错误修正库](https://app.notion.com/p/24f2c2d1a12647af888e64e08a2609c8)继续保存案例、失败证据、费用、采用与撤回状态。链接沿用原权限，本仓库不更改分享设置。

GitHub 保存通用规则、模板、工具和版本变更。Notion 的通用笔记作为来源与历史保留；本轮用户要求和当前项目采用决定优先，旧文档不自动覆盖当前决定。详见 [evidence-policy.md](evidence-policy.md)。

## 辅助工具

本地命令使用 Python 3.10+ 标准库。网页回读模块供带 Node.js 和 Playwright 标签接口的浏览器宿主使用。测试使用虚构材料。

```sh
python -m unittest discover -p "test_*.py" -v
python check_repository.py
node --test test_web_readback.mjs
```

`receipts.py` 是本地回执记录辅助工具；运行文件放在项目材料目录或本仓库忽略的 `runtime/`。示例中的虚构输入见 [receipts.md](receipts.md)。`repository-files.txt` 是上传清单，提交前按清单审查文件。
