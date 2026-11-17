// Package defaults embeds the built-in contracts, templates and workflows so
// a fresh install works with no files on disk.
package defaults

import "embed"

// FS holds contracts/*.json, templates/*, workflows/*.json.
//
//go:embed contracts/*.json templates workflows
var FS embed.FS
