# AI 短剧制作工作流

把剧本、实际配音和真实资产转成能执行、能接续、能交付的视频提示词。保留人物目的、直接回应、完整动作和最小有效返工；补齐网页执行与交付的衔接。

**版本：0.1.1，2026-10-22。** 这是工作流首版及辅助工具，不是自动生成软件。文档整理、程序测试与真实成片验证分别记录；本版新增方法尚待实片验证。

## 从哪里开始

- AI 执行入口：[AGENTS.md](AGENTS.md)。
- 制作顺序：[workflow.md](workflow.md)。
- 提示词写法：[prompt-writing.md](prompt-writing.md)。
- 网页执行、失败恢复与交付：[execution.md](execution.md)。
- GitHub 与 Notion 的分工：[evidence-policy.md](evidence-policy.md)。
- 项目启动与交付模板：[project.template.md](project.template.md)、[delivery.template.md](delivery.template.md)。模板由 AI 按已有材料填写，用户不填内部表。

首次使用只需给材料目录、目标集数及本次要求。有采用稿就从采用阶段接着做；制作页链接和旧授权不自动变成新一轮付费生成许可。

## 首版改进

1. 每段同时保留真实生成结果与实际取用顺序，区分素材齐、时间线齐和整集导出。
2. 提交前记录意图，点击不明只查历史；技术失败解决后保留旧失败并正常续提。
3. 原生、延长新增部分及包含源片的结果分别计时，避免重复算长或漏用延长素材。
4. 按阶段读取规则，导演、声音、资产和视觉四项职责可由一人承担，复杂任务再按环境权限分工。

保留按实际配音定位、必要切点最小余量、按项目决定镜头密度和快速视觉检查。新增工具不规定总素材时长公式，也不点击网页、上传音频或扣费。

## 验证数据仍在 Notion

[现有笔记入口](https://app.notion.com/p/3e0c10df3d5f816dbbc5c25514491648)与[错误修正库](https://app.notion.com/p/24f2c2d1a12647af888e64e08a2609c8)继续保存案例、失败证据、费用、采用与撤回状态。链接沿用原权限，本仓库不更改分享设置。

GitHub 保存通用规则、模板、工具和版本变更。Notion 的通用笔记作为来源与历史保留；本轮用户要求和当前项目采用决定优先，旧文档不自动覆盖当前决定。详见 [evidence-policy.md](evidence-policy.md)。

## 辅助工具

Python 3.10+，仅标准库，无 API 密钥。

```sh
python -m unittest discover -p "test_*.py" -v
python check_repository.py
python receipts.py --help
```

`receipts.py` 是本地回执记录辅助工具；运行文件放在项目材料目录或本仓库忽略的 `runtime/`。示例中的虚构输入见 [receipts.md](receipts.md)。`repository-files.txt` 是上传清单，提交前按清单审查文件。
