import unittest

from helpers import CWD, contracts, evaluate, ids, set_severity
from gitguard import commit_message
from gitguard.commands import git_call, parse
from gitguard.shellparse import BASH, POWERSHELL


def check(msg, header_only=False, mutate=None):
    return commit_message.check(msg, contracts(mutate).commit, header_only=header_only)


class Convention(unittest.TestCase):
    def test_valid_messages(self):
        for m in ["feat: add login", "fix(api): handle 404 from upstream", "refactor(ui/button)!: drop size prop",
                  "docs: update README for API v2", "feat: add retry\n\nLonger body.\n\nRefs: #12",
                  "feat!: new config\n\nBREAKING CHANGE: config moved to .env"]:
            with self.subTest(m=m):
                self.assertEqual(check(m).findings, [])

    def test_exempt_headers(self):
        for m in ["Merge branch 'main' into x", 'Revert "feat: add login"', "fixup! feat: add login"]:
            self.assertEqual(check(m).findings, [], m)

    def cases(self):
        return [
            ("Added login", "CM003"), ("feat:add login", "CM003"), ("feat (api): x", "CM003"), ("", "CM003"),
            ("feature: add login", "CM004"), ("Fix: typo", "CM004"),
            ("feat(API): add", "CM005"), ("feat(): add", "CM005"),
            ("fix: x", "CM006"),
            ("feat: " + "a" * 70, "CM007"),
            ("docs: fix typo.", "CM008"),
            ("feat: Add login", "CM009"),
            ("fix: fixed empty list", "CM010"),
            ("feat: x y z\nbody right away", "CM011"),
            ("feat: add x\n\n" + "w" * 120, "CM012"),
            ("feat: add x\n\nbreaking change: moved", "CM013"),
            ("feat: WIP login", "CM014"),
        ]

    def test_each_rule_fires(self):
        for msg, rid in self.cases():
            with self.subTest(rule=rid, msg=msg):
                self.assertIn(rid, ids(check(msg)))

    def test_severities(self):
        v = check("feat: Add login")
        self.assertEqual(v.decision, "warn")
        v = check("Added login")
        self.assertEqual(v.decision, "deny")

    def test_long_url_lines_are_ignored(self):
        self.assertEqual(check("docs: add link\n\nhttps://example.com/" + "x" * 120).findings, [])

    def test_hint_for_missing_space(self):
        msg = check("feat:add").findings[0].message
        self.assertIn("one space after the colon", msg)

    def test_allowed_scopes(self):
        def m(raw):
            for r in raw["commit-message"]["rules"]:
                if r["id"] == "CM005":
                    r["params"]["allowed_scopes"] = ["api"]
        self.assertEqual(check("feat(api): add x", mutate=m).findings, [])
        self.assertIn("CM005", ids(check("feat(web): add x", mutate=m)))

    def test_required_trailers_when_enabled(self):
        def m(raw):
            set_severity(raw, "CM015", "deny")
            for r in raw["commit-message"]["rules"]:
                if r["id"] == "CM015":
                    r["params"]["trailers"] = ["Signed-off-by"]
        self.assertIn("CM015", ids(check("feat: add x", mutate=m)))
        self.assertEqual(check("feat: add x\n\nSigned-off-by: A <a@b.c>", mutate=m).findings, [])

    def test_off_rule_is_silent(self):
        self.assertEqual(check("feat: Add login", mutate=lambda r: set_severity(r, "CM009", "off")).findings, [])

    def test_header_only_skips_body_rules(self):
        self.assertEqual(ids(check("feat: add x\nbody", header_only=True)), [])

    def test_cleanup_trims_whitespace(self):
        self.assertEqual(check("\n\nfeat: add x   \n\n\n\nbody\n\n").findings, [])


def extract(cmd, dialect=BASH, files=None):
    call = git_call(parse(cmd, dialect, CWD)[0])
    files = files or {}

    def read(path):
        for k, v in files.items():
            if path.endswith(k):
                return v
        raise FileNotFoundError(path)
    return commit_message.extract(call, read)


class Extraction(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(extract("git commit -m 'feat: a'").text, "feat: a")
        self.assertEqual(extract("git commit -am 'feat: a'").text, "feat: a")
        self.assertEqual(extract("git commit -m'feat: a'").text, "feat: a")
        self.assertEqual(extract("git commit --message='feat: a'").text, "feat: a")
        self.assertEqual(extract("git commit --message 'feat: a' -m body").text, "feat: a\n\nbody")

    def test_file_and_stdin(self):
        self.assertEqual(extract("git commit -F msg.txt", files={"msg.txt": "fix: b"}).text, "fix: b")
        self.assertEqual(extract("git commit --file=msg.txt", files={"msg.txt": "fix: b"}).text, "fix: b")
        self.assertEqual(extract("git commit -F - <<'EOF'\nfix: c\nEOF").text, "fix: c\n")
        self.assertEqual(extract("git commit -F missing.txt").kind, "unreadable")

    def test_reuse_and_none(self):
        self.assertEqual(extract("git commit").kind, "none")
        self.assertEqual(extract("git commit --amend").kind, "none")
        self.assertEqual(extract("git commit --amend --no-edit").kind, "reuse")
        self.assertEqual(extract("git commit --fixup HEAD~1").kind, "reuse")
        self.assertEqual(extract("git commit -C HEAD").kind, "reuse")

    def test_dynamic(self):
        self.assertEqual(extract('git commit -m "$MSG"').kind, "dynamic")
        self.assertEqual(extract('git commit -m "$msg"', POWERSHELL).kind, "dynamic")

    def test_powershell_here_string(self):
        self.assertEqual(extract("git commit -m @'\nfeat: a\n\nbody\n'@", POWERSHELL).text, "feat: a\n\nbody")


class CommitThroughEngine(unittest.TestCase):
    def test_good_commit_passes(self):
        self.assertEqual(evaluate("git add -A && git commit -m 'feat: add login'").findings, [])

    def test_bad_commit_denied(self):
        v = evaluate("git commit -m 'update stuff'")
        self.assertEqual(v.decision, "deny")
        self.assertIn("CM003", ids(v))

    def test_no_message_denied(self):
        self.assertIn("CM001", ids(evaluate("git commit")))

    def test_dynamic_asks(self):
        v = evaluate('git commit -m "$MSG"')
        self.assertEqual((v.decision, ids(v)), ("ask", ["CM002"]))

    def test_claude_style_heredoc(self):
        cmd = ("git commit -m \"$(cat <<'EOF'\nfeat(hooks): add git-guard\n\nChecks identity and messages.\n\n"
               "Co-Authored-By: Claude <noreply@anthropic.com>\nEOF\n)\"")
        self.assertEqual(evaluate(cmd).findings, [])


if __name__ == "__main__":
    unittest.main()
