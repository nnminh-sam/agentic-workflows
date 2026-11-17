"""Python expectations for rules helper functions -> internal/rules/testdata/functions.json."""
import json, os, sys
root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "tests"))
import helpers  # noqa
from helpers import contracts
from gitguard import identity, commit_message as cm, gh_policy, branching

c = contracts()
out = {}
urls = ["git@github.com:octocat/repo.git", "ssh://git@github.com/octocat/repo.git", "git+ssh://git@GitHub.com:22/o/r",
        "https://github.com/octocat/repo.git", "http://github.com/o/r", "git://github.com/o/r", "https://x:tok@github.com/o/r.git",
        "https://github.com/", "ftp://example.com/x", "../other", "D:/repos/x", "file:///tmp/x", "/abs/path", "relative",
        "github.com:owner", "C:\\repos\\x", "user@host:path/to/repo"]
out["parse_remote"] = [{"url": u, "kind": k, "host": h or "", "owner": o or ""} for u in urls for (k, h, o) in [identity.parse_remote(u)]]
out["is_local_url"] = [{"url": u, "local": identity.is_local_url(u)} for u in urls]
helpers_ = ["!gh auth git-credential", "!/opt/homebrew/bin/gh auth git-credential",
            '!"C:\\Program Files\\GitHub CLI\\gh.exe" auth git-credential', "osxkeychain", "manager", "!ghost auth git-credential",
            "", "!gh.exe auth git-credential", "gh auth git-credential", "!'gh' auth git-credential"]
out["is_gh_helper"] = [{"helper": h, "gh": identity.is_gh_helper(h)} for h in helpers_]
ssh = [('ssh -i "D:/keys/gh/id_ed25519" -o IdentitiesOnly=yes', "keys/gh/id_ed25519"),
       (r'ssh -i "D:\keys\gh\id_ed25519" -oIdentitiesOnly=YES', "keys/gh/id_ed25519"),
       (r'ssh -i D:\keys\gh\id_ed25519 -o IdentitiesOnly=yes', "keys/gh/id_ed25519"),
       ("ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes", "keys/gh/id_ed25519"),
       ('ssh -i "D:/keys/gh/id_ed25519"', "keys/gh/id_ed25519"),
       ("ssh -o IdentitiesOnly=yes", "keys/gh/id_ed25519"),
       ("ssh -o IdentitiesOnly=yes -i/k/gh/id_ed25519", "gh/id_ed25519"),
       ("ssh -i k -o IdentitiesOnly=yes", None),
       ("ssh", None),
       ("ssh -o 'IdentitiesOnly yes' -i k", "k"),
       ("ssh -i 'unterminated", "k")]
out["ssh_problems"] = [{"cmd": s, "key": k or "", "n": len(identity.ssh_command_problems(s, k, ["IdentitiesOnly=yes"]))} for s, k in ssh]
msgs = ["\n\nfeat: add x   \n\n\n\nbody\n\n", "a\r\nb\r\n", "", "  \n", "x\n\n\ny"]
out["cleanup"] = [{"in": m, "out": cm.cleanup(m)} for m in msgs]
check_msgs = ["feat: add login", "Added stuff.", "feat: add stuff.", "feat:add", "feat(API)!: X.", "Fix: Typo.",
              "feat: WIP " + "x" * 80, "fixup! anything", "Merge pull request #1 from x", "chore(release): v1.0.0",
              "feat: add x\nbody", "feat: add x\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
              "feat: add x\n\nGenerated with Claude Code", "revert: feat: add x", "feat(a,b): x y", "docs(readme): add usage",
              "feat: added login", "style: format", "BREAKING: x", "feat!: drop node 16\n\nBREAKING CHANGE: node 18+"]
out["check_message"] = []
for m in check_msgs:
    v = cm.check(m, c.commit)
    h = cm.check(m, c.commit, header_only=True, severity_override="warn")
    out["check_message"].append({"msg": m, "decision": v.decision, "rules": sorted({f.rule_id for f in v.findings}),
                                 "header_warn_decision": h.decision, "header_rules": sorted({f.rule_id for f in h.findings})})
GOOD = ("## Summary\nAdd x.\n\n## Detailed changes\n- add x\n\n## Explanation\nNeeded.\n\n"
        "## Test results\n### Unit\n`npm test`: 3 passed\n### Lint\n`npm run lint`: clean\n")
bodies = [GOOD, "", "nothing", GOOD.replace("Needed.", "<!-- why -->\nTODO"), GOOD.replace("### Lint\n`npm run lint`: clean\n", ""),
          GOOD.replace("## ", "# "), GOOD.replace("## Summary", "## summary"), GOOD + "\n## Extra\nx\n",
          "## Summary\n### sub\n\n## Detailed changes\n- a\n## Explanation\nb\n## Test results\nunit lint\n"]
p8 = c.gh.rule("GH008").params
out["body_sections"] = [{"body": b, "problems": gh_policy.body_section_problems(b, p8)} for b in bodies]
targets = [("develop/base", "develop/feat/x"), ("main", "develop/base"), ("main", "develop/feat/x"), ("main", None),
           ("develop/feat/y", "develop/feat/x"), (None, "x"), ("main", "octocat:develop/base"), ("develop/base", None)]
out["pr_target"] = [{"base": b or "", "head": h or "", "problem": bool(branching.pr_target_problem(b, h, c.branch))} for b, h in targets]
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1, ensure_ascii=False); fh.write("\n")
