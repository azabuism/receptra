"""Alembic environment configuration"""

import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, create_engine
from sqlalchemy import pool
from alembic import context

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model's MetaData object for 'autogenerate' support
# target_metadata = mymodel.Base.metadata
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode"""
    # Get DATABASE_URL from environment, fallback to config file
    url = os.getenv("DATABASE_URL")
    if url is None:
        url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode"""
    # Get DATABASE_URL from environment variable
    database_url = os.getenv("DATABASE_URL")

    if database_url is None:
        raise ValueError(
            "DATABASE_URL environment variable not set. "
            "Please set DATABASE_URL to your database connection string."
        )

    # Convert async URL to sync URL for alembic (alembic uses sync SQLAlchemy)
    # postgresql+asyncpg://... -> postgresql://...
    sync_url = database_url.replace("+asyncpg", "")

    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = sync_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
