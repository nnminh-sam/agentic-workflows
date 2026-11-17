//go:build !windows

package execx

import "os/exec"

// setCmdLine is a no-op: only Windows needs a raw command line.
func setCmdLine(*exec.Cmd, string) {}
