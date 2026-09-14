BEGIN IMMEDIATE;

ALTER TABLE capability_leases ADD COLUMN revoked_at_epoch INTEGER
    CHECK (revoked_at_epoch IS NULL OR revoked_at_epoch >= 1);
ALTER TABLE dispatch_requests ADD COLUMN restore_quarantined INTEGER NOT NULL
    DEFAULT 0 CHECK (restore_quarantined IN (0, 1));
ALTER TABLE effect_deliveries ADD COLUMN reconciliation_required INTEGER NOT NULL
    DEFAULT 0 CHECK (reconciliation_required IN (0, 1));

-- Keep historical receipts/authority records, but prohibit new execution using
-- a restored command, including retries through the repository's lower seams.
CREATE TRIGGER restore_no_attempt_insert BEFORE INSERT ON dispatch_attempts
WHEN EXISTS (SELECT 1 FROM dispatch_requests
             WHERE command_id = NEW.command_id AND restore_quarantined = 1)
BEGIN SELECT RAISE(ABORT, 'restore requires dispatch reconciliation'); END;

CREATE TRIGGER restore_no_attempt_update BEFORE UPDATE ON dispatch_attempts
WHEN EXISTS (SELECT 1 FROM dispatch_requests
             WHERE command_id = NEW.command_id AND restore_quarantined = 1)
BEGIN SELECT RAISE(ABORT, 'restore requires dispatch reconciliation'); END;

CREATE TRIGGER restore_no_capability_insert BEFORE INSERT ON capability_leases
WHEN EXISTS (SELECT 1 FROM dispatch_requests
             WHERE command_id = NEW.command_id AND restore_quarantined = 1)
BEGIN SELECT RAISE(ABORT, 'restore revoked command authority'); END;

CREATE TRIGGER restore_no_effect_claim BEFORE UPDATE OF claimed_by, claim_attempt_id, claimed_at
ON effect_deliveries WHEN OLD.reconciliation_required = 1
BEGIN SELECT RAISE(ABORT, 'restore requires effect reconciliation'); END;

CREATE TRIGGER restore_no_effect_receipt BEFORE INSERT ON effect_receipts
WHEN EXISTS (SELECT 1 FROM effect_deliveries
             WHERE event_id = NEW.outbox_event_id AND reconciliation_required = 1)
BEGIN SELECT RAISE(ABORT, 'restore requires effect reconciliation'); END;

INSERT INTO schema_migrations(version, name) VALUES (33, '0033_restore_authority');
PRAGMA user_version = 33;
COMMIT;
