# 批次操作

主控按本次范围使用下列入口。示例命令以工作流目录为当前目录，示例文件由主控根据实际材料整理。单集回执、真实 URL、正文和视频放在材料目录下。只出提示词时直接交稿。

## 1. 从已核的声音和正式稿开始

有完整原配音时，优先沿用本项目已核的定位。已有 Core 本地音频定位器时，用其现有环境执行 `localize.py --audio 原轨 --script 句表 --out 输出目录`，或直接用该定位器保存的 `result.json`。传入当次定位使用的原音频与原句表。其他来源的时码仍可按原制作流程采用。

```sh
python audio_windows.py runtime/audio/result.json --audio runtime/EP01.wav --script runtime/units.json --range 12 21 --insert 16 1
```

这个例子取原轨第 12 到 21 秒，在原轨第 16 秒的位置安排 1 秒额外画面，得到 10 秒段内计划。换稿或换音轨时工具会校验原文件指纹。检查原文、跨段句、未定位句和估计边界，再决定实际切点。最后采用的剪辑位置写入交付清单。

一段采用后将送入模型的完整正文保存为独立文本。原轨对应表、后期说明另存。按该卡素材在正文中的实际绑定顺序列引用，延长段的秒数填本次新增秒数。

```sh
python prompt_package.py runtime/EP01_S01.txt --mode native --seconds 12 --ref 女主日常装 --ref 客厅日景 --out runtime/EP01_S01.package.json
```

正式稿指纹按文件原始字节计算。重放同一版本可用原包；采用新稿时保留旧正文和包，另存新版本。工具核对可机械判断的格式、指纹与声明字段。动作、台词归属、素材是否适配仍按导演稿核对。

## 2. 多集共享一份清单

主控在材料目录中维护一份 UTF-8 `batch.json`。按实际集段逐批追加，路径相对于清单文件。每个分集指向自己的原回执，`target_url` 取该集实际页面。可在正式稿采用后填写该段的 `package`。只拿到计划、尚无源片的延长先记同集原生根任务 ID。

```json
{
  "project": "示例剧",
  "episodes": [
    {"episode": "01", "record": "EP01.sqlite", "tasks": [
      {"id": "S01", "package": "EP01_S01.package.json"},
      {"id": "S02", "source": "S01"}
    ]},
    {"episode": "02", "record": "EP02.sqlite", "tasks": [{"id": "S01"}]}
  ]
}
```

```sh
python batch.py --manifest runtime/batch.json
```

独立原生稿就绪后即可依次准备或提交；平台已接单可继续下一张。延长等指定原生结果可用，再核实际区间和末态。准备范围内，保存并回读后记 `confirm_prepared`。仅获准备授权时，批次显示 `saved_for_user` 即可交用户生成。遇到 `reconcile_original_card`，先回该卡查队列与历史；遇到 `check_download`，沿已完成结果检查下载。首次确实没有账本时根据当前集和授权初始化。恢复旧任务时要先找到原账本。

## 3. 在当前卡核正文和真实引用

有 Node.js 与 Playwright 标签接口的宿主可加载 [web_readback.mjs](web_readback.mjs)。先用已采用的目标与正式稿生成期望值，再读取实际页面。下面的 `pkg` 是执行包，`promptBytes` 是该包所指正文的原文件字节，`tab` 是主控当前操作的实际标签。

```js
const expected = expectedFromPackage(pkg, promptBytes, {
  url: adoptedUrl, card_index: adoptedCardIndex, model: adoptedModel,
  resolution: adoptedResolution, aspect_ratio: adoptedAspectRatio
});
const actual = await readJumengCard(tab);
const report = compareJumengCard(actual, expected);
```

三个函数均为该模块的具名导出。常规编号标记从正文提取，其他格式用 `markers` 提供实际在稿中对应的文字。同名图片要核实际库条目与缩略图。正式稿标记和文件名仍需对应到编辑器中的真实引用节点。

`mismatch` 时先修该卡再回读。`needs_review` 时由主控核报告列出的字段。延长源结果和区间、可见报价、当前队列均由主控在原页面核实。宿主无法加载模块或页面改版时，沿[同页核对](execution.md)完成相同的现场检查。保存证据取自实际页面。

## 4. 登记、提交、回收

新单集初始化沿用 [receipts.md](receipts.md) 的 `init`。已核实际卡和引用后，`register` 事件里的 `spec` 记录真实卡标识、已核正文 SHA、节点、生成方式、时长和参数。命令直接读取执行包。

```sh
python receipts.py --record runtime/EP01.sqlite --event runtime/register.json --package runtime/EP01_S01.package.json
```

只填稿时正常保存并回读当前卡，用 `confirm_prepared` 登记证据。授权生成时，同页核对后用 `intent`，拿到本次 `click_once` 再实际提交。中断后回查，不凭重复运行命令重提。若原始任务尚未提交、正式稿或卡片有修正，用 `amend` 记录新规格、执行包和改动依据，再次保存回读。已经提交的尝试沿原记录查结果和返修。

实际结果已完成且归属核实后，用 `observe` 记平台结果 ID。下载完成再用 `archive` 记本地文件，工具从磁盘计算大小和 SHA-256。最终采用哪个结果、源片重叠、取用区间与整集组装状态，填入[交付清单](delivery.template.md)。
