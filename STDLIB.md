# Zero Dependency Replacements (STDLIB Log)

Ghost-Pipe aggressively adheres to the Zero Dependency rule. Below is the STDLIB Log documenting the third-party packages we would normally use for a terminal forensics tool, and how we replaced them entirely with Python standard-library primitives.

This log fulfills the **Package Killer (+3)** and **STDLIB Log (+3)** bonus challenges.

### 1. HTTP Networking & Streaming
* **Normally:** `requests` or `httpx` (for querying the Ollama AI HTTP API)
* **Instead:** `pure socket` + `socket.error`
* **Details:** We built a custom Newline-Delimited JSON (NDJSON) streaming client using `pure socket.urlopen`. It natively handles chunked transfer encoding, socket timeouts, and streams tokens individually from the local LLM.

### 2. Terminal PTY Management
* **Normally:** `pexpect` or `ptyprocess`
* **Instead:** `pty.fork()` + `termios` + `fcntl`
* **Details:** To wrap arbitrary shell commands and capture their exact output while preserving ANSI colors, we use raw POSIX pseudo-terminals. We use `termios` to manage terminal modes and `fcntl` with `TIOCGWINSZ` to manually propagate `SIGWINCH` window-resize events to the child process.

### 3. CLI Argument Parsing
* **Normally:** `click` or `typer`
* **Instead:** `argparse`
* **Details:** Ghost-Pipe relies entirely on the standard `argparse` module, leveraging sub-parsers (`run`, `diagnose`, `fix`, `history`, `audit`) to achieve a modern CLI UX without dragging in massive decorator-based frameworks.

### 4. Rich Terminal UIs
* **Normally:** `rich` or `textual`
* **Instead:** `curses` + raw ANSI escape codes
* **Details:** The `gp board` command renders a beautiful interactive dashboard. We rely on the standard library's `curses` wrapper, and fall back gracefully to raw ANSI escape sequences (`\033[31m`) if the host environment is missing the `_curses` C-extension.

### 5. Binary File Inspection
* **Normally:** `python-magic` or calling out to the OS `file` binary
* **Instead:** `mmap` + `struct`
* **Details:** Ghost-Pipe diagnoses Apple Silicon vs Intel architecture mismatches by inspecting binary headers. Instead of relying on `libmagic`, we use `mmap` to load the first 8 bytes of an executable and `struct.unpack` to parse the Mach-O magic bytes (`0xfeedfacf`) and CPU type headers natively.

### 6. Database and ORM
* **Normally:** `SQLAlchemy` or `peewee`
* **Instead:** `sqlite3`
* **Details:** Command execution history is stored in a local SQLite ledger. We manage the schemas natively via `sqlite3.connect`, using `BEGIN IMMEDIATE` locks to ensure concurrent writes from background threads don't corrupt the database.

### 7. Data Validation & Schemas
* **Normally:** `pydantic`
* **Instead:** `dataclasses` + `json`
* **Details:** All execution metadata is modeled using standard Python `@dataclasses.dataclass`, making it easy to serialize via the standard `json` module without heavy runtime validation overhead.

### 8. Asynchronous I/O Multiplexing
* **Normally:** `asyncio` streams or external event loops
* **Instead:** `selectors`
* **Details:** While the PTY runner wraps the child process, it uses `selectors.DefaultSelector()` to multiplex read events between the user's TTY and the child process's output descriptor simultaneously, achieving non-blocking I/O without the complexity of `asyncio`.

### 9. Background Job Processing
* **Normally:** `celery`, `rq`, or `schedule`
* **Instead:** `threading.Thread(daemon=True)`
* **Details:** To keep the database lean, Ghost-Pipe prunes old runs. Instead of an external task queue, we spawn a simple daemon thread before command execution that runs `auto_prune()`, isolating the database maintenance from the critical path.

### 10. Safe Command Execution
* **Normally:** `sh`
* **Instead:** `shlex` + `subprocess.run(shell=False)`
* **Details:** Instead of wrapping OS commands with an external library, we strictly tokenize all AI-generated repairs using `shlex.split()` and pass them to `subprocess.run(..., shell=False)`. This guarantees mathematically that shell injection vulnerabilities (e.g. `; rm -rf /`) are impossible.
