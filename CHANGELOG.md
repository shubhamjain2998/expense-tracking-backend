# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository-health metadata: `LICENSE`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.editorconfig`, `.gitattributes`.
- `.github/` templates: `CODEOWNERS`, `dependabot.yml`, `PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/{bug_report,feature_request,config}`.
- `docs/` tree: `ARCHITECTURE.md`, `API.md`, `DATABASE.md` (with Mermaid ERD), `DEPLOYMENT.md`, `DEVELOPMENT.md`, `TESTING.md`, `ROADMAP.md`, `FAQ.md`, `GLOSSARY.md`, `examples/api.http`.
- Module-level docstrings on the FastAPI entry point and the router / service packages.

### Changed
- `README.md` rewritten as a landing page with badges, quick-start, repository tour, and a documentation index. Links across to the companion frontend repository.

## [1.0.0]

The historical `v1.0.0` baseline. Future tag backfill (`v0.1.0` → `v0.9.x`) and detailed per-release notes will land in a follow-up.

[Unreleased]: https://github.com/shubhamjain2998/expense-tracking-backend/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/shubhamjain2998/expense-tracking-backend/releases/tag/v1.0.0
