'''Explicit PostgreSQL setup commands; never invoked by web import/startup.'''

from __future__ import annotations

import argparse
import asyncio
import os

from app.database.repository import PostgresRepository, RepositoryError
from app.database.seed import seed_postgres


async def run(command: str, database_url: str) -> int:
    if database_url.startswith('memory://'):
        raise RepositoryError('DATABASE_URL must point to PostgreSQL for database commands')
    repository = PostgresRepository(database_url)
    if command == 'migrate':
        await repository.initialize()
        print('schema applied')
        return 0
    if command == 'seed':
        await repository.initialize()
        print(f'seeded {await seed_postgres(repository)} knowledge/history records')
        return 0
    healthy, message = await repository.health()
    print(message)
    return 0 if healthy else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Manage the OpsPilot PostgreSQL schema and seed')
    parser.add_argument('command', choices=('migrate', 'seed', 'check'))
    parser.add_argument('--database-url', default=os.getenv('OPSPILOT_DATABASE_URL', os.getenv('DATABASE_URL', 'memory://opspilot')))
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.command, args.database_url))
    except (RepositoryError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
