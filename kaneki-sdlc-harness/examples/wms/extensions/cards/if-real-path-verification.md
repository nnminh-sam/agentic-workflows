# if-real-path-verification (family `if`)

**Goal:** proof the interface works on its real path, per direction (import and/or export).

Import: drop the real-format file or call, run the receiving job, capture the rows created/updated.
Export: trigger the job, capture the produced file (sha256 + key lines) and the source rows.
Output `if-real-path.md` with one section per direction. **G6** before running against shared data.
