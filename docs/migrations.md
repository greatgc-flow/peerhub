# Database Migrations Workflow

PeerHub uses a bespoke SQLite migration runner located in `peerhub/persistence/sqlite.py`. This guide outlines the workflow for developing and applying schema migrations.

## Current State & Supported Workflow

- **Supported Migration Engine**: The bespoke runner in `peerhub/persistence/sqlite.py` reading SQL files from `peerhub/persistence/migrations/` is the **only supported migration path** in Phase 1.
- **Alembic Status**: Alembic scaffolding (`alembic.ini`, `alembic/`) was added as exploratory scaffolding during Tier-2, but is **frozen and unsupported** until the full Phase 2 cutover.
- **Warning**: Do **not** run `alembic upgrade head` or `alembic stamp head`. The existing Alembic baseline only reflects schema ~v12, whereas the active database schema is at v16+. Running Alembic commands against an active database will corrupt or attempt to overwrite schema state.

## Authoring New Bespoke Migrations

When adding new schema changes:

1. **Create a new SQL file**:
   Add `peerhub/persistence/migrations/NNNN_<descriptive_name>.sql` with the next sequential integer prefix (e.g. `0016_dispatch_artifact_manifests_event_log_fk.sql`).

2. **Follow the mandatory fail-closed migration template**:
   For any table recreation or foreign key modification, always use SQLite's 12-step table recreation pattern with an **in-transaction `PRAGMA foreign_key_check;` before `COMMIT;`**:

   ```sql
   PRAGMA foreign_keys = OFF;
   BEGIN IMMEDIATE;

   -- 1. Create replacement table with new constraints/columns
   CREATE TABLE my_table_new (
       id TEXT PRIMARY KEY,
       parent_id TEXT NOT NULL REFERENCES other_table(id),
       ...
   );

   -- 2. Copy data from old table
   INSERT INTO my_table_new (id, parent_id, ...)
   SELECT id, parent_id, ...
   FROM my_table;

   -- 3. Drop old table and rename new table
   DROP TABLE my_table;
   ALTER TABLE my_table_new RENAME TO my_table;

   -- 4. Recreate any indexes
   CREATE INDEX my_table_parent_idx ON my_table(parent_id);

   -- 5. Record migration in schema_migrations table
   INSERT INTO schema_migrations(version, name)
   VALUES (16, '0016_my_migration_name');
   PRAGMA user_version = 16;

   -- 6. Fail-closed verification: check foreign keys BEFORE commit
   PRAGMA foreign_key_check;

   COMMIT;
   PRAGMA foreign_keys = ON;
   ```

3. **Register migration in `SqliteStateStore.initialize()`**:
   Open `peerhub/persistence/sqlite.py` and register the migration execution block in sequential order:

   ```python
   versions = self._migration_versions(connection)
   if 16 not in versions:
       connection.executescript(
           self._migration_text(
               "0016_my_migration_name.sql"
           )
       )
   ```

4. **Add integration test coverage**:
   Add tests in `tests/integration/persistence/` validating:
   - Migration applies forward cleanly.
   - Foreign keys and constraints are enforced.
   - Negative / orphaned data cases fail closed without committing a corrupt state.
