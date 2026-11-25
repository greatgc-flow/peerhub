# Billing observations (#472)

The consumer reads the Bridge's fenced invocation and outcome evidence and the
normalized, delivery-scoped API usage used by session receipts. It also accepts
typed monetary facts from trusted, offline collectors that can verify the
identity to which their source attributes the amount.

- Balance observations, estimates and charges have separate observation kinds.
  Monetary facts carry an amount in millionths of the stated currency. API
  replies supply token counters, which stay in tokens. Neither usage nor a
  balance change is a charge. No grant state is inferred.
- For `oa` and `ga`, the same process-local key snapshot supplies the request
  header and a provider-qualified HMAC-SHA256 fingerprint. A random secret in
  `.<db-name>.billing/key` is shared by the adapter and collector for this
  store. It stays outside SQLite dumps. Restore it privately with the store.
  A rotated provider key gets a separate scope. `ca` and calls without access
  to the private key have unknown scope.
- The Bridge records the scope before invocation. Recovery uses that binding,
  and replay of a settled ask leaves it intact. For offline monetary facts, the
  collector verifies the account or key identity from its source. A stable
  source evidence ID deduplicates imports and cannot be rebound on rotation.
- The optional consumer derives delivery billing from the Bridge's durable
  invocation and outcome evidence. It preserves valid counters on uncertain
  replies and records missing usage and unknown scope as unreconciled.
  A proven pre-inference failure contributes no usage observation.
- Existing observation tables accept up to 1,024 distinct monetary facts per
  store. A full store accepts identical replays and rejects new IDs. Existing
  monetary facts above the cap and legacy usage rows are grandfathered.
  New deliveries add no duplicate observation rows. Unresolved evidence stays
  in the Bridge journal. Views show at most five recent facts per kind.
- Read views use peer identity for delivery usage and adapter provider for
  monetary facts. Freshness is derived from the fact's observed time.
- The first scoped write rekeys legacy digests in place under the store key.
  Read views withhold weak digests before upgrade. A detached, earlier dump
  retains the facts it originally contained.

Out of scope are provider billing requests, pricing, account equivalence across
keys, a new store, routing, automatic selection and balancing.
