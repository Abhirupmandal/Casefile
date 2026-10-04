"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = ${up_revision!r}
down_revision: str | tuple[str, ...] | None = ${down_revision!r}
branch_labels: str | tuple[str, ...] | None = ${branch_labels!r}
depends_on: str | tuple[str, ...] | None = ${depends_on!r}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
