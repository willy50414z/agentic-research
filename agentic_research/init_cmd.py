"""
agentic_research/init_cmd.py

`agentic-research init <name>` — create a new project directory with templates.

Creates:
  ./<name>/
  ├── spec.yaml
  ├── credentials.yaml
  ├── start.sh / start.bat
  ├── resume.sh / resume.bat
  ├── artifacts/
  └── logs/
"""

import os
import sys
from pathlib import Path

import httpx
import typer

_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"

# Fallback: look in the repo's templates/ directory (when running from source)
_REPO_TEMPLATES = Path(__file__).parent.parent / "templates"


def _get_template(name: str) -> str:
    for templates_dir in (_TEMPLATES_DIR, _REPO_TEMPLATES):
        p = templates_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Template not found: {name}")


def init(
    name: str = typer.Argument(..., help="Project name (used as directory and project label)."),
    api: str = typer.Option(
        "http://localhost:7001",
        "--api",
        help="framework-api base URL",
    ),
):
    """
    Create a new research project directory with spec.yaml template and run scripts.
    """
    project_dir = Path.cwd() / name

    if project_dir.exists():
        typer.echo(f"[ERROR] Directory '{name}' already exists.", err=True)
        raise typer.Exit(1)

    project_dir.mkdir(parents=True)
    (project_dir / "artifacts").mkdir()
    (project_dir / "logs").mkdir()

    # Write spec.md (substitute project name)
    spec_content = _get_template("spec.md")
    spec_content = spec_content.replace("{{PROJECT_LABEL}}", name)
    spec_content = spec_content.replace("{{PROJECT_NAME}}", name.replace("-", " ").title())
    (project_dir / "spec.md").write_text(spec_content, encoding="utf-8")

    # Write credentials.yaml
    cred_content = _get_template("credentials.yaml")
    (project_dir / "credentials.yaml").write_text(cred_content, encoding="utf-8")

    # Write start.sh / resume.sh (Unix)
    start_sh = _get_template("start.sh")
    resume_sh = _get_template("resume.sh")

    start_path = project_dir / "start.sh"
    resume_path = project_dir / "resume.sh"
    start_path.write_text(start_sh, encoding="utf-8")
    resume_path.write_text(resume_sh, encoding="utf-8")
    start_path.chmod(0o755)
    resume_path.chmod(0o755)

    # Write start.bat / resume.bat (Windows)
    try:
        start_bat = _get_template("start.bat")
        resume_bat = _get_template("resume.bat")
        (project_dir / "start.bat").write_text(start_bat, encoding="utf-8")
        (project_dir / "resume.bat").write_text(resume_bat, encoding="utf-8")
    except FileNotFoundError:
        pass  # optional; skip if Windows templates not present

    typer.echo(f"\n✓ Created project: {project_dir}")

    # Register stub in DB and create Planka card in Planning
    project_name = name.replace("-", " ").title()
    try:
        resp = httpx.post(
            f"{api}/project/init",
            json={"project_id": name, "project_name": project_name},
            timeout=10,
        )
        if resp.status_code == 200:
            typer.echo(f"✓ Planka card created in Planning (framework-api: {api})")
        else:
            typer.echo(f"[warn] /project/init returned {resp.status_code} — Planka card not created.")
    except Exception as e:
        typer.echo(f"[warn] Could not reach framework-api at {api}: {e}")
        typer.echo("       Start infra first with: agentic-research setup")

    typer.echo(f"\nNext steps:")
    typer.echo(f"  1. cd {name}")
    typer.echo(f"  2. Edit spec.md (fill in hypothesis, signals, performance thresholds)")
    typer.echo(f"  3. ./start.sh   ← spec review: no issues → Verify + research loop starts")
    typer.echo(f"                    issues found → spec.clarified.md written, card back to Planning")
    typer.echo(f"  4. (if clarifications) fill answers in spec.clarified.md → ./resume.sh")
