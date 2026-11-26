# Changelog

All notable changes to this project are listed here, newest first. Each release's section is
used as the notes of its GitHub release (`scripts/release.py`).

## [0.1.1] - 2026-11-26

### Added
- Push notifications are skipped while you are at the computer (macOS). A push goes out only
  when the last keyboard or mouse input is at least `notify.away_after_seconds` old (default
  120, 0 always sends) or the screen is locked (`notify.away_when_locked`, default on).
  Change both with `agents configure`, `agents admin config set` or
  `agents admin notify away-after|away-when-locked`. `agents admin notify test` ignores the check,
  and an idle time or lock state that cannot be read never suppresses a push.
- `scripts/release.py` uses the version's `CHANGELOG.md` section as the GitHub release notes and
  links the full comparison with the previous release; it refuses a release without such a section.

## [0.1.0] - 2026-11-25

### Added
- Initial public release: agterm launch and resume menus, projects and placement, session
  naming, shared agent instructions, skills store, read-only agent consults, push notifications
  through ntfy, Inbox integration, optional Textual configurator, installer and Homebrew tap.
