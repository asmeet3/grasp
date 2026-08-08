# Grasp Access-Control Plan

## Objective

Make authorization server-enforced, default-deny, organization-scoped, recoverable during first-run bootstrap, and auditable. Browser controls are usability aids only; they are never an authorization boundary.

## Roles and permissions

| System role | Intended permissions |
| --- | --- |
| `member` | Query and contribute |
| `knowledge_editor` | Member permissions plus knowledge review |
| `operator` | Editor permissions plus audit visibility and agents |
| `administrator` | All permissions, including `manage_users` |

Organization job titles such as Associate, Manager, and Partner remain separate from system roles and do not grant security permissions.

## Security invariants

1. Every user-management endpoint requires an authenticated session with `manage_users`.
2. Only `administrator` has `manage_users`.
3. The bootstrap key is not a permanent administrator credential.
4. Bootstrap access works only while no approved administrator exists and can establish only the first administrator.
   Synthetic migration identities that cannot authenticate do not satisfy this check.
5. Administrators can list or mutate users only in their own organization.
6. Administrators cannot demote or revoke themselves.
7. Access changes are validated again on the server, regardless of the browser request.
8. Approval, revocation, job-title changes, and system-role changes create append-only audit events with actor, target, organization, and before/after values.
9. Invalid roles, unapproved targets, cross-organization targets, and forbidden transitions fail closed.
10. Operators may enter the administration shell for agent and knowledge operations, but user-management APIs and UI remain Administrator-only.

## Request flows

### Normal administration

1. Verify the bearer session.
2. Build the authenticated authorization context from the database-backed user record.
3. Require `manage_users`.
4. Resolve the target within the actor's organization.
5. Validate the requested state transition.
6. Persist the change and its audit event.
7. Return the updated public user record.

### First-run bootstrap

1. Compare the configured bootstrap key in constant time.
2. Verify that no approved administrator exists.
3. Permit listing accounts solely for choosing or establishing the first administrator.
4. Require the first approved account to receive `administrator` access, or let an already-approved signed-in account claim it.
5. Disable bootstrap-key authorization as soon as an administrator exists.

## UI behavior

- Use explicit table column widths whose total is 100%; keep the access selector inside its own cell.
- Disable the current administrator's own access selector and explain why in a tooltip.
- Open a confirmation dialog before an access change. Show the target user and the old and new access levels.
- Revert the selector when the dialog is cancelled or the API rejects the change.
- Show a success notification only after the server confirms persistence.

## Verification

- A member cannot list users or change access, even when sending the old bootstrap key after setup.
- A valid administrator can change another same-organization user's access.
- Self-demotion and self-revocation return a conflict response.
- Cross-organization user IDs are indistinguishable from missing users.
- The bootstrap key stops working after the first administrator exists.
- The first bootstrap approval cannot create a non-administrator account.
- Static JavaScript syntax, Python linting, API tests, and the full test suite pass.

## Follow-up hardening

- Move first-administrator creation into a database transaction protected by a PostgreSQL advisory lock for multi-replica bootstrap races.
- Add an administrator-facing audit-history screen sourced from `audit_events`.
- Add step-up authentication for administrator promotion and other high-risk transitions if Grasp is exposed beyond a trusted internal network.
