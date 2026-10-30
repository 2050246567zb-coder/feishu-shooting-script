# 安装依赖与飞书授权

这个 Skill 的脚本只使用 Python 3 标准库，飞书操作使用 `lark-cli`，不用作者账号、不内置 App Secret 或访问令牌。Windows、macOS 和 Linux 使用同一组 Python 脚本。

## 已配置飞书

先检查：

```bash
lark-cli --version
lark-cli auth status --json --verify
```

必须确认 `identities.user.available` 为 true、`identities.user.status` 为 ready。`identity=bot` 或顶层 `verified=true` 本身不能证明个人用户授权有效。

本 Skill 的 CLI 适配基于本地核实的 lark-cli 1.0.90 命令形状。首次使用先查看 `base +base-create --help`、`base +base-copy --help`、`base +record-batch-create --help`，确认当前版本提供相应功能；不强制降级或静默更新用户配置。

## 未配置飞书

若有 `lark-shared` Skill，优先由其当前版本引导配置、范围授权和二维码流程。没有也可先检查当前 `lark-cli config init --help` 和 `lark-cli auth login --help` 后按 CLI 当前向导完成。CLI 的 npm 包名为 `@larksuite/cli`；安装或配置仍要遵守当前用户的授权与环境约定。

已有应用时复用，不擅自覆盖或新建第二个应用。应用密钥应通过 CLI 的 stdin 机制或用户自己完成的安全交互配置，不能放在聊天、命令参数、Skill 文件或安装包中。

本流程会按实际路径使用以下权限，不需要 IM 或邮箱权限：

- 新建：`base:app:create base:app:read base:table:create base:table:read base:table:delete base:field:read base:view:read base:view:write_only base:record:create base:record:read`。
- 复制用户模板：额外 `base:app:copy`。复制结构后补充地点选项时需要 `base:field:update`。
- 附参考图：额外 `docs:document.media:upload`，并以当前 CLI 返回的缺失 scope 为准增量补齐。
- 通常还需 `auth:user.id:read offline_access` 以维持用户身份；按当前 CLI 认证机制处理。

`base:table:delete` 用于 CLI 自定义首表创建流程清理新 Base 自动附带的空表，不是删除用户已有项目的许可。后台应用权限与用户授权都需要满足；缺 scope 时按原用户身份恢复，不自动改用 bot。

## 授权失效

需要重新登录时，用所需 scope 发起非阻塞授权：

```bash
lark-cli auth login --scope "<本次实际需要的权限>" --no-wait --json
lark-cli auth qrcode "<刚返回的verification_url>" --output "work/feishu-auth.png"
```

把原样授权链接和二维码一起展示，链接在前；交还控制权，让用户完成授权并回来告知。收到“已授权”后，Codex 再执行 `lark-cli auth login --device-code <本次device_code>` 完成认证。不要在首次展示链接的同一轮立刻阻塞轮询，不复用过期链接，也不把 device code 写进 Skill 或分发包。
