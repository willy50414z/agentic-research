"""
agentic_research/setup_cmd.py

`agentic-research setup` — one-time global infra setup per machine.

Actions:
  1. Copy docker-compose.yml for global stack (4 services) into CWD
  2. Interactive LLM credential discovery (claude → codex → gemini → local)
  3. Write .env into CWD
"""

import os
import shutil
from pathlib import Path

import typer

# Source compose file (bundled with the package)
_PKG_DIR = Path(__file__).parent
_COMPOSE_TEMPLATE = _PKG_DIR / "templates" / "docker-compose.global.yml"


def setup():
    """
    One-time global infrastructure setup.
    Writes docker-compose.yml and .env into the current directory.
    """
    out_dir = Path.cwd()
    env_file = out_dir / ".env"
    compose_file = out_dir / "docker-compose.yml"
    data_dir = out_dir / "data"

    typer.echo("\n─── Agentic Research Setup ───────────────────────────────────")
    typer.echo(f"Output directory: {out_dir}")

    # 1. Copy docker-compose.yml
    if _COMPOSE_TEMPLATE.exists():
        shutil.copy(_COMPOSE_TEMPLATE, compose_file)
    else:
        _write_default_compose(compose_file)
    typer.echo(f"✓ docker-compose.yml → {compose_file}")

    # 3. Interactive LLM credential setup
    typer.echo("\n─── LLM Configuration ────────────────────────────────────────")
    typer.echo("Checking providers in priority order: claude → codex → gemini → local\n")

    # GitHub username → determines the GHCR image name
    github_username = typer.prompt(
        "GitHub username (for GHCR image ghcr.io/<username>/agentic-research)",
        default="",
    )
    framework_image = (
        f"ghcr.io/{github_username}/agentic-research:latest"
        if github_username.strip()
        else "ghcr.io/your-username/agentic-research:latest"
    )

    env_vars: dict[str, str] = {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/agentic-research",
        "MLFLOW_TRACKING_URI": "http://localhost:5000",
        "FRAMEWORK_API_URL": "http://localhost:7001",
        "AGENTIC_HOME": str(out_dir),
        "VOLUME_BASE_DIR": str(data_dir),
        "FRAMEWORK_IMAGE": framework_image,
    }
    llm_chain: list[str] = []

    # Claude
    typer.echo("[1] Claude")
    claude_cred = Path.home() / ".claude" / ".credentials.json"
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
    gcloud_dir = Path.home() / ".gemini"
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
    _write_env(env_file, env_vars)
    typer.echo(f"\n✓ Written: {env_file}")

    # 5. Write teardown.sh
    _write_teardown(out_dir / "teardown.sh", compose_file)

    typer.echo("\n✓ Setup complete.")
    typer.echo(f"  docker-compose.yml : {compose_file}")
    typer.echo(f"  .env               : {env_file}")
    typer.echo(f"  Framework image    : {framework_image}")
    typer.echo("\nTo start Docker services, run:")
    typer.echo(f"  docker compose -f \"{compose_file}\" up -d")
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
              "AGENTIC_HOME", "VOLUME_BASE_DIR", "FRAMEWORK_IMAGE"):
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


def _write_teardown(path: Path, compose_file: Path) -> None:
    path.write_text(
        "#!/bin/bash\n"
        "# Stop all agentic-research services\n"
        f'docker compose -f "{compose_file}" down\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_default_compose(path: Path) -> None:
    """Write a fallback docker-compose.yml if template is not bundled."""
    # Import from the docker/ directory in the repo if running from source
    repo_compose = Path(__file__).parent.parent / "docker" / "docker-compose.global.yml"
    if repo_compose.exists():
        shutil.copy(repo_compose, path)
    else:
        typer.echo(f"[WARN] No docker-compose template found. Please copy manually to {path}")
