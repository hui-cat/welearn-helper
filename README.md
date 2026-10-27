# WeLearn Helper

**WE Learn (SFLEP) 自动刷课 / 刷时长工具** —— 适配 2026 新 SSO 登录

[![Build](https://github.com/hui-cat/welearn-helper/actions/workflows/release.yml/badge.svg)](https://github.com/hui-cat/welearn-helper/actions/workflows/release.yml)
[![Release](https://img.shields.io/github/v/release/hui-cat/welearn-helper?display_name=tag)](https://github.com/hui-cat/welearn-helper/releases/latest)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)

致敬初代版本开发者：**Avenshy & SSmJaE**

> 本工具仅供学习交流使用，请自行判断并承担使用风险。

---

## 功能

- **课程刷取**：自动完成课程练习，支持固定正确率或区间随机正确率
- **时长刷取**：多线程并发累积学习时长，实时进度刷新
- **双模式登录**：账号密码（SSO/OIDC）与 Cookie 粘贴登录
- **登录保持**：刷完一个任务回到主菜单无需重新登录，可随时切换账号
- **输入容错**：输错不退出、不掉登录，`b` 一键退回上一步
- **提交回读**：刷完自动回读服务端记录，确认正确率真的写进去了

## 快速开始

### 方式一：下载单文件 exe（推荐）

到 [Releases](https://github.com/hui-cat/welearn-helper/releases/latest) 下载 `welearn.exe`，
双击运行，无需安装 Python。

### 方式二：从源码运行

```bash
pip install requests
python welearn_decompiled.py
```

调试模式（打印每次提交的请求与响应，并写入 `.debug/session-*.log`）：

```bash
python welearn_decompiled.py --debug
```

### 方式三：自己打包

```bash
pip install requests pyinstaller
pyinstaller --onefile --name welearn welearn_decompiled.py
```

产物在 `dist/welearn.exe`。仓库的 GitHub Actions 也会在打 tag 时自动构建并发布到 Releases。

## 使用说明

启动后按菜单选择模式：

```
请选择 Welearn 刷课模式:
   1. Welearn 课程刷取（完成度 + 正确率）
   2. Welearn 时长刷取
   3. 重新登录 / 切换账号      ← 已登录时才显示
   0. 退出程序
```

### 输入约定

| 输入 | 含义 |
| --- | --- |
| `1` / `2` / `3` / `0` | 菜单、课程、单元序号 |
| `0`（单元选择处） | 该单元全部小节 |
| `1,3,5` | 多选，英文逗号分隔 |
| `80` | 正确率 80% |
| `80,95` | 正确率随机区间 80%~95% |
| `30` / `10,30` | 刷时长：固定 30 秒 / 随机 10~30 秒 |
| `b` | **返回上一步**（任何一步输错都可以用它退回去） |
| `y` | 重新提交已完成的小节 |

### 关于正确率

- 输入 `80` 表示目标 80 分；WE Learn 侧保存的就是百分数（实测回读为 `84`、`90`）。
- 默认跳过已完成的小节；要改已有成绩，在“是否重新提交已完成的小节”处回答 `y`。
- 刷完会打印「服务器记录」，每条形如 `crate=84 已完成 B2U3 ... Task 1`，可直接核对。

### 网络要求

`welearn.sflep.com` 在国内部分网络下无法直连，需要开代理：

- 推荐 [Watt Toolkit](https://steampp.net/)（勾选「网络加速」），或
- 系统级代理 / 全局代理。

若登录失败、课程列表为空、请求超时，先确认代理是否生效、能否在浏览器打开 WE Learn。

## 常见问题

**Q：分数一直显示 1 分？**
v1.2.1 已修复：`cid` 没写回全局导致所有 SCO 请求带 `cid=None`，服务端返回 `2001 参数错误`。
升级到最新版即可；已经提交失败的记录重新跑一次就会覆盖。

**Q：提交全失败，提示“参数错误”？**
同上，旧版本 bug。用 `--debug` 跑一次可看到 `setscoinfo` 的实际返回。

**Q：回到主菜单又要重新登录？**
v1.2.2 已修复：登录状态在进程内保持，主菜单会显示当前账号；要换号选 `3`。

**Q：Cookie 过了很久失效了？**
程序会自动识别（课程列表为空即视为失效），回到主菜单重新登录即可。

## 更新日志

### v1.2.3 (2026-10-27)

**修复：非 UTF-8 控制台 / 输出被重定向时启动即崩溃**

首次 GitHub Actions 构建时冒烟测试直接失败，日志是：

```
File "encodings\cp1252.py", line 19, in encode
UnicodeEncodeError: 'charmap' codec can't encode characters in position 14-23
```

Windows 默认代码页（cp1252 / cp437）或把输出重定向到管道时，打印中文 banner 就崩。
现在启动时把 `stdout`/`stderr` 重新配置为 UTF-8（`errors='replace'`），
真实控制台、CI 日志、管道输出都不会再触发编码异常。

### v1.2.2 (2026-10-27)

**修复：完成一次任务回到主菜单后还要重新登录**

- 登录状态（Cookie + `logged_in`）在整个进程内保持，回到主菜单不再要求重新登录
- 主菜单显示「当前登录：xxx（已保持登录，无需重新输入）」
- 新增主菜单选项 `3`：重新登录 / 切换账号（清空当前 Cookie）
- 课程列表为空（通常意味着 Cookie 失效）时自动标记未登录，回到主菜单会要求重新登录

**实测**：在「全新版大学高阶英语：综合教程（第三版）2（AI增强版）」Unit 2 / Unit 3 验证，
`cid=4451`、`setscoinfo` 返回 `{"ret":0,...}`、回读 `crate` 为 84，网页显示正常分数。

### v1.2.1 (2026-10-27)

**修复：所有 SCO 提交返回 `{"ret":2001,"mess":"参数错误"}`**

重构时选中的课程 `cid` 只存在局部变量里，没有写回全局，导致 `startsco160928` /
`setscoinfo` / `savescoinfo160928` 全部带 `cid=None` 发出。现在 `load_units` 会刷新全局
`cid`，`save_sco` 也加了防御：`cid`/`uid` 为空时本地拦截，不再发到服务器。

**新增**：提交后回读 `scoLeaves`，打印每个小节服务端实际保存的 `crate` 与完成状态。

### v1.2.0 (2026-10-27)

**修复：提交到 WE Learn 后分数一律为 1 分**

对照上游实现（[Avenshy/WELearnToSleep](https://github.com/Avenshy/WELearnToSleep)、
[WelearnBrainBurst](https://github.com/GDUTMeow/WelearnBrainBurst)、
[welearn-farm](https://github.com/foam340/welearn-farm)），定位到反编译时丢了两处协议细节：

1. `setscoinfo` 的 CMI 数据必须以 `[INTERACTIONINFO]` 结尾；缺了它服务器不会写入 `score`，分数停在默认值 1；
2. 反编译版在 `setscoinfo` 之后**无条件**再提交一次 `savescoinfo160928`，并把 `crate` 写死为 `100`，
   覆盖了用户设置的正确率。

现在的提交顺序：`startsco160928` → `setscoinfo`（带 `[INTERACTIONINFO]`）→
仅当方式一失败时才用 `savescoinfo160928` 兜底，`crate` 使用用户设置的值。

**新增：输入容错**

- 登录方式 / 账号密码 / Cookie / 课程编号 / 单元序号 / 正确率 / 时长 全部做格式与范围校验，输错原地重问
- 登录失败、Cookie 失效、网络异常时退回上一步，不再 `exit()`，登录状态不清空
- 任意步骤输入 `b` 退回上一步；`0` 在单元选择处表示“全部单元”、在主菜单表示“退出”

### v1.1.1

- 修复正确率无法设置、无法重复设置（取消第二次提交里写死的 `crate=100`）

### v1.0.0 (2026-05-24)

- 适配 2026 年新 SSO 登录系统 (OIDC)
- 时长刷取改为多线程并发 + 实时进度刷新
- 课程刷取支持双重提交策略
- 新增网络请求重试机制

## 免责声明

本项目仅供编程学习与技术研究，不得用于商业用途。使用本工具产生的一切后果（包括但不限于
成绩异常、账号处罚）由使用者自行承担。请优先遵守学校与平台的使用条款。

## 致谢

- [Avenshy/WELearnToSleep](https://github.com/Avenshy/WELearnToSleep) —— 初代实现
- [SSmJaE/WELearnHelper](https://github.com/SSmJaE/WELearnHelper) —— 题库与时长思路
- [GDUTMeow/WelearnBrainBurst](https://github.com/GDUTMeow/WelearnBrainBurst) —— 两段式提交参考
