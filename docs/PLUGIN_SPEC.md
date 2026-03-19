# Plugin Development Specification

> Full reference for building a `ResearchPlugin`. Read `docs/AGENT_CONTEXT.md` first for system overview, then use this as the implementation contract.

---

## 1. File Location & Registration

```
projects/
└── <your_plugin_name>/
    └── plugin.py     ← one file, one class, one @register
```

The framework auto-discovers this file on startup via `discover_plugins()`. No manual import needed.

---

## 2. Minimal Skeleton

```python
from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

@register
class MyPlugin(ResearchPlugin):
    name = "my_plugin"          # snake_case, globally unique

    def plan_node(self, state):      ...
    def implement_node(self, state): ...
    def test_node(self, state):      ...
    def analyze_node(self, state):   ...
    def revise_node(self, state):    ...
    def summarize_node(self, state): ...

    def get_review_interval(self) -> int:
        return 3   # Loop Review fires every 3 PASS loops (default: 5)
```

All six node methods are abstract — omitting any one raises `TypeError` at instantiation.

---

## 3. Node Contract

Each node receives the full `ResearchState` dict and returns a **partial update dict** (only keys that change). Never return the full state.

### 3.1 `plan_node(state) -> dict`

**Reads:** `loop_goal`, `loop_index`, `last_checkpoint_decision`

**Writes:**

| Key | Value |
|-----|-------|
| `implementation_plan` | `dict` — plugin-defined plan for this loop |
| `needs_human_approval` | `True` — always; triggers Plan Review interrupt |
| `loop_goal` | Updated string if replan notes should be absorbed |
| `last_checkpoint_decision` | `None` — clear after consuming |
| `last_result` | `"TERMINATE"` only if decision.action == "terminate" |

**Pattern:**
```python
def plan_node(self, state):
    decision = state.get("last_checkpoint_decision") or {}

    if decision.get("action") == "terminate":
        return {"last_result": "TERMINATE", "last_reason": "Human requested stop."}

    # absorb replan notes
    goal = state.get("loop_goal", "")
    if decision.get("action") == "replan" and decision.get("notes"):
        goal = (goal + f"  [REVISED: {decision['notes']}]")[:500]

    return {
        "loop_goal":             goal,
        "implementation_plan":   {"loop": state["loop_index"], ...},
        "needs_human_approval":  True,
        "last_checkpoint_decision": None,
    }
```

---

### 3.2 `implement_node(state) -> dict`

**Reads:** `needs_human_approval`, `implementation_plan`, `attempt_count`

**Writes:** `needs_human_approval=False`, optionally appends to `artifacts`

Call `interrupt()` **only** when `needs_human_approval=True`. The revise→implement path sets it False, so no interrupt fires on retry.

```python
def implement_node(self, state):
    from langgraph.types import interrupt

    if state.get("needs_human_approval", False):
        decision = interrupt({
            "checkpoint":  "plan_review",
            "loop_index":  state["loop_index"],
            "plan":        state["implementation_plan"],
            "instruction": "approve or reject this plan",
        })
        if isinstance(decision, dict) and decision.get("action") == "reject":
            return {
                "last_result":          "TERMINATE",
                "last_reason":          f"Plan rejected: {decision.get('reason','')}",
                "needs_human_approval": False,
            }

    # do actual work here (run code, fetch data, call LLM, ...)
    return {"needs_human_approval": False}
```

---

### 3.3 `test_node(state) -> dict`

**Reads:** `implementation_plan`, `attempt_count`

**Writes:** `test_metrics` (dict, plugin-defined keys), `attempt_count` (increment)

```python
def test_node(self, state):
    config   = state["implementation_plan"]["config"]
    attempt  = state.get("attempt_count", 0)
    result   = run_experiment(config)          # your logic here

    return {
        "test_metrics":  {"accuracy": result, **config},
        "attempt_count": attempt + 1,
    }
```

**Standard metric keys** (used by `loop_metrics` DB columns — set what applies):

| Key | DB column | Type |
|-----|-----------|------|
| `win_rate` | `win_rate` | float 0–1 |
| `alpha_ratio` | `alpha_ratio` | float |
| `max_drawdown` | `max_drawdown` | float |
| `is_profit_factor` | `is_profit_factor` | float |
| `oos_profit_factor` | `oos_profit_factor` | float |

Any additional keys in `test_metrics` are silently ignored by the DB layer (stored only in LangGraph checkpoint).

---

### 3.4 `analyze_node(state) -> dict`

**Reads:** `test_metrics`

**Writes:** `last_result`, `last_reason`

```python
def analyze_node(self, state):
    accuracy = state["test_metrics"].get("accuracy", 0)
    if accuracy >= 0.75:
        return {"last_result": "PASS",      "last_reason": f"accuracy={accuracy:.4f}"}
    else:
        return {"last_result": "FAIL",      "last_reason": f"accuracy={accuracy:.4f} < 0.75"}
    # or return {"last_result": "TERMINATE", "last_reason": "..."}
```

`last_result` drives graph routing. Any value other than `"PASS"` or `"TERMINATE"` is treated as `"FAIL"`.

---

### 3.5 `revise_node(state) -> dict`

**Reads:** `implementation_plan`, `last_reason`, `attempt_count`

**Writes:** `implementation_plan` (updated config)

```python
def revise_node(self, state):
    plan = state["implementation_plan"].copy()
    plan["config"] = next_config(plan["config"])   # your search strategy
    return {"implementation_plan": plan}
```

After `revise`, the graph goes back to `implement` **without** a Plan Review interrupt (`needs_human_approval` is still `False` from the previous implement call).

---

### 3.6 `summarize_node(state) -> dict`

**Reads:** `loop_index`, `loop_goal`, `test_metrics`, `last_reason`, `artifacts`

**Writes:**

| Key | Value |
|-----|-------|
| `last_reason` | Summary text (shown in CLI and Planka card) |
| `loop_index` | `loop_index + 1` |
| `loop_count_since_review` | incremented by 1 |
| `attempt_count` | reset to `0` |
| `artifacts` | append `{"type": "summary", "path": "..."}` |

```python
def summarize_node(self, state):
    loop    = state["loop_index"]
    metrics = state["test_metrics"]
    summary = f"Loop {loop} PASS — accuracy={metrics.get('accuracy',0):.4f}"

    # write report file
    path = Path(f"./artifacts/loop_{loop}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Loop {loop}\n\n{summary}\n")

    return {
        "last_reason":             summary,
        "loop_index":              loop + 1,
        "loop_count_since_review": state.get("loop_count_since_review", 0) + 1,
        "attempt_count":           0,
        "artifacts": state.get("artifacts", []) + [
            {"type": "summary", "path": str(path)}
        ],
    }
```

---

## 4. MLflow Integration (optional)

Add this helper to log metrics when `MLFLOW_TRACKING_URI` is set:

```python
import os

def _try_mlflow_log(project_id, loop, attempt, params, metrics):
    if not os.getenv("MLFLOW_TRACKING_URI"):
        return
    try:
        import mlflow
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI"))
        mlflow.set_experiment(project_id)
        with mlflow.start_run(run_name=f"loop_{loop}_attempt_{attempt}"):
            mlflow.log_params(params)
            for k, v in metrics.items():
                mlflow.log_metric(k, v)
    except Exception as e:
        logging.getLogger(__name__).debug("MLflow skipped: %s", e)
```

Call it from `test_node`. MLflow UI is at `http://localhost:5000`.

---

## 5. LLM Integration (optional)

```python
from framework.llm_agent.llm_svc import run_once
from framework.llm_agent.llm_target import LLMTarget

try:
    response = run_once(LLMTarget.CLAUDE, prompt, timeout=120)
    # parse: from framework.tag_parser import _extract_tag
    plan = _extract_tag(response, "PLAN")
except FileNotFoundError:
    plan = fallback_plan()   # Claude CLI not installed
```

Supported targets: `CLAUDE`, `GEMINI`, `CODEX`. Subprocess-based; the CLI tool must be installed in the container.

---

## 6. State Ownership Reference

| Key | Written by | Rule |
|-----|-----------|------|
| `project_id` | framework (at START) | Never write |
| `loop_goal` | `plan_node` | Update if replan notes |
| `implementation_plan` | `plan_node`, `revise_node` | Plugin-defined schema |
| `last_result` | `analyze_node`, `plan_node` (terminate) | Must be PASS/FAIL/TERMINATE |
| `last_reason` | `analyze_node`, `summarize_node` | Human-readable string |
| `needs_human_approval` | `plan_node` (True), `implement_node` (False) | Reset to False after interrupt |
| `attempt_count` | `test_node` (increment), `summarize_node` (reset to 0) | |
| `test_metrics` | `test_node` | Dict of domain metrics |
| `loop_index` | `summarize_node` (+1) | Increment exactly once per PASS |
| `loop_count_since_review` | `summarize_node` (+1), `notify_planka_node` (reset 0) | |
| `last_checkpoint_decision` | `notify_planka_node` (set), `plan_node` (clear to None) | |
| `artifacts` | Any node — append only | `state.get("artifacts",[]) + [new]` |

---

## 7. Checklist: New Plugin

- [ ] Class in `projects/<name>/plugin.py`
- [ ] `@register` decorator applied
- [ ] `name` attribute is snake_case and unique
- [ ] All six node methods implemented
- [ ] `plan_node` returns `needs_human_approval=True` and clears `last_checkpoint_decision`
- [ ] `plan_node` handles `action == "terminate"` from Loop Review
- [ ] `implement_node` calls `interrupt()` only when `needs_human_approval=True`
- [ ] `analyze_node` returns only `last_result` and `last_reason`
- [ ] `summarize_node` increments `loop_index` and `loop_count_since_review`, resets `attempt_count`
- [ ] `summarize_node` appends to `artifacts` (not replaces)
- [ ] `get_review_interval()` returns a sensible number (default 5)
- [ ] Tested with `docker exec agentic-langgraph python cli/main.py start --plugin <name> --project test_001 --goal "test"`
