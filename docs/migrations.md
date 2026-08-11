# Database Migrations Workflow

PeerHub currently runs a bespoke SQLite migration engine from `peerhub/persistence/sqlite.py`. A consolidated Alembic baseline now describes the same schema, but switching the runtime to Alembic is a separate cutover increment.

## Current State & Supported Workflow

- **Supported runtime migration engine**: The bespoke runner reading `peerhub/persistence/migrations/*.sql` remains authoritative until the Phase 2 runtime-cutover increment lands.
- **Authoritative current schema**: Applying bespoke migrations `0001` through `0019` produces `PRAGMA user_version = 19` and 19 `schema_migrations` rows. Earlier roadmap references to “schema v17” predated capability-lease migrations `0018` and `0019` and are obsolete.
- **Alembic baseline**: `v19_consolidated` is one final-state baseline for that exact schema. The automated parity test compares tables, normalized table constraints, columns/types/defaults/nullability/PKs, foreign keys, explicit and automatic indexes, `schema_migrations`, `user_version`, and `foreign_key_check` results.
- **Allowed before runtime cutover**: `alembic upgrade head` is for fresh, empty validation databases only. An existing database may be stamped only after the verification procedure below proves it is already equivalent to bespoke v19.
- **Never upgrade an existing bespoke database to the baseline**: the consolidated revision creates the final schema; it is not a sequence of upgrades from bespoke state and will collide with existing tables.

## Stamping an existing bespoke-v19 database

Alembic `stamp` is the correct operation because it records a revision in Alembic's version table without executing the baseline's `upgrade()` body. The integration test `test_stamp_marks_existing_bespoke_v19_without_replaying_schema` exercises this against a fresh bespoke-v19 database and verifies the complete domain schema is unchanged.

Before stamping:

1. Take a recoverable SQLite backup.
2. From the workspace root that owns `.peerhub/peerhub.sqlite3`, verify all three conditions:
   - `PRAGMA user_version` returns `19`;
   - `SELECT COUNT(*), MIN(version), MAX(version) FROM schema_migrations` returns `19, 1, 19`; and
   - `PRAGMA foreign_key_check` returns no rows.
3. If any condition fails, do not stamp. Bring the database to bespoke v19 with the currently supported runner or restore a known-good backup.

Then run, from that same workspace root:

```powershell
python -m alembic -c P:\peerhub\alembic.ini stamp v19_consolidated
python -m alembic -c P:\peerhub\alembic.ini current
```

The second command must report `v19_consolidated (head)`. Stamping does not switch runtime ownership; the bespoke runner remains active until increment 2 lands.

## Authoring New Bespoke Migrations

When adding new schema changes:

1. **Create a new SQL file**:
   Add `peerhub/persistence/migrations/NNNN_<descriptive_name>.sql` with the next sequential integer prefix (currently `0020`).

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
   VALUES (20, '0020_my_migration_name');
   PRAGMA user_version = 20;

   -- 6. Fail-closed verification: check foreign keys BEFORE commit
   PRAGMA foreign_key_check;

   COMMIT;
   PRAGMA foreign_keys = ON;
   ```

3. **No code registration needed.** `initialize()` discovers migrations
   from the packaged directory and applies whatever hasn't run yet, in
   ascending version order. Dropping the `.sql` file in place (step 1) is
   sufficient; there is no ladder in `peerhub/persistence/sqlite.py` to
   edit. `initialize()` verifies every discovered migration recorded
   itself before returning, so a script that runs without inserting its
   `schema_migrations` row fails loudly instead of silently re-running on
   every startup.

4. **Add integration test coverage**:
   Add tests in `tests/integration/persistence/` validating:
   - Migration applies forward cleanly.
   - Foreign keys and constraints are enforced.
   - Negative / orphaned data cases fail closed without committing a corrupt state.
