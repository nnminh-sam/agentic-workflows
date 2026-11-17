//go:build windows

package execx

import (
	"os/exec"
	"syscall"
)

// setCmdLine hands cmd.exe the raw command line, bypassing Go's quoting.
func setCmdLine(cmd *exec.Cmd, line string) {
	if line != "" {
		cmd.SysProcAttr = &syscall.SysProcAttr{CmdLine: line}
	}
}
