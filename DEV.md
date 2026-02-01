# 508 Agent Dev Workflow

## Start backend (dev)
```
cd backend
python dev_run.py
```

`dev_run.py` selects a free port, binds to `0.0.0.0`, and writes the backend URL to:
- `backend/.runtime/backend_url.txt`
- `frontend/frontend/public/backend_url.txt`

The URL is always written as `http://127.0.0.1:<port>` to avoid IPv6 localhost issues.

## Start frontend (Expo web)
```
cd frontend/frontend
npm run web
```

The web app fetches `/backend_url.txt` from its own origin (e.g. `http://localhost:8081/backend_url.txt`) and uses it as the base URL.

## Troubleshooting
- If `localhost` resolves to IPv6 and fails, the app auto-normalizes to `127.0.0.1` after a failed health check.
- CORS is enabled for `localhost:8081` and `127.0.0.1:8081` (and 8080) in dev.

