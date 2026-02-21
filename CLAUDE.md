# CLAUDE.md — Developable Backend

## Project Overview

**Developable** is an AI-powered backend code generator. It takes a database entity schema and business requirements as input and produces a complete, production-ready REST API (FastAPI or Express.js) using a multi-agent, human-in-the-loop workflow.

The system is itself a FastAPI application that orchestrates multiple Claude-powered agents to analyze, propose, and generate code.

---

## Repository Structure

```
backend/
├── main.py                          # FastAPI app entry point, all API routes
├── requirements.txt                 # Python dependencies
├── README.md                        # User-facing documentation
├── .gitignore
│
├── models/
│   ├── __init__.py
│   └── schemas.py                   # All Pydantic models / domain types
│
├── services/
│   ├── __init__.py
│   └── claude_service.py            # Anthropic API wrapper (ClaudeService)
│
├── agents/
│   ├── __init__.py
│   ├── schema_analyzer.py           # Agent: analyzes entity schema
│   ├── architecture_proposer.py     # Agent: proposes framework + architecture
│   ├── code_generator.py            # Agent: generates all source files
│   ├── tests_generator.py           # Agent: generates unit + integration tests
│   └── prompts/
│       ├── __init__.py
│       ├── code_generation_prompts.py   # Prompt templates for code generation
│       └── test_generation_prompts.py   # Prompt templates for test generation
│
└── utils/
    ├── __init__.py
    └── parsers.py                   # Schema parsing (SQL, JSON, Mongoose, SQLAlchemy)
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.104.1 |
| Server | Uvicorn (with standard extras) |
| Data validation | Pydantic v2 |
| AI model | Anthropic SDK (`anthropic==0.40.0`), model `claude-sonnet-4-20250514` |
| SQL parsing | sqlparse 0.4.4 |
| Templating | Jinja2 3.1.2 |
| HTTP client | httpx 0.25.1 |
| Config | python-dotenv |

---

## Environment Variables

Create a `.env` file in the project root (see `.env.example` in generated projects):

```env
ANTHROPIC_API_KEY=sk-ant-...   # Required — Anthropic API key
```

The `.env` file is git-ignored. The app will fail to start if `ANTHROPIC_API_KEY` is missing.

---

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server (port 8000)
python main.py
# or
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Interactive API docs are available at `http://localhost:8000/docs` (Swagger UI).

---

## API Workflow

The system implements a **linear state machine** per session. Sessions are stored in-memory (`workflow_sessions: Dict[str, WorkflowState]`). Use Redis in production.

### Step-by-step flow

```
POST /api/parse-schema    (optional) Convert raw schema text to EntitySchema format
        ↓
POST /api/analyze         Create session, analyze schema → returns session_id
        ↓
POST /api/propose/{id}    Generate architecture proposal (framework, endpoints, patterns)
        ↓
POST /api/feedback/{id}   Human approves or overrides the proposal
        ↓
POST /api/generate/{id}   Generate full source code (requires approved=true)
        ↓
POST /api/generate-tests/{id}  (incomplete — see Known Issues below)
```

### Session states

| `current_step` value | Meaning |
|---|---|
| `"input"` | Initial state |
| `"analyzed"` | `/api/analyze` completed |
| `"proposed"` | `/api/propose` completed |
| `"feedback_received"` | `/api/feedback` completed |
| `"completed"` | `/api/generate` completed |

Endpoints enforce state ordering and return HTTP 400 if called out of sequence.

### All endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | API info |
| GET | `/health` | Health check |
| POST | `/api/parse-schema` | Parse SQL/JSON/Mongoose/SQLAlchemy schema |
| POST | `/api/analyze` | Step 1: analyze schema, create session |
| POST | `/api/propose/{session_id}` | Step 2: generate architecture proposal |
| POST | `/api/feedback/{session_id}` | Step 3: submit human approval/override |
| POST | `/api/generate/{session_id}` | Step 4: generate complete source code |
| POST | `/api/generate-tests/{session_id}` | Step 5: generate tests (incomplete) |
| GET | `/api/status/{session_id}` | Get session state |
| DELETE | `/api/session/{session_id}` | Clean up session |

---

## Domain Models (`models/schemas.py`)

### Core enums

```python
FrameworkType: "fastapi" | "express"
ApplicationType: "rest_api" | "batch_job" | "event_driven"
```

### Key Pydantic models

- **`FieldSchema`** — A single entity field: `name`, `type`, `constraints: List[str]`, `description`
- **`EntitySchema`** — Full entity: `entity_name`, `fields`, `primary_key`, `indexes`, `relationships`
- **`ProjectInput`** — User input: `entity_schema`, `business_requirements`, `preferred_language` (`python`|`javascript`), `db_type` (`postgresql`|`mongodb`|`mysql`)
- **`ArchitectureProposal`** — AI output: `application_type`, `framework`, `architecture_pattern`, `suggested_endpoints`, `optimization_strategies`, `rationale`
- **`HumanFeedback`** — Human review: `approved: bool`, optional overrides for `application_type`, `framework`, `custom_requirements`
- **`GeneratedCode`** — Final output: `framework`, `files: Dict[str, str]` (filename → content), `dependencies: List[str]`
- **`WorkflowState`** — Per-session state holding all the above plus `session_id` and `current_step`

---

## Agents

All agents receive a `ClaudeService` instance via constructor injection.

### `SchemaAnalyzer` (`agents/schema_analyzer.py`)
- **`analyze(project_input) → Dict`** — Calls Claude with a text prompt, returns `{entity_analysis: str, entity_name: str, complexity_level: "low"|"medium"|"high"}`
- Complexity is determined by field count and relationship count (≤5 fields = low, ≤10 = medium, else high)

### `ArchitectureProposer` (`agents/architecture_proposer.py`)
- **`propose(project_input, entity_analysis) → ArchitectureProposal`** — Calls Claude for structured JSON response, parses into `ArchitectureProposal`

### `CodeGenerator` (`agents/code_generator.py`)
- **`generate(project_input, proposal, feedback) → GeneratedCode`** — Dispatches to `_generate_fastapi_code` or `_generate_express_code`
- Multi-step internal process:
  1. Create a generation plan (list of files grouped logically)
  2. Generate each file individually via Claude (low temperature = 0.1)
  3. Generate `Dockerfile`, `docker-compose.yml`, `.dockerignore`
  4. Generate dependency list
- FastAPI files use `max_tokens=8192`; Express files use `max_tokens=4096`
- Markdown fences are stripped from all generated content

### `TestsGenerator` (`agents/tests_generator.py`)
- **`generate(project_input, proposal, feedback, generated_code) → Dict[str, str]`** — Generates test files
- FastAPI: pytest + async fixtures, produces `pytest.ini` and `tests/conftest.py`
- Express: Jest + Supertest, produces `jest.config.js`
- **Note:** The `/api/generate-tests` endpoint in `main.py` does not correctly call this agent (missing arguments) — see Known Issues

---

## `ClaudeService` (`services/claude_service.py`)

Central wrapper around the Anthropic SDK.

```python
claude_service.generate_response(system_prompt, user_message, temperature=0.7, max_tokens=4096) → str
claude_service.generate_structured_response(system_prompt, user_message, response_schema, temperature=0.7) → Dict
```

- Model: `claude-sonnet-4-20250514`
- `generate_structured_response` appends a JSON schema instruction to the system prompt, then strips markdown fences from the response before parsing
- All Claude calls are made synchronously via the Anthropic client (despite `async def` wrappers — the underlying SDK call is blocking)

---

## Schema Parsing (`utils/parsers.py`)

The `parse_schema(schema_input, format)` function auto-detects or explicitly handles:

| Format | Detection | Parser |
|---|---|---|
| SQL DDL | `CREATE TABLE` keyword | `SchemaParser.parse_sql_ddl` via `sqlparse` + regex |
| JSON | Starts with `{` | `SchemaParser.parse_json_schema` |
| Mongoose | `mongoose.Schema` in text | `SchemaParser.parse_mongoose_schema` via regex |
| SQLAlchemy | `Column(` in text | `SchemaParser.parse_sqlalchemy_model` via regex |

Additional utilities:
- **`TypeMapper`** — Maps SQL types to Python or TypeScript types
- **`ValidationRuleExtractor`** — Extracts validation rules from field constraints

---

## Prompt Architecture

Prompts are centralized in `agents/prompts/`:

- **`CodeGenerationPrompts`** — Static methods returning system prompts and user messages for: planning, per-file FastAPI generation, per-file Express generation, Dockerfile, docker-compose, .dockerignore, dependencies, setup instructions
- **`TestGenerationPrompts`** — Static methods for: test planning, FastAPI test files, Express test files, pytest.ini, conftest.py, jest.config.js

**Key prompt conventions:**
- Code generation prompts use low temperature (0.1) and instruct Claude to output raw code only (no markdown, no explanations)
- Architecture/planning prompts use medium temperature (0.5) and request structured JSON
- System prompts are explicit: "Output ONLY raw [language] code"

---

## Generated Output Structure

### FastAPI output
```
{entity}-api/
├── main.py
├── requirements.txt
├── config.py
├── .env.example
├── README.md
├── models/
│   ├── entity.py
│   └── database.py
├── routes/
│   └── entity_routes.py
├── services/
│   └── entity_service.py
├── repositories/
│   └── entity_repository.py
├── Dockerfile
├── docker-compose.yml
└── .dockerignore
```

### Express output
```
{entity}-api/
├── server.js
├── package.json
├── .env.example
├── README.md
├── routes/
├── controllers/
├── services/
├── repositories/
├── models/
├── middleware/
├── config/
├── Dockerfile
├── docker-compose.yml
└── .dockerignore
```

---

## Known Issues / Incomplete Features

1. **`/api/generate-tests` endpoint is broken** (`main.py:266–270`): The call to `tests_generator.generate(...)` passes no arguments. The `TestsGenerator.generate()` method requires `project_input`, `architecture_proposal`, `human_feedback`, and `generated_code`. This endpoint needs to be wired to session state like the other steps.

2. **In-memory session storage**: `workflow_sessions` is a plain dict in the FastAPI process. Sessions are lost on restart and cannot scale horizontally. Replace with Redis for production.

3. **Synchronous Claude calls inside `async def`**: `ClaudeService` uses the synchronous Anthropic client inside async route handlers. Under load this will block the event loop. Use `anthropic.AsyncAnthropic` or wrap calls in `asyncio.to_thread`.

4. **CORS is fully open**: `allow_origins=["*"]` — tighten for production.

5. **No authentication**: All endpoints are publicly accessible.

---

## Development Conventions

- **Python version**: 3.11+ (implied by Docker requirements in prompts)
- **Async**: All route handlers and agent methods are `async def`; maintain this pattern
- **Error handling**: All routes catch exceptions and raise `HTTPException`; agents propagate exceptions upward
- **Type hints**: Use Pydantic models for request/response bodies; use `Dict`, `List`, `Optional` from `typing`
- **Imports**: Absolute imports from project root (e.g., `from models.schemas import ...`, `from services.claude_service import ...`)
- **No test suite** exists for the generator itself (only for the generated projects)

## Adding a New Agent

1. Create `agents/my_agent.py` with a class that takes `ClaudeService` in `__init__`
2. Add prompt methods to `agents/prompts/code_generation_prompts.py` or a new prompts file
3. Export from `agents/prompts/__init__.py`
4. Instantiate the agent in `main.py` alongside the other agents
5. Wire it to a new endpoint following the session-state pattern

## Adding a New Framework

1. Add a value to `FrameworkType` enum in `models/schemas.py`
2. Add a `_generate_{framework}_code` method to `CodeGenerator`
3. Add prompt methods to `CodeGenerationPrompts` (system prompt + user message for file generation)
4. Add corresponding test generation support in `TestsGenerator` and `TestGenerationPrompts`
5. Update the `generate()` dispatch in `CodeGenerator`
