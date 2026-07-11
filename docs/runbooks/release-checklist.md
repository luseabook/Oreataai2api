# Release Checklist

## Scope

Run this checklist before tagging, shipping, or opening customer traffic for a gateway build. Passing this checklist means the automatable checks are green. It does not replace real-credit video validation for advanced scenes.

## Pre-Release Preconditions

- No real `config.json`, `accounts.db`, cookie, token, or customer API key is staged.
- `OREATE_ENCRYPTION_KEY` is present in the deployment environment and stored separately from backups.
- Node.js is installed because `banti_jt_helper.js` is required for generation traffic.
- Deployment is configured as a single application worker.
- Reverse proxy and TLS acknowledgements match the actual ingress design.

## Required Commands

Run the full repository tests:

```bash
python -m unittest discover -s tests -p "*_tests.py" -v
```

Compile core Python entrypoints:

```bash
python -m py_compile server.py banti_token_generator.py
```

Parse the embedded admin JavaScript:

```bash
node -e 'const fs=require("fs"); const text=fs.readFileSync("server.py","utf8"); const html=text.match(/ADMIN_HTML = """([\s\S]*?)"""/)[1]; const script=html.match(/<script>([\s\S]*?)<\/script>/)[1]; new Function(script); console.log("js parse ok");'
```

Check whitespace and patch formatting:

```bash
git diff --check
```

Run a sensitive diff scan before staging:

```bash
SECRET_SCAN_PATTERN='AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._~+/=-]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|OUID'"="'|ouss'"="'
git diff -- . | rg -n "$SECRET_SCAN_PATTERN" -S
```

This sensitive diff scan should return no real secrets. Test fixtures with short dummy values are acceptable only when they cannot authenticate anywhere.

## Runtime Readiness Checks

After deployment to the target host:

```bash
curl http://127.0.0.1:8890/healthz
curl http://127.0.0.1:8890/readyz
```

Then verify:

- Admin login succeeds with non-default credentials.
- `/api/admin/apikeys` returns full key material only immediately after create.
- `/api/tasks`, `/api/admin/usage`, `/api/admin/uploads`, and `/api/admin/cost-report` return paginated operator data.
- `/v1/capabilities` exposes only enabled model and scene policies.
- Advanced scenes without live evidence remain disabled for ordinary external keys.

## Release Decision

Ship only when:

- all required commands pass,
- readiness checks pass,
- backup restore verification has been run for the target environment,
- no sensitive diff scan finding is unexplained,
- and any credit-spending validation has explicit human approval.

If automatable work is green but advanced video scenes still lack live samples, report the state as:

```text
S2-ready for already verified capabilities, but not full S2 for advanced video scenes until approved live samples exist.
```
