"""
cli/main.py

Entry point for the `agentic-research` command.

Usage:
    agentic-research init                — copy deployment files and configure LLM credentials
    agentic-research init-planka-board   — create Planka project/board/lists and write token to .env
"""

import shutil
import sys
from importlib.resources import as_file, files
from pathlib import Path

# Known CLI credential file locations (on the host machine)
_CLAUDE_CRED  = Path.home() / ".claude"
_GEMINI_CRED  = Path.home() / ".gemini"
_CODEX_CRED   = Path.home() / ".codex"


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------

def _ask_yn(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def _ask_str(prompt: str, default: str = "") -> str:
    display = f"  {prompt} [{default}]: " if default else f"  {prompt}: "
    raw = input(display).strip()
    return raw if raw else default


def _mask(key: str) -> str:
    """Show only first 8 chars of a key for confirmation."""
    return key[:8] + "..." if len(key) > 8 else key


# ---------------------------------------------------------------------------
# Per-provider configuration
# ---------------------------------------------------------------------------

def _configure_claude() -> tuple[list[str], dict[str, str]]:
    """Returns (chain_entries, env_updates)."""
    print()
    print("  Claude (Anthropic)")

    if _CLAUDE_CRED.exists():
        print(f"    ✓ Found credentials at {_CLAUDE_CRED}")
        print("      Will use claude-cli mode.")
        print("      Tip: mount ~/.claude into the container to share credentials.")
        return ["claude-cli"], {}

    print("    No local CLI credentials found.")
    mode = None
    while mode not in ("cli", "api", ""):
        mode = input("    Mode — (c)li / (a)pi / skip [skip]: ").strip().lower()
        if mode in ("c", "cli"):
            mode = "cli"
        elif mode in ("a", "api"):
            mode = "api"
        elif mode == "":
            mode = ""
        else:
            mode = None

    if mode == "cli":
        print("    Will use claude-cli. After starting containers, run:")
        print("      docker exec -it agentic-framework-api claude auth login")
        return ["claude-cli"], {}

    if mode == "api":
        key = _ask_str("    Anthropic API key")
        if key:
            print(f"    ✓ Key set ({_mask(key)})")
            return ["claude-api"], {"ANTHROPIC_API_KEY": key}
        print("    Skipped (you can set ANTHROPIC_API_KEY in .env later).")

    return [], {}


def _configure_gemini() -> tuple[list[str], dict[str, str]]:
    print()
    print("  Gemini (Google)")

    if _GEMINI_CRED.exists():
        print(f"    ✓ Found credentials at {_GEMINI_CRED}")
        print("      Will use gemini-cli mode.")
        return ["gemini-cli"], {}

    print("    No local CLI credentials found.")
    mode = None
    while mode not in ("cli", "api", ""):
        mode = input("    Mode — (c)li / (a)pi / skip [skip]: ").strip().lower()
        if mode in ("c", "cli"):
            mode = "cli"
        elif mode in ("a", "api"):
            mode = "api"
        elif mode == "":
            mode = ""
        else:
            mode = None

    if mode == "cli":
        print("    Will use gemini-cli. After starting containers, run:")
        print("      docker exec -it agentic-framework-api gemini auth login")
        return ["gemini-cli"], {}

    if mode == "api":
        key = _ask_str("    Gemini API key")
        if key:
            print(f"    ✓ Key set ({_mask(key)})")
            return ["gemini-api"], {"GEMINI_API_KEY": key}
        print("    Skipped (you can set GEMINI_API_KEY in .env later).")

    return [], {}


def _configure_openai() -> tuple[list[str], dict[str, str]]:
    """OpenAI API + Codex CLI share the same OPENAI_API_KEY."""
    print()
    print("  OpenAI / Codex")

    if _CODEX_CRED.exists():
        print(f"    ✓ Found Codex credentials at {_CODEX_CRED}")
        print("      Will use codex-cli mode.")
        return ["codex-cli"], {}

    print("    No local Codex credentials found.")
    mode = None
    while mode not in ("cli", "api", ""):
        mode = input("    Mode — (c)odex cli / (a)pi key / skip [skip]: ").strip().lower()
        if mode in ("c", "cli"):
            mode = "cli"
        elif mode in ("a", "api"):
            mode = "api"
        elif mode == "":
            mode = ""
        else:
            mode = None

    if mode == "cli":
        print("    Will use codex-cli. After starting containers, run:")
        print("      docker exec -it agentic-framework-api codex login")
        return ["codex-cli"], {}

    if mode == "api":
        key = _ask_str("    OpenAI API key")
        if key:
            print(f"    ✓ Key set ({_mask(key)})")
            return ["openai-api"], {"OPENAI_API_KEY": key}
        print("    Skipped (you can set OPENAI_API_KEY in .env later).")

    return [], {}


def _configure_local() -> tuple[list[str], dict[str, str]]:
    print()
    print("  Local LLM (Ollama)")
    endpoint = _ask_str("    Ollama endpoint", default="http://host.docker.internal:11434")
    model    = _ask_str("    Model name", default="llama3.2")
    return ["local"], {"LOCAL_LLM_ENDPOINT": endpoint, "LOCAL_LLM_MODEL": model}


# ---------------------------------------------------------------------------
# LLM configuration orchestrator
# ---------------------------------------------------------------------------

def _configure_llm() -> dict[str, str]:
    """Walk through each provider and return env var overrides."""
    print()
    print("─── LLM Configuration ───────────────────────────────────────────────")
    print("  Configure which LLM providers to use.")
    print("  For CLI mode, credentials must be present in (or mounted into) the container.")
    print()

    chain: list[str] = []
    env_updates: dict[str, str] = {}

    providers = [
        ("Claude (Anthropic)",   _configure_claude),
        ("Gemini (Google)",      _configure_gemini),
        ("OpenAI / Codex",       _configure_openai),
        ("Local LLM (Ollama)",   _configure_local),
    ]

    for label, configure_fn in providers:
        if _ask_yn(f"Use {label}?", default=label.startswith("Claude")):
            entries, updates = configure_fn()
            chain.extend(entries)
            env_updates.update(updates)

    print()
    if chain:
        env_updates["LLM_CHAIN"] = ",".join(chain)
        print(f"  LLM_CHAIN = {env_updates['LLM_CHAIN']}")
    else:
        env_updates["LLM_CHAIN"] = ""
        print("  Warning: no LLM configured — edit LLM_CHAIN in .env before starting.")

    return env_updates


# ---------------------------------------------------------------------------
# .env patching
# ---------------------------------------------------------------------------

def _patch_env(content: str, updates: dict[str, str]) -> str:
    """Replace KEY=value lines in .env content. Preserves comments and order."""
    lines = content.splitlines(keepends=True)
    patched: set[str] = set()
    result = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            result.append(f"{key}={updates[key]}\n")
            patched.add(key)
        else:
            result.append(line)

    # Append any keys not already present in the template
    for key, val in updates.items():
        if key not in patched:
            result.append(f"{key}={val}\n")

    return "".join(result)


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------

def _init(dest: Path) -> None:
    deploy_pkg = files("deploy")
    static_files = ["docker-compose.yml", "schema.sql"]
    copied: list[str] = []
    skipped: list[str] = []

    # Copy static files
    for fname in static_files:
        dst = dest / fname
        if dst.exists():
            skipped.append(fname)
        else:
            with as_file(deploy_pkg.joinpath(fname)) as src:
                shutil.copy2(src, dst)
            copied.append(fname)

    # .env: interactive LLM config if new, skip if already exists
    env_dst = dest / ".env"
    if env_dst.exists():
        skipped.append(".env")
    else:
        env_updates = _configure_llm()
        raw = deploy_pkg.joinpath(".env").read_text(encoding="utf-8")
        env_dst.write_text(_patch_env(raw, env_updates), encoding="utf-8")
        copied.append(".env")

    # Summary
    print()
    print("─── Files ───────────────────────────────────────────────────────────")
    all_files = static_files + [".env"]
    width = max(len(f) for f in all_files)
    for f in copied:
        print(f"  created  {f:<{width}}  →  {dest / f}")
    for f in skipped:
        print(f"  skipped  {f:<{width}}  (already exists)")

    if not copied:
        print("\n  Nothing was changed.")
        return

    print("""
─── Next steps ───────────────────────────────────────────────────────────
  1. Edit .env
       - VOLUME_BASE_DIR   path where Docker volume data will be stored
       - SECRET_KEY        openssl rand -hex 32

  2. Start services
       docker compose up -d

  3. Run database migration
       docker exec -i agentic-research-postgres psql -U agentic-postgres-user -d agentic-research < schema.sql

  4. Open Planka  →  http://localhost:7002
       Log in with DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD from .env
       Accept the Terms of Service (first login only)

  5. Create board and write token to .env
       agentic-research init-planka-board

  6. Restart engine to apply Planka settings
       docker compose restart agentic-framework-api
""")


# ---------------------------------------------------------------------------
# Planka board initialisation
# ---------------------------------------------------------------------------

_PLANKA_LISTS = [
    ("Planning",           10000),
    ("Spec Pending Review", 20000),
    ("Verify",             25000),
    ("Review",             30000),
    ("Done",               40000),
    ("Failed",             50000),
]

_PLANKA_CUSTOM_FIELDS = [
    ("max_loops", "number"),
]


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file, skipping comments and blanks."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env


def _planka_login(base_url: str, email: str, password: str) -> str:
    """Login to Planka and return a bearer token. Handles terms-acceptance step."""
    import httpx

    resp = httpx.post(
        f"{base_url}/api/access-tokens",
        json={"emailOrUsername": email, "password": password},
        timeout=10,
    )
    data = resp.json()

    # First-time login requires accepting terms of service in the browser.
    if data.get("step") == "accept-terms":
        print()
        print("  Planka requires you to accept the Terms of Service before the API can be used.")
        print(f"  Open {base_url} in your browser, log in, accept the terms, then come back.")
        input("  Press Enter once you have accepted the terms...")
        resp = httpx.post(
            f"{base_url}/api/access-tokens",
            json={"emailOrUsername": email, "password": password},
            timeout=10,
        )
        data = resp.json()

    token = data.get("item")
    if not token:
        raise RuntimeError(f"Login failed: {data.get('message') or resp.text[:200]}")
    return token


def _init_planka_board(dest: Path) -> None:
    import httpx

    env_path = dest / ".env"
    if not env_path.exists():
        print("  No .env found in current directory. Run 'agentic-research init' first.",
              file=sys.stderr)
        sys.exit(1)

    env = _load_dotenv(env_path)

    base_url  = env.get("PLANKA_API_URL", "http://localhost:7002").rstrip("/")
    email     = env.get("DEFAULT_ADMIN_EMAIL", "agentic@local.dev")
    password  = env.get("DEFAULT_ADMIN_PASSWORD", "agentic-planka-pwd")

    # Warn if already configured
    if env.get("PLANKA_TOKEN") and env.get("PLANKA_BOARD_ID"):
        if not _ask_yn("PLANKA_TOKEN and PLANKA_BOARD_ID already set in .env. Re-initialize?",
                       default=False):
            print("  Skipped.")
            return

    # ── 1. Login ────────────────────────────────────────────────────────────
    print()
    print(f"  Connecting to Planka at {base_url} ...")
    try:
        token = _planka_login(base_url, email, password)
    except Exception as e:
        print(f"  Login failed: {e}", file=sys.stderr)
        sys.exit(1)
    print("  ✓ Logged in")

    headers = {"Authorization": f"Bearer {token}"}

    # ── 2. Create Planka project ─────────────────────────────────────────────
    proj_name = _ask_str("  Planka project name", default="Agentic Research")
    resp = httpx.post(
        f"{base_url}/api/projects",
        headers=headers,
        json={"name": proj_name, "type": "shared"},
        timeout=10,
    )
    resp.raise_for_status()
    project_id = resp.json()["item"]["id"]
    print(f"  ✓ Project '{proj_name}' created  (id: {project_id})")

    # ── 3. Create board ──────────────────────────────────────────────────────
    board_name = _ask_str("  Board name", default="Research Workflow")
    resp = httpx.post(
        f"{base_url}/api/projects/{project_id}/boards",
        headers=headers,
        json={"name": board_name, "position": 1},
        timeout=10,
    )
    resp.raise_for_status()
    board_id = resp.json()["item"]["id"]
    print(f"  ✓ Board '{board_name}' created  (id: {board_id})")

    # ── 4. Create lists (columns) ────────────────────────────────────────────
    resp = httpx.get(f"{base_url}/api/boards/{board_id}", headers=headers, timeout=10)
    resp.raise_for_status()
    existing_lists = {
        lst["name"]
        for lst in (resp.json().get("included", {}).get("lists") or [])
    }
    for name, position in _PLANKA_LISTS:
        if name in existing_lists:
            print(f"  – List '{name}' already exists, skipping")
            continue
        r = httpx.post(
            f"{base_url}/api/boards/{board_id}/lists",
            headers=headers,
            json={"name": name, "position": position, "type": "active"},
            timeout=10,
        )
        r.raise_for_status()
        print(f"  ✓ List '{name}' created")

    # ── 5. Create custom fields ──────────────────────────────────────────────
    # New Planka API: fields live inside a group.
    # Step 5a: ensure the group exists
    resp = httpx.get(f"{base_url}/api/boards/{board_id}", headers=headers, timeout=10)
    resp.raise_for_status()
    included      = resp.json().get("included", {})
    existing_groups = included.get("customFieldGroups") or []
    existing_fields = {cf["name"] for cf in (included.get("customFields") or [])}

    group_name = "Research Config"
    group_id   = next((g["id"] for g in existing_groups if g["name"] == group_name), None)
    if group_id:
        print(f"  – Custom field group '{group_name}' already exists, skipping")
    else:
        r = httpx.post(
            f"{base_url}/api/boards/{board_id}/custom-field-groups",
            headers=headers,
            json={"name": group_name, "position": 1},
            timeout=10,
        )
        r.raise_for_status()
        group_id = r.json()["item"]["id"]
        print(f"  ✓ Custom field group '{group_name}' created")

    # Step 5b: create each field under the group
    for position, (name, _ftype) in enumerate(_PLANKA_CUSTOM_FIELDS, start=1):
        if name in existing_fields:
            print(f"  – Custom field '{name}' already exists, skipping")
            continue
        r = httpx.post(
            f"{base_url}/api/custom-field-groups/{group_id}/custom-fields",
            headers=headers,
            json={"name": name, "position": position},
            timeout=10,
        )
        r.raise_for_status()
        print(f"  ✓ Custom field '{name}' created")

    # ── 6. Create webhook ────────────────────────────────────────────────────
    # Default: internal Docker service name so Planka can reach the engine.
    default_webhook_url = env.get(
        "FRAMEWORK_WEBHOOK_URL", "http://langgraph-engine:8000/planka-webhook"
    )
    webhook_url = _ask_str("  Webhook URL (Planka→Framework)", default=default_webhook_url)

    # Check if a webhook with same URL already exists (webhooks are global in Planka)
    resp = httpx.get(f"{base_url}/api/webhooks", headers=headers, timeout=10)
    existing_webhooks = resp.json().get("items", []) if resp.status_code == 200 else []
    existing_webhook = next((w for w in existing_webhooks if w.get("url") == webhook_url), None)

    if existing_webhook:
        print(f"  – Webhook '{webhook_url}' already exists, skipping")
    else:
        r = httpx.post(
            f"{base_url}/api/webhooks",
            headers=headers,
            json={
                "name": "agentic-research",
                "url": webhook_url,
                "events": "cardUpdate",
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            print(f"  ✓ Webhook created  → {webhook_url}")
        else:
            print(f"  ✗ Webhook creation failed ({r.status_code}): {r.text[:120]}")
            print("    Set it manually: Planka → Admin → Webhooks → Create Webhook")
            print(f"    URL: {webhook_url}  |  Events: cardUpdate")

    # ── 7. Write PLANKA_TOKEN and PLANKA_BOARD_ID back to .env ──────────────
    raw = env_path.read_text(encoding="utf-8")
    raw = _patch_env(raw, {"PLANKA_TOKEN": token, "PLANKA_BOARD_ID": board_id})
    env_path.write_text(raw, encoding="utf-8")
    print()
    print("  ✓ PLANKA_TOKEN  written to .env")
    print("  ✓ PLANKA_BOARD_ID written to .env")
    print()
    print("  Next: restart the engine to pick up the new settings")
    print("    docker compose restart agentic-framework-api")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("Usage: agentic-research <command>")
        print()
        print("Commands:")
        print("  init                Copy deployment files to current directory and configure LLM")
        print("  init-planka-board   Create Planka project/board/lists and write token to .env")
        sys.exit(0)

    if args[0] == "init":
        _init(Path.cwd())
    elif args[0] == "init-planka-board":
        _init_planka_board(Path.cwd())
    else:
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        print("Run 'agentic-research --help' for usage.", file=sys.stderr)
        sys.exit(1)
