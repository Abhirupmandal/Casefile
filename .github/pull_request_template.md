## What changed

<!-- One or two sentences on the change and the problem it solves. -->

## Why

<!-- Motivation, linked issue, or failing behaviour. -->

## Checklist

- [ ] `poetry run black --check .` passes
- [ ] `poetry run ruff check .` passes
- [ ] `poetry run mypy src tests` passes
- [ ] `poetry run pytest tests/unit -m unit` passes
- [ ] `poetry run pytest tests/integration` passes (if workflow/agent/tool/API code changed)
- [ ] New behaviour has a test; existing assertions were not weakened
- [ ] Docs updated (`README.md`, `AGENTS.md`, `docs/`) if behaviour or contracts changed
- [ ] Commit messages follow Conventional Commits

## Notes for reviewers

<!-- Anything subtle: migrations, budget semantics, prompt changes, replay impact. -->
