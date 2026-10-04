"""CASEFILE Alembic environment (SQLite, SQLAlchemy 2.x).

Database URL resolution: `-x url=<URL>` overrides CASEFILE_DATABASE_URL,
which overrides the sqlalchemy.url in alembic.ini.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from casefile.models.persistence import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url_override = context.get_x_argument(as_dictionary=True).get("url")
database_url = (
    url_override
    or os.environ.get("CASEFILE_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script generation)."""
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live database connection)."""
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
