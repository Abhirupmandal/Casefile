# Contributing to CASEFILE

Thank you for your interest in contributing to CASEFILE!

---

## Development Setup

### Prerequisites

- Python 3.11+
- Poetry 1.8+
- Docker & Docker Compose
- Git

### Initial Setup

```bash
# Clone repository
git clone https://github.com/your-org/casefile.git
cd casefile

# Install dependencies
poetry install

# Install pre-commit hooks
poetry run pre-commit install

# Start development services
docker-compose up -d

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run database migrations (when implemented in Phase 1)
poetry run alembic upgrade head

# Verify setup
poetry run casefile version
poetry run casefile healthcheck
```

---

## Development Workflow

### 1. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
```

Branch naming conventions:
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `refactor/` - Code refactoring
- `test/` - Test additions/changes

### 2. Make Changes

- Write code following project conventions
- Add tests for new functionality
- Update documentation as needed
- Run code quality checks frequently

### 3. Code Quality Checks

```bash
# Run linting
poetry run ruff check src tests

# Run formatting
poetry run black src tests

# Run type checking
poetry run mypy src

# Run all pre-commit hooks
poetry run pre-commit run --all-files
```

### 4. Run Tests

```bash
# Run all tests
poetry run pytest

# Run only unit tests (fast)
poetry run pytest -m unit

# Run with coverage
poetry run pytest --cov=casefile --cov-report=html

# Run integration tests (requires services running)
docker-compose up -d
poetry run pytest -m integration
```

### 5. Commit Changes

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git commit -m "feat: add new feature"
git commit -m "fix: resolve bug in extraction"
git commit -m "docs: update README"
git commit -m "refactor: simplify budget enforcement"
git commit -m "test: add investigator unit tests"
```

**Commit message format**:
```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, no logic change)
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Build/tooling changes
- `perf`: Performance improvements

**Example**:
```
feat(investigator): add parallel tool execution

Implement concurrent tool calls to reduce investigation latency
by 50%. Uses asyncio.gather() to run policy_lookup,
claim_history_lookup, and fraud_signal_lookup in parallel.

Closes #123
```

### 6. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub:
- **Title**: Use conventional commit format
- **Description**: Explain what changed and why
- **Link issues**: Reference related issues with `Closes #123`
- **Screenshots**: Include for UI changes
- **Checklist**: Complete the PR template checklist

---

## Code Style Guidelines

### Python Code Style

- **PEP 8** compliance (enforced by ruff)
- **Black** formatting (line length: 100)
- **Type hints** for all functions (enforced by mypy)
- **Docstrings** for all public functions/classes (Google style)

**Example**:

```python
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class BudgetState(BaseModel):
    """
    Tracks budget consumption across multiple dimensions.

    Attributes:
        workflow_id: Unique workflow identifier
        total_tokens: Combined input + output tokens consumed
        total_cost_usd: Total cost in USD across all LLM calls
        step_count: Number of agent invocations
        rework_count: Number of rework cycles executed
    """

    workflow_id: UUID
    total_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0.00")
    step_count: int = 0
    rework_count: int = 0

    def exceeds_limit(self, limits: "BudgetLimits") -> bool:
        """
        Check if current budget state exceeds any limit.

        Args:
            limits: Budget limits to check against

        Returns:
            True if any limit is exceeded, False otherwise
        """
        return (
            self.total_tokens > limits.max_total_tokens
            or self.total_cost_usd > limits.max_cost_usd
            or self.step_count >= limits.max_steps
            or self.rework_count >= limits.max_rework_cycles
        )
```

### Testing Guidelines

- **Unit tests**: Test individual functions/classes in isolation
- **Integration tests**: Test interactions between components
- **Fixtures**: Use pytest fixtures for test data
- **Mocking**: Mock external dependencies (LLM providers, tools)
- **Coverage**: Aim for >90% code coverage

**Example**:

```python
import pytest
from decimal import Decimal

from casefile.models.contracts import BudgetState, BudgetLimits


class TestBudgetState:
    """Unit tests for BudgetState model."""

    def test_exceeds_token_limit(self) -> None:
        """Test that exceeds_limit returns True when token limit exceeded."""
        budget = BudgetState(
            workflow_id=UUID("..."),
            total_tokens=160_000,
        )
        limits = BudgetLimits(max_total_tokens=150_000)

        assert budget.exceeds_limit(limits) is True

    def test_within_all_limits(self) -> None:
        """Test that exceeds_limit returns False when all limits satisfied."""
        budget = BudgetState(
            workflow_id=UUID("..."),
            total_tokens=10_000,
            total_cost_usd=Decimal("1.50"),
            step_count=10,
        )
        limits = BudgetLimits()

        assert budget.exceeds_limit(limits) is False
```

---

## Project Structure Conventions

### Module Organization

```
src/casefile/
├── agents/          # Agent implementations (Extractor, Investigator, etc.)
├── models/          # Pydantic models (contracts, state)
├── persistence/     # Database repositories, migrations
├── tools/           # Tool implementations
├── workflow/        # LangGraph workflow definition
└── observability/   # OpenTelemetry instrumentation
```

### File Naming

- Python modules: `snake_case.py`
- Test files: `test_<module>.py`
- Configuration: `kebab-case.yaml`

### Import Organization

```python
# Standard library
import os
from datetime import datetime
from typing import Any

# Third-party packages
from pydantic import BaseModel
from sqlalchemy import Column, String

# Local imports
from casefile.models.contracts import ClaimInput
from casefile.persistence.repositories import WorkflowRepository
```

---

## Documentation Guidelines

### Code Documentation

- **Module docstrings**: Describe module purpose
- **Class docstrings**: Describe class responsibility
- **Function docstrings**: Google style (Args, Returns, Raises)
- **Type hints**: Use for all public functions

### Architecture Documentation

Architecture changes require updates to:
- Relevant `/docs/*.md` files
- ADRs if architectural decision made
- README.md if user-facing changes
- AGENTS.md if agent behavior changes

---

## Pull Request Guidelines

### PR Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] Code quality checks pass (`pre-commit run --all-files`)
- [ ] New features have tests (unit + integration)
- [ ] Documentation updated (if applicable)
- [ ] Commit messages follow Conventional Commits
- [ ] PR description explains changes clearly
- [ ] Breaking changes documented in CHANGELOG.md

### PR Review Process

1. **Automated checks**: CI runs tests, linting, type checking
2. **Code review**: At least one approval required
3. **Architecture review**: Tech lead approval for significant changes
4. **Merge**: Squash and merge to main

---

## Testing Strategy

### Test Pyramid

```
        ┌─────────────┐
        │  Evaluation │  (30+ scenarios, replay-based)
        │    Tests    │
        └─────────────┘
       ┌───────────────┐
       │  Integration  │  (agent handoffs, end-to-end)
       │     Tests     │
       └───────────────┘
     ┌─────────────────────┐
     │     Unit Tests      │  (individual functions/classes)
     └─────────────────────┘
```

### Test Markers

```python
@pytest.mark.unit
def test_budget_enforcement():
    """Fast, isolated unit test"""
    pass

@pytest.mark.integration
def test_agent_handoff():
    """Integration test (requires services)"""
    pass

@pytest.mark.slow
def test_evaluation_scenario():
    """Slow test (>1 second)"""
    pass
```

---

## Common Tasks

### Run Specific Tests

```bash
# Run tests matching pattern
poetry run pytest -k "test_budget"

# Run tests in specific file
poetry run pytest tests/unit/test_budget_enforcement.py

# Run with verbose output
poetry run pytest -v
```

### Database Migrations

```bash
# Create new migration
poetry run alembic revision --autogenerate -m "Add checkpoints table"

# Apply migrations
poetry run alembic upgrade head

# Rollback migration
poetry run alembic downgrade -1

# View migration history
poetry run alembig history
```

### Update Dependencies

```bash
# Add new dependency
poetry add langchain-core

# Add development dependency
poetry add --group dev pytest-asyncio

# Update all dependencies
poetry update

# Lock dependencies (without update)
poetry lock
```

---

## Getting Help

- **Documentation**: Read `/docs/` for architecture details
- **Issues**: Search existing issues before creating new ones
- **Discussions**: Use GitHub Discussions for questions
- **Slack**: Join #casefile-dev channel

---

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on the code, not the person
- Assume good intentions

---

## Release Process

(Coming in Phase 8 - Production Hardening)

---

Thank you for contributing to CASEFILE! 🎉
