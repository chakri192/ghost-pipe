# Ghost-Pipe

Ghost-Pipe is a single-file, zero-dependency terminal forensics and automated repair engine. Built specifically for macOS and Zsh, it captures command failures, securely redacts sensitive data, and diagnoses the root cause using a hybrid deterministic and local AI engine.

## Architecture and Design

- **Zero Dependencies:** Built entirely on the Python 3 Standard Library. No `requests`, `rich`, or `click`.
- **Single Monolith:** Delivered as a single executable file (`ghost-pipe.py`).
- **SQLite Ledger:** Uses a transactional SQLite database with a daemon background pruning thread to manage run history.
- **Privacy First:** Operates entirely offline. No cloud APIs, no telemetry, no hidden telemetry.
- **Local AI:** Optional integration with local Ollama instances (`127.0.0.1:11434`). If Ollama is offline, Ghost-Pipe falls back to deterministic rule engines.
- **Transparent Execution:** PTY mode perfectly preserves ANSI color sequences, terminal dimensions, and exact exit codes.

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
- **Deterministic Engine:** Instantly diagnoses known failure classes. Includes a custom Mach-O binary parser (using `mmap` and `struct.unpack`) to perfectly detect Apple Silicon / Intel architecture mismatches.
- **Generative Engine:** Unrecognised failures are routed to a local Ollama model for root-cause analysis via a zero-dependency, chunk-aware HTTP client built purely on `urllib.request`.

### 4. Safe Repair Workflow
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
- **Automated Repair Loop:** `gp run --repair-loop 3 -- <command>`
- **Safe Worktree Repair:** `gp fix latest --worktree`
- **View Run History:** `gp history`
- **Inspect Context Payload:** `gp inspect latest`

### Built-In Diagnostics
- **System Doctor:** `gp doctor`
- **Security Audit:** `gp audit`
- **Self-Test Suite:** `gp self-test`
- **Performance Benchmark:** `gp benchmark --redaction <file>`
- **Automated Demo:** `gp demo`

## Security Boundaries

Ghost-Pipe is designed to assist, not to act autonomously. Please note the following security properties:
- **Worktree Sandboxing:** The `--worktree` flag isolates the Git index and working directory. It does not provide OS-level containerization (e.g., cgroups or namespaces).
- **Redaction Limits:** The context firewall uses a fixed set of heuristics. It is a defense-in-depth measure, not a replacement for proper secret management.
- **Process Signals:** Terminal state is protected against application crashes via `SIGTERM` and `SIGQUIT` traps. Uncatchable signals (e.g., `SIGKILL`) will bypass these traps.

## Requirements

- Python 3.9 or higher
- Zsh (for hook integration)
- Ollama (optional, for generative diagnostics)
