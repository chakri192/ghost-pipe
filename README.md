# Ghost-Pipe

Ghost-Pipe is a single-file, zero-dependency terminal forensics and automated repair engine. Built specifically for macOS and Zsh, it captures command failures, securely redacts sensitive data, and diagnoses the root cause using a hybrid deterministic and local AI engine.

## Architecture and Design

- **Zero Dependencies:** Built entirely on the Python 3 Standard Library. No `requests`, `rich`, `click`, or `pyperclip`.
- **Single Monolith:** Delivered as a single executable file (`ghost-pipe.py` — ~1500 lines of code).
- **SQLite Ledger:** Uses a transactional SQLite database with a daemon background pruning thread to manage run history.
- **Privacy First:** Operates entirely offline. No cloud APIs, no telemetry, no hidden tracking.
- **Local AI:** Optional integration with local Ollama instances (`127.0.0.1:11434`). If Ollama is offline, Ghost-Pipe falls back to deterministic rule engines.
- **Cross-Platform Auto-Clipboard:** Zero-dependency OS detection (`pbcopy`, `xclip`, `clip`) automatically copies the recommended fix to your clipboard immediately upon diagnosis.
- **Zero-Dependency CLI Styling:** Features a custom ANSI formatting engine that provides beautiful color-coded tables, status indicators, and semantic logging without requiring third-party libraries.

## Core Capabilities

### 1. Execution Engine
Ghost-Pipe captures command context through two mechanisms:
- **Hook Mode:** Zsh integration (`preexec`/`precmd`) quietly records command metadata into a local SQLite ledger.
- **PTY Mode (`gp run`):** Wraps executions in a genuine pseudo-terminal (`pty.fork`) to capture exact interactive output while mirroring it back to the user.
- **Terminal Safety:** Actively traps `SIGWINCH` for geometry sync, and `SIGTERM`/`SIGQUIT` to prevent terminal corruption.
- **Zsh Keybindings:** Inject `Ctrl-E` to instantly explain a failure, or `Ctrl-F` to drop a safe repair suggestion directly into your prompt buffer.

### 2. Context Firewall
Before any diagnostic data leaves the execution context, a redaction engine sanitizes the output. The firewall strips:
- Cloud provider credentials (AWS, Azure, GCP)
- Access tokens (GitHub, Slack, Bearer tokens)
- Cryptographic material (PEM keys, SSH keys)
- Generic secret assignments (`password=...`)

### 3. Diagnostic Engine
Failures are processed through a two-stage diagnostic pipeline:
- **Deterministic Engine:** Instantly diagnoses known failure classes, designed for day-to-day developer pain points.
  - **Port Conflicts:** Detects Node.js/Python `EADDRINUSE` errors and suggests exact `lsof`/`kill` commands.
  - **Missing Dependencies:** Detects `ModuleNotFoundError` and builds immediate `pip install` commands.
  - **Custom Rules:** Expandable JSON rules engine (`~/.ghost-pipe/rules.json`) maps internal company errors (e.g., AWS SSO token expiration) to exact fixes.
  - **Binary Inspection:** Includes a custom Mach-O binary parser (using `mmap` and `struct.unpack`) to perfectly detect Apple Silicon / Intel architecture mismatches.
- **Generative Engine:** Unrecognised failures are routed to a local Ollama model for root-cause analysis via a zero-dependency, chunk-aware HTTP client built purely on `urllib.request`.

### 4. Interactive Dashboard UI
- **`gp board`:** A full-screen forensic terminal dashboard built on the standard `curses` library. Navigate through historical runs, view exit codes, and press `Enter` on any failure to immediately trigger a secure Context Firewall analysis.

### 5. Safe Repair Workflow
Repairs are proposed and executed through a strict security boundary:
- **Shell-Injection Immunity:** All repair executions are forcefully tokenized via `shlex.split` and executed with `shell=False`.
- **Destructive Command Guard:** Blocks obvious malicious patterns (path traversal, recursive deletion, subshell execution, fork bombs).
- **Worktree Isolation:** Proposes applying fixes inside a temporary Git worktree (`git worktree add`) to verify the repair before applying it to the host repository.
- **Explicit Consent:** Ghost-Pipe will never execute a generated repair command without explicit `[y/N]` user confirmation.

## Usage Guide

### Setup and Integration
- **Initialize Database:** `gp doctor`
- **Install Zsh Hooks:** `gp install-zsh`
- **Uninstall Zsh Hooks:** `gp uninstall-zsh`

### Core Commands
- **Run with PTY Capture:** `gp run -- <command>`
- **Diagnose Failure:** `gp diagnose latest`
- **Interactive Dashboard:** `gp board`
- **Automated Repair Loop:** `gp run --repair-loop 3 -- <command>`
- **Safe Worktree Repair:** `gp fix latest --worktree`
- **View Run History:** `gp history`
- **Inspect Context Payload:** `gp inspect latest`

### Built-In Diagnostics
- **System Doctor:** `gp doctor`
- **Security Audit:** `gp audit`
- **Self-Test Suite:** `gp self-test`
- **Automated Demo:** `gp demo`

## Requirements

- Python 3.9 or higher
- macOS or Linux (Requires a POSIX-compliant OS for PTY support)
- *Ollama (Optional, for generative AI diagnostics)*
- *Zsh (Optional, for automatic hook integration)*
