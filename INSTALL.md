# 飞书拍摄协作脚本 Skill

安装后，在 Codex 里发送 Word、文字或 Markdown 脚本，就可以把脚本整理为飞书拍摄协作多维表格。首次使用应明确说“把这份脚本整理成飞书拍摄协作表”，之后同一上下文中可直接继续发送脚本。

默认每份脚本独立建表，也支持先复制你自己指定的模板。七列为：镜号、台词／文案、镜头描述、画面参考、拍摄地点、拍摄备注、已拍摄。包含完整脚本、按地点拍摄、待拍镜头三个视图。

## 安装

解压 ZIP，保留完整的 `feishu-shooting-script` 文件夹。可把文件夹交给 Codex，并说：

> 帮我安装这个 Skill，先阅读其中的 INSTALL.md。

也可在终端运行随包安装脚本：

```bash
python /实际路径/feishu-shooting-script/scripts/install_skill.py
```

Windows 示例：

```powershell
python ".\feishu-shooting-script\scripts\install_skill.py"
```

脚本复制到 `$CODEX_HOME/skills/feishu-shooting-script`；没有设置 `CODEX_HOME` 时使用 `~/.codex/skills/feishu-shooting-script`。如果没有终端中的 Python，可以让 Codex 使用其环境中可用的 Python 解释器，或者直接复制该文件夹到对应目录。

已有同名 Skill 时安装器不会直接覆盖。需要更新可加 `--replace`，原版本会备份到 skills 目录旁的 `skill-backups`。安装完成后新开一个 Codex 聊天；如果列表尚未刷新，再重启 Codex。

## 配置飞书

需要 Python 3 和已配置的 `lark-cli`。这个 Skill 只用 Python 标准库，不需要额外 pip 包。飞书 CLI 的 npm 包名是 `@larksuite/cli`，按它当前版本的向导配置你自己的应用与个人账号；不要把 App Secret 发进聊天。

已经配置好飞书的人只需让 Codex 检查个人授权是否有效。没有配置的人可以说：

> 我安装了飞书拍摄协作脚本 Skill，请先检查飞书 CLI，按当前向导帮我完成配置和个人账号授权。

具体权限和授权流程在 [references/setup.md](references/setup.md)。不需要作者的账号、表格权限或私人脚本；额外的 `lark-base` / `lark-shared` Skill 不是运行前提，安装了则优先使用它们的当前说明。

## 使用

发送脚本文件，并说：

> 用 $feishu-shooting-script 把这份脚本整理到飞书，命名为“某项目拍摄协作脚本”。

要沿用你已有的布局，可以同时给出自己的模板链接：

> 复制这个模板后导入脚本，原模板保留。

导入中断后回到同一聊天说“继续导入”，保留任务目录，以便核对已有镜头后续传。需要修改现有表时直接说清楚目标表和改动，Codex 会保留团队已经填写的参考图、地点、备注和拍摄状态。

脚本中的口播与画面按原文对应；地点不明确则标“待定”。有逐镜头参考图时作为附件导入，没有图就留空。只有口播时不会擅自编造镜头描述。默认不生成图片或视频。

## 验证范围

随包包含本地离线自测：

```bash
python /实际路径/feishu-shooting-script/scripts/self_test.py
```

这些测试使用模拟飞书接口，覆盖七列与三视图、分批导入、中断续传、重复防护、保留现场修改及参考图附件。离线测试不代表你的账号已授权或线上接口一定可用；每次实际导入还会读回线上记录核对。

列宽不是当前 CLI 适配可直接设置的属性。已有模板可优先继承布局；有可用浏览器控制时 Codex 会检查并调整镜号列，目标约120px。浏览器不可操作时，会明确告知列宽尚未调整，已验收的数据和视图仍可使用。
