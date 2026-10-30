# 飞书拍摄协作脚本 Skill

把视频脚本交给 Codex，整理成可供团队使用的飞书拍摄协作多维表格。支持 Word、文字和 Markdown 脚本。

一句台词与对应镜头放在同一行，保留原文，方便团队补参考图、按地点拍摄和勾选进度。

| 镜号 | 台词／文案 | 镜头描述 | 画面参考 | 拍摄地点 | 拍摄备注 | 已拍摄 |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | 我做了一个桌面装置。 | 在工作室把装置放到桌上。 | 图片附件 | 工作室 |  | ☐ |
| 002 | 碰一下，它就有反应。 | 手指触碰，拍清完整动作。 | 图片附件 | 待定 | 留出反应时间 | ☐ |

同一份数据包含三个视图：

- **完整脚本**：按镜号查看。
- **按地点拍摄**：按场地分组，再按镜号排列。
- **待拍镜头**：按地点集中显示尚未勾选的镜头。

## 安装

下载 [最新安装包](https://github.com/2050246567zb-coder/feishu-shooting-script/releases/latest)，解压后把整个文件夹交给 Codex：

> 帮我安装这个 Skill，先阅读其中的 INSTALL.md。

也可以克隆并运行安装脚本：

```bash
git clone https://github.com/2050246567zb-coder/feishu-shooting-script.git
python feishu-shooting-script/scripts/install_skill.py
```

安装到 `$CODEX_HOME/skills/feishu-shooting-script`；没有设置 `CODEX_HOME` 时使用 `~/.codex/skills/feishu-shooting-script`。安装后新开一个 Codex 聊天。

需要 Python 3 和已经配置个人账号的 `lark-cli`。飞书 CLI 的 npm 包名是 `@larksuite/cli`，具体配置和授权见 [安装说明](INSTALL.md) 与 [飞书依赖说明](references/setup.md)。本 Skill 不绑定作者账号或作者的飞书表格，不包含授权凭据。

## 使用

发送脚本文件，并告诉 Codex：

> 使用 $feishu-shooting-script，把这份脚本整理成飞书拍摄协作表，命名为“某项目拍摄协作脚本”。

默认每份脚本独立建表。也可给出自己的模板链接，要求先复制模板结构后导入。首次明确使用这个流程后，同一聊天中可以继续直接发送脚本。

- 保留台词、画面及镜头顺序；只有口播时不会擅自编造画面描述。
- 地点依据原文或用户指定，不能确定则标为“待定”。
- 逐镜头参考图作为附件导入，没有图就留空。
- 支持分批导入、断点续传和写入后核对，避免重复镜头。
- 同一任务续传时保留团队已经补充的参考图、地点、备注和已拍状态。

## 验证与边界

```bash
python scripts/self_test.py
```

19项离线测试覆盖建表配置、批量导入、中断恢复、去重、现场修改保留、附件匹配及安装备份。发布前还用85镜头 Word 做过提取回归，并完成当前飞书 CLI 的只读适配检查。

离线测试不代替用户账号的授权与实际线上导入验收。每次导入会读回记录核对。镜号列宽目标约120px，需要可用的浏览器控制或继承已有模板布局；当前 CLI 适配不会虚报已调整列宽。

## 文件

- [SKILL.md](SKILL.md)：Codex 的任务入口与完整流程。
- [assets/template.json](assets/template.json)：七列和三个视图的可复用定义。
- [references/normalize.md](references/normalize.md)：脚本对应关系与地点整理规则。
- [scripts/extract_docx.py](scripts/extract_docx.py)：读取 Word 的段落、分镜表、合并关系与内嵌图片。
- [scripts/feishu_shots.py](scripts/feishu_shots.py)：校验、创建／复制、导入、续传和读回核对。
