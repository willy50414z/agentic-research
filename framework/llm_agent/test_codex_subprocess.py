"""
framework/llm_agent/test_codex_subprocess.py

診斷腳本：對比 framework subprocess 呼叫 codex 與手動執行的差異。

用法（在 agentic-research 根目錄下執行）：
    python -m framework.llm_agent.test_codex_subprocess

測試項目：
  1. cwd=work_dir (非 git 目錄)
  2. cwd=None (繼承，即 project root)
  兩次都使用新版 prompt（明確要求用工具寫檔，不要搜尋其他 spec）
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ─── 設定 ────────────────────────────────────────────────────────────────────

VOLUME_BASE = os.getenv("VOLUME_BASE_DIR", r"E:\docker_data\agentic-research")
SPEC_PATH   = r"E:\users\download\spec.md"
RULES_PATH  = str((Path(__file__).parent.parent.parent / ".ai" / "rules" / "spec-review.md").resolve())
PROMPT_FILE = str((Path(__file__).parent.parent / "prompts" / "spec_review" / "spec_agent_primary.txt").resolve())
REPO_ROOT   = str(Path(__file__).parent.parent.parent.resolve())

# ─── 工具 ────────────────────────────────────────────────────────────────────

def _safe_print(text: str):
    """Print text, replacing unencodable chars so cp950 terminals don't crash."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


def _resolve_codex() -> str:
    if os.name == "nt":
        found = shutil.which("codex.cmd")
        if found:
            return found
    return shutil.which("codex") or "codex"


def _list_dir(path: Path, label: str):
    _safe_print(f"\n[{label}] 目錄：{path}")
    if not path.exists():
        _safe_print("  (目錄不存在)")
        return
    files = list(path.iterdir())
    if not files:
        _safe_print("  (空目錄)")
    for f in sorted(files):
        size = f.stat().st_size if f.is_file() else "-"
        _safe_print(f"  {f.name}  ({size} bytes)")


def _run_codex(prompt: str, work_dir_str: str | None, output_dir: str, label: str,
               stdin_mode: str = "inherit"):
    """
    stdin_mode:
      "inherit"  - 繼承父進程 stdin（server 通常非 TTY）
      "devnull"  - stdin = /dev/null（明確斷開）
      "pipe"     - stdin = PIPE，不傳任何資料
    """
    codex = _resolve_codex()
    cmd = [codex, "exec", "--dangerously-bypass-approvals-and-sandbox", prompt]

    _safe_print(f"\n{'='*60}")
    _safe_print(f"[{label}]")
    _safe_print(f"  codex binary : {codex}")
    _safe_print(f"  cwd          : {work_dir_str or '(繼承)'}")
    _safe_print(f"  output_dir   : {output_dir}")
    _safe_print(f"  stdin_mode   : {stdin_mode}")
    _safe_print(f"  prompt 長度  : {len(prompt)} chars")

    _list_dir(Path(output_dir), "執行前")

    if stdin_mode == "devnull":
        stdin_arg = subprocess.DEVNULL
        input_arg = None
    elif stdin_mode == "pipe":
        stdin_arg = subprocess.PIPE
        input_arg = ""
    else:  # inherit
        stdin_arg = None
        input_arg = None

    try:
        result = subprocess.run(
            cmd,
            stdin=stdin_arg,
            input=input_arg,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir_str,
            env=dict(os.environ),
            timeout=300,
        )
        _safe_print(f"\n  returncode : {result.returncode}")
        _safe_print(f"  stdout len : {len(result.stdout or '')}")
        _safe_print(f"  stderr len : {len(result.stderr or '')}")
        _safe_print("\n--- STDOUT (前 3000 字) ---")
        _safe_print((result.stdout or "")[:3000])
        _safe_print("\n--- STDERR (前 2000 字) ---")
        _safe_print((result.stderr or "")[:2000])
    except subprocess.TimeoutExpired:
        _safe_print("  !! TimeoutExpired")
    except Exception as e:
        _safe_print(f"  !! Exception: {type(e).__name__}: {e}")

    _list_dir(Path(output_dir), "執行後")


# ─── 主程式 ──────────────────────────────────────────────────────────────────

def _make_work_dir(spec_src: Path) -> tuple[str, str]:
    """建立 work_dir，複製 spec.md，回傳 (work_dir, spec_path)。"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S.%f")[:-4]
    work_dir = Path(VOLUME_BASE) / "agentic-framework-api" / "llm" / ts
    work_dir.mkdir(parents=True, exist_ok=True)
    if spec_src.exists():
        shutil.copy(spec_src, work_dir / "spec.md")
    else:
        (work_dir / "spec.md").write_text("# Test Spec\n\nThis is a dummy test spec.\n", encoding="utf-8")
        _safe_print(f"[WARN] {spec_src} 不存在，使用 dummy spec")
    return str(work_dir), str(work_dir / "spec.md")


def _load_prompt_template() -> str:
    return Path(PROMPT_FILE).read_text(encoding="utf-8")


def main():
    spec_src = Path(SPEC_PATH)
    prompt_template = _load_prompt_template()

    _safe_print("=" * 60)
    _safe_print("Codex Subprocess 診斷腳本")
    _safe_print(f"rules    : {RULES_PATH}")
    _safe_print(f"prompt   : {PROMPT_FILE}")

    # ── 測試：original prompt + rules 複製到 work_dir ──────────────────────
    wd1, sp1 = _make_work_dir(spec_src)
    # Copy rules into work_dir so codex finds it when scanning with rg/Get-ChildItem
    local_rules = Path(wd1) / "spec-review.md"
    shutil.copy(RULES_PATH, local_rules)
    _safe_print(f"\n[setup] rules 複製到 {local_rules}")

    # Load template and substitute paths; collapse to single line (codex.cmd truncates at newlines)
    prompt_template = _load_prompt_template()
    prompt = (
        prompt_template
        .replace("{SPEC_PATH}", sp1)
        .replace("{OUTPUT_DIR}", wd1)
        .replace("{RULES_PATH}", RULES_PATH)
        .strip()
        .replace("\n", " ")
    )
    _safe_print(f"\n[debug] prompt (single-line, len={len(prompt)}):\n{prompt[:300]}...\n")
    # cwd = repo root (same as aa.py) so AGENTS.md loads and enables tool use
    _run_codex(prompt, REPO_ROOT, wd1, "測試A: single-line prompt, cwd=repo root")

    _safe_print("\n" + "=" * 60)
    _safe_print("診斷完成。若有 exec 工具呼叫且輸出目錄產出檔案 → 修法有效。")


if __name__ == "__main__":
    main()
