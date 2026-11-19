package shell

import "testing"

// FuzzParse asserts the parser never panics, for both dialects and both
// Bash paths. Run: go test -fuzz=FuzzParse -fuzztime=20s ./internal/shell/
func FuzzParse(f *testing.F) {
	for _, s := range []string{
		"git add . && git commit -m x; echo hi | cat || true",
		`git commit -m "feat: say \"hi\"" -m 'it'"'"'s ok'`,
		"git commit -m \"$(cat <<'EOF'\nfeat(api): add retry\n\nBody.\nEOF\n)\"",
		"git commit -F - <<'EOF'\nfix: handle null\nEOF\necho done",
		"bash -lc 'cd x && git push'",
		`powershell -NoProfile -Command "gh repo delete a/b --yes"`,
		"git commit -m @'\nfeat: add login\n'@",
		"git commit -m \"fix: say `\"hi`\"\" -m 'it''s'",
		`$env:GIT_SSH_COMMAND = "ssh -i k"; git push`,
		`& "C:\Program Files\Git\cmd\git.exe" status`,
		"git -C sub -c user.name=x --no-pager commit -m 'fix: y'",
		"if true; then git push; fi",
		"git commit -m $'feat: x\\n\\nbody'",
		"(cd sub && git push) 2>&1 > /dev/null",
		"Invoke-RestMethod -Uri https://api.github.com/user",
		`git commit -m "unterminated`,
	} {
		f.Add(s, false)
		f.Add(s, true)
	}
	f.Fuzz(func(t *testing.T, s string, ps bool) {
		d := Bash
		if ps {
			d = PowerShell
		}
		Lex(s, d)
		LexFallback(s, d)
		for _, inv := range ParseWith(s, d, "/repo", Options{Home: "/home/u"}) {
			if call, ok := AsGit(inv); ok {
				call.Argv()
			}
			GhPath(inv)
			inv.Dynamic()
		}
	})
}
