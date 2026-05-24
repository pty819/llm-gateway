# Test Spec: Self-Service Auth And Team Permissions

## Backend Coverage

- Registration creates a subject, personal project, gateway key, guest membership, and session.
- Login returns a session token and user profile.
- `/auth/me` returns teams, available models, and redacted keys.
- `/auth/keys` issues a one-time plaintext key for the logged-in user.
- Admin bootstrap creates `guest`, `admin`, bootstrap admin, and admin model grants.
- Team grants allow `/v1/models` access.
- A user in multiple teams gets the union of model grants.
- Existing project/key/subject entitlements still allow access.
- Admin-token diagnostics and admin APIs remain compatible.

## Frontend Coverage

- Login/register screen renders.
- Admin account can load the admin console.
- Team management controls typecheck and build.
- User session state can call authenticated APIs.

## Verification Commands

```bash
uv run python scripts/init_db.py
uv run pytest -q

cd frontend
npm run check
npm run test
npm run build
npm run test:e2e
```

## Latest Evidence

- Backend: 12 tests passed.
- Frontend unit tests: 8 passed.
- Frontend e2e: 2 passed.
- Frontend typecheck: 0 errors, 0 warnings.
- Live backend auth smoke: `POST /auth/login` returned HTTP 200 for bootstrap admin.
