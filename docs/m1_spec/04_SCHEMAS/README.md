# Schema status

Shipped schemas are copied byte-for-byte into the wheel; runtime validation uses the corresponding schema or Pydantic model.

| Status | Schemas | Runtime consumer / package location |
| --- | --- | --- |
| Shipped | `peer`, `stream`, `record`, `offset` | Core wire validation; `peerhub/core/schemas/` |
| Shipped | `peer-observation`, `resource-pool` | Observation/resource models; `peerhub/extensions/schemas/` |
| Shipped | `extension-manifest` | `peerhub.extensions.manifest.ExtensionManifest`; `peerhub/extensions/schemas/` |
| Design-only | `model-catalog`, `skill-catalog` | No runtime consumer of these schema files; not shipped |
| Design-only (future) | `future/memory-record`, `future/harness-target` | No runtime consumer of these schema files; not shipped |

Names above refer to `.schema.json` files. Design-only schemas describe proposed contracts and do not establish runtime validation guarantees. Examples live in `examples/`.
