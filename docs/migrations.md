# Database Migrations Workflow

PeerHub is transitioning to Alembic for managing SQLite schema migrations. This guide outlines the workflow for developing new migrations.

## Current State

The initial 12 migrations (`0001` through `0012`) were applied sequentially by a bespoke runner in `peerhub/persistence/sqlite.py`. We have generated a single Alembic baseline revision (`alembic/versions/*_baseline_schema.py`) that encapsulates the exact schema state produced by those 12 scripts.

**Important**: The bespoke runner is still active during this transition. You must NOT delete the existing `0001` through `0012` `.sql` files or remove the bespoke runner yet.

## Working with Alembic

### For Existing Developer Databases
If you already have a populated development database (`.peerhub/peerhub.sqlite3`) created by the bespoke runner:
```bash
alembic stamp head
```
This tells Alembic that your database already contains all the tables from the baseline schema, without trying to recreate them.

### For Fresh Databases
If you are starting from a completely clean slate with a new database:
```bash
alembic upgrade head
```
This applies the baseline schema from scratch using Alembic.

### Authoring New Migrations
When adding new schema changes:

1. **Create a new revision**:
   ```bash
   alembic revision -m "Add new feature X"
   ```
2. **Edit the revision file**:
   Open the newly generated `.py` file in `alembic/versions/` and use `op.execute()` to define your raw fail-closed SQL. We avoid using Alembic's ORM or autogenerate tools.
   
   Example:
   ```python
   def upgrade() -> None:
       op.execute("""
       CREATE TABLE feature_x (
           id TEXT PRIMARY KEY,
           name TEXT NOT NULL
       );
       """)
   ```

3. **Apply the migration**:
   ```bash
   alembic upgrade head
   ```

*(Note: During the transition period, any new tables must also be compatible with or dual-registered in the bespoke runner until it is fully retired).*
