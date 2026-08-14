# Backup And Restore Runbook

## Scope

This runbook protects gateway state stored in `accounts.db`, private deployment settings stored in `config.json`, and the external `OREATE_ENCRYPTION_KEY`. The encryption key must be backed up separately from the database. Anyone with both can decrypt account credentials.

## Backup Inputs

- `accounts.db`
- `config.json`
- applied migration files under `migrations/`
- the exact application revision
- `OREATE_ENCRYPTION_KEY`, stored in a separate secret manager or sealed operator vault

Never include live cookies, plaintext account passwords, or API keys in notes, tickets, or chat transcripts.

## Online Safety Gate

Prefer a maintenance window:

1. Stop the reverse proxy from sending new generation requests.
2. Wait for queued/running tasks to complete or deliberately pause the worker.
3. Confirm there is exactly one gateway process.
4. Export the current `OREATE_ENCRYPTION_KEY` from the secret manager into the service environment only, not into the shell history.

## Backup Command

Use the admin backup endpoint when available because it revokes sensitive session state from the backup flow:

```bash
curl -H "Authorization: Bearer <admin-session-token>" \
  -o backup.zip \
  http://127.0.0.1:8890/api/admin/backup
```

For an offline filesystem backup, stop the service first and copy:

```bash
cp accounts.db backups/accounts.db.$(date +%Y%m%d-%H%M%S)
cp config.json backups/config.json.$(date +%Y%m%d-%H%M%S)
```

Store the encryption key separately:

```text
OREATE_ENCRYPTION_KEY: save in secret manager only
```

## Backup Verification

Run these checks against the backup artifact or copied files:

```bash
python -m py_compile server.py banti_token_generator.py
python - <<'PY'
from pathlib import Path
blob = Path("backups/accounts.db").read_bytes()
for marker in (b"OUID" + b"=", b"ouss" + b"=", b"plain-password"):
    assert marker not in blob, marker
print("backup secret spot check ok")
PY
```

If the backup contains plaintext account credentials, do not promote it as a production backup. Re-run secret migration with `OREATE_ENCRYPTION_KEY` present, then back up again.

## Restore Procedure

1. Stop inbound traffic at the reverse proxy.
2. Stop the gateway service.
3. Move the current broken files aside, do not overwrite them:

```bash
mkdir -p restore-hold
mv accounts.db restore-hold/accounts.db.broken
mv config.json restore-hold/config.json.broken
```

4. Restore `accounts.db` and `config.json` from the selected backup.
5. Export the matching `OREATE_ENCRYPTION_KEY`.
6. Start one gateway worker.
7. Run restore verification before traffic returns.

## Restore Verification

Required restore verification:

```bash
curl http://127.0.0.1:8890/healthz
curl http://127.0.0.1:8890/readyz
python -m unittest tests.security_regression_tests.SecurityRegressionTests.test_admin_restore_revokes_existing_sessions_and_requires_relogin -v
python -m unittest tests.security_regression_tests.SecurityRegressionTests.test_account_sensitive_fields_are_encrypted_at_rest -v
```

Manual checks:

- Admin login works with the restored `config.json` credentials.
- Old browser admin sessions are rejected. These stale admin sessions must not survive restore.
- API key list shows `key_preview` only, not full keys.
- Accounts can be decrypted with the restored `OREATE_ENCRYPTION_KEY`.
- `/v1/capabilities` returns model and scene data from verified accounts.

## Stale Session Revocation

After any restore, stale admin sessions are treated as unsafe because they may predate the restored database state. The backup/restore path must revoke or invalidate `admin_sessions` so a copied browser session cannot administer the restored service.

If verification shows old admin sessions still work, keep traffic closed and either restore a newer backup or clear `admin_sessions` manually while the service is stopped:

```bash
sqlite3 accounts.db "DELETE FROM admin_sessions;"
```

Then restart the service and repeat restore verification.

## Rollback From A Bad Restore

1. Stop the service.
2. Move the failed restore files into `restore-hold/failed-restore-*`.
3. Move the previous files from `restore-hold/*.broken` back into place.
4. Export the matching `OREATE_ENCRYPTION_KEY` for those files.
5. Start one worker.
6. Run `/healthz`, `/readyz`, and the restore verification checks again.

Do not mix a database backup with an unrelated encryption key.
