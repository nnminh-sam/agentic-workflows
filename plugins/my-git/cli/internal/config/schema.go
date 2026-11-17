package config

import _ "embed"

//go:embed schema.json
var schemaJSON []byte

// Schema returns the JSON Schema (draft 2020-12) of a config file, for
// `mygit config schema` and editor integration. The caller gets a copy.
func Schema() []byte { return append([]byte(nil), schemaJSON...) }
