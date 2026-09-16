# AhuAIComplete

Inline AI code completion for Sublime Text 4, with the interaction model of
Codeium / Copilot: grey ghost text appears at the cursor as you type, `Tab`
accepts it, `Esc` dismisses it.

No vendor lock-in — a local Ollama instance, DeepSeek, OpenAI, or an internal
vLLM / one-api deployment all work as long as the API is compatible. Zero
third-party dependencies: Sublime's bundled Python is enough.

```
def fib(n):
    if n < 2:
        return n
    return fib(n-1) + fib(n-2)      <- grey part is the suggestion, Tab accepts
```

## Installation

```bash
./install.sh            # symlink into Packages/, keeping one copy of the source
./install.sh --copy     # copy, handy for deploying to another machine
./install.sh --uninstall
```

Manual installation works too: drop the whole `AhuAIComplete` directory into
`~/Library/Application Support/Sublime Text/Packages/` (macOS).

> **After a symlink install, restart Sublime when you change the code.**
> Sublime's file watcher does not follow symlinks, so edits are not hot
> reloaded and you end up staring at the old module. Use `--copy` if you plan
> to edit frequently, then work directly in `Packages/AhuAIComplete/`.

Once installed, run **AhuAIComplete: Test Connection** from the command
palette. It sends one real request and prints the result in the output panel,
so a misconfiguration is obvious at a glance.

## Configuration

`Preferences -> Package Settings -> AhuAIComplete -> Settings`.

The default is a local Ollama:

```bash
ollama pull qwen2.5-coder:1.5b     # lightweight, fine for everyday use
ollama pull qwen2.5-coder:7b       # noticeably better, if your machine allows
```

To use a hosted service, change `provider` and fill in the matching block:

| provider | API | Best for |
| --- | --- | --- |
| `ollama` | `/api/generate` + `suffix` | Local models: private and free |
| `openai_fim` | `/completions` + `suffix` | **Best for completion**: real fill-in-the-middle |
| `openai` | `/chat/completions` | General fallback: works with any chat model |

For example, the DeepSeek fill-in-the-middle endpoint:

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

To keep the key out of the settings file, set the `AICOMPLETE_API_KEY` or
`OPENAI_API_KEY` environment variable. Note that it must be exported in the
shell that launches Sublime; starting the editor from the Dock will not pick
it up.

## Key bindings

| Action | macOS | Windows / Linux |
| --- | --- | --- |
| Accept the whole suggestion | `Tab` | `Tab` |
| Accept only the next word | `Ctrl+Option+->` | `Ctrl+Alt+->` |
| Accept only the next line | `Ctrl+Option+Down` | `Ctrl+Alt+Down` |
| Dismiss | `Esc` | `Esc` |
| Request a completion manually | `Cmd+Shift+Enter` | `Alt+\` |
| Cycle candidates | `Cmd+Shift+[` / `]` | `Alt+[` / `]` |

> macOS note: `Option` plus a key produces a composed character (`Option+\`
> types `«`), so every shortcut that originally used `Option` became
> `Cmd+Shift+...` on macOS, which always matches. When a key does not work,
> **`AhuAIComplete: Request Completion`** in the command palette does the same
> thing independently of any key binding.

`Tab` is only taken over while a suggestion is visible and the autocomplete
popup is not showing, so ordinary indentation and snippet navigation are
unaffected.

For several candidates, set `num_suggestions` to 2 or 3 (the default is 3).
`openai` and `openai_fim` use the API's `n` parameter to get them in one
request; `ollama` does not support `n`, so it fires **several parallel
requests with different seeds** instead. Running 3 with a local 1.5b model is
cheap. When candidates are available, press `Cmd+Shift+[` / `]`
(Windows / Linux: `Alt+[` / `]`) to cycle; a badge shows the current index.

## How this differs from other completion packages

There is only one goal here — fast inline ghost text — so there is no chat
panel, no "edit selection" command, and no setup wizard. Specifics:

- **Real fill-in-the-middle instead of a chat prompt.** The `openai_fim` and
  `ollama` providers send the code after the cursor as a `suffix`, so the
  model fills the gap rather than continuing a conversation. This is the
  single biggest factor in completion quality.
- **Multiple candidates with cycling.** Other packages return one suggestion.
  Here you can request several and step through them, which matters with small
  local models that are otherwise deterministic.
- **Standard library only, no installer and no build step.** There is no
  companion CLI, no GUI installer, and no third-party dependency to vendor; it
  runs on the Python that ships with Sublime Text 4.
- **Per-view control.** Completion can be toggled globally, for one file, or
  via the status bar indicator.

## A few design notes

**Network requests stay off Sublime's async thread.** Sublime's async worker
is a single-threaded queue; putting HTTP on it would block every plugin's
asynchronous events. Requests run on a separate daemon thread and only hop
back to the main thread via `set_timeout`.

**Suggestions are reused while you type.** If the characters you keep typing
match the beginning of the suggestion, it is trimmed in place and displayed
again instead of triggering a new request — fewer tokens, no flicker. The 64
characters before the cursor are checksummed first, so edits elsewhere cannot
shift the position unnoticed.

**Stale responses are always discarded.** Every view carries an incrementing
token; a response is dropped when the token does not match, when
`change_count` has moved on, or when the cursor is no longer in place.

**Model output is cleaned before display.** Models ramble, so
`lib/postprocess.py` strips markdown fences, removes a repeated copy of the
prefix, drops closing brackets that duplicate the suffix, truncates overly long
output, and removes special tokens. This is the key step for completion
quality and the focus of the test suite.

**Triggering only happens at the end of a line.** By default a request is only
sent when nothing substantial follows the cursor (trailing whitespace, or
closing characters such as `) ] } ; ,`). Turn off `trigger_only_at_line_end`
to trigger everywhere.

## Tests

```bash
python3 tests/test_logic.py     # 20 pure logic tests, no Sublime needed
python3 tests/test_live.py      # hits a real backend, three completion cases
python3 tests/test_live.py ollama qwen2.5-coder:7b   # pick provider / model
```

## Code layout

```
ai_complete.py          commands and event listeners (Sublime loads only
                        the .py files at the package root)
lib/settings.py         settings access, with environment-variable fallback
lib/context.py          prefix/suffix extraction, language detection,
                        cross-file context, trigger conditions
lib/client.py           HTTP for the three providers, standard library only
lib/postprocess.py      model-output cleaning pipeline
lib/ghost.py            phantom rendering of the grey suggestion
lib/engine.py           debounce, concurrency, stale-response dropping,
                        LRU cache, candidate management
```

## Troubleshooting

Enable `"debug": true` first; the log goes to the Sublime console
(`` Ctrl+` ``).

- **Nothing happens at all** — run the connection test; make sure the cursor
  is at the end of a line; look for `AI ...` in the status bar.
- **The status bar stays on `AI err`** — the console holds the full error.
  Usually `base_url`, the model name, or the key is wrong.
- **Suggestions contain duplicated brackets** — a backend quality issue; try a
  coder-series model, or add a targeted trimming rule to `postprocess.py`.
- **SSL errors with a self-signed certificate** — set `verify_ssl` to `false`
  temporarily.
- **Too noisy or too expensive** — raise `debounce_ms`, or request completions
  manually only (`Cmd+Shift+Enter` on macOS, `Alt+\` on Windows / Linux; with
  `enabled` set to `false`, manual requests still work).
