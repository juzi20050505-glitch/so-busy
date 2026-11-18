# 本地回执辅助工具

`receipts.py` 只记录状态，不连接浏览器、下载视频、操作平台或扣费。每集沿一份真实本地记录恢复；不能复制数据库、换任务 ID 或重新初始化来绕过已有提交。数据库和输入 JSON 不提交 GitHub。

使用 Python 3.10+。执行者从真实页面生成内部事件文件，不让用户手工填 JSON。命令：

```sh
python receipts.py --record runtime/episode.sqlite --event runtime/event.json
python receipts.py --record runtime/episode.sqlite
```

不带事件只读状态，永远不补发点击许可。写事件成功提交后才输出本次 `click_once`；拿到该输出后，执行者在刚核准的页面正常点击一次。不跨回合缓存许可。输出丢失或点击结果不明时先查原历史。

## 事件

每个输入为 `{"op":"操作名","data":{...}}`。下列字段由执行者按已知事实填，不是用户作业表。

| op | data | 作用 |
| --- | --- | --- |
| init | scope、authorization | 首次创建；拒绝重置旧记录 |
| authorize | mode、quote、source | 记录已有明确授权；不自动扩集或预算 |
| register | id、spec、package | 登记已核真实卡、采用正文与执行包 |
| amend | id、spec、package、evidence | 原始段尚未提交时重新采用，保留改动记录并清除旧保存状态 |
| confirm_prepared | id、scope、visible、evidence | 核对保存后回读的完整规格，登记已填稿 |
| intent | id、scope、visible、no_active_same_task=true、evidence | visible 必须与采用 spec 完全相同；持久化后仅本次返回一次点击标记 |
| observe | id、state、evidence；完成加 result_id，失败加 cause | 回读真实处理中／未知／完成／失败，不提交 |
| archive | id、result_id、path、evidence，可选 sha256 | 登记该完成结果的本地下载文件，计算字节数和指纹，相同回执可重复核对 |
| retry | id、new_id、condition_evidence、no_active_same_task=true | 明确技术失败且条件已恢复，保留原失败后建立同规格新尝试 |
| pause / resume | evidence | 记录用户决定；恢复不清除未决意图 |

`scope` 为 project、episode、target_url，均来自本次实际目标。authorization.mode 为 prepare 或 generate；generate 需要已有直接用户授权的 quote 和 source，工具无法判断其真实性，执行者必须自行核实。

`spec` 必须有 card、prompt_sha256、references、mode、seconds、model、resolution。references 为实际绑定的 `{name,node}` 列表，不能用资产库存在代替上传；无需参考时可为空。其余当前影响执行的字段如画幅也应放入 spec，intent 同样完整比较。

extend 另需 source：`{task,result_id,start,end,qualified:true,evidence}`。本工具只支持核过的原生结果为源；平台能力、最大连续长度、实际末态与源媒体真实性由执行者另核，不硬编码为所有平台通则。

0.2.0 新建的单集账本采用格式 2。用 `--package 执行包.json` 或在事件 `data.package` 附完整执行包。登记、确认已保存和生成意图均会重读采用稿，核其字节指纹、秒数、生成方式和有序素材名。技术重试从原任务读取同一个包。旧账本中已登记的无包任务可沿原卡和原授权恢复；新增或未提交原始任务也可以绑定执行包。

本地归档要求 `path` 指向已下载且归属经现场核实的文件。同一结果的已登记指纹如与现有字节冲突，先核实际下载。批次汇总读取登记时的指纹，并轻查路径与大小；需复核字节时再次提交原 `archive` 事件。

## 虚构初始化示例

```json
{
  "op": "init",
  "data": {
    "scope": {"project": "example", "episode": "01", "target_url": "https://example.invalid/project"},
    "authorization": {"mode": "prepare"}
  }
}
```

测试里的标识和 URL 均为虚构。真实授权、卡片、收费及原回执写本地记录，并将长期采用与验证事实更新到原 Notion 项目档。

## 适用边界

SQLite 事务保证并发 intent 不会同时拿到该记录的许可，写入失败不输出许可。它不能防止执行者绕过工具直接点击、重复建库或填写假事实；不是平台幂等 API，也不提供全局扣费上限。

retry 只支持已知 balance、access、platform 技术失败的同规格恢复；未知／处理中／prompt／model 不适用。每次仍须确认条件已解决、原卡无迟到受理、原授权和预算覆盖；同一问题未解决不能循环重试。创作修正另按原项目采用记录处理，保留完整尝试谱系。

本地记录在运行期间是防重复操作的必要事实，不成为第二份长期验证库。持久验证、费用与用户反馈继续留 Notion；清理运行文件前先结束活动任务并保存必要采用定位。
