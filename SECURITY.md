# Security Policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.
Use GitHub's private vulnerability reporting instead:
**Security → Report a vulnerability** in this repository.

You will get an answer within 7 days. Please include the version, a description and – if possible – steps to reproduce. Never include access tokens or other secrets.

## Security design

| Topic | Implementation |
|---|---|
| Credentials | OAuth2 via Home Assistant *Application Credentials*. Every user registers their own pCloud app – no shared client secret is shipped in this repository. The pCloud password is never handled. |
| Token handling | Stored by Home Assistant in the config entry. Sent only as `Authorization: Bearer` header over HTTPS to `api.pcloud.com` / `eapi.pcloud.com`. Never logged, never put in URLs, redacted in diagnostics. |
| Network | Connections only to pCloud and to the Frigate URL configured by the user. No telemetry. |
| File access | Only folders allowed by Home Assistant (`allowlist_external_dirs` / media dirs). Checked on save and on every run. Symlinks are never followed. |
| Remote paths | Components are sanitized; `..` is rejected. |
| Deletion | Local files are only deleted if enabled by the user **and** the upload was verified by SHA-1 checksum **and** the file did not change during the upload. Remote files are never deleted. |
| Partial uploads | `nopartial=1` – pCloud discards incomplete uploads. |
| Input validation | Frigate event ids are validated against `^[A-Za-z0-9._-]{1,128}$`; Frigate URLs must be `http(s)`. In-memory downloads are capped at 200 MB. |
