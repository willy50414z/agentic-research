"""
agentic_research/project_cmds.py

Host-side `start` and `resume` commands.

User-facing spec files:
  spec.md           → user writes research requirements in markdown
  spec.clarified.md → user reads LLM questions and fills in answers (markdown Q&A)

The AI agent (framework-api) handles all YAML conversion and LangGraph logic.
These commands just handle file I/O and HTTP communication.
"""

from pathlib import Path

import httpx
import typer
import yaml


def start(
    spec: Path = typer.Option(
        Path("spec.md"),
        "--spec", "-s",
        help="Path to spec.md",
        exists=True,
    ),
    out: Path = typer.Option(
        Path("spec.clarified.md"),
        "--out", "-o",
        help="Path to write spec.clarified.md",
    ),
    api: str = typer.Option(
        "http://localhost:7001",
        "--api",
        help="framework-api base URL",
    ),
):
    """
    Phase 1: register project in DB, generate spec.clarified.md with LLM questions.

    Reads spec.md → POSTs markdown to /start → writes spec.clarified.md.
    """
    typer.echo(f"[start] Reading {spec}")
    spec_md = spec.read_text(encoding="utf-8")

    typer.echo(f"[start] Posting to {api}/start ...")
    try:
        resp = httpx.post(
            f"{api}/start",
            json={"spec_md": spec_md},
            timeout=60,
        )
    except httpx.ConnectError:
        typer.echo(
            f"[ERROR] Cannot connect to framework-api at {api}.\n"
            "        Is the infra stack running? Try: agentic-research setup",
            err=True,
        )
        raise typer.Exit(1)

    if resp.status_code != 200:
        typer.echo(f"[ERROR] /start returned {resp.status_code}: {resp.text[:300]}", err=True)
        raise typer.Exit(1)

    data = resp.json()
    project_id = data.get("project_id", "?")
    clarifications = data.get("clarifications", [])
    status = data.get("status", "")

    if not clarifications:
        # No issues — graph already starting
        typer.echo(
            f"\n[start] Project '{project_id}' spec is clean — research loop starting.\n"
            "        Check Planka (http://localhost:7002) — card is now in Verify."
        )
        if status == "started":
            typer.echo("        Graph is running in the background.")
        return

    # Clarifications needed — write spec.clarified.md and prompt user
    _write_clarified_md(out, data.get("spec", {}), clarifications)
    typer.echo(f"[start] Written: {out}")
    typer.echo(f"\n[start] {len(clarifications)} clarification question(s) — card moved back to Planning:")
    for c in clarifications:
        typer.echo(f"\n  Field   : {c['field']}")
        typer.echo(f"  Question: {c['question']}")
    typer.echo(f"\n  Fill in answers in {out}, then run ./resume.sh")


def resume(
    spec_clarified: Path = typer.Option(
        Path("spec.clarified.md"),
        "--spec-clarified",
        help="Path to spec.clarified.md",
    ),
    spec: Path = typer.Option(
        Path("spec.md"),
        "--spec",
        help="Path to spec.md (to read project_id)",
    ),
    api: str = typer.Option(
        "http://localhost:7001",
        "--api",
        help="framework-api base URL",
    ),
):
    """
    Advance Phase 1 clarification or trigger Phase 2 research loop.

    Checks spec.clarified.md for unanswered questions.
    If all answered: POSTs to /resume to start the research loop.
    """
    if not spec_clarified.exists():
        typer.echo(
            f"[ERROR] {spec_clarified} not found. Run ./start.sh first.", err=True
        )
        raise typer.Exit(1)

    # Extract project_id from spec.md (first heading or send to server)
    project_id = _extract_project_id(spec)

    clarifications = _load_clarifications_md(spec_clarified)

    # Check for unanswered questions
    unanswered = [c for c in clarifications if not (c.get("answer") or "").strip()]
    if unanswered:
        typer.echo(f"\n[resume] Project '{project_id}' — unanswered clarifications:\n")
        for c in clarifications:
            answered = bool((c.get("answer") or "").strip())
            status = "✓" if answered else "✗"
            typer.echo(f"  [{status}] {c['field']}")
            if not answered:
                typer.echo(f"       Q: {c['question']}")
        typer.echo(
            f"\n  Fill in all answers in {spec_clarified}, then run ./resume.sh again."
        )
        raise typer.Exit(0)

    # All answered — collect answers and POST
    answers = {c["field"]: c.get("answer", "") for c in clarifications}
    typer.echo(f"[resume] All clarifications answered. Posting to {api}/resume ...")

    payload = {
        "project_id": project_id,
        "decision": {
            "action": "answers",
            "answers": answers,
            "confirmed": True,
        },
    }

    try:
        resp = httpx.post(f"{api}/resume", json=payload, timeout=30)
    except httpx.ConnectError:
        typer.echo(
            f"[ERROR] Cannot connect to framework-api at {api}.\n"
            "        Is the infra stack running?",
            err=True,
        )
        raise typer.Exit(1)

    if resp.status_code == 409:
        typer.echo(f"[resume] Confirmation required: {resp.json()}")
        raise typer.Exit(0)

    if resp.status_code != 200:
        typer.echo(f"[ERROR] /resume returned {resp.status_code}: {resp.text[:300]}", err=True)
        raise typer.Exit(1)

    data = resp.json()
    typer.echo(f"[resume] {data.get('status', 'ok')} — research loop starting.")
    typer.echo("         Check Planka (http://localhost:7002) for real-time status.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_project_id(spec_path: Path) -> str:
    """Extract project label from spec.md heading, or fallback to directory name."""
    import re
    if spec_path.exists():
        content = spec_path.read_text(encoding="utf-8")
        m = re.search(r"^# Research Spec:\s*(.+)$", content, re.MULTILINE)
        if m:
            name = m.group(1).strip()
            return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    return spec_path.parent.name


def _load_clarifications_md(path: Path) -> list[dict]:
    """Parse clarifications from spec.clarified.md."""
    import re
    content = path.read_text(encoding="utf-8")
    clarifications = []

    # Match sections: ## N. `field`
    section_pattern = re.compile(
        r"## \d+\.\s+`([^`]+)`\s*\n"
        r".*?\*\*Original value\*\*\s*\n```\n(.*?)```\s*\n"
        r".*?\*\*Question\*\*\s*\n(.*?)\n\n"
        r".*?\*\*Answer\*\*\s*\n<!-- fill in below -->\s*\n"
        r"(.*?)(?=\n---|\n## \d|\Z)",
        re.DOTALL,
    )
    for m in section_pattern.finditer(content):
        clarifications.append({
            "field": m.group(1).strip(),
            "original": m.group(2).strip(),
            "question": m.group(3).strip(),
            "answer": m.group(4).strip(),
        })
    return clarifications


def _write_clarified_md(path: Path, spec: dict, clarifications: list[dict]) -> None:
    """Write spec.clarified.md with questions and answer slots."""
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    project = spec.get("project", {})
    lines = [
        "# Spec Clarifications",
        "",
        f"**Project**: {project.get('label', 'unknown')}  ",
        f"**Generated**: {timestamp}",
        "",
        "> Fill in each **Answer** below, then run `./resume.sh`.",
        "",
        "---",
        "",
    ]

    if not clarifications:
        lines += [
            "## No Clarifications Needed",
            "",
            "All fields are clear. Run `./resume.sh` to start the research loop.",
            "",
        ]
    else:
        for i, c in enumerate(clarifications, 1):
            lines += [
                f"## {i}. `{c['field']}`",
                "",
                "**Original value**  ",
                "```",
                str(c["original"]),
                "```",
                "",
                "**Question**  ",
                c["question"],
                "",
                "**Answer**  ",
                "<!-- fill in below -->",
                "",
                "",
                "---",
                "",
            ]

    if spec:
        spec_yaml = yaml.dump(spec, allow_unicode=True, default_flow_style=False, sort_keys=False)
        lines += [
            "<details>",
            "<summary>Original spec snapshot (YAML)</summary>",
            "",
            "```yaml",
            spec_yaml.rstrip(),
            "```",
            "</details>",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
