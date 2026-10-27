# WeLearn Helper v1.2.0

WE Learn (SFLEP) 自动刷课 / 刷时长工具

致敬初代版本开发者：Avenshy & SSmJaE

## 功能

- **课程刷取**：自动完成课程练习，支持指定/随机正确率
- **时长刷取**：多线程并发累积学习时长，实时进度显示
- **双模式登录**：支持账号密码登录和 Cookie 登录
- **输入容错**：任何一步输错都不会退出程序、不会掉登录，输入 `b` 退回上一步重新选

## 使用方法

1. 双击 `run_welearn.bat` 运行
2. 或直接运行 `welearn.exe`

### 从源码运行

```bash
pip install requests
python welearn_decompiled.py
```

调试模式（会打印每次提交的原始响应，便于排查提交失败）：

```bash
python welearn_decompiled.py --debug
```

### 打包 exe

```bash
pip install pyinstaller
pyinstaller --onefile --name welearn welearn_decompiled.py
```

## 操作提示

- 所有输入都做格式校验：数字、`下限,上限`（英文逗号）、`1,3,5` 这类序号列表；
  输错只会提示并原地重新询问，**不会退出程序**。
- 任何一步输入 `b`（个别步骤输入 `0`）表示**退回上一步**，登录状态（Cookie）全程保留。
- 刷新课模式默认跳过已完成的小节；如果想重刷已有记录的小节（例如改正确率），
  在选择单元后对“是否重新提交已完成的小节”回答 `y`。
- 提交成功后会分别显示“方式1/方式2”，方便判断服务器走的是哪条提交链路。

## 注意事项

- 本工具仅供学习交流使用
- 需要 WE Learn (SFLEP) 有效账号
- 需要使用 Watt Toolkit 等代理工具才能访问 welearn.sflep.com

## 更新日志

### v1.2.1 (2026-10-27)

**修复：所有 SCO 提交都返回 `{"ret":2001,"mess":"参数错误"}`**

重构后选中的课程 cid 只存在局部变量里，没有写回全局 `cid`，导致 `startsco160928` /
`setscoinfo` / `savescoinfo160928` 全部带着 `cid=None` 发出，服务器直接判参数错误 ——
这也是"提交全失败、分数永远 1 分"的直接原因。现在 `load_units` 会刷新全局
`cid`，`save_sco` 里也加了防御：`cid`/`uid` 为空时直接报"内部状态异常"，不会再把手滑发到服务器。

**新增：提交后回读服务器记录**

刷完会打印每个小节的 `crate` 与完成状态（例如 `crate=1 已完成  B2U2 ... Task 1`），
用来确认服务端到底存进去了什么。旧版本提交 `crate=100` 后，服务端记录为 `"crate":"1"`，
说明 WE Learn 把正确率当 0~1 的比例存，这条实测数据是后续校准分数格式的依据。

### v1.2.0 (2026-10-27)

**修复：提交到 WE Learn 后分数一律为 1 分**

对照上游（[Avenshy/WELearnToSleep](https://github.com/Avenshy/WELearnToSleep)、
[WelearnBrainBurst](https://github.com/GDUTMeow/WelearnBrainBurst)、
[welearn-farm](https://github.com/foam340/welearn-farm)）的实现，定位到本仓库在反编译时丢了两处协议细节：

1. `setscoinfo` 提交的 CMI 数据必须以 `[INTERACTIONINFO]` 结尾。
   服务器靠这个标记切分 CMI JSON 与交互数据；缺了它，`score` 不会被写入，
   只有完成度和正确率（走 `savescoinfo160928` 的参数）生效，分数就停在默认的 1 分。
2. 反编译版本在 `setscoinfo` 之后**无条件**又提交了一次 `savescoinfo160928`，
   并且把 `crate` 写死成 `100`，既覆盖了用户设置的正确率，也可能重新计算分数。

现在的提交顺序：`startsco160928` → `setscoinfo`（带 `[INTERACTIONINFO]`）→
仅当方式1失败时才用 `savescoinfo160928` 兜底，且 `crate` 使用用户设置的值。

**新增：输入容错，输错不再退出**

- 登录方式/账号密码/Cookie/课程编号/单元序号/正确率/时长 全部做格式与范围校验，
  输错只打印原因并重新询问；
- 登录失败（密码错、Cookie 格式错、Cookie 失效）不会退出程序，也不会清除已有登录状态，
  可以原地换一种方式重试；
- 网络异常、接口返回异常时退回上一步（例如回到课程选择），不再直接 `exit()`；
- 任意步骤输入 `b` 可退回上一步；`0` 在单元选择处表示“全部单元”、在主菜单表示“退出”。

**其他**

- 刷课模式默认跳过已完成的小节，可手动选择重刷（避免无意义的重复提交）；
- 请求统一加 20 秒超时，避免某个线程卡死导致主程序假死；
- 时长模式会显示每个小节的实时进度。

### v1.1.1
- 修复正确率无法设置、无法重复设置的问题（取消第二次提交里写死的 `crate=100`）

### v1.0.0 (2026-05-24)
- 适配 2026 年新 SSO 登录系统 (OIDC)
- 时长刷取改为多线程并发 + 实时进度刷新
- 课程刷取支持双重提交策略（指定正确率 + 100%正确率）
- 新增网络请求重试机制
