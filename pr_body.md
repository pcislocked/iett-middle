# release: v0.4.0

This Pull Request contains all changes for the v0.4.0 release:

## Features
- **SQLite Caching:** Improved backend caching mechanism by migrating to a more robust SQLite-based solution, complete with updated cachetools dependencies.
- **Multi-Probe Backend & Variant Routing:** Expanded support for multi-probe features and variant routing functionality.

## Documentation
- **Swagger Updates:** Comprehensively expanded docstrings across all API endpoints (announcements, arac, fleet, garages, routes, stops, traffic) to provide detailed Swagger documentation.
- **README Updates:** Updated API endpoints in README.md to match the v0.4.0 release.

## Code Quality & Dependencies
- **Ruff Compliance:** Enforced strict ruff format and ruff check across the entire codebase (app/ and tests/) for consistent styling and import sorting.
- **Typing Fixes:** Resolved pyright error handler mismatches, context var annotations, and cookies assignment type mismatches for strict typing compliance.
- **Dependabot Bumps:** Updated multiple dependencies including actions/checkout (v7), setup-python (v6), docker/metadata-action (v6), pytest, ruff, pyright, pytest-asyncio, and beautifulsoup4.
