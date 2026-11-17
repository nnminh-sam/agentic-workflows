package shell

// Port of tests/test_shellparse.py. Every Bash lexer case runs through both
// the mvdan.cc/sh path (Lex) and the hand-written fallback (LexFallback).
// Subtest names are the Python test names.

import (
	"path/filepath"
	"reflect"
	"testing"
)

var testCwd = string(filepath.Separator) + "repo"

func vals(segs []Segment) [][]string {
	out := [][]string{}
	for _, s := range segs {
		out = append(out, s.Values())
	}
	return out
}

var lexers = map[string]func(string, Dialect) []Segment{"mvdan": Lex, "fallback": LexFallback}

func TestLexBash(t *testing.T) {
	cases := []struct {
		name, in string
		want     [][]string
	}{
		{"test_splits_on_operators", "git add . && git commit -m x; echo hi | cat || true",
			[][]string{{"git", "add", "."}, {"git", "commit", "-m", "x"}, {"echo", "hi"}, {"cat"}, {"true"}}},
		{"test_newlines_split_commands", "git status\ngit log", [][]string{{"git", "status"}, {"git", "log"}}},
		{"test_quotes_and_escapes", `git commit -m "feat: say \"hi\"" -m 'it'"'"'s ok'`,
			[][]string{{"git", "commit", "-m", `feat: say "hi"`, "-m", "it's ok"}}},
		{"test_operators_inside_quotes_do_not_split", `git commit -m "fix: a && b; c | d"`,
			[][]string{{"git", "commit", "-m", "fix: a && b; c | d"}}},
		{"test_redirections_are_dropped", "git push origin main 2>&1 > /dev/null", [][]string{{"git", "push", "origin", "main"}}},
		{"test_comments", "git status # check it\n# whole line\ngit log", [][]string{{"git", "status"}, {"git", "log"}}},
		{"test_ansi_c_quotes", "git commit -m $'feat: x\\n\\nbody'", [][]string{{"git", "commit", "-m", "feat: x\n\nbody"}}},
		{"test_line_continuation", "git commit \\\n  -m 'fix: y'", [][]string{{"git", "commit", "-m", "fix: y"}}},
		{"test_subshell_parens_split", "(cd sub && git push)", [][]string{{"cd", "sub"}, {"git", "push"}}},
		// extra
		{"env_prefix_kept_as_word", "GIT_SSH_COMMAND='ssh -i k' git push", [][]string{{"GIT_SSH_COMMAND=ssh -i k", "git", "push"}}},
		{"author_with_spaces", `git commit --author="octocat <a@b.c>" -m 'feat: add x'`,
			[][]string{{"git", "commit", "--author=octocat <a@b.c>", "-m", "feat: add x"}}},
		{"background_and_semicolons", "git fetch & git status;; ", nil},
	}
	for _, c := range cases {
		for lname, lex := range lexers {
			t.Run(c.name+"/"+lname, func(t *testing.T) {
				got := vals(lex(c.in, Bash))
				if c.want == nil { // only must not panic and must find git
					if len(got) == 0 {
						t.Errorf("%q: no segments", c.in)
					}
					return
				}
				if !reflect.DeepEqual(got, c.want) {
					t.Errorf("%q:\n got %q\nwant %q", c.in, got, c.want)
				}
			})
		}
	}
}

func TestLexBashHeredocs(t *testing.T) {
	for lname, lex := range lexers {
		t.Run("test_cat_heredoc_message_is_resolved/"+lname, func(t *testing.T) {
			cmd := "git commit -m \"$(cat <<'EOF'\nfeat(api): add retry\n\nBody with \"quotes\" and ) parens.\nEOF\n)\""
			seg := lex(cmd, Bash)[0]
			if seg.Words[3].Value != "feat(api): add retry\n\nBody with \"quotes\" and ) parens." || seg.Words[3].Dynamic {
				t.Errorf("%#v", seg.Words[3])
			}
		})
		t.Run("test_heredoc_to_stdin/"+lname, func(t *testing.T) {
			segs := lex("git commit -F - <<'EOF'\nfix: handle null\n\nbody\nEOF\necho done", Bash)
			if len(segs) != 2 || !reflect.DeepEqual(segs[0].Values(), []string{"git", "commit", "-F", "-"}) ||
				!segs[0].HasStdin || segs[0].Stdin != "fix: handle null\n\nbody\n" ||
				!reflect.DeepEqual(segs[1].Values(), []string{"echo", "done"}) {
				t.Errorf("%#v", segs)
			}
		})
		t.Run("test_dynamic_words/"+lname, func(t *testing.T) {
			seg := lex(`git commit -m "$MSG" -m "$(git log -1 --format=%s)" -m plain`, Bash)[0]
			if d := []bool{seg.Words[3].Dynamic, seg.Words[5].Dynamic, seg.Words[7].Dynamic}; !reflect.DeepEqual(d, []bool{true, true, false}) {
				t.Errorf("dynamic: %v", d)
			}
		})
		t.Run("backtick_and_braced_var_dynamic/"+lname, func(t *testing.T) {
			seg := lex("git commit -m \"`date`\" -m ${X}", Bash)[0]
			if !seg.Words[3].Dynamic || !seg.Words[5].Dynamic {
				t.Errorf("%#v", seg.Words)
			}
		})
		t.Run("unquoted_heredoc_delimiter/"+lname, func(t *testing.T) {
			segs := lex("git commit -F - <<EOF\nfix: plain\nEOF", Bash)
			if len(segs) != 1 || segs[0].Stdin != "fix: plain\n" {
				t.Errorf("%#v", segs)
			}
		})
	}
}

func TestLexPowerShell(t *testing.T) {
	t.Run("test_here_string", func(t *testing.T) {
		seg := Lex("git commit -m @'\nfeat: add login\n\nIt's \"quoted\".\n'@", PowerShell)[0]
		if !reflect.DeepEqual(seg.Values(), []string{"git", "commit", "-m", "feat: add login\n\nIt's \"quoted\"."}) || seg.Words[3].Dynamic {
			t.Errorf("%#v", seg.Words)
		}
	})
	t.Run("test_backtick_escapes_and_doubled_quotes", func(t *testing.T) {
		got := vals(Lex("git commit -m \"fix: say `\"hi`\"\" -m 'it''s'", PowerShell))
		if want := [][]string{{"git", "commit", "-m", `fix: say "hi"`, "-m", "it's"}}; !reflect.DeepEqual(got, want) {
			t.Errorf("%q", got)
		}
	})
	t.Run("test_semicolon_and_pipeline", func(t *testing.T) {
		got := vals(Lex("git status; gh pr list | Select-Object -First 1", PowerShell))
		if want := [][]string{{"git", "status"}, {"gh", "pr", "list"}, {"Select-Object", "-First", "1"}}; !reflect.DeepEqual(got, want) {
			t.Errorf("%q", got)
		}
	})
	t.Run("test_variable_is_dynamic", func(t *testing.T) {
		if !Lex(`git commit -m "$msg"`, PowerShell)[0].Words[3].Dynamic {
			t.Error("ps variable should be dynamic")
		}
	})
	// extra
	t.Run("double_quoted_here_string_with_var_is_dynamic", func(t *testing.T) {
		seg := Lex("git commit -m @\"\nfeat: $x\n\"@", PowerShell)[0]
		if !seg.Words[3].Dynamic {
			t.Errorf("%#v", seg.Words[3])
		}
	})
	t.Run("single_quotes_are_literal", func(t *testing.T) {
		seg := Lex(`git commit -m '$msg'`, PowerShell)[0]
		if seg.Words[3].Dynamic || seg.Words[3].Value != "$msg" {
			t.Errorf("%#v", seg.Words[3])
		}
	})
	t.Run("and_and_chain_pwsh7", func(t *testing.T) {
		got := vals(Lex("git add . && git commit -m x", PowerShell))
		if want := [][]string{{"git", "add", "."}, {"git", "commit", "-m", "x"}}; !reflect.DeepEqual(got, want) {
			t.Errorf("%q", got)
		}
	})
}

func TestInvocations(t *testing.T) {
	opt := Options{Home: filepath.Join(testCwd, "home")}
	parse := func(cmd string, d Dialect) []Invocation { return ParseWith(cmd, d, testCwd, opt) }
	t.Run("test_program_name", func(t *testing.T) {
		for in, want := range map[string]string{`C:\Program Files\Git\cmd\git.exe`: "git", "/usr/bin/gh": "gh",
			"GH.EXE": "gh", "script.ps1": "script", "Invoke-RestMethod": "invoke-restmethod"} {
			if got := ProgramName(in); got != want {
				t.Errorf("%q: %q want %q", in, got, want)
			}
		}
	})
	t.Run("test_cd_changes_cwd", func(t *testing.T) {
		if inv := parse("cd sub && git push", Bash)[0]; inv.Cwd != filepath.Join(testCwd, "sub") {
			t.Errorf("cwd %q", inv.Cwd)
		}
	})
	t.Run("test_env_prefix_and_export", func(t *testing.T) {
		invs := parse("export GH_TOKEN=abc; GIT_SSH_COMMAND='ssh -i k' git push", Bash)
		if !reflect.DeepEqual(invs[0].Env, map[string]string{"GH_TOKEN": "abc", "GIT_SSH_COMMAND": "ssh -i k"}) {
			t.Errorf("env %v", invs[0].Env)
		}
	})
	t.Run("test_powershell_env_assignment", func(t *testing.T) {
		if invs := parse(`$env:GIT_SSH_COMMAND = "ssh -i k"; git push`, PowerShell); invs[0].Env["GIT_SSH_COMMAND"] != "ssh -i k" {
			t.Errorf("env %v", invs[0].Env)
		}
	})
	t.Run("test_powershell_call_operator", func(t *testing.T) {
		inv := parse(`& "C:\Program Files\Git\cmd\git.exe" status`, PowerShell)[0]
		if inv.Program != "git" || !reflect.DeepEqual(inv.Argv(), []string{"status"}) {
			t.Errorf("%v %v", inv.Program, inv.Argv())
		}
	})
	t.Run("test_nested_shells", func(t *testing.T) {
		invs := parse("bash -lc 'cd x && git push'", Bash)
		if len(invs) != 1 || invs[0].Program != "git" || invs[0].Cwd != filepath.Join(testCwd, "x") {
			t.Errorf("%+v", invs)
		}
		invs = parse(`powershell -NoProfile -Command "gh repo delete a/b --yes"`, Bash)
		if len(invs) != 1 || invs[0].Program != "gh" || !reflect.DeepEqual(GhPath(invs[0]), []string{"repo", "delete"}) {
			t.Errorf("%+v", invs)
		}
	})
	t.Run("test_git_globals", func(t *testing.T) {
		call, ok := AsGit(parse("git -C sub -c user.name=x --no-pager commit -m 'fix: y'", Bash)[0])
		if !ok || call.Sub != "commit" || call.Cwd != filepath.Join(testCwd, "sub") ||
			!reflect.DeepEqual(call.Config, map[string]string{"user.name": "x"}) || !reflect.DeepEqual(call.Argv(), []string{"-m", "fix: y"}) {
			t.Errorf("%+v", call)
		}
	})
	// extra
	t.Run("nested_depth_limit", func(t *testing.T) {
		invs := parse(`bash -c "bash -c \"bash -c 'bash -c \\\"git push\\\"'\""`, Bash)
		for _, inv := range invs {
			if inv.Program == "git" {
				t.Errorf("depth limit not applied: %+v", invs)
			}
		}
	})
	t.Run("pwsh_command_and_cmd_c", func(t *testing.T) {
		if invs := parse(`pwsh -c "git push"`, PowerShell); len(invs) != 1 || invs[0].Program != "git" {
			t.Errorf("%+v", invs)
		}
	})
	t.Run("wrappers", func(t *testing.T) {
		for _, cmd := range []string{"sudo git status", "env -i A=b git status", "command git status", "nohup git status", "time git status"} {
			if invs := parse(cmd, Bash); len(invs) != 1 || invs[0].Program != "git" {
				t.Errorf("%q: %+v", cmd, invs)
			}
		}
	})
	t.Run("bare_assignment_sets_env", func(t *testing.T) {
		invs := parse("A=1; git status", Bash)
		if len(invs) != 1 || invs[0].Env["A"] != "1" {
			t.Errorf("%+v", invs)
		}
	})
	t.Run("set_location_and_tilde", func(t *testing.T) {
		if inv := parse("Set-Location ~/code; git status", PowerShell)[0]; inv.Cwd != filepath.Join(opt.Home, "code") {
			t.Errorf("cwd %q", inv.Cwd)
		}
		if inv := parse("cd; git status", Bash)[0]; inv.Cwd != opt.Home {
			t.Errorf("bare cd %q", inv.Cwd)
		}
		call, _ := AsGit(parse("git -C ~/x status", Bash)[0])
		if call.Cwd != filepath.Join(opt.Home, "x") {
			t.Errorf("git -C ~ %q", call.Cwd)
		}
	})
	t.Run("git_without_subcommand", func(t *testing.T) {
		if _, ok := AsGit(parse("git --version", Bash)[0]); ok {
			t.Error("git --version has no subcommand")
		}
		call, ok := AsGit(parse("git --git-dir .git --work-tree . log", Bash)[0])
		if !ok || call.Sub != "log" || len(call.GlobalOpts) != 4 {
			t.Errorf("%+v", call)
		}
	})
	t.Run("dynamic_invocation", func(t *testing.T) {
		if !parse(`git commit -m "$X"`, Bash)[0].Dynamic() || parse("git status", Bash)[0].Dynamic() {
			t.Error("Dynamic()")
		}
	})
	t.Run("http_clients", func(t *testing.T) {
		for _, p := range []string{"curl", "wget", "invoke-restmethod", "irm", "iwr", "xh"} {
			if !IsHTTPClient(p) {
				t.Error(p)
			}
		}
		if IsHTTPClient("git") {
			t.Error("git")
		}
	})
	t.Run("dialect_string", func(t *testing.T) {
		if Bash.String() != "bash" || PowerShell.String() != "powershell" {
			t.Error("String()")
		}
	})
	t.Run("parse_default_home", func(t *testing.T) {
		if invs := Parse("git status", Bash, testCwd); len(invs) != 1 {
			t.Errorf("%+v", invs)
		}
	})
	t.Run("resolve_dir", func(t *testing.T) {
		abs := filepath.Join(testCwd, "abs")
		if got := ResolveDir(testCwd, abs, ""); got != abs {
			t.Errorf("abs %q", got)
		}
		if got := ResolveDir(testCwd, "~", ""); got != testCwd {
			t.Errorf("no home %q", got)
		}
		if got := ResolveDir(testCwd, "../x", ""); got != filepath.Join(filepath.Dir(testCwd), "x") {
			t.Errorf("rel %q", got)
		}
	})
}

// TestMvdanFallback covers text mvdan.cc/sh rejects: Lex must fall back to
// the hand lexer (same result as LexFallback) and still find the command.
func TestMvdanFallback(t *testing.T) {
	for _, in := range []string{
		`git commit -m "unterminated`,
		"git commit -m 'feat: x' && (",
		"git push origin main; fi",
		"git commit -m \"$(cat <<'EOF'\nfeat: x\n", // unterminated heredoc and $(
		"git push )",
	} {
		if _, ok := lexBash(in); ok {
			t.Errorf("%q: expected mvdan to reject it", in)
			continue
		}
		got, fb := Lex(in, Bash), LexFallback(in, Bash)
		if !reflect.DeepEqual(got, fb) {
			t.Errorf("%q: Lex != LexFallback\n%#v\n%#v", in, got, fb)
		}
		if len(got) == 0 || got[0].Values()[0] != "git" {
			t.Errorf("%q: fallback lost the git command: %q", in, vals(got))
		}
	}
}

// TestMvdanWalksControlFlow documents the intentional deviation: mvdan walks
// into if/while/for bodies, so the commands inside are seen.
func TestMvdanWalksControlFlow(t *testing.T) {
	for _, in := range []string{
		"if true; then git push origin main; fi",
		"while false; do git push origin main; done",
		"for b in a; do git push origin main; done",
		"f() { git push origin main; }",
		"case x in x) git push origin main;; esac",
		"! git push origin main",
		"{ git push origin main; }",
	} {
		found := false
		for _, inv := range Parse(in, Bash, testCwd) {
			if inv.Program == "git" {
				found = true
			}
		}
		if !found {
			t.Errorf("%q: git push not found: %q", in, vals(Lex(in, Bash)))
		}
	}
}

func TestNeverPanics(t *testing.T) {
	for _, s := range []string{"'", "\"", "$(", "`", "<<", "<<EOF", "@'\n", "@\"\n", "a\\", "$", "${", "(((", "&&&",
		"git commit -m \"unterminated", "& ", "$env:", "$env:X =", "bash -c", "powershell -Command", "git -C", "git -c",
		"cd", "export", "env", "sudo", "\x00", "git commit -m $'\\x", "<<<", "git <<<'x'", "|||", ";;;", "))))"} {
		for _, d := range []Dialect{Bash, PowerShell} {
			Lex(s, d)
			LexFallback(s, d)
			for _, inv := range Parse(s, d, testCwd) {
				AsGit(inv)
				GhPath(inv)
			}
		}
	}
}
