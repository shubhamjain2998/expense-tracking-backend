# Testing

The backend uses [pytest](https://docs.pytest.org/) for all automated tests. This document covers the layout, how to run tests, and the conventions for adding new ones.

## Layout

```
tests/
├── __init__.py
├── conftest.py                  # shared pytest fixtures
├── fixtures/
│   └── april_regalia.pdf        # real HDFC Regalia statement used as input
├── test_auth_cookie.py          # /auth endpoints and cookie handling
├── test_upload_pipeline.py      # /uploads end-to-end (PDF + text paths)
├── test_qa_group_c.py           # QA findings — group C
├── test_qa_group_e.py           # QA findings — group E
└── test_qa_group_f.py           # QA findings — group F
```

`pytest.ini` discovers anything named `test_*.py` under `tests/`. Deprecation warnings are silenced via `filterwarnings`.

## Running tests

```bash
pytest                              # full suite
pytest tests/test_upload_pipeline.py   # single file
pytest -k "auto_categorise"         # tests matching a name
pytest -x                           # stop on first failure
pytest --lf                         # re-run only last failures
pytest -vv                          # extra verbose
pytest -s                           # don't capture stdout (use with print debugging)
```

## Fixtures

Shared fixtures live in `tests/conftest.py`. The most-used today:

```python
@pytest.fixture(scope="session")
def april_regalia_pdf() -> Path:
    """HDFC Regalia April 2026 statement.

    Parses to 61 valid + 20 skipped rows. Use this as the input for any
    regression test of the upload pipeline.
    """
```

Reach for an existing fixture before introducing a new file under `tests/fixtures/`. If a new fixture is unavoidable, prefer the smallest synthetic example you can build in-test over checking in another binary.

## Writing a new test

1. **One file per router or feature area** is the working pattern. Add to an existing file if it fits; create `test_<feature>.py` if not.
2. **Use the FastAPI `TestClient`** rather than calling functions directly — this exercises serialisation, auth, and CORS the same way the real API does.
3. **Set up the DB explicitly** in the test (insert seed rows via the ORM), or use a fixture for the common case. There is intentionally no automatic database fixture today; tests are responsible for their own state.
4. **Cover both the happy path and the most likely failure mode** — `404` for missing rows, `409` for conflicts, `401` for missing auth.

```python
def test_create_category_conflict(client, authenticated_user):
    client.post("/categories", json={"name": "groceries"})
    r = client.post("/categories", json={"name": "groceries"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
```

## Coverage

A coverage threshold and HTML report are on the [roadmap](ROADMAP.md) (PR 3 of the repo-hardening pass). Until then, aim for at least one test per new endpoint and one per non-trivial helper.

## Continuous integration

A GitHub Actions workflow will run `pre-commit run --all-files` and `pytest` on every push to `main` and on every PR (PR 3 of the repo-hardening pass). Until that lands, run both locally before pushing.

## Related documents

- [`DEVELOPMENT.md`](DEVELOPMENT.md) — how to install deps and run the app
- [`ROADMAP.md`](ROADMAP.md) — what's planned for the test suite
