# PRD: Self-Service Auth And Team Permissions

## Goals

- Users can register with username/password.
- Registered users automatically receive a gateway key and join `guest`.
- Users can log in and see their identity, teams, keys, and available models.
- Users can issue additional personal gateway keys.
- Admins can manage teams, memberships, and model-team grants.
- Every model alias is granted to the `admin` team.
- A user's available models are the union of models granted to all active teams they belong to.
- Existing direct model entitlements remain compatible.

## Access Semantics

Effective model access:

```text
active legacy entitlement
OR
active team membership to a team with active model grant
```

Built-in bootstrap guarantees:

- `guest` team exists.
- `admin` team exists.
- bootstrap admin subject exists.
- bootstrap admin is an active member of `admin`.
- every model alias has an active grant to `admin`.

## API Surface

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `POST /auth/keys`
- `GET/POST/PATCH /admin/teams`
- `GET/POST /admin/team-memberships`
- `PATCH /admin/team-memberships/{membership_id}/state`
- `GET/POST /admin/model-team-grants`
- `PATCH /admin/model-team-grants/{grant_id}/state`

## Compatibility

- Gateway API clients still authenticate with gateway keys.
- Admin APIs still accept `x-admin-token`.
- Old subject/project/key/model entitlement grants still work.
