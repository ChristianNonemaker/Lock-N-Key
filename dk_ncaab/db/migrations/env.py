"""Alembic env – wires up our SQLAlchemy models so autogenerate works."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from dk_ncaab.db.models import Base            # noqa: F401  (registers metadata)
from dk_ncaab.config.settings import get_settings

config = context.config

# Override sqlalchemy.url from our settings (supports env-var override)
config.set_main_option("sqlalchemy.url", get_settings().database.url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
