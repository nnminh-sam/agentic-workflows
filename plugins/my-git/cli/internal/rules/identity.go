package rules

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// networkSubs are the git subcommands that talk to a remote.
var networkSubs = map[string]bool{"push": true, "pull": true, "fetch": true, "clone": true, "ls-remote": true}

var (
	authorRe   = regexp.MustCompile(`^\s*(.*?)\s*<([^>]*)>\s*$`)
	emailRe    = regexp.MustCompile(`^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$`)
	winDriveRe = regexp.MustCompile(`^[A-Za-z]:[\\/]`)
	scpLikeRe  = regexp.MustCompile(`^[\w.-]+@[\w.-]+:`)
	scpRe      = regexp.MustCompile(`^[\w.-]+@([\w.-]+):/?(.*)$`)
	urlRe      = regexp.MustCompile(`(?i)^([a-z+]+)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.*)$`)
	optCanonRe = regexp.MustCompile(`\s*[= ]\s*`)
	ghHelperRe = regexp.MustCompile(`(?i)(^|[\s/\\!"'])gh(\.exe)?["']?\s+auth\s+git-credential(\s|$)`)
	userinfoRe = regexp.MustCompile(`(?i)^([a-z+]+://)([^@/]+)@`)
)

// ---------------------------------------------------------------- author (ID001 / ID002)

// EffectiveAuthor returns the author name and email a commit would use:
// --author, then -c user.name/user.email, GIT_AUTHOR_NAME/EMAIL, git config.
func EffectiveAuthor(call *shell.GitCall, repo Repo, getenv func(string) string) (name, email string) {
	argv := call.Argv()
	for i, a := range argv {
		val, ok := "", false
		if strings.HasPrefix(a, "--author=") {
			val, ok = strings.SplitN(a, "=", 2)[1], true
		} else if a == "--author" && i+1 < len(argv) {
			val, ok = argv[i+1], true
		}
		if ok {
			if m := authorRe.FindStringSubmatch(val); m != nil {
				return m[1], m[2]
			}
		}
	}
	pick := func(cfgKey, envKey string) string {
		if v := call.Config[cfgKey]; v != "" {
			return v
		}
		if v := lookup(call.Env, getenv, envKey); v != "" {
			return v
		}
		v, _ := repo.Config(cfgKey)
		return v
	}
	return pick("user.name", "GIT_AUTHOR_NAME"), pick("user.email", "GIT_AUTHOR_EMAIL")
}

func nameProblem(name string, params map[string]any) string {
	expected := nonEmpty(policy.Strings(params, "expected"))
	if name == "" {
		return "commit author name is unset"
	}
	if len(expected) > 0 && !contains(expected, name) {
		return fmt.Sprintf("commit author name is '%s', expected one of %s", name, pyList(expected))
	}
	return ""
}

func emailProblem(email string, params map[string]any) string {
	expected := nonEmpty(policy.Strings(params, "expected"))
	pats := policy.Strings(params, "patterns")
	if email == "" {
		return "commit author email is unset"
	}
	if len(expected) > 0 || len(pats) > 0 {
		if contains(lowerAll(expected), strings.ToLower(email)) {
			return ""
		}
		for _, p := range pats {
			if reFullmatch(p, email, true) {
				return ""
			}
		}
		allowed := append([]string{}, expected...)
		for _, p := range pats {
			allowed = append(allowed, "/"+p+"/")
		}
		return fmt.Sprintf("commit author email is '%s', expected one of %s", email, pyList(allowed))
	}
	if !emailRe.MatchString(email) {
		return fmt.Sprintf("commit author email '%s' is not a valid email address", email)
	}
	return ""
}

func firstOr(xs []string, def string) string {
	if len(xs) > 0 {
		return xs[0]
	}
	return def
}

// CheckAuthor applies ID001 (author name) and ID002 (author email) to a git commit.
func CheckAuthor(call *shell.GitCall, c *policy.Contract, repo Repo, getenv func(string) string) verdict.Verdict {
	var v verdict.Verdict
	r1, r2 := c.Rule("ID001"), c.Rule("ID002")
	if r1.Severity == verdict.Off && r2.Severity == verdict.Off {
		return v
	}
	name, email := EffectiveAuthor(call, repo, getenv)
	if r1.Severity != verdict.Off {
		if prob := nameProblem(name, r1.Params); prob != "" {
			want := firstOr(nonEmpty(policy.Strings(r1.Params, "expected")), "<your name>")
			v.Addf("ID001", r1.Severity, "%s. Fix: git -C \"%s\" config user.name \"%s\"", prob, call.Cwd, want)
		}
	}
	if r2.Severity != verdict.Off {
		if prob := emailProblem(email, r2.Params); prob != "" {
			want := firstOr(nonEmpty(policy.Strings(r2.Params, "expected")), "<your email>")
			v.Addf("ID002", r2.Severity, "%s. Fix: git -C \"%s\" config user.email \"%s\"", prob, call.Cwd, want)
		}
	}
	return v
}

// ---------------------------------------------------------------- signing (ID006)

// CheckSigning applies ID006: when the profile's signing_format is set and
// not "none", a git commit must be signed (-S / --gpg-sign, or
// -c commit.gpgsign=true) and must not pass --no-gpg-sign or
// -c commit.gpgsign=false.
func CheckSigning(call *shell.GitCall, c *policy.Contract, vars map[string]any) verdict.Verdict {
	var v verdict.Verdict
	r := c.Rule("ID006")
	format, _ := vars["signing_format"].(string)
	if r.Severity == verdict.Off || call.Sub != "commit" || format == "" || format == "none" {
		return v
	}
	signed, unsigned := signFlags(call.Argv())
	switch gpgsign := strings.ToLower(call.Config["commit.gpgsign"]); {
	case unsigned:
		v.Addf("ID006", r.Severity, "the profile signs commits (signing_format '%s') but this commit passes "+
			"--no-gpg-sign: remove it and pass -S, e.g. git commit -S -m '<message>'", format)
	case gpgsign == "false" || gpgsign == "no" || gpgsign == "off" || gpgsign == "0":
		v.Addf("ID006", r.Severity, "the profile signs commits (signing_format '%s') but this commit passes "+
			"-c commit.gpgsign=%s: remove it and pass -S, e.g. git commit -S -m '<message>'", format, call.Config["commit.gpgsign"])
	case !signed && gpgsign != "true" && gpgsign != "yes" && gpgsign != "on" && gpgsign != "1":
		v.Addf("ID006", r.Severity, "the profile signs commits (signing_format '%s') but this commit is not "+
			"signed: add -S, e.g. git commit -S -m '<message>'", format)
	}
	return v
}

// signFlags scans git commit arguments for -S/--gpg-sign and --no-gpg-sign.
func signFlags(argv []string) (signed, unsigned bool) {
	for _, a := range argv {
		switch {
		case a == "--":
			return signed, unsigned
		case a == "--gpg-sign" || strings.HasPrefix(a, "--gpg-sign="):
			signed = true
		case a == "--no-gpg-sign":
			unsigned = true
		case strings.HasPrefix(a, "-") && !strings.HasPrefix(a, "--"):
			signed = signed || clusterHasS(a[1:])
		}
	}
	return signed, unsigned
}

// clusterHasS reports -S inside a short-option cluster, stopping at the
// first option that takes a value (-m, -F, -C, -c, -t).
func clusterHasS(body string) bool {
	for _, ch := range body {
		switch {
		case ch == 'S':
			return true
		case strings.ContainsRune("mFCct", ch):
			return false
		}
	}
	return false
}

// ---------------------------------------------------------------- remotes & ssh (ID003 / ID004)

// IsLocalURL reports file paths and file:// URLs.
func IsLocalURL(url string) bool {
	if strings.HasPrefix(url, "file://") || winDriveRe.MatchString(url) {
		return true
	}
	if strings.Contains(url, "://") {
		return false
	}
	head := strings.SplitN(url, "/", 2)[0]
	return !strings.Contains(head, ":")
}

func isSSHURL(url string) bool { return strings.HasPrefix(url, "ssh://") || scpLikeRe.MatchString(url) }

// ParseRemote returns (kind, host, owner) of a remote URL; kind is "ssh",
// "https", "http", "git" or "other". owner is "" when unknown.
func ParseRemote(url string) (kind, host, owner string) {
	var path string
	if m := scpRe.FindStringSubmatch(url); m != nil && !strings.Contains(url, "://") {
		kind, host, path = "ssh", m[1], m[2]
	} else {
		m := urlRe.FindStringSubmatch(url)
		if m == nil {
			return "other", "", ""
		}
		switch scheme := strings.ToLower(m[1]); scheme {
		case "ssh", "git+ssh", "ssh+git":
			kind = "ssh"
		case "https", "http", "git":
			kind = scheme
		default:
			kind = "other"
		}
		host, path = m[2], m[3]
	}
	if p := strings.Trim(path, "/"); strings.Contains(p, "/") {
		owner = strings.SplitN(p, "/", 2)[0]
	}
	return kind, strings.ToLower(host), owner
}

var (
	cloneWithValue = valueSet("-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference",
		"--reference-if-able", "--template", "-u", "--upload-pack", "-j", "--jobs", "--filter",
		"--separate-git-dir", "--shallow-since", "--shallow-exclude", "--server-option", "--bundle-uri", "--revision")
	fetchWithValue = valueSet("--depth", "--deepen", "--shallow-since", "--shallow-exclude", "-o", "--server-option",
		"--upload-pack", "--receive-pack", "--exec", "-j", "--jobs", "--negotiation-tip",
		"--recurse-submodules-default", "--refmap", "--push-option", "--strategy", "-s", "-X",
		"--strategy-option", "--repo", "--force-with-lease")
)

func valueSet(items ...string) map[string]bool {
	m := make(map[string]bool, len(items))
	for _, s := range items {
		m[s] = true
	}
	return m
}

func positionals(args []string, withValue map[string]bool) []string {
	var out []string
	for i := 0; i < len(args); {
		a := args[i]
		if a == "--" {
			return append(out, args[i+1:]...)
		}
		if strings.HasPrefix(a, "-") {
			if withValue[a] && !strings.Contains(a, "=") {
				i += 2
			} else {
				i++
			}
			continue
		}
		out = append(out, a)
		i++
	}
	return out
}

// RemoteURLs returns the URLs a git command would talk to (or set); empty when unknown.
func RemoteURLs(call *shell.GitCall, repo Repo) []string {
	args := call.Argv()
	switch call.Sub {
	case "clone":
		if pos := positionals(args, cloneWithValue); len(pos) > 0 {
			return pos[:1]
		}
	case "remote":
		pos := positionals(args, nil)
		if len(pos) >= 3 && (pos[0] == "add" || pos[0] == "set-url") {
			return []string{pos[2]}
		}
	case "push", "pull", "fetch", "ls-remote":
		return fetchURLs(call, args, repo)
	}
	return nil
}

func fetchURLs(call *shell.GitCall, args []string, repo Repo) []string {
	var names []string
	if pos := positionals(args, fetchWithValue); call.Sub == "fetch" && contains(args, "--all") {
		names = repo.Remotes()
	} else if len(pos) > 0 {
		names = pos[:1]
	} else {
		names = []string{defaultRemote(repo)}
	}
	var urls []string
	for _, name := range names {
		switch {
		case name == "":
		case strings.ContainsAny(name, `/:\`):
			urls = append(urls, name)
		default:
			if u, ok := repo.RemoteURL(name, call.Sub == "push"); ok {
				urls = append(urls, u)
			}
		}
	}
	return urls
}

func defaultRemote(repo Repo) string {
	if branch, ok := repo.CurrentBranch(); ok {
		if r, ok := repo.Config("branch." + branch + ".remote"); ok {
			return r
		}
	}
	return "origin"
}

// effectiveSSHCommand returns the ssh command git would use and where it came from.
func effectiveSSHCommand(call *shell.GitCall, repo Repo, getenv func(string) string) (string, string) {
	if v := lookup(call.Env, getenv, "GIT_SSH_COMMAND"); v != "" {
		return v, "GIT_SSH_COMMAND"
	}
	if v, ok := call.Config["core.sshcommand"]; ok {
		return v, "-c core.sshCommand"
	}
	if call.Sub == "clone" {
		args := call.Argv()
		for i, a := range args {
			kv := ""
			if (a == "-c" || a == "--config") && i+1 < len(args) {
				kv = args[i+1]
			} else if strings.HasPrefix(a, "--config=") {
				kv = strings.SplitN(a, "=", 2)[1]
			}
			if strings.HasPrefix(strings.ToLower(kv), "core.sshcommand=") {
				return strings.SplitN(kv, "=", 2)[1], "clone --config core.sshCommand"
			}
		}
	}
	if v, ok := repo.Config("core.sshCommand"); ok {
		return v, "git config core.sshCommand"
	}
	return "", ""
}

// keyMatches: same file, or key ends with wanted on a path boundary.
func keyMatches(key, wanted, home string) bool {
	raw := func(s string) string { return strings.TrimRight(strings.ToLower(strings.ReplaceAll(s, `\`, "/")), "/") }
	norm := func(s string) string { return raw(expandUser(s, home)) }
	if norm(key) == norm(wanted) {
		return true
	}
	suffix := strings.TrimLeft(strings.TrimLeft(raw(wanted), "~"), "/")
	for _, k := range []string{raw(key), norm(key)} {
		if k == suffix || strings.HasSuffix(k, "/"+suffix) {
			return true
		}
	}
	return false
}

func canonOpt(s string) string {
	s = pyStrip(s)
	if loc := optCanonRe.FindStringIndex(s); loc != nil {
		s = s[:loc[0]] + "=" + s[loc[1]:]
	}
	return strings.ToLower(s)
}

// SSHCommandProblems lists problems with an ssh command; keyPath "" means
// any -i key is acceptable. home resolves ~ in paths.
func SSHCommandProblems(cmd, keyPath string, required []string, home string) []string {
	var words []string
	if segs := shell.Lex(cmd, shell.Bash); len(segs) > 0 {
		words = segs[0].Values()
	}
	var problems []string
	key := ""
	var opts []string
	for i, w := range words {
		switch {
		case w == "-i" && i+1 < len(words):
			key = words[i+1]
		case strings.HasPrefix(w, "-i") && len(w) > 2:
			key = w[2:]
		}
		switch {
		case w == "-o" && i+1 < len(words):
			opts = append(opts, words[i+1])
		case strings.HasPrefix(w, "-o") && len(w) > 2:
			opts = append(opts, w[2:])
		}
	}
	if key == "" {
		problems = append(problems, "no -i <key> in the ssh command")
	} else if keyPath != "" && !keyMatches(key, keyPath, home) {
		problems = append(problems, fmt.Sprintf("key '%s' is not the profile's ssh_key '%s'", key, keyPath))
	}
	have := map[string]bool{}
	for _, o := range opts {
		have[canonOpt(o)] = true
	}
	for _, r := range required {
		if !have[canonOpt(r)] {
			problems = append(problems, "missing -o "+r)
		}
	}
	return problems
}

// Transport returns the profile's git_transport: "gh", or "ssh" (the default).
func Transport(vars map[string]any) string {
	if v, _ := vars["git_transport"].(string); v == "gh" || v == "ssh" {
		return v
	}
	return "ssh"
}

func kindName(kind string) string {
	if kind == "other" {
		return "this URL form"
	}
	return strings.ToUpper(kind)
}

// RemoteProblem explains why url breaks ID004's params under transport mode, or "".
func RemoteProblem(url string, params map[string]any, mode string) string {
	for _, p := range policy.Strings(params, "extra_patterns") {
		if reFullmatch(p, url, false) {
			return ""
		}
	}
	kind, host, owner := ParseRemote(url)
	if mode == "gh" {
		if kind != "https" {
			return "git_transport is 'gh': use an HTTPS remote (https://<host>/<owner>/<repo>.git) so git " +
				"authenticates through the gh credential helper, not " + kindName(kind)
		}
	} else if policy.Bool(params, "require_ssh", true) && kind != "ssh" {
		return "use an SSH remote (git@<host>:<owner>/<repo>.git), not " + kindName(kind)
	}
	hosts := lowerAll(policy.Strings(params, "hosts"))
	if len(hosts) > 0 && !contains(hosts, host) {
		return fmt.Sprintf("host '%s' is not one of %s", orNone(host, host != ""), pyList(hosts))
	}
	owners := nonEmpty(policy.Strings(params, "allowed_owners"))
	if len(owners) > 0 && !contains(lowerAll(owners), strings.ToLower(owner)) {
		return fmt.Sprintf("owner '%s' is not one of %s", orNone(owner, owner != ""), pyList(owners))
	}
	return ""
}

// NetworkInput bundles what CheckNetwork needs besides the call.
type NetworkInput struct {
	Contract *policy.Contract    // identity contract
	Vars     map[string]any      // profile variables (git_transport)
	Repo     Repo                // facts for the call's cwd
	Getenv   func(string) string // process environment
}

// CheckNetwork applies ID004 (allowed remotes) and ID003 (pinned SSH key or
// gh credential helper) to push / pull / fetch / clone / ls-remote / remote.
func CheckNetwork(call *shell.GitCall, in NetworkInput) verdict.Verdict {
	var v verdict.Verdict
	c := in.Contract
	r3, r4 := c.Rule("ID003"), c.Rule("ID004")
	if !networkSubs[call.Sub] && call.Sub != "remote" {
		return v
	}
	mode := Transport(in.Vars)
	var urls []string
	for _, u := range RemoteURLs(call, in.Repo) {
		if !IsLocalURL(u) {
			urls = append(urls, u)
		}
	}
	if r4.Severity != verdict.Off {
		for _, url := range urls {
			if prob := RemoteProblem(url, r4.Params, mode); prob != "" {
				v.Addf("ID004", r4.Severity, "remote '%s' is not allowed: %s", redact(url), prob)
			}
		}
	}
	if r3.Severity == verdict.Off || !networkSubs[call.Sub] {
		return v
	}
	if mode == "gh" {
		hosts := lowerAll(policy.Strings(r4.Params, "hosts"))
		for _, url := range urls {
			kind, host, _ := ParseRemote(url)
			if kind == "https" && (len(hosts) == 0 || contains(hosts, host)) {
				for _, prob := range ghCredentialProblems(call, url, host, in) {
					v.Add("ID003", r3.Severity, prob)
				}
			}
		}
		return v
	}
	for _, u := range urls {
		if isSSHURL(u) {
			v.Extend(checkSSHPinned(call, r3, in))
			break
		}
	}
	return v
}

func checkSSHPinned(call *shell.GitCall, r3 policy.Rule, in NetworkInput) verdict.Verdict {
	var v verdict.Verdict
	cmd, src := effectiveSSHCommand(call, in.Repo, in.Getenv)
	key := policy.Str(r3.Params, "key_path", "")
	required := policy.Strings(r3.Params, "required_options")
	if cmd == "" {
		opts := make([]string, len(required))
		for i, o := range required {
			opts[i] = "-o " + o
		}
		v.Addf("ID003", r3.Severity,
			"no SSH key is pinned for 'git %s'. Set it once per repo: "+
				"git config core.sshCommand 'ssh -i \"%s\" %s' "+
				"(for clone: git clone -c core.sshCommand='...' <url>). "+
				"Single GitHub account on HTTPS + gh? Set git_transport to 'gh' instead.",
			call.Sub, firstOr(nonEmpty([]string{key}), "<your key>"), strings.Join(opts, " "))
		return v
	}
	if probs := SSHCommandProblems(cmd, key, required, HomeDir(in.Getenv)); len(probs) > 0 {
		v.Addf("ID003", r3.Severity, "SSH command from %s is not pinned correctly: %s", src, strings.Join(probs, "; "))
	}
	return v
}

// ---------------------------------------------------------------- gh credential helper (git_transport 'gh')

// redact hides a password/token embedded in a URL.
func redact(url string) string {
	m := userinfoRe.FindStringSubmatchIndex(url)
	if m == nil {
		return url
	}
	scheme, userinfo := url[m[2]:m[3]], url[m[4]:m[5]]
	if !strings.Contains(userinfo, ":") {
		return url
	}
	return scheme + strings.SplitN(userinfo, ":", 2)[0] + ":***@" + url[m[1]:]
}

func stripUserinfo(url string) string { return userinfoRe.ReplaceAllString(url, "$1") }

func hasCredential(url string) bool {
	m := userinfoRe.FindStringSubmatch(url)
	return m != nil && strings.Contains(m[2], ":")
}

// IsGhHelper reports whether a credential helper is gh's (gh auth git-credential).
func IsGhHelper(helper string) bool {
	return helper != "" && ghHelperRe.MatchString(strings.TrimSpace(helper))
}

func urlPrefixMatches(pattern, url string) bool {
	pattern = strings.TrimRight(strings.ToLower(pattern), "/")
	url = strings.ToLower(stripUserinfo(url))
	return url == pattern || strings.HasPrefix(url, pattern+"/")
}

// effectiveCredentialHelper returns the helper git would use for url and its
// source. -c overrides win; a URL-scoped one beats the generic one.
func effectiveCredentialHelper(call *shell.GitCall, url string, repo Repo) (string, string) {
	scopedKey := ""
	for _, k := range configKeysInOrder(call) {
		if strings.HasPrefix(k, "credential.") && strings.HasSuffix(k, ".helper") && k != "credential.helper" &&
			len(k) > len("credential..helper") && urlPrefixMatches(k[len("credential."):len(k)-len(".helper")], url) {
			scopedKey = k
		}
	}
	if scopedKey != "" {
		return call.Config[scopedKey], "-c " + scopedKey
	}
	if v, ok := call.Config["credential.helper"]; ok {
		return v, "-c credential.helper"
	}
	if v, ok := repo.ConfigURLMatch("credential.helper", stripUserinfo(url)); ok {
		return v, "git config"
	}
	return "", ""
}

// configKeysInOrder lists the -c keys (lower-cased) in first-seen order, like
// iterating a Python dict.
func configKeysInOrder(call *shell.GitCall) []string {
	var keys []string
	seen := map[string]bool{}
	for i := 0; i+1 < len(call.GlobalOpts); i++ {
		if call.GlobalOpts[i] != "-c" {
			continue
		}
		k, _, _ := strings.Cut(call.GlobalOpts[i+1], "=")
		k = strings.ToLower(k)
		if !seen[k] {
			seen[k] = true
			keys = append(keys, k)
		}
		i++
	}
	return keys
}

func ghTTL(c *policy.Contract) time.Duration {
	return time.Duration(policy.Int(settings(c), "gh_status_cache_seconds", 300)) * time.Second
}

func ghToken(env map[string]string, getenv func(string) string) string {
	if t := lookup(env, getenv, "GH_TOKEN"); t != "" {
		return t
	}
	return lookup(env, getenv, "GITHUB_TOKEN")
}

func ghCredentialProblems(call *shell.GitCall, url, host string, in NetworkInput) []string {
	var probs []string
	if hasCredential(url) {
		probs = append(probs, fmt.Sprintf("remote '%s' embeds a credential; remove it so the gh credential helper is used: "+
			"git -C \"%s\" remote set-url <name> %s", redact(url), call.Cwd, stripUserinfo(url)))
	}
	helper, src := effectiveCredentialHelper(call, url, in.Repo)
	if !IsGhHelper(helper) {
		got := "none"
		if helper != "" {
			got = fmt.Sprintf("'%s' (from %s)", helper, src)
		}
		probs = append(probs, fmt.Sprintf("git_transport is 'gh' but the credential helper for %s is %s, not gh. "+
			"Fix: gh auth setup-git --hostname %s", host, got, host))
	}
	login, note := in.Repo.GhActiveLogin(host, ghToken(call.Env, in.Getenv), ghTTL(in.Contract))
	if prob := ghLoginProblem(login, note, host, in.Contract.Rule("ID005").Params); prob != "" {
		probs = append(probs, fmt.Sprintf("'git %s' authenticates as the active gh account: %s", call.Sub, prob))
	}
	return probs
}

// ---------------------------------------------------------------- gh account (ID005)

var ghAccountExempt = [][]string{{"auth"}, {"help"}, {"version"}, {"--version"}, {"completion"}}

func hasPrefixWords(argv, prefix []string) bool {
	if len(argv) < len(prefix) {
		return false
	}
	for i, p := range prefix {
		if argv[i] != p {
			return false
		}
	}
	return true
}

// CheckGhAccount applies ID005: gh must be logged in as the expected account.
func CheckGhAccount(inv shell.Invocation, c *policy.Contract, repo Repo, getenv func(string) string) verdict.Verdict {
	var v verdict.Verdict
	r := c.Rule("ID005")
	argv := inv.Argv()
	if r.Severity == verdict.Off || len(argv) == 0 {
		return v
	}
	for _, e := range ghAccountExempt {
		if hasPrefixWords(argv, e) {
			return v
		}
	}
	host := policy.Str(settings(c), "gh_hostname", "github.com")
	for i, a := range argv {
		if a == "--hostname" && i+1 < len(argv) {
			host = argv[i+1]
		}
	}
	login, note := repo.GhActiveLogin(host, ghToken(inv.Env, getenv), ghTTL(c))
	if prob := ghLoginProblem(login, note, host, r.Params); prob != "" {
		v.Add("ID005", r.Severity, prob)
	}
	return v
}

func ghLoginProblem(login, note, host string, params map[string]any) string {
	expected := nonEmpty(policy.Strings(params, "expected_logins"))
	if login == "" {
		return fmt.Sprintf("gh is not usable on %s (%s). Fix: gh auth login --hostname %s", host, note, host)
	}
	if len(expected) > 0 && !contains(lowerAll(expected), strings.ToLower(login)) {
		return fmt.Sprintf("active gh account on %s is '%s', expected one of %s. "+
			"Fix: gh auth switch --hostname %s --user %s", host, login, pyList(expected), host, expected[0])
	}
	return ""
}
