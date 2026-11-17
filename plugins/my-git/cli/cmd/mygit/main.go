// Command mygit is the git/gh policy CLI for people and AI agents.
package main

import (
	"os"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/cli"
)

// version is set at build time: -ldflags "-X main.version=..."
var version = "dev"

func main() {
	os.Exit(cli.Main(version, os.Args[1:], cli.DefaultIO()))
}
