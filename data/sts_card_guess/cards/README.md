# STS2 Card Archive

- This archive lives under `src/cards` as requested by the current workspace task.
- Top-level folders are split by `attack`, `skill`, `power`, `curse`, `status`, and `quest`.
- JSON files are then split by source pool such as `ironclad`, `silent`, `defect`, `necrobinder`, `regent`, `colorless`, `event`, `curse`, and `status`.
- Card `name` / `description` / upgraded fields are stored in Chinese.
- English source fields are preserved for traceability and later schema migration.
- These files are reference content aligned with the repo's content layering, but they are not auto-loaded by the current battle runtime yet.
