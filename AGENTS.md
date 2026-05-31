# AGENTS.md

## Cursor Cloud specific instructions

### Project Overview

Cornerstone Remote Control is a pure Python (3.8+ stdlib only, no third-party deps) suite for remotely controlling LECO Cornerstone scientific instruments. Three packages form a layered architecture:

- **`CornerstoneCLI/`** — Protocol + TCP/HTTP communication core (`cornerstone-cli`)
- **`CornerstoneBridge/`** — TCP gateway + REST API (`cornerstone-bridge`)
- **`CornerstoneWeb/`** — Static SPA + `/api/*` reverse proxy (`cornerstone-web`)

### Running the Application

One-command dev startup (Bridge + Web in one process):

```bash
cd CornerstoneWeb
python3 -m cornerstone_web.dev_web
```

Requires a `cornerstone-web.config.json` in CWD (copy from `cornerstone-web.config.example.json`). The `upstream_host`/`upstream_port` point to a real Cornerstone instrument; without one, the Bridge will log a connection failure but still serve the Web UI and REST API.

Key ports (defaults):
- Web UI: `http://127.0.0.1:8080/`
- Bridge REST API: `http://127.0.0.1:8081/`
- TCP Gateway: `0.0.0.0:54321`

### Lint / Test

No lint tooling (flake8, ruff, mypy, etc.) or test suite is configured in this repo. Basic validation: `python3 -m py_compile <file>` for syntax, `python3 -c "import cornerstone_cli; import cornerstone_bridge; import cornerstone_web"` for import checks.

### Important Caveats

- Use `python3` not `python` (the system may not have an unversioned `python` symlink).
- Scripts are installed to `~/.local/bin`; ensure `PATH` includes it.
- The upstream instrument connection will always fail in Cloud Agent environments (no real instrument). This is normal — the web UI and API still function, just API calls that require instrument data will return errors.
- Config file resolution: env var `CORNERSTONE_WEB_CONFIG` > CWD `cornerstone-web.config.json` > example file fallback.
