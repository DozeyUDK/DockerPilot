# Secure Deploy Approval Model

Approval version: **1**  
TTL: **≤ 10 minutes** (cannot exceed remaining plan TTL)

## States

`pending` → `approved` → `consumed`  
also: `pending|approved` → `expired|revoked`

Illegal transitions are rejected (`approval_transition` / CAS failure).

## Binding fields

- `approval_version`, `approval_id`
- `plan_id`, `plan_sha256`
- `actor` (from authenticated session — never from request JSON actor)
- `nonce` (CSPRNG)
- `issued_at`, `approved_at`, `expires_at`
- `session_id_hash` (hash of session material; **not** raw cookie)
- `status`

Never stored: TOTP, passwords, session cookie, OpenBao values, sudo password.

## Endpoints

- `POST /api/secure-deploy/plans/<plan_id>/approve` — step-up TOTP + plan hash confirm
- `POST /api/secure-deploy/approvals/<approval_id>/revoke` — step-up TOTP
- `GET /api/secure-deploy/approvals/<approval_id>`

No apply endpoint.

## Step-up MFA

Approve/revoke require a fresh TOTP in the JSON body (`totp_code`), validated with rate limit + single-use window markers. Codes are popped from the request dict and never logged/stored.
