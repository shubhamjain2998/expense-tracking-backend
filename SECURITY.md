# Security

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

To report a security issue, contact the maintainer privately via GitHub:
[@shubhamjain2998](https://github.com/shubhamjain2998).

Include a description of the issue, reproduction steps, and the potential impact. You will receive a response within a reasonable timeframe.

## Supported versions

The latest tagged release on `main` is the only supported version. Older tags do not receive security backports.

## Scope

This policy covers the code in this repository. It does **not** cover:

- The companion frontend (see [expense-tracking-frontend](https://github.com/shubhamjain2998/expense-tracking-frontend) for its own policy).
- Hosted infrastructure (Supabase, Render) — report those through their respective vendor channels.
- Third-party dependencies — please report upstream first; we will pick up patched versions via Dependabot.

## Known limitations

### JWT secret shared with Supabase

The backend verifies JWTs signed with `SUPABASE_JWT_SECRET`. Anyone with access to that secret can mint valid tokens for any user. Rotate the secret in Supabase and update the Render environment variable together if compromise is suspected.

### Bearer-token fallback

For backwards compatibility with older frontend builds, the API accepts tokens via the `Authorization: Bearer` header in addition to the preferred `httpOnly` cookie. The header path is intentional but exposes the token to JavaScript in clients that still use it.
