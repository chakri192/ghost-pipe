# ghost-pipe

A terminal tool that records failed commands, hides secrets in their output, and tells you what went wrong. It can also suggest a fix. It's a single Python file with no dependencies and works fully offline. AI explanations are optional and run locally through Ollama.

Made for the Zero Dependency Hackathon (August 2026).

<img src="demo.gif" alt="ghost-pipe demo" width="720" />

## Requirements

- Python 3.9+
- macOS or Linux
- Optional: [Ollama](https://ollama.com) with `ollama pull qwen2.5-coder:7b`, for AI explanations
- Optional: zsh, for the keyboard shortcuts

## Install

```sh
git clone https://github.com/chakri192/ghost-pipe.git
cd ghost-pipe
chmod +x ghost-pipe.py
alias gp="$PWD/ghost-pipe.py"      # optional short name, used below
```

## Usage

Run a command through ghost-pipe so its output is saved:

```sh
gp run -- npm start
```

If it fails, find out why:

```sh
gp diagnose latest     # built-in checks, offline
gp explain latest      # built-in checks, then asks Ollama if nothing matched
gp inspect latest      # see the saved output with secrets hidden
```

The suggested fix is copied to your clipboard.

Try it without setting anything up:

```sh
gp demo
```

### Commands

| Command | |
|---|---|
| `run -- CMD` | Run a command and save its output |
| `run --repair-loop N -- CMD` | If it fails, suggest a fix, ask before running it, and try again (up to N times) |
| `diagnose RUN` | Explain a failure using built-in checks |
| `explain RUN` | Same, falling back to the local AI model |
| `inspect RUN` | Show the saved output with secrets hidden |
| `fix RUN --dry-run` | Show the suggested fix without running it |
| `fix RUN --worktree` | Test the fix in a temporary git worktree first, then offer to apply it |
| `fix RUN --apply` | Ask, then run the fix |
| `board` | Browse past runs in a full-screen view (arrow keys, Enter to analyse, `q` to quit) |
| `history` | Last 15 runs |
| `show RUN` | Details of one run |
| `compare RUN --last-good` | Compare a failure with the last successful run of the same command |
| `bundle RUN` | Package a run into a `.tar.gz` to share |
| `prune --days N` | Delete runs older than N days |
| `install-zsh` / `uninstall-zsh` | Add or remove the zsh integration |
| `enable` / `disable` | Turn recording on or off |
| `doctor` | Check the setup |
| `audit` | Confirm the tool only uses the Python standard library |
| `self-test` | Run the built-in tests |

`RUN` is `latest` or the start of a run ID from `history`.

### What it detects

- Port already in use (suggests `lsof -i :PORT`)
- Missing Python module (suggests `pip install ...`)
- Binary built for the wrong CPU (Intel vs Apple Silicon)
- Anything you add to `~/.ghost-pipe/rules.json`:

```json
[
  { "rule": "Missing AWS Profile", "regex": "botocore.exceptions.ProfileNotFound", "action": "aws sso login", "risk": "low" }
]
```

### Hidden secrets

AWS keys, GitHub and Slack tokens, JWTs, bearer tokens, private keys, passwords in URLs, and `password=` / `api_key=` style values are replaced with `<REDACTED>` in both the command and its output before they're shown or sent to the model.

### Zsh integration

`gp install-zsh` adds a block to `~/.zshrc`:

- Every command you type is recorded (command and exit code only, not output)
- `Ctrl-E` explains the last failure
- `Ctrl-F` puts the suggested fix into your prompt so you can review it before pressing Enter

Use `gp run --` for commands you want fully analysed, since the zsh hooks don't capture output.

## Safety

- Fixes never run without you typing `y`.
- Fixes run without a shell, so `;`, `&&`, and `|` in a suggestion can't chain extra commands.
- Dangerous commands (`rm -rf /`, `curl ... | sh`, `mkfs`, `dd` to a disk, and similar) are refused.

## Data

Everything is stored in `~/.ghost-pipe/`: the run history (`history.sqlite`), saved output, `rules.json`, and the last suggestion.

## Contributors

| | |
|---|---|
| [chakri192](https://github.com/chakri192) | Author |
