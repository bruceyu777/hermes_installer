---
marp: true
theme: default
paginate: true
title: QA 虚拟机上的 AI 编程助手
---

# QA 虚拟机上的 AI 编程助手

**opencode** 与 **Hermes Agent**，一个脚本装好

- 使用公司内部模型，自动回退
- 内置 Jenkins、Mantis 和 Log Intelligence
- 终端和 VS Code 都能用（包括 Remote-SSH）

FortiOS QA · 2026-09-23

---

## 为什么要做这个

手工配置一个 AI 助手要花一个下午：

- 用哪个网关、哪个模型、哪个密钥？
- 模型预算用完或下线了怎么办？
- 怎么接入 Jenkins、Mantis、Log Intelligence？
- 哪些工具可以放心让它用？

现在只需**克隆、填入自己的 token、运行一个脚本**，每个人得到的都是同一套经过测试的配置。

---

## 两个助手，同一套配置

| | **opencode** | **Hermes Agent** |
|---|---|---|
| 是什么 | 带终端界面的编程助手 | 通用助手：终端、对话、技能、记忆 |
| 在 VS Code 中 | `sst-dev.opencode` 扩展（Ctrl+Escape） | ACP Client 扩展 → "Hermes Agent" |
| 仓库 | `bruceyu777/opencode_installer` | `bruceyu777/hermes_installer` |
| 模型、工具、防护 | 相同 | 相同 |

选一个用，或两个都用。**一个 `tokens.env` 文件两边通用。**

---

## 快速开始（约 5 分钟）

```bash
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode
cd ~/git/opencode
cp tokens.env.example tokens.env && chmod 600 tokens.env
vi tokens.env             # 填入自己的 token
./install.sh --cron

# Hermes：同样的步骤，复用同一个 token 文件
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes
cd ~/git/hermes && cp ~/git/opencode/tokens.env . && ./install.sh --cron
```

仓库是私有的：请先申请协作者权限。
以后更新：`git pull && ./install.sh`，已有 token 会保留。

---

## 需要哪些 token

填写你有的，其余留空。

| Token | 用途 | 留空时 |
|---|---|---|
| `FOS_AI_API_KEY` | 你的个人 fos-ai 模型密钥 | 模型链从下一个密钥开始 |
| `FOS_AI_FALLBACK_API_KEY` | 第二个 fos-ai 密钥（例如项目 token） | 复用个人密钥 |
| `LOCAL_QWEN_API_KEY` | 内部 Qwen 服务器（最后的回退） | 没有本地模型 |
| `MANTIS_MCP_TOKEN` | Mantis 工具 | 关闭 Mantis |
| `JENKINS_MCP_TOKEN` | Jenkins 工具（`user:apitoken`） | 关闭 Jenkins |
| `LOGINTEL_MCP_KEY` | Log Intelligence 工具（`limcp_…`） | 关闭 Log Intelligence |

token 文件永远不会提交，token 也不会出现在配置文件里。

---

## 模型：是一条链，不是单个模型

```
个人密钥            ──►  第二个 fos-ai 密钥  ──►  内部 Qwen         ──►  更大的 Qwen
glm-5.3-flash            glm-5.3                 qwen3.6-35b         qwen3.5-122b
```

- 某个模型失败时（被拒绝、预算用完、服务器错误），**同一条消息会发给下一个模型**
- 会弹出提示告诉你由哪个模型接手
- 失败的模型会被跳过 30 分钟，之后再重试

---

## 模型链是测量出来的，不是写死的

**2026-09-23 发生的事：** 几小时之内，网关

- 不再允许个人密钥使用 `glm-5.3`
- 不再允许任何密钥使用 `deepseek-v4.1-flash`，尽管它还在模型列表中

如果模型链是固定的，每次对话都会悄悄回退到本地 Qwen 服务器。

**现在的做法**（与 Log Intelligence 模型监测规则相同）：

- 每小时用一个真实的小请求测试每个首选模型
- 只使用有应答的模型，按首选顺序排列
- 网关新出现的模型只报告；在有人批准之前不使用
- 网关连不上时，保留当前的模型链

---

## 助手能用的工具

与 **Log Intelligence AI Assistant** 相同的一套：

| 服务器 | 例子 | 工具数 |
|---|---|---|
| Log Intelligence | 失败用例、构建健康度、QAID 历史、构建对比、分诊状态 | 26（全部只读） |
| Mantis | 搜索 bug、语义搜索、查询知识库、准备 / 提交 bug | 8 |
| Jenkins | 任务和构建状态、构建日志、测试结果、触发 / 重跑构建 | 10 |

不在这个列表中的工具对助手隐藏。
Microsoft 365 为可选，尚未测试。

---

## 防护：改动任何东西之前都会先问你

以下操作一定要你同意：

- 提交 Mantis bug · 添加 Mantis 备注 · 发送邮件
- 启动或重跑 Jenkins 构建

| 在哪里 | 会发生什么 |
|---|---|
| 终端 | 弹出提示：允许一次 / 本会话内允许 / 拒绝 |
| VS Code | 弹出权限对话框 |
| 单次运行或定时运行（无人应答） | **直接拦截** |

其余操作（搜索、日志、报告）不需要询问。

---

## 提示词示例

- *"最新的 FortiOS 7.6 构建中哪些用例失败了？按根因分组。"*
- *"QAID 123456 是新的回归吗？给出它的历史。"*
- *"在 Mantis 中搜索和这段崩溃日志相似的 bug，然后起草一个新 bug。"*（提交前会询问）
- *"获取任务 X #1234 构建日志的最后 200 行，解释失败原因。"*
- *"对比构建 3510 和 3498：有什么新问题？"*

---

## 在 VS Code 中使用

**opencode**
- 打开一个项目文件夹 → **Ctrl+Escape**

**Hermes**
- **Developer: Reload Window** → **ACP: Connect to Agent** → *Hermes Agent*
- 修改配置后：**ACP: Restart Agent**

提示：在项目文件夹中启动，不要在主目录中启动。

---

## 了解限制

- **防护不是安全边界。** 它控制的是助手的*工具*。助手仍以你的身份执行 shell 命令，也能读取你的 token。在我们的虚拟机上，有一次助手就用 token 文件自己写了一个 Mantis 脚本。请留意它运行的命令。
- **"Allow always"** 是永久的。建议选"允许一次"或"本会话内允许"。
- **关闭审批**（`--yolo`）也会关闭这些提示。
- **回答可能有错。** 提交或发送之前请先检查。

---

## 出现问题时

| 看到 | 处理 |
|---|---|
| `refused … does not permit model` | 无需处理：模型链会继续往下走，每小时的检查会选出可用的模型 |
| `429 … budget` | 无需处理：该密钥当前时段预算已用完 |
| 某个 MCP 服务器显示失败 | 检查 `tokens.env` 中对应的 token，运行 `./install.sh` |
| VS Code 中 opencode 终端 2 秒后关闭 | `install.sh` 已修复（Python 终端自动激活） |
| 配置修改没有生效 | 重启助手（VS Code：重新加载 / Restart Agent） |

随时做健康检查：`./install.sh --check`

---

## 内部实现（给感兴趣的同事）

| 组件 | opencode | Hermes |
|---|---|---|
| 模型偏好 | `config/model-preferences.json` | `config/model-preferences.yaml` |
| 每小时重新检查 | `install.sh --adapt`（cron 在 :47） | `install.sh --adapt`（cron 在 :17） |
| 回退 | `chain-fallback.js` 插件 | 内置 `fallback_providers` |
| 先询问规则 | 按工具的 `permission` 规则 | `mcp-ask-first` 插件 |
| 固定版本 | opencode 1.18.32 | Hermes v0.21.4 |

想加入新模型？把它加到偏好列表里，然后运行 `./install.sh --adapt`。

---

## 今天就开始

1. 申请这两个仓库的访问权限
2. 准备好你的 token（见"token"那一页的表格）
3. `./install.sh --cron`
4. 在你最新的构建上试一个示例提示词

欢迎提问和提建议，特别是你希望助手接下来能用哪些工具。
