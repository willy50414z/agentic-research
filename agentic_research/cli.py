"""
agentic_research/cli.py

Host-side CLI: `agentic-research setup | init | start | resume`.
"""
import typer
from agentic_research.setup_cmd import setup
from agentic_research.init_cmd import init
from agentic_research.project_cmds import start, resume

app = typer.Typer(
    name="agentic-research",
    add_completion=False,
    help="Agentic Research — manage global infra and create research projects.",
)

app.command()(setup)
app.command()(init)
app.command()(start)
app.command()(resume)
