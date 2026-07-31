-- Slice 3 correction: both caller identity namespaces independently
-- bind to an admission. Multiple aliases may therefore reference the
-- same command and immutable admission receipt.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

ALTER TABLE client_request_bindings
RENAME TO client_request_bindings_slice3;

CREATE TABLE client_request_bindings (
    client_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL
        REFERENCES admission_receipts(admission_receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (client_id, client_request_id)
);

INSERT INTO client_request_bindings (
    client_id,
    client_request_id,
    payload_digest,
    command_id,
    admission_receipt_id,
    created_at
)
SELECT
    client_id,
    client_request_id,
    payload_digest,
    command_id,
    admission_receipt_id,
    created_at
FROM client_request_bindings_slice3
ORDER BY created_at, client_id, client_request_id;

DROP TABLE client_request_bindings_slice3;

ALTER TABLE command_idempotency_bindings
RENAME TO command_idempotency_bindings_slice3;

CREATE TABLE command_idempotency_bindings (
    client_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL
        REFERENCES admission_receipts(admission_receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (
        client_id,
        command_type,
        idempotency_key
    )
);

INSERT INTO command_idempotency_bindings (
    client_id,
    command_type,
    idempotency_key,
    payload_digest,
    command_id,
    admission_receipt_id,
    created_at
)
SELECT
    client_id,
    command_type,
    idempotency_key,
    payload_digest,
    command_id,
    admission_receipt_id,
    created_at
FROM command_idempotency_bindings_slice3
ORDER BY
    created_at,
    client_id,
    command_type,
    idempotency_key;

DROP TABLE command_idempotency_bindings_slice3;

INSERT INTO schema_migrations(version, name)
VALUES (4, '0004_idempotency_aliases');

PRAGMA user_version = 4;

COMMIT;

PRAGMA foreign_keys = ON;
