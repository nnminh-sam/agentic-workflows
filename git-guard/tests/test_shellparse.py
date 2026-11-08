import os
import unittest

from helpers import CWD  # noqa: F401  (sets sys.path)
from gitguard.commands import git_call, parse, program_name
from gitguard.shellparse import BASH, POWERSHELL, lex


def vals(text, dialect=BASH):
    return [s.values for s in lex(text, dialect)]


class LexBash(unittest.TestCase):
    def test_splits_on_operators(self):
        self.assertEqual(vals("git add . && git commit -m x; echo hi | cat || true"),
                         [["git", "add", "."], ["git", "commit", "-m", "x"], ["echo", "hi"], ["cat"], ["true"]])

    def test_newlines_split_commands(self):
        self.assertEqual(vals("git status\ngit log"), [["git", "status"], ["git", "log"]])

    def test_quotes_and_escapes(self):
        self.assertEqual(vals("""git commit -m "feat: say \\"hi\\"" -m 'it'"'"'s ok'"""),
                         [["git", "commit", "-m", 'feat: say "hi"', "-m", "it's ok"]])

    def test_operators_inside_quotes_do_not_split(self):
        self.assertEqual(vals('git commit -m "fix: a && b; c | d"'), [["git", "commit", "-m", "fix: a && b; c | d"]])

    def test_cat_heredoc_message_is_resolved(self):
        cmd = 'git commit -m "$(cat <<\'EOF\'\nfeat(api): add retry\n\nBody with "quotes" and ) parens.\nEOF\n)"'
        seg = lex(cmd)[0]
        self.assertEqual(seg.values[3], 'feat(api): add retry\n\nBody with "quotes" and ) parens.')
        self.assertFalse(seg.words[3].dynamic)

    def test_heredoc_to_stdin(self):
        cmd = "git commit -F - <<'EOF'\nfix: handle null\n\nbody\nEOF\necho done"
        segs = lex(cmd)
        self.assertEqual(segs[0].values, ["git", "commit", "-F", "-"])
        self.assertEqual(segs[0].stdin, "fix: handle null\n\nbody\n")
        self.assertEqual(segs[1].values, ["echo", "done"])

    def test_dynamic_words(self):
        seg = lex('git commit -m "$MSG" -m "$(git log -1 --format=%s)" -m plain')[0]
        self.assertEqual([w.dynamic for w in seg.words[3::2]], [True, True, False])

    def test_redirections_are_dropped(self):
        self.assertEqual(vals("git push origin main 2>&1 > /dev/null"), [["git", "push", "origin", "main"]])

    def test_comments(self):
        self.assertEqual(vals("git status # check it\n# whole line\ngit log"), [["git", "status"], ["git", "log"]])

    def test_ansi_c_quotes(self):
        self.assertEqual(vals("git commit -m $'feat: x\\n\\nbody'"), [["git", "commit", "-m", "feat: x\n\nbody"]])

    def test_line_continuation(self):
        self.assertEqual(vals("git commit \\\n  -m 'fix: y'"), [["git", "commit", "-m", "fix: y"]])

    def test_subshell_parens_split(self):
        self.assertEqual(vals("(cd sub && git push)"), [["cd", "sub"], ["git", "push"]])


class LexPowerShell(unittest.TestCase):
    def test_here_string(self):
        cmd = "git commit -m @'\nfeat: add login\n\nIt's \"quoted\".\n'@"
        seg = lex(cmd, POWERSHELL)[0]
        self.assertEqual(seg.values, ["git", "commit", "-m", "feat: add login\n\nIt's \"quoted\"."])
        self.assertFalse(seg.words[3].dynamic)

    def test_backtick_escapes_and_doubled_quotes(self):
        self.assertEqual(vals('git commit -m "fix: say `"hi`"" -m \'it\'\'s\'', POWERSHELL),
                         [["git", "commit", "-m", 'fix: say "hi"', "-m", "it's"]])

    def test_semicolon_and_pipeline(self):
        self.assertEqual(vals("git status; gh pr list | Select-Object -First 1", POWERSHELL),
                         [["git", "status"], ["gh", "pr", "list"], ["Select-Object", "-First", "1"]])

    def test_variable_is_dynamic(self):
        seg = lex('git commit -m "$msg"', POWERSHELL)[0]
        self.assertTrue(seg.words[3].dynamic)


class Invocations(unittest.TestCase):
    def test_program_name(self):
        self.assertEqual(program_name(r"C:\Program Files\Git\cmd\git.exe"), "git")
        self.assertEqual(program_name("/usr/bin/gh"), "gh")

    def test_cd_changes_cwd(self):
        inv = parse("cd sub && git push", BASH, CWD)[0]
        self.assertEqual(inv.cwd, os.path.join(CWD, "sub"))

    def test_env_prefix_and_export(self):
        invs = parse("export GH_TOKEN=abc; GIT_SSH_COMMAND='ssh -i k' git push", BASH, CWD)
        self.assertEqual(invs[0].env, {"GH_TOKEN": "abc", "GIT_SSH_COMMAND": "ssh -i k"})

    def test_powershell_env_assignment(self):
        invs = parse('$env:GIT_SSH_COMMAND = "ssh -i k"; git push', POWERSHELL, CWD)
        self.assertEqual(invs[0].env["GIT_SSH_COMMAND"], "ssh -i k")

    def test_powershell_call_operator(self):
        inv = parse('& "C:\\Program Files\\Git\\cmd\\git.exe" status', POWERSHELL, CWD)[0]
        self.assertEqual((inv.program, inv.argv), ("git", ["status"]))

    def test_nested_shells(self):
        invs = parse("bash -lc 'cd x && git push'", BASH, CWD)
        self.assertEqual([i.program for i in invs], ["git"])
        self.assertEqual(invs[0].cwd, os.path.join(CWD, "x"))
        invs = parse('powershell -NoProfile -Command "gh repo delete a/b --yes"', BASH, CWD)
        self.assertEqual([(i.program, i.argv[:2]) for i in invs], [("gh", ["repo", "delete"])])

    def test_git_globals(self):
        inv = parse("git -C sub -c user.name=x --no-pager commit -m 'fix: y'", BASH, CWD)[0]
        call = git_call(inv)
        self.assertEqual(call.sub, "commit")
        self.assertEqual(call.cwd, os.path.join(CWD, "sub"))
        self.assertEqual(call.config, {"user.name": "x"})
        self.assertEqual(call.argv, ["-m", "fix: y"])


if __name__ == "__main__":
    unittest.main()
