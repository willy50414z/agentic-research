"""
framework/llm_agent/test_gemini_subprocess.py

診斷腳本：測試 framework subprocess 呼叫 gemini 能否產出 spec review 檔案。

用法（在 agentic-research 根目錄下執行）：
    python -m framework.llm_agent.test_gemini_subprocess
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
PROMPT_FILE = str((Path(__file__).parent.parent / "prompts" / "spec_agent_primary.txt").resolve())

# ─── 工具 ────────────────────────────────────────────────────────────────────

def _safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


def _resolve_gemini() -> str:
    if os.name == "nt":
        found = shutil.which("gemini.cmd")
        if found:
            return found
    return shutil.which("gemini") or "gemini"


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


def _run_gemini(prompt: str, work_dir_str: str, output_dir: str, label: str):
    gemini = _resolve_gemini()
    cmd = [gemini, "--approval-mode", "auto_edit", "--prompt", prompt]

    _safe_print(f"\n{'='*60}")
    _safe_print(f"[{label}]")
    _safe_print(f"  gemini binary : {gemini}")
    _safe_print(f"  cwd           : {work_dir_str}")
    _safe_print(f"  output_dir    : {output_dir}")
    _safe_print(f"  prompt 長度   : {len(prompt)} chars")

    _list_dir(Path(output_dir), "執行前")

    try:
        result = subprocess.run(
            cmd,
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
    ts = datetime.now().strftime("%Y%m%d%H%M%S.%f")[:-4]
    work_dir = Path(VOLUME_BASE) / "agentic-framework-api" / "llm" / ts
    work_dir.mkdir(parents=True, exist_ok=True)
    if spec_src.exists():
        shutil.copy(spec_src, work_dir / "spec.md")
    else:
        (work_dir / "spec.md").write_text("# Test Spec\n\nThis is a dummy test spec.\n", encoding="utf-8")
        _safe_print(f"[WARN] {spec_src} 不存在，使用 dummy spec")
    return str(work_dir), str(work_dir / "spec.md")


def main():
    spec_src = Path(SPEC_PATH)
    prompt_template = Path(PROMPT_FILE).read_text(encoding="utf-8")

    _safe_print("=" * 60)
    _safe_print("Gemini Subprocess 診斷腳本")
    _safe_print(f"rules  : {RULES_PATH}")
    _safe_print(f"prompt : {PROMPT_FILE}")

    wd, sp = _make_work_dir(spec_src)

    prompt = (
        "STRICT RULE: write exactly two files and nothing else."
        " File 1: reviewed_spec_primary.md at the path stated in the prompt."
        " File 2: EITHER status_pass.txt (content: PASS) if no clarification questions,"
        " OR status_need_update.txt (one question per line) if questions exist."
        " Do NOT create any other file, directory, script, or code."
        " Do NOT implement any software. Do NOT run shell commands."
        " Use write_file tool. Do NOT print file contents to stdout.\n\n"
        + prompt_template
        .replace("{SPEC_PATH}",  sp)
        .replace("{OUTPUT_DIR}", wd)
        .replace("{RULES_PATH}", RULES_PATH)
    )
    _safe_print(f"\n[debug] prompt (len={len(prompt)}):\n{prompt[:300]}...\n")

    _run_gemini(prompt, wd, wd, "測試: gemini spec review, cwd=work_dir")

    _safe_print("\n" + "=" * 60)
    _safe_print("診斷完成。若輸出目錄出現 reviewed_spec_primary.md + status_*.txt → 成功。")


if __name__ == "__main__":
    main()
