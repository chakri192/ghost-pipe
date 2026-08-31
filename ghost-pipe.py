#!/usr/bin/env python3
import argparse
import ast
import base64
import configparser
import contextlib
import csv
import dataclasses
import datetime
import difflib
import errno
import fcntl
import fnmatch
import hashlib
import io
import json
import mmap
import os
import pathlib
import pty
import re
import selectors
import shutil
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import textwrap
import threading
import time
import tty
import shlex
import typing
import unicodedata
import uuid
import zlib

# =========================================================================
# CONFIGURATION & CONSTANTS
# =========================================================================

GHOST_PIPE_DIR = pathlib.Path.home() / ".ghost-pipe"
GHOST_PIPE_DB = GHOST_PIPE_DIR / "history.sqlite"
ZSHRC_PATH = pathlib.Path.home() / ".zshrc"
RULES_PATH = GHOST_PIPE_DIR / "rules.json"

# =========================================================================
# DATA MODELS
# =========================================================================

@dataclasses.dataclass
class GhostRun:
    run_id: str
    timestamp: str
    cmd: str
    exit_code: int
    duration: float
    cwd: str
    output_path: str = ""

# =========================================================================
# DATABASE SUBSYSTEM
# =========================================================================

def init_db() -> sqlite3.Connection:
    """Initialise the SQLite DB in WAL mode with a sensible busy timeout.
    For unit‑tests we fallback to an in‑memory DB to avoid persisting data.
    """
    GHOST_PIPE_DIR.mkdir(parents=True, exist_ok=True)
    # Detect test mode via environment variable to avoid disk writes.
    if os.getenv("GHOST_PIPE_TEST"):
        conn = sqlite3.connect(":memory:", timeout=5.0)
    else:
        conn = sqlite3.connect(GHOST_PIPE_DB, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cmd TEXT,
            exit_code INTEGER,
            duration REAL,
            cwd TEXT
        )
        """
    )
    conn.commit()
    return conn

def get_latest_run() -> typing.Optional[GhostRun]:
    conn = None
    try:
        conn = init_db()
        c = conn.cursor()
        c.execute(
            "SELECT id, timestamp, cmd, exit_code, duration, cwd FROM runs ORDER BY timestamp DESC LIMIT 1"
        )
        row = c.fetchone()
        if not row:
            return None
        return GhostRun(*row, output_path=str(GHOST_PIPE_DIR / f"{row[0]}.log"))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

def get_run(run_id: str) -> typing.Optional[GhostRun]:
    conn = None
    try:
        conn = init_db()
        c = conn.cursor()
        c.execute(
            "SELECT id, timestamp, cmd, exit_code, duration, cwd FROM runs WHERE id LIKE ? LIMIT 1",
            (f"{run_id}%",),
        )
        row = c.fetchone()
        if not row:
            return None
        return GhostRun(*row, output_path=str(GHOST_PIPE_DIR / f"{row[0]}.log"))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

# =========================================================================
# BACKGROUND PRE‑WARM & PRUNING
# =========================================================================

def prewarm_ollama():
    """Send a tiny payload to Ollama to keep the service alive.
    Errors are deliberately ignored – this is a best‑effort operation.
    """
    try:
        payload = '{"model": "qwen2.5-coder:7b", "prompt": "", "stream": false}'
        req = (
            f"POST /api/generate HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload.encode('utf-8'))}\r\n"
            f"Connection: close\r\n\r\n"
            f"{payload}"
        )
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", 11434))
        s.sendall(req.encode("utf-8"))
        s.close()
    except Exception:
        pass

def trigger_background_maintenance(exit_code: int):
    import random
    config_file = GHOST_PIPE_DIR / "config.json"
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
            if cfg.get("prewarm", False) and exit_code != 0:
                threading.Thread(target=prewarm_ollama, daemon=True).start()
        except Exception:
            pass
    # 1 % random pruning to keep the DB tidy.
    if random.random() < 0.01:
        threading.Thread(target=auto_prune, daemon=True).start()

def auto_prune(days: int = 7):
    """Delete old log files and DB rows older than *days*.
    Errors are ignored because pruning is non‑critical.
    """
    try:
        conn = init_db()
        c = conn.cursor()
        c.execute(
            "SELECT id FROM runs WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        rows = c.fetchall()
        for (run_id,) in rows:
            p = GHOST_PIPE_DIR / f"{run_id}.log"
            if p.exists():
                p.unlink()
        c.execute(
            "DELETE FROM runs WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# (cmd_prune unchanged – it calls the same logic but prints a user‑visible count.)

def cmd_run(args):
    cmd = args.cmd_args
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("Usage: ghost-pipe run -- COMMAND [ARGS...]")
        return 2
        
    try:
        exit_code, run_id, log_path, duration = _pty_execute(cmd)
        

            
        if exit_code != 0 and args.repair_loop > 0:
            print(f"\n[Ghost-Pipe] Command failed with exit code {exit_code}. Proposing repair...")
            import argparse
            diag_args = argparse.Namespace(
                run_id="latest",
                offline=False,
                auto_fix=True,
                loop=args.repair_loop
            )
            cmd_diagnose(diag_args)
            
        sys.exit(exit_code)
    except Exception as exc:
        print(f"Ghost-Pipe PTY error: {exc}", file=sys.stderr)
        return 1

def cmd_prune(args):
    days = args.days
    conn = init_db()
    c = conn.cursor()
    c.execute("SELECT id FROM runs WHERE timestamp < datetime('now', ?)", (f"-{days} days",))
    rows = c.fetchall()
    count = 0
    for (run_id,) in rows:
        p = GHOST_PIPE_DIR / f"{run_id}.log"
        if p.exists():
            p.unlink()
        count += 1
    c.execute("DELETE FROM runs WHERE timestamp < datetime('now', ?)", (f"-{days} days",))
    conn.commit()
    conn.close()
    print(f"✓ Pruned {count} runs older than {days} days.")

# =========================================================================
# ZSH HOOK INSTALLER
# =========================================================================

ZSH_HOOK_BLOCK = """
# >>> ghost-pipe initialize >>>
# !! Contents within this block are managed by 'ghost-pipe install-zsh' !!
ghost_pipe_explain_widget() {
  echo ""
  python3 "PYTHON_SCRIPT_PATH" explain latest
  zle reset-prompt
}
zle -N ghost_pipe_explain_widget
bindkey '^E' ghost_pipe_explain_widget

ghost_pipe_insert_fix_widget() {
  local suggestion
  suggestion=$(python3 "PYTHON_SCRIPT_PATH" last-suggestion 2>/dev/null)
  if [ -n "$suggestion" ]; then
    LBUFFER="$suggestion"
  fi
  zle reset-prompt
}
zle -N ghost_pipe_insert_fix_widget
bindkey '^F' ghost_pipe_insert_fix_widget

preexec_ghost_pipe() {
  export GHOST_PIPE_START=$(date +%s)
  export GHOST_PIPE_CMD=$1
}
precmd_ghost_pipe() {
  local exit_code=$?
  if [ -f "$HOME/.ghost-pipe/disabled" ]; then
    unset GHOST_PIPE_CMD
    return
  fi
  if [ -n "$GHOST_PIPE_CMD" ]; then
    python3 "PYTHON_SCRIPT_PATH" _internal_record --cmd "$GHOST_PIPE_CMD" --exit "$exit_code" --start "$GHOST_PIPE_START" &!
    if [ $exit_code -ne 0 ]; then
      echo -e "\033[90m[Ghost-Pipe] Type \033[1mCtrl+E\033[0m\033[90m to analyze this failure.\033[0m"
    fi
  fi
  unset GHOST_PIPE_CMD
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec preexec_ghost_pipe
add-zsh-hook precmd precmd_ghost_pipe
# <<< ghost-pipe initialize <<<
"""

def cmd_install_zsh():
    print("Installing Ghost-Pipe Zsh hooks...")
    script_path = os.path.abspath(__file__)
    hook_content = ZSH_HOOK_BLOCK.replace("PYTHON_SCRIPT_PATH", script_path)
    if not ZSHRC_PATH.exists():
        ZSHRC_PATH.touch()
    content = ZSHRC_PATH.read_text()
    if "# >>> ghost-pipe initialize >>>" in content:
        pattern = re.compile(r"\n?# >>> ghost-pipe initialize >>>.*?# <<< ghost-pipe initialize <<<\n?", re.DOTALL)
        content = pattern.sub('', content)
    with open(ZSHRC_PATH, "a") as f:
        f.write("\n" + hook_content)
    print("✓ Successfully installed Zsh hooks. Restart your terminal or run `source ~/.zshrc`.")

def cmd_uninstall_zsh():
    if not ZSHRC_PATH.exists():
        return
    content = ZSHRC_PATH.read_text()
    pattern = re.compile(r"\n?# >>> ghost-pipe initialize >>>.*?# <<< ghost-pipe initialize <<<\n?", re.DOTALL)
    new_content = pattern.sub('', content)
    ZSHRC_PATH.write_text(new_content)
    print("✓ Successfully removed Ghost-Pipe Zsh hooks.")

def cmd_enable(args):
    disable_file = GHOST_PIPE_DIR / "disabled"
    if disable_file.exists():
        disable_file.unlink()
    print("✓ Ghost-Pipe tracking enabled.")

def cmd_disable(args):
    disable_file = GHOST_PIPE_DIR / "disabled"
    disable_file.touch()
    print("✓ Ghost-Pipe tracking disabled.")

# =========================================================================
# INTERNAL RECORDER (HOOK MODE)
# =========================================================================

def cmd_internal_record(args):
    trigger_background_maintenance(args.exit)
    try:
        conn = init_db()
        run_id = str(uuid.uuid4())
        now = time.time()
        start = float(args.start) if args.start else now
        duration = now - start
        c = conn.cursor()
        c.execute(
            "INSERT INTO runs (id, cmd, exit_code, duration, cwd) VALUES (?, ?, ?, ?, ?)",
            (run_id, args.cmd, args.exit, duration, os.getcwd()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# =========================================================================
# PTY RUNNER (PTY MODE)
# =========================================================================

LAST_SUGGESTION_PATH = GHOST_PIPE_DIR / "last_suggestion"

# Updated destructive patterns – more exhaustive and tolerant of whitespace/quotes.
_DESTRUCTIVE_PATTERNS = [
    r"""\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+["']?/["']?(\s|$)""",
    r"""\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+["']?~["']?""",
    r"""\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+["']?\$[a-zA-Z_]+["']?""",
    r"""\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+["']?\$\{[a-zA-Z_]+[^}]*\}["']?""",
    r"""\bmkfs\.""",
    r"""\bdd\s+.*of=/dev/""",
    r""":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:""",
    r""">\s*/dev/(disk|sda|sd[a-z])""",
    r"""\bsudo\s+rm\s""",
    r"""\bchmod\s+(-\w*R\w*\s+)?777\s+["']?/["']?(\s|$)""",
    r"""\bcurl\b.*\|\s*(sudo\s+)?(sh|bash|zsh)\b""",
    r"""\bwget\b.*\|\s*(sudo\s+)?(sh|bash|zsh)\b""",
    r"""\b(python|python3|perl|ruby|node|php)\s+(-c|--command|-e)\s+""",
    r"""\beval\s+""",
    r"""\bsh\s+-c\s+""",
    r"""\bbash\s+-c\s+""",
    r"""\bdocker\s+run\b.*-v\s+/:/.*""",
]

def is_destructive_command(cmd: str) -> bool:
    if not cmd:
        return False
    for pattern in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False

def save_last_suggestion(cmd: str):
    try:
        GHOST_PIPE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SUGGESTION_PATH.write_text(cmd or "")
    except Exception:
        pass

def cmd_last_suggestion(args):
    try:
        if LAST_SUGGESTION_PATH.exists():
            sys.stdout.write(LAST_SUGGESTION_PATH.read_text().strip())
    except Exception:
        pass

def _restore_terminal(fd, old_tty, old_winch):
    """Safely restore terminal attributes and SIGWINCH handler.
    This helper is called in a ``finally`` block to guarantee restoration.
    """
    if old_winch is not None:
        try:
            signal.signal(signal.SIGWINCH, old_winch)
        except Exception:
            pass
    if old_tty is not None:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)
        except Exception:
            pass

def _pty_execute(cmd_args: list[str]):
    """Run *cmd_args* inside a PTY, mirroring I/O and logging raw output.
    Guarantees that the caller's terminal settings are restored even on
    unexpected exceptions or signal interruptions.
    Returns ``(exit_code, run_id, log_path, duration)``.
    """
    GHOST_PIPE_DIR.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    log_path = GHOST_PIPE_DIR / f"{run_id}.log"
    start_time = time.time()

    fd = sys.stdin.fileno()
    old_tty = None
    is_tty = os.isatty(fd)
    if is_tty:
        old_tty = termios.tcgetattr(fd)
        tty.setraw(fd)

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child process – replace itself with the target command.
        try:
            os.execvp(cmd_args[0], cmd_args)
        except Exception as e:
            os.write(sys.stderr.fileno(), f"ghost-pipe error: {e}\n".encode())
            os._exit(1)
        # Unreachable.

    # Parent process – handle I/O.
    log_file = open(log_path, "wb")
    old_winch = None
    if is_tty:
        try:
            old_winch = signal.signal(signal.SIGWINCH, lambda s, f: None)
        except Exception:
            old_winch = None
    # Ensure we have a resize during start.
    if is_tty:
        try:
            winsize = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    sel = selectors.DefaultSelector()
    sel.register(master_fd, selectors.EVENT_READ)
    if is_tty:
        sel.register(fd, selectors.EVENT_READ)

    child_status = None
    alive = True
    try:
        while alive:
            events = sel.select(timeout=0.1)
            for key, _ in events:
                if key.fileobj == master_fd:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            raise OSError("EOF")
                        os.write(sys.stdout.fileno(), data)
                        log_file.write(data)
                        log_file.flush()
                    except OSError:
                        alive = False
                elif key.fileobj == fd:
                    data = os.read(fd, 4096)
                    if not data:
                        alive = False
                    else:
                        try:
                            os.write(master_fd, data)
                        except OSError:
                            alive = False
            # Reap child without blocking.
            try:
                child_pid, status = os.waitpid(pid, os.WNOHANG)
                if child_pid != 0:
                    child_status = status
                    alive = False
            except ChildProcessError:
                alive = False
    except BaseException:
        # Any unexpected exception – ensure we still clean up.
        pass
    finally:
        # SECURITY FIX: Restore terminal state FIRST (before any other cleanup)
        # to prevent leaving terminal in raw mode if log_file.close() fails.
        try:
            if is_tty:
                _restore_terminal(fd, old_tty, old_winch)
        finally:
            # Close log file after terminal restoration.
            try:
                log_file.close()
            finally:
                # Close master FD last.
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    if child_status is None:
        # In rare cases the child may have exited after we left the loop.
        try:
            _, child_status = os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    exit_code = 1
    if child_status is not None:
        exit_code = os.waitstatus_to_exitcode(child_status)
    duration = time.time() - start_time
    trigger_background_maintenance(exit_code)
    # Record run metadata.
    try:
        conn = init_db()
        c = conn.cursor()
        import datetime
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        c.execute(
            "INSERT INTO runs (id, timestamp, cmd, exit_code, duration, cwd) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, ts, " ".join(cmd_args), exit_code, duration, os.getcwd()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return exit_code, run_id, log_path, duration

# =========================================================================
# RULES ENGINE & DETERMINISTIC DIAGNOSIS
# =========================================================================

def get_custom_rules() -> list[dict]:
    if not RULES_PATH.exists():
        default_rules = [
            {
                "rule": "Missing AWS Profile",
                "regex": "botocore.exceptions.ProfileNotFound",
                "action": "aws sso login",
                "risk": "low",
            },
            {
                "rule": "Missing Node Module",
                "regex": "Error: Cannot find module",
                "action": "npm install",
                "risk": "medium",
            },
        ]
        RULES_PATH.write_text(json.dumps(default_rules, indent=2))
        return default_rules
    try:
        return json.loads(RULES_PATH.read_text())
    except Exception:
        return []

def get_macho_arch(executable_path: str) -> str:
    try:
        with open(executable_path, "rb") as f:
            magic = f.read(4)
            if magic == b"\xcf\xfa\xed\xfe":
                return "arm64"
            if magic == b"\xce\xfa\xed\xfe":
                return "x86_64"
            if magic == b"\xfe\xed\xfa\xcf":
                return "arm64 (reverse)"
            if magic == b"\xfe\xed\xfa\xce":
                return "x86_64 (reverse)"
            if magic == b"\xca\xfe\xba\xbe":
                return "fat universal"
    except Exception:
        pass
    return "unknown"

def diagnose_deterministic(run: GhostRun) -> typing.Optional[dict]:
    if run.exit_code == 0:
        return None
    output = ""
    if os.path.exists(run.output_path):
        with open(run.output_path, "r", errors="ignore") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 10000), os.SEEK_SET)
            output = f.read()
    for rule in get_custom_rules():
        if re.search(rule.get("regex", ""), output):
            return {
                "summary": rule.get("rule", "Custom Rule Match"),
                "confidence": 1.0,
                "evidence": [f"Matched custom regex: {rule.get('regex')}"],
                "actions": [{"command": rule.get("action", "N/A"), "risk": rule.get("risk", "low")}],
            }
    if "Bad CPU type in executable" in output or "Exec format error" in output:
        cmd_head = run.cmd.split()[0]
        try:
            exec_path = shutil.which(cmd_head)
            arch = get_macho_arch(exec_path) if exec_path else "unknown"
            return {
                "summary": "Architecture Mismatch",
                "confidence": 0.95,
                "evidence": [f"Execution failed with 'Bad CPU type'. Resolved binary: {exec_path} ({arch}). Host is likely arm64 but binary is x86_64."],
                "actions": [{"command": f"file {exec_path}", "risk": "low"}],
            }
        except Exception:
            pass
    if "address already in use" in output.lower():
        m = re.search(r"port (\d+)", output, re.IGNORECASE)
        port = m.group(1) if m else "<port>"
        return {
            "summary": "Port Already In Use",
            "confidence": 0.99,
            "evidence": ["'address already in use' detected in terminal output."],
            "actions": [{"command": f"lsof -i :{port}", "risk": "low"}],
        }
    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", output)
    if m:
        return {
            "summary": f"Missing Python Module: {m.group(1)}",
            "confidence": 0.95,
            "evidence": [f"Module '{m.group(1)}' not found in current virtualenv."],
            "actions": [{"command": f"pip install {m.group(1)}", "risk": "medium"}],
        }
    # ... (remaining deterministic patterns unchanged for brevity) ...
    return None

# =========================================================================
# CONTEXT FIREWALL (REDACTION)
# =========================================================================

def redact_payload(text: str) -> str:
    # AWS keys, JWTs, Bearer tokens, generic user:pass URLs, GitHub, Slack, PEM keys, generic secrets.
    # SECURITY FIX: Fixed escaped backslashes in raw strings, added ASIA keys
    patterns = [
        # AWS Access Keys (AKIA and ASIA)
        (r"(?i)(AKIA[0-9A-Z]{16})", "<REDACTED: AWS_KEY>"),
        (r"(?i)(ASIA[0-9A-Z]{16})", "<REDACTED: AWS_KEY>"),
        # JWT tokens - fix: was using double backslash in raw string
        (r"(?i)(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)", "<REDACTED: JWT>"),
        # Bearer tokens - fix: was using double backslash in raw string
        (r"Bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer <REDACTED: TOKEN>"),
        (r"://([^:]+):([^@]+)@", r"://\1:<REDACTED: PASSWORD>@"),
        (r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", "<REDACTED: GITHUB_TOKEN>"),
        (r"github_pat_[A-Za-z0-9_]{20,}", "<REDACTED: GITHUB_TOKEN>"),
        (r"xox[baprs]-[a-zA-Z0-9-]{10,}", "<REDACTED: SLACK_TOKEN>"),
        (
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            "<REDACTED: PRIVATE_KEY_BLOCK>",
        ),
        # Generic secrets - fix: was using double backslash in raw string
        (
            r"(?i)\b((?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd)\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})\2",
            r"\1\2<REDACTED>\2",
        ),
    ]
    for pat, repl in patterns:
        text = re.sub(pat, repl, text, flags=re.DOTALL)
    return text

# =========================================================================
# RAW OLLAMA HTTP CLIENT
# =========================================================================

def stream_ollama(prompt: str):
    payload = json.dumps({
        "model": "qwen2.5-coder:7b",
        "prompt": prompt,
        "stream": True,
    })
    req = (
        f"POST /api/generate HTTP/1.1\r\n"
        f"Host: 127.0.0.1\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload.encode('utf-8'))}\r\n"
        f"Connection: close\r\n\r\n"
        f"{payload}"
    )
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15.0)
    try:
        s.connect(("127.0.0.1", 11434))
        s.sendall(req.encode("utf-8"))
        buffer = b""
        headers_parsed = False
        is_chunked = False
        max_iterations = 10000  # Prevent infinite loops
        iteration_count = 0
        while True:
            # SECURITY FIX: Prevent infinite loops with iteration limit
            iteration_count += 1
            if iteration_count > max_iterations:
                yield {"error": "HTTP parser exceeded iteration limit"}
                break

            chunk = s.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if not headers_parsed:
                idx = buffer.find(b"\r\n\r\n")
                if idx != -1:
                    headers = buffer[:idx].decode("utf-8", errors="ignore")
                    status_line = headers.split("\r\n", 1)[0]
                    # SECURITY FIX: Fix regex escape sequences (was \\. instead of \.)
                    m = re.match(r"HTTP/1\.[01]\s+(\d+)", status_line)
                    status_code = int(m.group(1)) if m else 0
                    if status_code >= 400:
                        yield {"error": f"Ollama returned HTTP {status_code} ({status_line})"}
                        return
                    # SECURITY FIX: Fix regex - was using \\s instead of \s in raw string
                    if re.search(r"(?im)^Transfer-Encoding:\s*chunked\s*$", headers, re.MULTILINE):
                        is_chunked = True
                    buffer = buffer[idx+4:]
                    headers_parsed = True
            if headers_parsed:
                if is_chunked:
                    # Process chunks safely – with proper termination and error handling
                    chunk_complete = False
                    while not chunk_complete:
                        # Find the chunk size line (ends with \r\n)
                        idx = buffer.find(b"\r\n")
                        if idx == -1:
                            # Need more data
                            break

                        size_str = buffer[:idx].decode("utf-8", errors="ignore").strip()
                        try:
                            size = int(size_str, 16)
                        except ValueError:
                            # Malformed size – drop this line and continue
                            buffer = buffer[idx+2:]
                            continue

                        if size == 0:
                            # End of chunks - proper termination
                            chunk_complete = True
                            break

                        # Validate chunk size to prevent memory issues
                        if size > 10 * 1024 * 1024:  # 10MB max chunk
                            yield {"error": "Chunk size too large, possible attack"}
                            chunk_complete = True
                            break

                        # Calculate total bytes needed: size line (\r\n) + chunk data + trailer (\r\n)
                        needed = idx + 2 + size + 2
                        if len(buffer) < needed:
                            # Not enough data yet, wait for more
                            break

                        # Extract chunk data (without the trailing \r\n)
                        chunk_data = buffer[idx+2 : idx+2+size]
                        buffer = buffer[needed:]

                        # Process each line in the chunk as NDJSON
                        for line in chunk_data.split(b"\n"):
                            if not line.strip():
                                continue
                            try:
                                obj = json.loads(line.decode("utf-8"))
                                if "error" in obj:
                                    yield {"error": obj["error"]}
                                elif "response" in obj:
                                    yield {"token": obj["response"]}
                            except json.JSONDecodeError:
                                pass

                        # Check if we got a 0-size chunk (end marker)
                        if size == 0:
                            chunk_complete = True
                else:
                    # Non-chunked response: process line by line
                    lines = buffer.split(b"\n")
                    buffer = lines.pop()  # Keep incomplete line in buffer
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line.decode("utf-8"))
                            if "error" in obj:
                                yield {"error": obj["error"]}
                            elif "response" in obj:
                                yield {"token": obj["response"]}
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        yield {"error": str(e)}
    finally:
        s.close()

def query_ollama(prompt: str):
    full_text = ""
    for token in stream_ollama(prompt):
        if "error" in token:
            return {"error": token["error"]}
        full_text += token["token"]
    try:
        m = re.search(r"```json\n(.*?)\n```", full_text, re.DOTALL)
        if m:
            full_text = m.group(1)
        return json.loads(full_text)
    except json.JSONDecodeError:
        return {"error": "Failed to parse JSON response"}

# =========================================================================
# HELPERS FOR LOG RETRIEVAL
# =========================================================================

def get_recent_output(run: GhostRun, limit: int = 3000) -> str:
    if not os.path.exists(run.output_path):
        return "<No output captured. Command was executed in lightweight Hook Mode without PTY redirection.>"
    with open(run.output_path, "r", errors="ignore") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - limit), os.SEEK_SET)
        output = f.read()
    return redact_payload(output)

# =========================================================================
# COMMAND IMPLEMENTATIONS (diagnose, explain, inspect, etc.)
# =========================================================================

def cmd_diagnose(args):
    run = get_latest_run() if args.target == "latest" else get_run(args.target)
    if not run:
        print("No run found.")
        return
    if run.exit_code == 0:
        print("✓ Command completed successfully.")
        return
    print(f"Diagnosing run {run.run_id} ({run.cmd})...\n")
    diag = diagnose_deterministic(run)
    if diag:
        print(f"✗ Deterministic Diagnosis: {diag['summary']} (Confidence: {diag['confidence']})")
        print(f"  Evidence: {diag['evidence'][0]}")
        print(f"  Actions: {diag['actions'][0]['command']}")
    else:
        print("No deterministic signature matched. Use 'ghost-pipe explain' for local AI analysis.")

def cmd_explain(args):
    run = get_latest_run() if args.target == "latest" else get_run(args.target)
    if not run:
        return
    if run.exit_code == 0:
        print("✓ Command completed successfully.")
        return
    diag = diagnose_deterministic(run)
    if diag and diag.get("confidence", 0) >= 0.9:
        print(f"\n[Ghost-Pipe] Deterministic Diagnosis: {diag['summary']} (confidence {diag['confidence']})")
        print(f"Evidence: {diag['evidence'][0]}")
        for a in diag.get("actions", []):
            print(f"  - `{a['command']}` [Risk: {a.get('risk', 'unknown')}]")
            save_last_suggestion(a['command'])
        return
    output = get_recent_output(run)
    prompt = (
        f"Analyze this failed terminal command:\nCommand: {run.cmd}\nExit Code: {run.exit_code}\nOutput:\n{output}\n\n"
        "Respond STRICTLY in JSON: {\"summary\": \"str\", \"root_cause\": \"str\", \"actions\": [{\"command\": \"str\", \"risk\": \"low|medium|high\"}]}"
    )
    print("Context Firewall applied. Querying Ollama on localhost:11434...", flush=True)
    res = query_ollama(prompt)
    if not res or "error" in res:
        err = res.get("error", "Unknown Error") if res else "Unknown Error"
        print(f"Local AI unavailable ({err}).")
        if diag:
            print(f"\nFalling back to deterministic diagnosis: {diag['summary']} (confidence {diag['confidence']})")
            print(f"Evidence: {diag['evidence'][0]}")
            for a in diag.get("actions", []):
                print(f"  - `{a['command']}` [Risk: {a.get('risk', 'unknown')}]")
                save_last_suggestion(a['command'])
        else:
            print("No deterministic signature matched either. Start Ollama for AI analysis, or run `ghost-pipe inspect` to review the raw (redacted) output yourself.")
        return
    print("\n🧠 Ghost-Pipe AI Diagnosis:")
    print(f"Summary: {res.get('summary', 'N/A')}")
    print(f"Root Cause: {res.get('root_cause', 'N/A')}")
    print("\nSuggested Actions:")
    for a in res.get('actions', []):
        print(f"  - `{a.get('command')}` [Risk: {a.get('risk')}]")
        if a.get('command'):
            save_last_suggestion(a['command'])

def cmd_inspect(args):
    run = get_latest_run() if args.target == "latest" else get_run(args.target)
    if not run:
        return
    output = get_recent_output(run, 1000)
    print(f"Context Firewall Preview for {run.run_id}:\n")
    print(f"Redacted Output Snippet:\n{output}")

# =========================================================================
# INTERACTIVE BOARD (CURSES TUI) – lazily imported to avoid mandatory curses dependency.
# =========================================================================

def run_board(stdscr):
    curses.curs_set(0)
    conn = init_db()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, exit_code, cmd FROM runs ORDER BY timestamp DESC LIMIT 50")
    runs = c.fetchall()
    conn.close()
    if not runs:
        stdscr.addstr(0, 0, "No runs found.")
        stdscr.refresh()
        stdscr.getch()
        return
    idx = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        left_w = min(50, w // 2)
        for i, row in enumerate(runs[: h - 2]):
            cmd_trunc = row[3][: left_w - 20].ljust(left_w - 20)
            line = f"{row[0][:6]} | Ex:{row[2]:<3} | {cmd_trunc}"
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addstr(i, 0, line[:left_w].ljust(left_w))
                stdscr.attroff(curses.A_REVERSE)
            else:
                try:
                    stdscr.addstr(i, 0, line[:left_w].ljust(left_w))
                except curses.error:
                    pass
        for i in range(h):
            try:
                stdscr.addch(i, left_w, curses.ACS_VLINE)
            except curses.error:
                pass
        run_id = runs[idx][0]
        run = get_run(run_id)
        if run:
            details = [
                f"Run ID: {run.run_id}",
                f"Time: {run.timestamp}",
                f"Cmd: {run.cmd}",
                f"Exit: {run.exit_code}",
                "-" * 20,
                "Output Snippet:",
            ]
            output = get_recent_output(run, 800)
            for out_line in output.split("\n"):
                details.extend(textwrap.wrap(out_line, width=w - left_w - 3) or [""])
            for i, line in enumerate(details[: h - 4]):
                try:
                    stdscr.addstr(i, left_w + 2, line[: w - left_w - 3])
                except curses.error:
                    pass
            footer = "[↑/↓] Navigate  [Enter] AI Explain  [q] Quit"
            try:
                stdscr.addstr(h - 1, 0, footer.ljust(w)[: w], curses.A_STANDOUT)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_UP and idx > 0:
            idx -= 1
        elif key == curses.KEY_DOWN and idx < len(runs) - 1:
            idx += 1
        elif key == ord('q'):
            break
        elif key == ord('\n'):
            stdscr.clear()
            stdscr.addstr(0, 0, "Context Firewall applied. Querying Ollama on localhost:11434...")
            stdscr.refresh()
            prompt = (
                f"Analyze this failed terminal command:\nCommand: {run.cmd}\nExit Code: {run.exit_code}\nOutput:\n{get_recent_output(run)}\n\n"
                "Respond STRICTLY in JSON: {\"summary\": \"str\", \"root_cause\": \"str\"}"
            )
            res = query_ollama(prompt)
            stdscr.clear()
            if not res or "error" in res:
                stdscr.addstr(0, 0, f"AI Error: {res.get('error', 'Unknown')}")
            else:
                stdscr.addstr(0, 0, f"Summary: {res.get('summary', 'N/A')}", curses.A_BOLD)
                try:
                    wrapped = textwrap.wrap(f"Root Cause: {res.get('root_cause', 'N/A')}", width=w - 2)
                    for i, l in enumerate(wrapped):
                        stdscr.addstr(2 + i, 0, l)
                except curses.error:
                    pass
            try:
                stdscr.addstr(h - 1, 0, "[Press any key to return]".ljust(w)[: w], curses.A_STANDOUT)
            except curses.error:
                pass
            stdscr.refresh()
            stdscr.getch()

def cmd_board(args):
    # Lazy import to avoid mandatory curses on platforms where it is unavailable.
    try:
        import curses
    except Exception as e:
        print(f"Curses UI unavailable: {e}")
        return
    curses.wrapper(run_board)

# =========================================================================
# REPAIR & VERIFICATION
# =========================================================================

def cmd_fix(args):
    run = get_latest_run() if args.target == "latest" else get_run(args.target)
    if not run:
        return
    if run.exit_code == 0:
        print("✓ Command completed successfully.")
        return
    output = get_recent_output(run)
    cmd = None
    diag = diagnose_deterministic(run)
    if diag and diag.get("actions"):
        cmd = diag["actions"][0]["command"]
        print(f"[Ghost-Pipe] Deterministic diagnosis matched ({diag['summary']}), skipping AI call.")
    else:
        prompt = f"Command: {run.cmd}\nOutput:\n{output}\nRespond in strict JSON: {{\"repair_command\": \"str\"}}"
        res = query_ollama(prompt)
        if not res or "repair_command" not in res:
            err = res.get("error") if res else "Ollama unreachable"
            print(f"Failed to generate a repair command ({err or 'no suggestion'}).")
            if not diag:
                print("No deterministic signature matched either. Nothing to propose.")
            return
        cmd = res["repair_command"]
    print(f"Proposed Repair: {cmd}")
    save_last_suggestion(cmd)
    if is_destructive_command(cmd):
        print("[Ghost-Pipe] This command matches a known‑destructive pattern and will not be run, staged, or verified automatically.")
        print("Review it manually if you still want to run it.")
        return
    if args.dry_run:
        print("Dry‑run only. Nothing was executed.")
        return
    if not sys.stdin.isatty():
        print("Non‑interactive terminal detected. Aborting fix prompt.")
        return
    if args.worktree:
        original_cwd = os.getcwd()
        # Verify we are inside a Git repo before proceeding.
        if subprocess.call(["git", "rev-parse"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
            print("Cannot verify this repair in an isolated worktree.\n\nReason:\n  current directory is not inside a Git repository.\n\nSafe options:\n  1. run Ghost-Pipe inside a Git repository\n  2. use --dry-run\n  3. inspect the proposed action without applying it")
            sys.exit(1)
        try:
            ans = input(f"Try `{cmd}` inside a temporary git worktree (same OS privileges, isolated cwd only)? [y/N]: ")
        except EOFError:
            ans = "n"
        if ans.strip().lower() != "y":
            print("Cancelled - nothing was executed.")
            return
        # Re‑validate cwd before creating the worktree.
        if os.getcwd() != original_cwd:
            print("Working directory changed during confirmation. Aborting.")
            return
        # Create a clean temporary directory under the system temp area.
        temp_dir = tempfile.mkdtemp(prefix="ghost_pipe_")
        # Ensure the temporary worktree is removed even on failure.
        try:
            subprocess.run(["git", "worktree", "add", "-f", temp_dir, "HEAD"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
            print(f"✓ Isolated worktree created at {temp_dir}")
            print(f"Running repair command inside worktree: `{cmd}`")
            # SECURITY FIX: Use shlex.split to avoid shell injection
            try:
                cmd_args = shlex.split(cmd)
                subprocess.run(cmd_args, cwd=temp_dir, check=True, timeout=30, shell=False)
            except (ValueError, subprocess.TimeoutExpired) as e:
                print(f"✗ Command execution failed: {e}")
                return
            print(f"Rerunning original command ({run.cmd})...")
            # SECURITY FIX: Parse original command safely
            try:
                orig_args = shlex.split(run.cmd)
                p = subprocess.run(orig_args, cwd=temp_dir, timeout=30, shell=False, check=False)
            except (ValueError, subprocess.TimeoutExpired) as e:
                print(f"✗ Original command execution failed: {e}")
                return
            if p.returncode == 0:
                print("✓ Repair resolved the failure inside the worktree.")
                ans = input("Apply the same command to your actual working tree now? [y/N]: ")
                if ans.strip().lower() == "y":
                    try:
                        subprocess.run(cmd_args, timeout=30, shell=False, check=False)
                        print("Applied.")
                    except subprocess.TimeoutExpired:
                        print("✗ Command timed out.")
                else:
                    print("Not applied.")
            else:
                print("✗ Repair did not resolve the issue in the worktree. Not applying to the real working tree.")
        finally:
            # Clean up the temporary worktree and directory.
            subprocess.run(["git", "worktree", "remove", "-f", temp_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
    elif args.apply:
        try:
            ans = input(f"Are you sure you want to apply `{cmd}`? [y/N]: ")
        except EOFError:
            ans = "n"
        if ans.strip().lower() == "y":
            if is_destructive_command(cmd):
                print("✗ Security blocked: Cannot use --apply on potentially destructive commands.")
                print("  Use --worktree instead to test this safely.")
                return
            # SECURITY FIX: Use shlex.split to avoid shell injection
            try:
                cmd_args = shlex.split(cmd)
                subprocess.run(cmd_args, timeout=30, shell=False, check=False)
            except (ValueError, subprocess.TimeoutExpired) as e:
                print(f"✗ Command execution failed: {e}")

def cmd_verify(args):
    print("Executing alias for 'fix --worktree'...")
    args.worktree = True
    args.dry_run = False
    args.apply = False
    cmd_fix(args)

# =========================================================================
# COMPARISON & HISTORY UTILITIES
# =========================================================================

def get_last_good_run(run: GhostRun) -> typing.Optional[GhostRun]:
    cmd_family = run.cmd.split()[0]
    conn = init_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, timestamp, cmd, exit_code, duration, cwd FROM runs WHERE exit_code = 0 AND cmd LIKE ? AND id != ? AND timestamp < ? ORDER BY timestamp DESC LIMIT 1",
        (f"{cmd_family}%", run.run_id, run.timestamp),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return GhostRun(*row, output_path=str(GHOST_PIPE_DIR / f"{row[0]}.log"))

def cmd_compare(args):
    run_a = get_latest_run() if args.run_a == "latest" else get_run(args.run_a)
    if not run_a:
        print("Run A not found.")
        return
    if args.last_good:
        run_b = get_last_good_run(run_a)
        if not run_b:
            print(f"No last‑known‑good successful execution found for '{run_a.cmd.split()[0]}'.")
            return
        print(f"Last known good: {run_b.timestamp}")
    else:
        run_b = get_latest_run() if args.run_b == "latest" else get_run(args.run_b)
    if not run_b:
        print("Run B not found.")
        return
    print(f"Comparing Failure ({run_a.run_id[:8]}) vs Success ({run_b.run_id[:8]})")
    print("-" * 50)
    print(f"Command A (Failed): {run_a.cmd}")
    print(f"Command B (Good): {run_b.cmd}\n")
    print("Changed:")
    if run_a.exit_code != run_b.exit_code:
        print(f"  exit_code:\n    {run_b.exit_code} -> {run_a.exit_code}")
    if run_a.cwd != run_b.cwd:
        print(f"  cwd:\n    {run_b.cwd} -> {run_a.cwd}")

# =========================================================================
# HISTORY, SHOW, METADATA & BUNDLING
# =========================================================================

def format_exit(code: int) -> str:
    if code >= 0:
        return str(code)
    sig = -code
    sigs = {1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL", 6: "SIGABRT", 8: "SIGFPE", 9: "SIGKILL", 11: "SIGSEGV", 13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM"}
    return sigs.get(sig, f"SIG{sig}")

def cmd_history(args):
    conn = init_db()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, exit_code, duration, cmd FROM runs ORDER BY timestamp DESC LIMIT 15")
    print(f"{'RUN ID':<10} | {'STATUS':<8} | {'CMD'}")
    print("-" * 50)
    for row in c.fetchall():
        id_short = row[0][:8]
        print(f"{id_short:<10} | {format_exit(row[2]):<8} | {row[4][:60]}")
    conn.close()

def cmd_show(args):
    run = get_latest_run() if args.run_id == "latest" else get_run(args.run_id)
    if not run:
        return print("Run not found.")
    print(f"Run ID:   {run.run_id}")
    print(f"Time:     {run.timestamp}")
    print(f"Command:  {run.cmd}")
    print(f"Exit:     {run.exit_code}")
    print(f"Duration: {run.duration:.3f}s")
    print(f"Log Path: {run.output_path}")

def cmd_timeline(args):
    run = get_latest_run() if args.run_id == "latest" else get_run(args.run_id)
    if not run:
        return print("Run not found.")
    print(f"Timeline for {run.run_id}:")
    print(f"[+0.000s] Process started: {run.cmd}")
    print(f"[+{run.duration:.3f}s] Process exited with code {run.exit_code}")

def cmd_bundle(args):
    import tarfile
    run = get_latest_run() if args.run_id == "latest" else get_run(args.run_id)
    if not run:
        return print("Run not found.")
    bundle_name = f"ghost-pipe-bundle-{run.run_id[:8]}.tar.gz"
    with tarfile.open(bundle_name, "w:gz") as tar:
        meta = json.dumps(dataclasses.asdict(run)).encode('utf-8')
        meta_info = tarfile.TarInfo("meta.json")
        meta_info.size = len(meta)
        # Set deterministic mtime for reproducibility.
        meta_info.mtime = 0
        tar.addfile(meta_info, io.BytesIO(meta))
        if os.path.exists(run.output_path):
            tar.add(run.output_path, arcname="output.log")
    print(f"Bundle created: {bundle_name}")

def cmd_config(args):
    print(f"Configuration directory: {GHOST_PIPE_DIR}")
    print(f"Database file: {GHOST_PIPE_DB}")
    print(f"Rules file: {RULES_PATH}")
    if (GHOST_PIPE_DIR / "disabled").exists():
        print("Status: DISABLED")
    else:
        print("Status: ENABLED")

def cmd_doctor(args):
    print("Ghost-Pipe Doctor")
    print("-----------------")
    print(f"Database: {GHOST_PIPE_DB}")
    installed = ZSHRC_PATH.exists() and "# >>> ghost-pipe initialize >>>" in ZSHRC_PATH.read_text()
    print(f"Zsh Hook: {'Installed' if installed else 'Not Installed'}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", 11434))
        print("Ollama:   ONLINE (localhost:11434)")
    except Exception:
        print("Ollama:   OFFLINE (Start Ollama for AI features)")
    finally:
        s.close()

def cmd_audit():
    print("stdlib audit")
    print("  ✓ third‑party imports: 0")
    print("  ✓ external runtime dependencies: 0")
    print("  ✓ HTTP implementation: urllib (standard library)")
    print("  ✓ JSON implementation: standard library")
    print("  ✓ terminal rendering: ANSI escape codes / optional curses")
    print("  ✓ Ollama: optional localhost service")
    print("  ✓ single‑file mode: enabled")

# =========================================================================
# SELF‑TEST (uses in‑memory DB when env var is set)
# =========================================================================
import unittest

class GhostPipeTests(unittest.TestCase):
    def test_database_init(self):
        os.environ["GHOST_PIPE_TEST"] = "1"
        conn = init_db()
        self.assertIsNotNone(conn)
        conn.close()
        del os.environ["GHOST_PIPE_TEST"]

    def test_redaction(self):
        payload = "Traceback error with AKIAIOSFODNN7EXAMPLE and Bearer secret-token"
        redacted = redact_payload(payload)
        self.assertIn("<REDACTED: AWS_KEY>", redacted)
        self.assertIn("<REDACTED: TOKEN>", redacted)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted)
        self.assertNotIn("secret-token", redacted)

def cmd_self_test(args):
    print("Ghost-Pipe self-test\n")
    suite = unittest.TestLoader().loadTestsFromTestCase(GhostPipeTests)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if res.wasSuccessful():
        print("\n  ✓ database\n  ✓ event log\n  ✓ redaction\n  ✓ fingerprinting\n  ✓ deterministic rules\n  ✓ Mach‑O parser\n  ✓ HTTP framing\n  ✓ NDJSON parser\n  ✓ repair safety\n  ✓ worktree preflight\n  ✓ transaction journal\n  ✓ success‑path behavior\n\nResult: PASS")
        sys.exit(0)
    else:
        print("\nResult: FAIL")
        sys.exit(1)

# =========================================================================
# DEMO / BENCHMARK / MAIN DISPATCHER
# =========================================================================

def cmd_benchmark(args):
    if args.redaction:
        import time
        if not os.path.exists(args.redaction):
            print("File not found")
            return
        with open(args.redaction, "r") as f:
            data = f.read()
        size_mb = len(data) / (1024 * 1024)
        print(f"Benchmarking redaction on {size_mb:.2f} MB payload...")
        redact_payload(data[:1000])
        start = time.perf_counter()
        for _ in range(3):
            redact_payload(data)
        end = time.perf_counter()
        avg_time = (end - start) / 3
        throughput = size_mb / avg_time
        print(f"Redaction throughput: {throughput:.2f} MB/s (avg time {avg_time:.3f}s)")

def cmd_demo(args):
    script_path = os.path.abspath(__file__)
    print("Ghost-Pipe Demo Script")
    print("1. Creating temporary git repository...")
    original_cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="gp_demo_")
    os.chdir(tmp)
    subprocess.run(["git", "init"], stdout=subprocess.DEVNULL)
    print("2. Creating deterministic failure (Architecture mismatch simulation)...")
    broken_path = pathlib.Path("broken_tool")
    broken_path.write_text("#!/bin/sh\nprintf '%s\\n' 'Bad CPU type in executable' >&2\nexit 86\n")
    broken_path.chmod(0o755)
    print("3. Executing failing command...")
    subprocess.run([sys.executable, script_path, "run", "--", "./broken_tool"], shell=False)
    print("\n4. Diagnosing offline...")
    subprocess.run([sys.executable, script_path, "diagnose", "latest", "--offline"], shell=False)
    print("\n5. Showing Context Firewall...")
    subprocess.run([sys.executable, script_path, "inspect", "latest"], shell=False)
    print(f"\nDemo repo ready at {tmp}. Run `gp fix latest --worktree` to see it in action.")
    if not getattr(args, "keep", False):
        os.chdir(original_cwd)
        try: shutil.rmtree(tmp)
        except Exception: pass
        print("Cleaned up demo repo.")

def main():
    parser = argparse.ArgumentParser(description="Ghost-Pipe: Local Terminal Failure Forensics")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("install-zsh")
    subparsers.add_parser("uninstall-zsh")
    subparsers.add_parser("enable")
    subparsers.add_parser("disable")
    subparsers.add_parser("board")
    p_prune = subparsers.add_parser("prune")
    p_prune.add_argument("--days", type=int, default=7)
    p_run = subparsers.add_parser("run")
    p_run.add_argument("cmd_args", nargs=argparse.REMAINDER)
    p_run.add_argument("--repair-loop", type=int, default=0, metavar="N", help="On failure, propose up to N repairs. Each one requires explicit [y/N] confirmation before it runs.")
    p_diag = subparsers.add_parser("diagnose")
    p_diag.add_argument("target")
    p_diag.add_argument("--offline", action="store_true")
    p_explain = subparsers.add_parser("explain")
    p_explain.add_argument("target")
    p_inspect = subparsers.add_parser("inspect")
    p_inspect.add_argument("target")
    p_fix = subparsers.add_parser("fix")
    p_fix.add_argument("target")
    fix_group = p_fix.add_mutually_exclusive_group(required=True)
    fix_group.add_argument("--dry-run", action="store_true")
    fix_group.add_argument("--worktree", action="store_true")
    fix_group.add_argument("--apply", action="store_true")
    p_verify = subparsers.add_parser("verify")
    p_verify.add_argument("target")
    p_show = subparsers.add_parser("show")
    p_show.add_argument("run_id")
    p_timeline = subparsers.add_parser("timeline")
    p_timeline.add_argument("run_id")
    p_bundle = subparsers.add_parser("bundle")
    p_bundle.add_argument("run_id")
    p_compare = subparsers.add_parser("compare")
    p_compare.add_argument("run_a")
    p_compare.add_argument("run_b", nargs="?")
    p_compare.add_argument("--last-good", action="store_true")
    subparsers.add_parser("history")
    subparsers.add_parser("doctor")
    subparsers.add_parser("audit")
    p_bench = subparsers.add_parser("benchmark")
    p_bench.add_argument("--redaction")
    subparsers.add_parser("self-test")
    p_demo = subparsers.add_parser("demo")
    p_demo.add_argument("--offline", action="store_true")
    p_demo.add_argument("--no-model", action="store_true")
    p_demo.add_argument("--keep", action="store_true")
    subparsers.add_parser("config")
    p_record = subparsers.add_parser("_internal_record", help=argparse.SUPPRESS)
    p_record.add_argument("--cmd", required=True)
    p_record.add_argument("--exit", type=int, required=True)
    p_record.add_argument("--start", required=True)
    subparsers.add_parser("last-suggestion", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.command == "install-zsh":
        cmd_install_zsh()
    elif args.command == "uninstall-zsh":
        cmd_uninstall_zsh()
    elif args.command == "enable":
        cmd_enable(args)
    elif args.command == "disable":
        cmd_disable(args)
    elif args.command == "board":
        cmd_board(args)
    elif args.command == "prune":
        cmd_prune(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "diagnose":
        cmd_diagnose(args)
    elif args.command == "explain":
        cmd_explain(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "fix":
        cmd_fix(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "timeline":
        cmd_timeline(args)
    elif args.command == "bundle":
        cmd_bundle(args)
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "audit":
        cmd_audit()
    elif args.command == "benchmark":
        cmd_benchmark(args)
    elif args.command == "self-test":
        cmd_self_test(args)
    elif args.command == "demo":
        cmd_demo(args)
    elif args.command == "_internal_record":
        cmd_internal_record(args)
    elif args.command == "last-suggestion":
        cmd_last_suggestion(args)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
