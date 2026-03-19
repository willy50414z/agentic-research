## ADDED Requirements

### Requirement: Plugin abstract base class
Every plugin MUST subclass `framework.plugin_interface.ResearchPlugin` and implement all abstract methods. Instantiating a class that does not implement all abstract methods SHALL raise `TypeError`.

#### Scenario: Incomplete plugin raises at instantiation
- **WHEN** a class subclasses `ResearchPlugin` but omits `analyze_node`
- **THEN** instantiating that class raises `TypeError`

---

### Requirement: Required node methods
Each plugin MUST implement the following six node methods. All methods SHALL accept a single `state: dict` argument and return a `dict` of state updates (partial state — only keys being modified).

| Method | Reads from state | Writes to state |
|--------|-----------------|-----------------|
| `plan_node` | `loop_goal`, `last_checkpoint_decision` | `implementation_plan`, `needs_human_approval=True` |
| `implement_node` | `implementation_plan`, `needs_human_approval` | `needs_human_approval=False`, `artifacts` (append) |
| `test_node` | `implementation_plan`, `artifacts` | plugin-defined metric keys, `test_metrics` |
| `analyze_node` | `test_metrics` | `last_result` (`"PASS"`/`"FAIL"`/`"TERMINATE"`), `last_reason` |
| `revise_node` | `loop_goal`, `last_reason`, `attempt_count` | `loop_goal` (revised), `implementation_plan` (optional) |
| `summarize_node` | `loop_goal`, `last_reason`, `artifacts`, `loop_count_since_review` | `last_reason` (summary text), `artifacts` (append report ref), `loop_index` (incremented), `loop_count_since_review` (incremented), `attempt_count` (reset to 0) |

#### Scenario: plan_node sets approval flag
- **WHEN** `plan_node` is called at the start of a new loop
- **THEN** the returned dict includes `needs_human_approval: True` and a non-empty `implementation_plan`

#### Scenario: analyze_node sets routing signal
- **WHEN** tests fail validation criteria
- **THEN** `analyze_node` returns `{"last_result": "FAIL", "last_reason": "<explanation>"}`

---

### Requirement: Framework-reserved state keys
Plugins SHALL NOT write to the following state keys (owned exclusively by the framework):

`project_id`, `last_checkpoint_decision`

`loop_count_since_review` is incremented by `summarize_node` (plugin-managed) and reset to `0` by `notify_planka_node` (framework-managed).

#### Scenario: Framework key ownership
- **WHEN** a plugin's node function returns a dict containing `last_checkpoint_decision`
- **THEN** the value will be overwritten by the framework's `notify_planka_node` on the next loop review

---

### Requirement: Plugin name property
Each plugin MUST expose a `name` property (abstract `@property`) returning a unique string identifier in snake_case.

#### Scenario: Plugin name uniqueness
- **WHEN** two plugins are registered with the same name
- **THEN** `register` raises `ValueError: Plugin '<name>' is already registered.`

---

### Requirement: Optional review interval override
Plugins MAY override `get_review_interval() -> int` to change the default PASS loop count between human reviews. If not overridden, the default is `5`.

#### Scenario: Custom review interval
- **WHEN** a plugin overrides `get_review_interval()` to return `3`
- **THEN** `notify_planka` is triggered after every 3 PASS loops unless overridden by `--review-interval`

---

### Requirement: Plugin registration
Plugins MUST be registered using the `@register` decorator from `framework.plugin_registry` at module import time.

`resolve(name: str) -> ResearchPlugin` SHALL return the instantiated plugin for the given name, or raise `KeyError` if not found.

`list_plugins() -> list[str]` SHALL return all currently registered plugin names.

#### Scenario: Plugin resolves after import
- **WHEN** `import projects.dummy.plugin` is executed (triggering `@register`)
- **THEN** `resolve("dummy")` returns a `DummyPlugin` instance

#### Scenario: Unknown plugin raises KeyError
- **WHEN** `resolve("nonexistent")` is called
- **THEN** `KeyError` is raised with the plugin name in the message