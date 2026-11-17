"""Generate cli/internal/engine/testdata/cases.json with expectations computed by the Python git-guard.

Run: python3 gen_cases.py <plugin-root> <out.json>
Cases with "source": "go" (ID006, Go-only behaviour) carry hand-written expectations and are not run in Python.
"""
import json
import os
import sys

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "tests"))
import helpers  # noqa: E402
from helpers import GOOD_SSH, GH_HELPER, FakeRunner, contracts, evaluate, ids, PROFILE  # noqa: E402

GOOD_BODY = ("## Summary\nAdd x.\n\n## Detailed changes\n- add x\n\n## Explanation\nNeeded.\n\n"
             "## Test results\n### Unit\n`npm test`: 3 passed\n### Lint\n`npm run lint`: clean\n")
FILES = {"pr.md": GOOD_BODY, "msg.txt": "fix(api): handle null body\n", "bad.txt": "update stuff\n"}
GH_PROFILE = {**{k: v for k, v in PROFILE.items() if k != "ssh_key"}, "git_transport": "gh"}
HTTPS = {"origin": "https://github.com/octocat/repo.git"}
HEREDOC = "git commit -m \"$(cat <<'EOF'\nfeat(hooks): add git-guard\n\nChecks identity and messages.\nEOF\n)\""
HEREDOC_AI = ("git commit -m \"$(cat <<'EOF'\nfeat(hooks): add git-guard\n\nChecks identity.\n\n"
              "Co-Authored-By: Claude <noreply@anthropic.com>\nEOF\n)\"")

# (name, command, dialect, repo kwargs for FakeRunner, profile, extra)
C = []


def case(name, command, dialect="bash", repo=None, profile="default", cwd=None, env=None, files=None):
    C.append(dict(name=name, command=command, dialect=dialect, repo=repo or {}, profile=profile,
                  cwd=cwd, env=env or {}, files=files))


# ---- spec / basics
case("spec_block_example", 'git commit -m "Added stuff."')
case("spec_fixed_example", 'git commit -m "feat: add stuff"')
case("subject_period", 'git commit -m "feat: add stuff."')
case("not_interesting", "ls -la && echo done")
case("read_only_chain", "git status; git log --oneline | head -5")
case("and_chain_good_commit", "git add -A && git commit -m 'feat: add login'")
case("chain_echo_bad_commit", "echo hi && git commit -m 'update stuff'")
case("or_chain", "git pull --rebase || git commit -m 'Fix stuff'")
case("pipe_redirect_push", "git push origin develop/feat/test 2>&1 | tee push.log")
case("bash_c_commit", "bash -c \"git commit -m 'feat: add x'\"")
case("bash_lc_cd_push_main", "bash -lc 'cd x && git push origin main'")
case("sh_c_bad_commit", "sh -c 'git commit -m WIP'")
case("env_prefix_author", "GIT_AUTHOR_NAME=bob git commit -m 'feat: add x'")
case("env_cmd_prefix_email", "env GIT_AUTHOR_EMAIL=m@evil.io git commit -m 'feat: add x'")
case("export_ssh_then_push", f"export GIT_SSH_COMMAND='{GOOD_SSH}'; git push", repo={"ssh": None})
case("env_prefix_ssh_fetch", f"GIT_SSH_COMMAND='{GOOD_SSH}' git fetch", repo={"ssh": None})
case("git_C_commit", "git -C sub commit -m 'feat: add x'")
case("dash_c_user_name", "git -c user.name=mallory commit -m 'feat: add x'")
case("dash_c_ssh_pull", f"git -c core.sshCommand='{GOOD_SSH}' pull", repo={"ssh": None})
case("heredoc_message", HEREDOC)
case("heredoc_ai_coauthor", HEREDOC_AI)
case("heredoc_stdin_F", "git commit -F - <<'EOF'\nfix: handle null\n\nbody\nEOF")
case("file_F_good", "git commit -F msg.txt", files=FILES)
case("file_F_bad", "git commit --file=bad.txt", files=FILES)
case("file_F_missing", "git commit -F nope.txt", files=FILES)
case("dynamic_message", 'git commit -m "$MSG"')
case("amend_no_edit", "git commit --amend --no-edit")
case("commit_no_message", "git commit")
case("commit_on_main", "git commit -m 'feat: add x'", repo={"branch": "main"})
case("initial_commit_main", "git commit -m 'chore: initialize repository'", repo={"branch": "main", "has_commits": False})
case("wrong_identity", "git commit -m 'feat: add x'", repo={"name": "someone", "email": "x@other.example"})
# ---- push / branching
case("push_work_branch", "git push -u origin develop/feat/test")
case("push_force_work_branch", "git push --force origin develop/feat/test")
case("push_force_main", "git push --force origin main")
case("push_force_plus_base", "git push --force origin +develop/base")
case("push_head_to_main", "git push origin HEAD:refs/heads/main")
case("push_delete_main", "git push origin --delete main")
case("push_all", "git push --all origin")
case("push_from_main", "git push", repo={"branch": "main"})
case("first_push_protected", "git push -u origin develop/base", repo={"branch": "develop/base", "remote_branches": []})
case("sudo_push_main", "sudo git push origin main")
case("subshell_push_main", "(cd sub && git push origin main)")
case("push_no_ssh", "git push", repo={"ssh": None})
case("push_https_remote", "git push", repo={"remotes": HTTPS})
case("push_foreign_remote", "git push upstream", repo={"remotes": {"upstream": "git@github.com:someone/repo.git"}})
case("fetch_all_mixed", "git fetch --all", repo={"remotes": {"origin": "git@github.com:octocat/r.git", "other": "https://gitlab.com/x/y.git"}})
case("remote_add_https", "git remote add up https://github.com/a/b")
case("clone_ssh_pinned", f"git clone -c core.sshCommand='{GOOD_SSH}' git@github.com:octocat/x.git", repo={"ssh": None})
case("switch_c_good", "git switch -c develop/feat/login develop/base")
case("switch_c_bad_name", "git switch -c Feature/Login develop/base")
case("switch_c_bad_base", "git switch -c develop/feat/x main")
case("checkout_b_from_work", "git checkout -b develop/fix/x")
case("branch_listing", "git branch -a && git switch develop/base")
case("merge_on_base", "git merge develop/feat/x", repo={"branch": "develop/base"})
# ---- gh
case("gh_pr_list", "gh pr list")
case("gh_wrong_account", "gh pr list", repo={"gh_login": "someone-else"})
case("gh_not_logged_in", "gh issue list", repo={"gh_login": None})
case("gh_repo_delete", "gh repo delete octocat/x --yes")
case("gh_auth_token", "gh auth token")
case("gh_pr_merge", "gh pr merge 3 --squash")
case("gh_api_delete", "gh api -X DELETE repos/octocat/x/git/refs/heads/y")
case("gh_api_graphql_mutation", "gh api graphql -f query='mutation { x }'")
case("gh_api_foreign_owner", "gh api repos/someone/x/issues")
case("gh_pr_create_body_file", "gh pr create --base develop/base --title 'feat: add x' --body-file pr.md", files=FILES)
case("gh_pr_create_body_file_eq", "gh pr create --base develop/base --title 'feat: add x' --body-file=pr.md", files=FILES)
case("gh_pr_create_body_missing", "gh pr create --base develop/base --title 'feat: add x' --body-file nope.md", files=FILES)
case("gh_pr_create_no_base", "gh pr create --title 'feat: add x' -F pr.md", files=FILES)
case("gh_pr_create_interactive", "gh pr create")
case("gh_pr_create_bad_title", "gh pr create --title 'Fix stuff' --base develop/base -F pr.md", files=FILES)
case("gh_pr_create_release", "gh pr create --base main --head develop/base --title 'chore(release): v1.0.0' -F pr.md", files=FILES)
case("gh_repo_create_public", "gh repo create octocat/x --public")
case("gh_repo_create_no_vis", "gh repo create octocat/x")
case("gh_chain_strictest", "gh pr list && gh pr merge 1 && gh repo delete a/b --yes")
case("gh_auth_switch_wrong", "gh auth switch --user octocat", repo={"gh_login": "someone-else"})
case("nested_powershell_from_bash", 'powershell -NoProfile -Command "gh auth token"')
# ---- http clients
case("curl_api", "curl -s https://api.github.com/user")
case("curl_post_api", "curl -X POST -H 'Accept: application/json' https://api.github.com/repos/a/b/issues -d '{}'")
case("wget_api", "wget -qO- https://api.github.com/rate_limit")
case("curl_lookalike", "curl https://example.com/api.github.com.html")
# ---- gh transport profile
case("gh_transport_push", "git push", repo={"ssh": None, "remotes": HTTPS, "helper": GH_HELPER}, profile="gh")
case("gh_transport_bad_helper", "git push", repo={"ssh": None, "remotes": HTTPS, "helper": "osxkeychain"}, profile="gh")
case("gh_transport_token_url", "git push https://x:ghp_secret@github.com/octocat/repo.git develop/feat/test",
     repo={"ssh": None, "remotes": HTTPS, "helper": GH_HELPER}, profile="gh")
# ---- no profile
case("no_profile_commit", "git commit -m 'feat: add x'", profile="none")
case("no_profile_push", "git push", profile="none")
# ---- PowerShell
P = "powershell"
case("ps_spec_block_example", 'git commit -m "Added stuff."', P)
case("ps_here_string_good", "git commit -m @'\nfeat: add login\n\nIt's \"quoted\".\n'@", P)
case("ps_here_string_bad", "git commit -m @'\nupdated things\n'@", P)
case("ps_backtick_escape", 'git commit -m "fix: say `"hi`""', P)
case("ps_dynamic", 'git commit -m "$msg"', P)
case("ps_irm_api", "Invoke-RestMethod -Uri https://api.github.com/repos/a/b", P)
case("ps_irm_alias_post", "irm https://api.github.com/user -Method Post", P)
case("ps_iwr_example", "iwr https://example.com", P)
case("ps_env_ssh_push", f'$env:GIT_SSH_COMMAND = "{GOOD_SSH.replace(chr(34), "`" + chr(34))}"; git push', P, repo={"ssh": None})
case("ps_call_operator_push_main", '& "C:\\Program Files\\Git\\cmd\\git.exe" push origin main', P)
case("ps_semicolon_strictest", "git status; gh repo delete a/b --yes", P)
case("ps_pipeline", "gh pr list | Select-Object -First 1", P)
case("ps_set_location_commit", "Set-Location sub; git commit -m 'feat: add x'", P)
case("ps_pr_create_body_file", "gh pr create --base develop/base --title \"feat: add x\" --body-file pr.md", P, files=FILES)
case("ps_switch_bad_name", "git switch -c feat/login develop/base", P)
case("ps_bash_c_inside", "bash -c 'git push origin main'", P)

PROFILES = {"default": None, "gh": GH_PROFILE, "none": {}}


def py_runner(r):
    kw = {}
    for k in ("name", "email", "ssh", "remotes", "gh_login", "branch", "helper", "has_commits"):
        if k in r:
            kw[k] = r[k]
    if "remote_branches" in r:
        kw["remote_branches"] = tuple(r["remote_branches"])
    return FakeRunner(**kw)


out = []
for c in C:
    prof = PROFILES[c["profile"]]
    kwargs = dict(dialect=c["dialect"], runner=py_runner(c["repo"]), contracts_obj=contracts(profile=prof),
                  files=c["files"])
    if c["env"]:
        kwargs["env"] = c["env"]
    if c["cwd"]:
        kwargs["cwd"] = c["cwd"]
    v = evaluate(c["command"], **kwargs)
    item = {k: c[k] for k in ("name", "command", "dialect", "profile")}
    for k in ("repo", "env", "files"):
        if c[k]:
            item[k] = c[k]
    item["decision"] = v.decision
    item["rules"] = ids(v)
    item["source"] = "python"
    out.append(item)

# ---- Go-only cases: ID006 (profile signing_format), hand-written expectations
SIGN = [
    ("id006_unsigned", "git commit -m 'feat: add x'", "ssh", "warn", ["ID006"]),
    ("id006_S", "git commit -S -m 'feat: add x'", "ssh", "allow", []),
    ("id006_aS", "git commit -aS -m 'feat: add x'", "ssh", "allow", []),
    ("id006_gpg_sign", "git commit --gpg-sign -m 'feat: add x'", "ssh", "allow", []),
    ("id006_gpg_sign_key", "git commit --gpg-sign=ABC123 -m 'feat: add x'", "openpgp", "allow", []),
    ("id006_config_true", "git -c commit.gpgsign=true commit -m 'feat: add x'", "ssh", "allow", []),
    ("id006_no_gpg_sign", "git commit -S --no-gpg-sign -m 'feat: add x'", "ssh", "warn", ["ID006"]),
    ("id006_config_false", "git -c commit.gpgsign=false commit -S -m 'feat: add x'", "ssh", "warn", ["ID006"]),
    ("id006_format_none", "git commit -m 'feat: add x'", "none", "allow", []),
    ("id006_with_bad_message", "git commit -m 'Added stuff.'", "x509", "deny", ["CM003", "ID006"]),
    ("id006_not_commit", "git push origin develop/feat/test", "ssh", "allow", []),
    ("id006_powershell", "git commit -S -m 'feat: add x'", "ssh", "allow", []),
]
for name, cmd, fmt, dec, rules in SIGN:
    out.append({"name": name, "command": cmd, "dialect": "powershell" if "powershell" in name else "bash",
                "profile": "default", "vars": {"signing_format": fmt}, "decision": dec, "rules": rules,
                "source": "go"})

with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print(len(out), "cases")
