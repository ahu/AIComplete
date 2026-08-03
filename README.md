# AIComplete

Sublime Text 4 的内联 AI 代码补全插件，交互对齐 Codeium / Copilot：
灰色 ghost text 跟着光标浮现，`Tab` 接受，`Esc` 丢弃。

后端不绑定任何厂商 —— 本地 Ollama、DeepSeek、OpenAI、公司内网的
vLLM/one-api，只要接口兼容都能接。零第三方依赖，Sublime 自带的
Python 就能跑。

```
def fib(n):
    if n < 2:
        return n
    return fib(n-1) + fib(n-2)      ← 灰色部分是建议，Tab 接受
```

## 安装

```bash
./install.sh            # 软链进 Packages/，源码只保留一份
./install.sh --copy     # 复制安装，适合部署到别的机器
./install.sh --uninstall
```

手动装也行：把整个 `AIComplete` 目录丢进
`~/Library/Application Support/Sublime Text/Packages/`（macOS）。

> **软链安装后改代码要重启 Sublime。** Sublime 的文件监视不跟随
> symlink，改完源码它不会热重载，你会对着旧模块干瞪眼（这个坑我踩过）。
> 需要频繁改代码就用 `--copy`，直接在 `Packages/AIComplete/` 里编辑。

装完在命令面板跑一次 **AIComplete: 测试与服务的连通性**，
它会打一个真实请求并把结果贴到输出面板，配错了一眼能看出来。

## 配置

`Preferences → Package Settings → AIComplete → Settings`。

默认走本地 Ollama：

```bash
ollama pull qwen2.5-coder:1.5b     # 轻量，够日常用
ollama pull qwen2.5-coder:7b       # 机器扛得住就上这个，质量明显更好
```

换成云端服务，改 `provider` 再填对应那一段：

| provider | 接口 | 适合 |
| --- | --- | --- |
| `ollama` | `/api/generate` + `suffix` | 本地模型，隐私、免费 |
| `openai_fim` | `/completions` + `suffix` | **代码补全首选**，真正的中间填充 |
| `openai` | `/chat/completions` | 通用兜底，任何 chat 模型都能用 |

比如接 DeepSeek 的 FIM 端点：

```jsonc
{
    "provider": "openai_fim",
    "providers": {
        "openai_fim": {
            "base_url": "https://api.deepseek.com/beta",
            "api_key": "sk-...",
            "model": "deepseek-chat"
        }
    }
}
```

不想把 key 写进配置文件，就设环境变量 `AICOMPLETE_API_KEY`
或 `OPENAI_API_KEY`（注意要在启动 Sublime 的那个 shell 里 export，
从 Dock 点开的 Sublime 读不到）。

## 快捷键

| 操作 | macOS | Windows / Linux |
| --- | --- | --- |
| 接受整条建议 | `Tab` | `Tab` |
| 只接受下一个词 | `Ctrl+Option+→` | `Ctrl+Alt+→` |
| 只接受下一行 | `Ctrl+Option+↓` | `Ctrl+Alt+↓` |
| 丢弃 | `Esc` | `Esc` |
| 手动触发一次 | `Cmd+Shift+Enter` | `Alt+\` |
| 切换候选 | `Cmd+Shift+[` / `]` | `Alt+[` / `]` |

> macOS 注意：`Option`+按键会产生组合字符（如 `Option+\` 打出 `«`），
> 所以凡是原本用 `Option` 的组合在 macOS 上都改成了 `Cmd+Shift+…`，
> 这样一定能匹配上。
> 键盘不灵时，随时可以用命令面板搜 **`AIComplete: 立即补全一次`** 触发——和按键无关。

`Tab` 只在建议可见、且自动补全弹窗没显示时才被接管，
正常的缩进和 snippet 跳转不受影响。

想要多个候选，把 `num_suggestions` 调到 2~3（默认值就是 3）。
`openai` / `openai_fim` 用接口的 `n` 参数一次拿多条；`ollama` 不支持 `n`，
改为用**不同 seed 并行多次请求**得到多个续写。本地 1.5b 模型开 3 很轻。
候选出现时按 `Cmd+Shift+[` / `]`（Win/Linux：Alt+[ / ]）切换，角标显示
当前第几条。

## 值得知道的几个设计

**网络请求不占 Sublime 的 async 线程。** Sublime 的 async worker 是
单线程队列，HTTP 放上去会把所有插件的异步事件一起堵死。这里的请求跑在
独立 daemon 线程，只用 `set_timeout` 回主线程。

**边打字边复用建议。** 你继续敲的字符如果正好是建议的开头，插件直接把
建议往前裁一截接着显示，不再发新请求 —— 省 token 也不闪。裁之前会拿
光标前 64 字符做校验，防止别处的编辑把位置带偏。

**过期响应一律丢弃。** 每个 view 有一个自增代号，响应回来时代号对不上、
或者 `change_count` 变了、或者光标不在原位，直接扔掉。

**输出要洗干净。** 模型很爱多嘴，`lib/postprocess.py` 专门处理这些：
剥 markdown 围栏、砍掉重复抄写的前缀、去掉和后文重复的闭合括号、
截断超长输出、清理特殊 token。这是补全质量的关键环节，也是测试覆盖的重点。

**行尾才触发。** 默认只在光标后面没有实质内容时请求（后面只剩
空白或 `) ] } ; ,` 这类闭合符号也算），避免在句子中间乱插。
想全场景触发就把 `trigger_only_at_line_end` 关掉。

## 测试

```bash
python3 tests/test_logic.py     # 20 条纯逻辑测试，不需要 Sublime
python3 tests/test_live.py      # 打真实后端，验证三种补全场景
python3 tests/test_live.py ollama qwen2.5-coder:7b   # 指定 provider/模型
```

## 代码结构

```
ai_complete.py          命令与事件监听（Sublime 只加载包根目录的 .py）
lib/settings.py         配置读取，含环境变量兜底
lib/context.py          抽取前后文、语言判断、跨文件上下文、触发条件
lib/client.py           三种 provider 的 HTTP 实现，纯标准库
lib/postprocess.py      模型输出清洗流水线
lib/ghost.py            phantom 渲染灰色建议
lib/engine.py           防抖、并发、过期丢弃、LRU 缓存、候选管理
```

## 排查

先打开 `"debug": true`，日志会打到 Sublime 控制台（`` Ctrl+` ``）。

- **完全没反应** —— 跑一次连通性测试；确认光标在行尾；看状态栏有没有 `AI ⋯`。
- **状态栏一直 `AI ✕`** —— 控制台里有完整错误。多半是 `base_url`、
  `model` 名或 key 不对。
- **建议里有重复的括号** —— 后端返回质量问题，换 coder 系列的模型；
  也可以在 `postprocess.py` 里加针对性的裁剪规则。
- **公司自签证书报 SSL 错** —— 临时把 `verify_ssl` 设成 `false`。
- **太吵/太费** —— 调大 `debounce_ms`，或者只在需要时手动触发
  （macOS 按 `Ctrl+Alt+\`，Win/Linux 按 `Alt+\`；把 `enabled` 设为 `false`
  后手动触发依然可用）。
