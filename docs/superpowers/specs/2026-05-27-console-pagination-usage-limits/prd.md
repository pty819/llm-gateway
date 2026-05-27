# Console Pagination And Usage Limits PRD

## Problem

The admin console still renders several unbounded lists and analytics tables. Even
when backend queries succeed, large tables make the UI noisy and slow. Usage cards
also default to whatever range is currently empty or broad, while the operator's
normal need is recent one-week pressure.

## Goals

- Merge the old overview and usage surfaces into a single usage-oriented admin
  landing page.
- Default admin usage windows to the most recent seven days.
- Keep manual range controls for hour, day, week, and month inspection by editing
  the date range.
- Show recent/top results by default:
  - time buckets: 5 most recent buckets
  - drilldown: top 5
  - usage summary detail: top 5 by token pressure
  - ranking: top 50 with pagination
  - audit: top 50 per page
  - team memberships: top 30 per page with team/user/role/state filters
  - users/projects/keys: top 30 per page with practical filters
- Cap long user selector option lists at 20 options after local search.

## Non-goals

- Replacing all admin list endpoints with server-side pagination in this pass.
- Changing permission semantics.
- Removing Postgres compatibility endpoints.

## Functional Requirements

- The default active admin page is usage; the old overview nav entry is removed.
- Usage query calls DuckDB with `limit=5` for buckets and drilldown.
- Ranking query defaults to `limit=50`.
- User table supports fuzzy search and name sorting.
- Project, gateway key, team-membership, ranking, and audit tables show paginated
  slices with next/previous controls.
- Gateway key filters include user, project, and state.
- Team membership filters include team, user, role, and state.
- Select dropdowns using user search should display at most 20 matching users.

## Acceptance Criteria

- Opening/refreshing the admin console does not render unbounded audit/user/key/team
  membership tables.
- The usage banner represents the default one-week range after login.
- Usage trend and drilldown visible tables contain no more than five rows.
- Ranking and audit pages show at most fifty rows per page.
- User/project/key/team membership pages show at most thirty rows per page.
