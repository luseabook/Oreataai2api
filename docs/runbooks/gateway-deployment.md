# Gateway Deployment Runbook

## Scope

This runbook covers a small-production deployment of the OreateAI image/video gateway. The current runtime is designed for one process-local scheduler, one rate-limit bucket set, and one task worker, so it must run as a single application worker.

## Prerequisites

- Python 3.11 or newer.
- Node.js 18 or newer. The live generation path shells out through `banti_jt_helper.js`, so Node is a runtime dependency, not a development-only tool.
- `OREATE_ENCRYPTION_KEY` generated with Fernet and stored outside Git, logs, shell history, and ordinary database backups.
- A private `config.json` copied from `config.example.json` and edited with non-default admin credentials.
- A current backup of `accounts.db`, `config.json`, and the separately stored encryption key before upgrade or migration.

Generate a key in a controlled terminal:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it before startup:

```bash
export OREATE_ENCRYPTION_KEY="<fernet-key>"
```

PowerShell:

```powershell
$env:OREATE_ENCRYPTION_KEY = "<fernet-key>"
```

## Install And Smoke Test

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m py_compile server.py banti_token_generator.py
node --version
python server.py
```

In another terminal:

```bash
curl http://127.0.0.1:8890/healthz
curl http://127.0.0.1:8890/readyz
```

`/readyz` must return 200 before customer traffic is allowed.

## Worker Boundary

Run exactly one single application worker:

```bash
export OREATE_APP_WORKERS=1
export WEB_CONCURRENCY=1
uvicorn server:app --workers 1
```

If Gunicorn is used:

```bash
export OREATE_APP_WORKERS=1
export GUNICORN_CMD_ARGS="--workers 1"
gunicorn server:app --worker-class uvicorn.workers.UvicornWorker --workers 1
```

The app validates worker declarations and also takes a database-bound lock file. If a process manager needs a separate runtime directory, set `OREATE_WORKER_LOCK_PATH` to a writable path on the same host.

## Reverse Proxy And TLS

Keep the gateway bound to `127.0.0.1` where possible. Public traffic should terminate TLS at a reverse proxy such as Nginx, Caddy, Cloudflare Tunnel, or an equivalent managed ingress.

Only bind the gateway to `0.0.0.0` after all three deployment acknowledgements are set:

```json
{
  "deployment": {
    "allow_public_bind": true,
    "trust_reverse_proxy": true,
    "tls_terminated_by_proxy": true
  }
}
```

These flags document the deployment boundary. They do not add TLS by themselves. The reverse proxy must enforce HTTPS, forward only intended paths, and restrict direct access to the admin surface according to the operator's network policy.

## systemd Example

```ini
[Unit]
Description=OreateAI Gateway
After=network-online.target

[Service]
WorkingDirectory=/opt/oreateai
Environment=OREATE_ENCRYPTION_KEY=replace-with-secret-manager-injection
Environment=OREATE_APP_WORKERS=1
Environment=WEB_CONCURRENCY=1
ExecStart=/opt/oreateai/.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8890 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Prefer injecting `OREATE_ENCRYPTION_KEY` from the host secret manager instead of storing it literally in the unit file.

## Windows Service Notes

Use a service wrapper that starts one command only:

```powershell
$env:OREATE_ENCRYPTION_KEY = "<secret-manager-value>"
$env:OREATE_APP_WORKERS = "1"
$env:WEB_CONCURRENCY = "1"
python -m uvicorn server:app --host 127.0.0.1 --port 8890 --workers 1
```

Do not configure multiple service instances against the same `accounts.db`.

## Upgrade Steps

1. Stop inbound customer traffic at the reverse proxy.
2. Stop the gateway service.
3. Run the backup procedure in `docs/runbooks/backup-restore.md`.
4. Install dependencies with `python -m pip install -r requirements.txt`.
5. Start the gateway with `OREATE_ENCRYPTION_KEY` present.
6. Check `/healthz`, `/readyz`, admin login, `/v1/capabilities`, and a non-credit-spending dry request where possible.
7. Restore traffic at the reverse proxy.

## Rollback

1. Stop inbound traffic.
2. Stop the gateway.
3. Restore the last known-good code, `accounts.db`, and `config.json`.
4. Re-export the matching `OREATE_ENCRYPTION_KEY`.
5. Start one worker and run `/healthz` plus `/readyz`.
6. Re-open traffic only after restore verification passes.
