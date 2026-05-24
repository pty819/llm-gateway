# API Client And State

## Client Shape

Create one frontend API client responsible for:

- Base URL configuration.
- Admin token header injection.
- JSON request/response handling.
- Error normalization.
- Redaction helper for secret-looking values.

Suggested TypeScript interface:

```ts
type ApiError = {
  status: number;
  message: string;
  detail?: unknown;
};

type AdminClient = {
  get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T>;
  post<T>(path: string, body: unknown): Promise<T>;
  patch<T>(path: string, body: unknown): Promise<T>;
};
```

## Backend Enum Types

Frontend should model these exact strings:

```ts
type SubjectType = "user" | "service";
type ResourceState = "active" | "disabled";
type IPPolicyMode = "all_pass" | "allowlist";
type RouterPolicy = "consistent_hash" | "cache_aware";
type EndpointFamily = "openai_chat" | "anthropic_messages";
type RequestOutcome =
  | "success"
  | "auth_failure"
  | "policy_denial"
  | "rate_limited"
  | "adapter_failure"
  | "upstream_failure"
  | "client_cancelled";
```

## Resource Types

The frontend should keep generated or hand-written resource types close to backend fields. Do not rename backend keys in API objects; map only for display labels.

Minimum resources:

- Subject
- Project
- ProjectMembership
- GatewayKey
- ModelAlias
- ModelEntitlement
- UpstreamTarget
- RouterCommandConfig response wrapper
- RatePolicy
- RequestFactSummaryRow
- AuditEvent

## State Management

MVP can use Svelte stores or route-level load functions without a global cache library.

Recommended stores:

- `adminToken`
- `runtimeStatus`
- `resourceLabels`: ID-to-label maps for subjects, projects, keys, model aliases, upstreams.
- `toastQueue`

Per-screen state:

- table sort/filter/search
- selected row
- drawer/dialog open state
- form draft
- saving/loading/error flags

## Data Refresh

Rules:

- After a successful mutation, refetch the affected list and close the form only after the refetch succeeds.
- Health checks refresh only the selected upstream row.
- Usage and audit refresh manually and on entering the route.
- Overview refreshes all overview calls together.

## Client-Side Validation

CIDR validation:

- Accept IPv4 and IPv6 CIDR notation.
- Accept host IPs by converting or warning that backend accepts CIDR strings; recommended input is CIDR.
- Reject blank entries.

Rate policy validation:

- Scope must be `key`, `subject`, or `project`.
- Scope ID must be selected from loaded resources.
- At least one of RPM or concurrency should be provided.
- Values must be integers greater than or equal to zero.

Router command validation:

- At least one worker URL.
- Every worker URL starts with `http://` or `https://`.
- Port is 1 to 65535.
- Extra args are valid JSON object.

Upstream validation:

- Base URL starts with `http://` or `https://`.
- Health path starts with `/`.
- Extra headers are valid string-to-string JSON.

## Error Handling

Backend error forms:

- FastAPI validation errors may return `detail` as a list.
- Gateway proxy adapter errors return `{ error: { type, message } }`.
- Admin auth failures return `detail`.

Frontend display:

- Prefer a compact message in toast.
- Include raw detail in collapsible error panel for operators.
- Never include secret fields in copied error text.

## Security Display Rules

- Do not render `key_hash`.
- Do not render `api_key_value`.
- Mask values whose key name includes `key`, `token`, `secret`, `password`, or `authorization`.
- Gateway plaintext key can be displayed only from the create-key response and only until the dialog closes.

## API Coverage Checklist

- [ ] Every `GET /admin/*` list endpoint has a visible screen or a screen-level dependency.
- [ ] Every current `POST /admin/*` endpoint has a create form.
- [ ] Every current `PATCH /admin/*` endpoint has an edit or state action.
- [ ] `/health/ready` and `/admin/diagnostics` are visible in Overview or Diagnostics.
- [ ] `/v1/models` has at least an optional gateway-key diagnostics path or is explicitly deferred.
