## ADDED Requirements

### Requirement: POST /resume endpoint
The server SHALL expose `POST /resume` to resume any paused LangGraph thread with a human decision.

**Request body** (`application/json`):
```json
{
  "project_id": "<string>",
  "decision": {
    "action": "approve | reject | continue | replan | terminate",
    "notes": "<optional string>",
    "reason": "<optional string, for reject>"
  }
}
```

**Response** (`200 OK`):
```json
{"status": "resumed", "project_id": "<string>"}
```

**Error responses:**
- `404` — project not found in `projects` table

The endpoint SHALL call `graph.invoke(Command(resume=decision), config)` and then record the decision in `checkpoint_decisions`.

#### Scenario: Successful resume via API
- **WHEN** `POST /resume` is called with a valid `project_id` and `decision`
- **THEN** the response is `{"status": "resumed", "project_id": "..."}` and the graph continues execution

#### Scenario: Unknown project returns 404
- **WHEN** `POST /resume` is called with a `project_id` that does not exist in `projects`
- **THEN** the server responds with HTTP 404 and `{"detail": "Project '<id>' not found."}`

---

### Requirement: POST /planka-webhook endpoint
The server SHALL expose `POST /planka-webhook` to receive Planka card-move events and translate them into graph resume calls.

The endpoint SHALL:
1. Read `payload["list"]["name"]` — if not `"Approved"` or `"Rejected"`, return `{"status": "ignored"}`
2. Extract `project_id` from the card description using the pattern `thread_id: <value>`
3. Map `"Approved"` → `action="continue"`, `"Rejected"` → `action="terminate"`
4. If `PLANKA_API_URL` and `PLANKA_TOKEN` are set, fetch the latest card comment as `notes`
5. Resume the graph with `Command(resume={"action": action, "notes": notes})`

**Response** (`200 OK`):
```json
{"status": "ok", "project_id": "<string>", "action": "<string>"}
```

#### Scenario: Approved card resumes with continue
- **WHEN** Planka sends a webhook with `list.name = "Approved"` and the card description contains `thread_id: my_project`
- **THEN** the graph is resumed with `{"action": "continue"}` and the response contains `"action": "continue"`

#### Scenario: Unrecognised list ignored
- **WHEN** Planka sends a webhook with `list.name = "In Progress"`
- **THEN** the server responds `{"status": "ignored", "list": "In Progress"}` without touching the graph

#### Scenario: Missing thread_id logs warning and returns error
- **WHEN** the card description does not contain `thread_id: ...`
- **THEN** the server returns `{"status": "error", "detail": "thread_id not found in card description"}`

---

### Requirement: GET /health endpoint
The server SHALL expose `GET /health` returning `{"status": "ok"}` with HTTP 200 for liveness checks.

#### Scenario: Health check always succeeds
- **WHEN** `GET /health` is called
- **THEN** the response is `{"status": "ok"}` with HTTP 200

---

### Requirement: Server configuration via environment variables
The server SHALL read configuration exclusively from environment variables at startup:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | psycopg3 connection string for PostgreSQL |
| `PLANKA_API_URL` | Planka REST API base URL (optional) |
| `PLANKA_TOKEN` | Planka Bearer token (optional) |

#### Scenario: Missing DATABASE_URL causes project resolution to fail
- **WHEN** `DATABASE_URL` is empty and `POST /resume` is called
- **THEN** the project lookup fails and returns HTTP 404 or 500