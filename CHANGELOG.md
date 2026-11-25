# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-25

### Added
- OAuth2 login with your own pCloud app (Application Credentials), automatic EU/US region detection, re-authentication flow.
- Folder sync of allowed local folders with include/exclude patterns, minimum file age, interval and per-run limit.
- Actions `sync_now`, `upload_file`, `upload_camera_snapshot`, `upload_frigate_event`.
- Optional local cache with automatic retry.
- Optional deletion of local files after a SHA-1 verified upload.
- Dry-run mode, activity log, events, sensors (status, counters, quota), sync button and redacted diagnostics.
- English and German translations.
