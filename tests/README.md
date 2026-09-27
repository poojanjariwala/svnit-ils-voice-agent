# tests/

This folder is for integration and end-to-end tests.

## Structure

```
tests/
├── README.md               ← this file
├── test_integration.py     ← Suhas: full flow integration tests
└── test_voice_flow.py      ← Suhas: Vonage webhook flow tests
```

> Unit tests live in `backend/test_api.py`
> Integration tests that require a running server go here.

## Running tests

```bash
# Unit tests (no server required)
cd backend
pytest test_api.py -v

# Integration tests (backend must be running)
cd tests
pytest test_integration.py -v
```
