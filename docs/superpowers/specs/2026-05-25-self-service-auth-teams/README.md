# Self-Service Auth And Team Permissions

This spec implements the next MVP slice after the operator console: account login, self-service registration, user-owned gateway keys, and team-based model permissions.

Core rule:

```text
user usable models = union(model grants for all active teams the user belongs to)
```

Compatibility rule:

```text
effective access = legacy direct entitlement OR team grant
```

Built-ins:

- `guest` team for self-registered users.
- `admin` team for administrators.
- Bootstrap admin account from environment settings.
- All model aliases are granted to the `admin` team.

See `.omx/plans/prd-self-service-auth-teams.md` and `.omx/plans/test-spec-self-service-auth-teams.md` for the implementation contract.
