# Prioritize Standards/OSS Alternatives for Existing Features

## Worth Retaining/Reusing
- Python stdlib: `sqlite3`, `dataclasses`, `argparse`, `pathlib`, `logging`, `subprocess`, `tomllib`.
- SQLite WAL/transaction/UoW patterns.
- Areas that actually need `psutil`/Windows process identity-tree handling.
- Git/Git worktree.
- Distribution platforms such as GitHub Actions/PyPI trusted publishing.

## Not Adopted by Default
- Alembic: do not add if the current simple ordered SQL migrations are sufficient.
- Pluggy: use explicit composition until a real third-party plugin ecosystem exists.
- Kafka/Redis: excessive for M1 local durable communication.
- Celery/Temporal/LangGraph: do not adopt until there is an explicit workflow engine requirement.
- Rich/Textual: optional once UI value is validated; do not make them Core dependencies.

## External Standards
Use JSON Schema/Agent Skills by default.
Use MCP/A2A/OTel/OpenAPI/AsyncAPI/CloudEvents only at actual boundaries.
