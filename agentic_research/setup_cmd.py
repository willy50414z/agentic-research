"""
agentic_research/setup_cmd.py

`agentic-research setup` — one-time global infra setup per machine.

Actions:
  1. Create ~/.agentic-research/ directory structure
  2. Copy docker-compose.yml for global stack (4 services)
  3. Interactive LLM credential discovery (claude → codex → gemini → local)
  4. Write ~/.agentic-research/.env
  5. docker compose up -d
  6. Create Planka board + columns
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

_GLOBAL_DIR = Path.home() / ".agentic-research"
_ENV_FILE = _GLOBAL_DIR / ".env"
_COMPOSE_FILE = _GLOBAL_DIR / "docker-compose.yml"
_DATA_DIR = _GLOBAL_DIR / "data"

# Source compose file (bundled with the package)
_PKG_DIR = Path(__file__).parent
_COMPOSE_TEMPLATE = _PKG_DIR / "templates" / "docker-compose.global.yml"


def setup():
    """
    One-time global infrastructure setup.
    Creates ~/.agentic-research/, starts 4 Docker services, configures LLM credentials.
    """
    typer.echo("\n─── Agentic Research Setup ───────────────────────────────────")

    # 1. Create directory structure
    _GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    (_DATA_DIR / "postgres").mkdir(parents=True, exist_ok=True)
    (_DATA_DIR / "mlflow").mkdir(parents=True, exist_ok=True)
    typer.echo(f"✓ Created {_GLOBAL_DIR}")

    # 2. Copy docker-compose.yml
    if _COMPOSE_TEMPLATE.exists():
        shutil.copy(_COMPOSE_TEMPLATE, _COMPOSE_FILE)
    else:
        _write_default_compose(_COMPOSE_FILE)
    typer.echo(f"✓ docker-compose.yml → {_COMPOSE_FILE}")

    # 3. Interactive LLM credential setup
    typer.echo("\n─── LLM Configuration ────────────────────────────────────────")
    typer.echo("Checking providers in priority order: claude → codex → gemini → local\n")

    env_vars: dict[str, str] = {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/agentic-research",
        "MLFLOW_TRACKING_URI": "http://localhost:5000",
        "FRAMEWORK_API_URL": "http://localhost:7001",
        "AGENTIC_HOME": str(_GLOBAL_DIR),
        "VOLUME_BASE_DIR": str(_DATA_DIR),
    }
    llm_chain: list[str] = []

    # Claude
    typer.echo("[1] Claude")
    claude_cred = Path.home() / ".claude" / "credentials"
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if claude_cred.exists():
        typer.echo(f"    ✓ Found: {claude_cred}")
        use = typer.confirm("    Use as primary?", default=True)
        if use:
            env_vars["CLAUDE_CREDENTIALS_PATH"] = str(claude_cred)
            llm_chain.append("claude")
    elif anthropic_key:
        typer.echo(f"    ✓ Found ANTHROPIC_API_KEY in environment")
        use = typer.confirm("    Use as primary?", default=True)
        if use:
            env_vars["ANTHROPIC_API_KEY"] = anthropic_key
            llm_chain.append("claude")
    else:
        typer.echo("    ✗ No credentials found")
        key = typer.prompt("    Enter Anthropic API key (or Enter to skip)", default="")
        if key.strip():
            env_vars["ANTHROPIC_API_KEY"] = key.strip()
            llm_chain.append("claude")

    # Codex / OpenAI
    typer.echo("\n[2] Codex / OpenAI (fallback)")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    codex_dir = Path.home() / ".codex"
    if openai_key:
        typer.echo(f"    ✓ Found OPENAI_API_KEY in environment")
        use = typer.confirm("    Use as fallback?", default=True)
        if use:
            env_vars["OPENAI_API_KEY"] = openai_key
            llm_chain.append("codex")
    elif codex_dir.exists():
        typer.echo(f"    ✓ Found: {codex_dir}")
        use = typer.confirm("    Use as fallback?", default=True)
        if use:
            llm_chain.append("codex")
    else:
        typer.echo("    ✗ OPENAI_API_KEY not set, ~/.codex/ not found")
        key = typer.prompt("    Enter OpenAI API key (or Enter to skip)", default="")
        if key.strip():
            env_vars["OPENAI_API_KEY"] = key.strip()
            llm_chain.append("codex")

    # Gemini
    typer.echo("\n[3] Gemini (fallback)")
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    gcloud_dir = Path.home() / ".config" / "gcloud"
    if gemini_key:
        typer.echo(f"    ✓ Found Gemini API key in environment")
        use = typer.confirm("    Use as fallback?", default=True)
        if use:
            env_vars["GEMINI_API_KEY"] = gemini_key
            llm_chain.append("gemini")
    elif gcloud_dir.exists():
        typer.echo(f"    ✓ Found: {gcloud_dir}")
        use = typer.confirm("    Use as fallback?", default=True)
        if use:
            llm_chain.append("gemini")
    else:
        typer.echo("    ✗ No Gemini credentials found")
        key = typer.prompt("    Enter Gemini API key (or Enter to skip)", default="")
        if key.strip():
            env_vars["GEMINI_API_KEY"] = key.strip()
            llm_chain.append("gemini")

    # Local LLM
    typer.echo("\n[4] OpenCode / Local LLM (fallback)")
    local_endpoint = typer.prompt("    Endpoint", default="http://localhost:11434")
    import httpx
    reachable = False
    try:
        httpx.get(local_endpoint, timeout=3)
        reachable = True
    except Exception:
        pass
    if reachable:
        typer.echo("    ✓ Reachable")
    else:
        typer.echo("    ✗ Not reachable (will skip at runtime)")
    local_model = typer.prompt("    Model name", default="llama3.2")
    env_vars["LOCAL_LLM_ENDPOINT"] = local_endpoint
    env_vars["LOCAL_LLM_MODEL"] = local_model
    llm_chain.append("local")

    env_vars["LLM_CHAIN"] = ",".join(llm_chain)
    chain_str = " → ".join(llm_chain) if llm_chain else "(none)"
    typer.echo(f"\nLLM chain: {chain_str}")
    typer.echo("─────────────────────────────────────────────────────────────")

    # 4. Write .env
    _write_env(_ENV_FILE, env_vars)
    typer.echo(f"\n✓ Written: {_ENV_FILE}")

    # 5. Write teardown.sh
    _write_teardown(_GLOBAL_DIR / "teardown.sh")

    # 6. docker compose up -d
    typer.echo("\nStarting Docker services...")
    _compose_up()

    # 7. Create Planka board + columns
    typer.echo("Setting up Planka board...")
    _setup_planka(env_vars)

    typer.echo("\n✓ Setup complete.")
    typer.echo(f"  Config dir : {_GLOBAL_DIR}")
    typer.echo(f"  .env       : {_ENV_FILE}")
    typer.echo("  Services   : postgres | mlflow | planka | framework-api")
    typer.echo("\nNext step: agentic-research init <project-name>")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_env(path: Path, vars: dict) -> None:
    lines = [
        "# Auto-generated by `agentic-research setup` — DO NOT commit",
        "",
        "# LLM chain (ordered by priority)",
    ]
    lines.append(f"LLM_CHAIN={vars.get('LLM_CHAIN', '')}")
    lines.append("")
    lines.append("# Credentials")
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_CREDENTIALS_PATH",
              "OPENAI_API_KEY", "GEMINI_API_KEY",
              "LOCAL_LLM_ENDPOINT", "LOCAL_LLM_MODEL"):
        if k in vars:
            lines.append(f"{k}={vars[k]}")
    lines.append("")
    lines.append("# Infrastructure")
    for k in ("DATABASE_URL", "MLFLOW_TRACKING_URI", "FRAMEWORK_API_URL",
              "AGENTIC_HOME", "VOLUME_BASE_DIR"):
        if k in vars:
            lines.append(f"{k}={vars[k]}")
    lines.append("")
    lines.append("# Postgres (used by planka + postgres container)")
    lines.append("DATABASE=agentic-research")
    lines.append("DATABASE_URL_FOR_PLANKA=postgresql://postgres:postgres@postgres:5432/agentic-research")
    lines.append("SECRET_KEY=changeme-in-production")
    lines.append("")
    lines.append("# Planka (auto-configured by setup)")
    for k in ("PLANKA_API_URL", "PLANKA_TOKEN", "PLANKA_BOARD_ID"):
        if k in vars:
            lines.append(f"{k}={vars[k]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_teardown(path: Path) -> None:
    path.write_text(
        "#!/bin/bash\n"
        "# Stop all agentic-research services\n"
        f'docker compose -f "{_COMPOSE_FILE}" down\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _compose_up() -> None:
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(_COMPOSE_FILE), "up", "-d"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            typer.echo(f"[WARN] docker compose up returned non-zero: {result.stderr[:200]}")
        else:
            typer.echo("✓ Services started")
    except FileNotFoundError:
        typer.echo("[WARN] `docker` not found on PATH. Start services manually.")


def _setup_planka(env_vars: dict) -> None:
    """
    Auto-create Planka project, board, and columns.
    Required columns: Planning | Spec Pending Review | Verify | Review | Done | Failed
    """
    import time
    import httpx

    planka_url = "http://localhost:7002"
    admin_email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("DEFAULT_ADMIN_PASSWORD", "adminpassword")

    # Wait for Planka to be ready (up to 30 s)
    for i in range(10):
        try:
            r = httpx.get(f"{planka_url}/api/config", timeout=3)
            if r.status_code < 500:
                break
        except Exception:
            pass
        time.sleep(3)
    else:
        typer.echo("[WARN] Planka not reachable after 30 s — skipping board setup.")
        typer.echo(f"       Start Planka at {planka_url} then re-run setup.")
        return

    try:
        # 1. Login
        r = httpx.post(
            f"{planka_url}/api/access-tokens",
            json={"emailOrUsername": admin_email, "password": admin_password},
            timeout=10,
        )
        if r.status_code != 200:
            typer.echo(f"[WARN] Planka login failed ({r.status_code}). Board not created.")
            return
        token = r.json()["item"]["token"]
        h = {"Authorization": f"Bearer {token}"}

        # 2. Create project "Agentic Research"
        r = httpx.post(
            f"{planka_url}/api/projects",
            headers=h,
            json={"name": "Agentic Research", "type": "shared", "position": 65535},
            timeout=10,
        )
        project_id = r.json()["item"]["id"]

        # 3. Create board "Research"
        r = httpx.post(
            f"{planka_url}/api/projects/{project_id}/boards",
            headers=h,
            json={"name": "Research", "position": 65535},
            timeout=10,
        )
        board_id = r.json()["item"]["id"]

        # 4. Create columns in order
        columns = [
            ("Planning",           10000),
            ("Spec Pending Review", 20000),
            ("Verify",             25000),
            ("Review",             30000),
            ("Done",               40000),
            ("Failed",             50000),
        ]
        for name, position in columns:
            httpx.post(
                f"{planka_url}/api/boards/{board_id}/lists",
                headers=h,
                json={"name": name, "position": position, "type": "active"},
                timeout=10,
            )

        env_vars["PLANKA_API_URL"] = planka_url
        env_vars["PLANKA_TOKEN"] = token
        env_vars["PLANKA_BOARD_ID"] = board_id

        typer.echo(f"✓ Planka board created (id: {board_id})")
        typer.echo(f"  Columns: Planning → Spec Pending Review → Verify → Review → Done/Failed")
        typer.echo(f"  Open: {planka_url}")
    except Exception as e:
        typer.echo(f"[WARN] Could not auto-configure Planka: {e}")
        typer.echo("       You can configure it manually via the Planka UI.")


def _write_default_compose(path: Path) -> None:
    """Write a fallback docker-compose.yml if template is not bundled."""
    # Import from the docker/ directory in the repo if running from source
    repo_compose = Path(__file__).parent.parent / "docker" / "docker-compose.global.yml"
    if repo_compose.exists():
        shutil.copy(repo_compose, path)
    else:
        typer.echo(f"[WARN] No docker-compose template found. Please copy manually to {path}")
