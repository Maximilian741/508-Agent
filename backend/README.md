# Backend (FastAPI)

Service-oriented remediation engine scaffolding.

## Local run

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --port 8000
```

Health check:

```bash
curl http://localhost:8000/healthz
```
