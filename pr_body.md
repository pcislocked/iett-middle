# release: v0.4.1

This Pull Request contains all changes for the v0.4.1 hotfix release:
- **Dependabot Resolution:** Bumped and consolidated backend dependencies (`httpx>=0.28.1`, `cryptography>=49.0.0`, `pydantic>=2.13.4`, `pydantic-settings>=2.14.2`, `ruff>=0.16.0`, `pyright>=1.1.411`).
- **Aiohttp & Aioresponses Compatibility:** Pinned `aiohttp>=3.11.0,<3.13` to resolve breaking `ClientResponse` initializer mismatch with `aioresponses`.
- **Code Quality & Linter Compliance:** Configured `pyproject.toml` for `ruff 0.16.0` and `pyright 1.1.411`, fixing all import formatting and typecheck annotations across `app/` and `tests/`.
- **Test Suite:** Maintained 100% green test suite (363/363 pytest tests passing, 0 ruff errors, 0 pyright errors).
