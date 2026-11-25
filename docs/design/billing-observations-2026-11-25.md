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
  header and a provider-qualified fingerprint. A rotated key gets a separate
  scope. The fingerprint identifies a credential, not the underlying ledger.
  `ca` uses the opaque `ant` login. Its call evidence is retained with unknown
  scope. Environment keys cannot verify the identity used by `ant`.
- The Bridge records the scope before invocation. Recovery uses that binding,
  and replay of a settled ask leaves it intact. For offline monetary facts, the
  collector verifies the account or key identity from its source. A stable
  source evidence ID deduplicates imports and cannot be rebound on rotation.
- Uncertain execution, missing usage and unverified scope remain unreconciled.
  Proven pre-inference failures do not create usage observations.
- Use the existing immutable `core.db` observation and rollup tables. Keep only
  normalized counters or monetary amounts, source, timestamps, scope and
  replay identity. Keep one row per distinct fact for the life of the store
  to recognize replays and preserve unresolved evidence. Read views show a
  bounded recent sample. Freshness is evaluated from the observation time and
  does not change the meaning of historical evidence.

Out of scope are provider billing requests, pricing, account equivalence across
keys, a new store, routing, automatic selection and balancing.
