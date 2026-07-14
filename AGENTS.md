# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python 3.12 FastAPI service for coordinating multi-agent tasks.

- `app/main.py` creates the application and wires API routers and services.
- `app/api/` contains HTTP route modules for agents and tasks.
- `app/core/` defines Pydantic models, enums, and model-client behavior.
- `app/services/` contains storage, task orchestration, and tool execution logic.
- `app/workflows/` contains the LangGraph task workflow.
- `tests/` mirrors application behavior with pytest API and unit tests.
- `docs/iterations/` records implementation iterations and design notes.

Keep new modules within the matching layer; avoid placing business logic in route handlers.

## Build, Test, and Development Commands

Create an isolated environment and install runtime plus test dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

Run the local API with automatic reload:

```bash
uvicorn app.main:app --reload
```

Run the complete test suite, or target one module during development:

```bash
pytest -q
pytest -q tests/test_tasks.py
```

## Coding Style & Naming Conventions

Follow standard Python conventions: four-space indentation, type annotations for public functions, `snake_case` for modules/functions/variables, and `PascalCase` for classes and Pydantic models. Keep imports grouped as standard library, third-party, then local `app` imports. Prefer small service methods and explicit model fields over untyped dictionaries when data has a stable schema. No formatter or linter is currently configured, so match surrounding code and keep diffs focused.

## Testing Guidelines

Tests use pytest and FastAPI's `TestClient`. Name files `test_<area>.py` and tests `test_<expected_behavior>`. Use `tmp_path` for file-backed state and `monkeypatch` to isolate model calls or external behavior. Add or update tests whenever API responses, workflow transitions, storage, or tool execution behavior changes. Real model calls are disabled by the autouse fixture in `tests/conftest.py`.

## Commit & Pull Request Guidelines

Git history currently contains only the initial commit, so no established commit convention exists. Use short, imperative subjects such as `Add task retry handling`, and keep each commit focused. Pull requests should explain the problem, summarize the solution, list verification commands, and note API or configuration changes. Link relevant issues; include example requests/responses when endpoint behavior changes.

## Security & Configuration Tips

Use environment variables documented in `README.md` and `.env.example`. Never commit API keys, credentials, private endpoints, generated agent data, or local virtual environments. Use placeholder values in documentation and tests.
