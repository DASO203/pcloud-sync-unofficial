# Contributing

Thanks for helping! 🎉

1. Fork the repository and create a branch from `main`.
2. Install the test tools: `pip install -r requirements_test.txt`
3. Run linters and tests:
   ```bash
   ruff check .
   ruff format --check .
   pytest
   ```
4. Update `CHANGELOG.md`, the READMEs (English **and** German) and `strings.json` + `translations/*.json` if you change user-facing behaviour.
5. Open a pull request and describe *what* and *why*.

## Ground rules

- Security first: never log tokens, never follow symlinks, never delete data without verification.
- Keep pure logic in `helpers.py` so it can be unit-tested without Home Assistant.
- Be kind – see the [Contributor Covenant](https://www.contributor-covenant.org/).
