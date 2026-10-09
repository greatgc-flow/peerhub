# Extension Authoring Guide

- Core only contains features that are strictly required.
- Everything else is an Extension.
- Core MUST NOT import Extension.
- Extension tables are owned by the Extension.
- Core startup does not depend on Extension schemas.
- write extension only mutates via public Core ports.
- readonly extension does not receive write ports at all.
- vendor sessions/models/protocols belong inside adapters.
- extension failures do not halt default communication.
