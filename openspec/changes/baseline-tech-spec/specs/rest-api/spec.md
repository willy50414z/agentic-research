## ADDED Requirements

### Requirement: FastAPI application
The REST API SHALL be implemented as a FastAPI application defined in `framework/api/server.py` and exposed as the `app` object imported by `main.py`.

The application SHALL run on port `7001` (via Docker Compose) and SHALL expose a `GET /health` endpoint that returns `{"status": "ok"}` with HTTP 200.

#### Scenario: Health check returns OK
- **WHEN** `GET /health` is called
- **THEN** the response is HTTP 200 with body `{"status": "ok"}`

---

### Requirement: POST /resume endpoint
`POST /resume` SHALL resume a paused LangGraph graph thread with a human decision.

**Request body** (JSON):
```json
{
  "project_id": "<string>",
  "decision": {
    "action": "<approve|reject|continue|replan|terminate>",
    "notes": "<optional string>",
    "reason": "<optional string, used with reject>"
  }
}
```

**Response** (HTTP 200):
```json
{"status": "resumed", "project_id": "<string>"}
```

**Error responses**:
- HTTP 404 if `project_id` does not exist in the `projects` table
- HTTP 422 if the request body is malformed (FastAPI/Pydantic validation)

The endpoint SHALL:
1. Load the project from the `projects` table
2. Resolve the plugin and get the compiled graph
3. Read `loop_index` from the pre-resume state snapshot
4. Call `graph.invoke(Command(resume=decision), config)` to resume execution
5. Write a row to `checkpoint_decisions` with the action and notes

The endpoint SHALL be usable for both Plan Review interrupts (`approve`/`reject`) and Loop Review interrupts (`continue`/`replan`/`terminate`).

#### Scenario: Approve resumes Plan Review
- **WHEN** `POST /resume` with `{"project_id": "qa_001", "decision": {"action": "approve"}}`
- **THEN** the graph resumes from `implement`, HTTP 200 is returned, and a `checkpoint_decisions` row is written

#### Scenario: Unknown project returns 404
- **WHEN** `POST /resume` with a `project_id` not in the `projects` table
- **THEN** HTTP 404 is returned with `{"detail": "Project '<id>' not found."}`

#### Scenario: Replan with notes
- **WHEN** `POST /resume` with `{"action": "replan", "notes": "use ATR filter"}`
- **THEN** `checkpoint_decisions` contains a row with `action="replan"` and `notes="use ATR filter"`

---

### Requirement: POST /planka-webhook endpoint
`POST /planka-webhook` SHALL receive Planka card-move events and translate them to Loop Review decisions.

This endpoint is optional infrastructure — it SHALL function correctly only when `PLANKA_API_URL` and `PLANKA_TOKEN` environment variables are set. When they are not set, Planka comment fetching is skipped but the card-move translation still works.

**Expected webhook payload** (Planka format, simplified):
```json
{
  "list": {"name": "Approved"},
  "item": {
    "id": "<card_id>",
    "description": "thread_id: <project_id>\n..."
  }
}
```

**Behaviour**:
- If `list.name` is not `"Approved"` or `"Rejected"`: return `{"status": "ignored", "list": "<name>"}` (HTTP 200)
- If `list.name == "Approved"`: translate to `action="continue"` and resume the graph
- If `list.name == "Rejected"`: translate to `action="terminate"` and resume the graph
- `thread_id` SHALL be extracted from the card description using the pattern `thread_id: <value>`
- If `thread_id` cannot be extracted: return `{"status": "error", "detail": "thread_id not found in card description"}` (HTTP 200)
- If `PLANKA_TOKEN` is set: fetch the latest comment on the card and include it as `notes`

**Response** (on successful resume):
```json
{"status": "ok", "project_id": "<string>", "action": "<continue|terminate>"}
```

#### Scenario: Approved card resumes with continue
- **WHEN** a Planka card is moved to the "Approved" list and the payload contains `thread_id: qa_001`
- **THEN** the graph for `qa_001` is resumed with `{"action": "continue"}` and response is `{"status": "ok", ...}`

#### Scenario: Rejected card terminates
- **WHEN** a Planka card is moved to the "Rejected" list
- **THEN** the graph is resumed with `{"action": "terminate"}`

#### Scenario: Unrecognised list name is ignored
- **WHEN** a Planka card is moved to the "In Progress" list
- **THEN** the response is `{"status": "ignored", "list": "In Progress"}` and no graph action occurs

#### Scenario: Missing thread_id returns error
- **WHEN** the card description does not contain `thread_id: ...`
- **THEN** the response is `{"status": "error", "detail": "thread_id not found in card description"}`

---

### Requirement: Plugin auto-registration on startup
`main.py` SHALL call `framework.plugin_registry.discover_plugins()` before mounting the API app, ensuring all plugins under `projects/*/plugin.py` are registered before the first request is handled.

#### Scenario: Plugins available on first API request
- **WHEN** the FastAPI process starts and the first `/resume` request arrives
- **THEN** all plugins discovered under `projects/` are resolvable by `resolve_plugin(name)`
