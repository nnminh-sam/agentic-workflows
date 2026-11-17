package rules

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

var (
	graphqlMutationRe = regexp.MustCompile(`^\s*mutation\b|query=\s*mutation\b`)
	ownerRe           = regexp.MustCompile(`^(?:https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)?([^/\s]+)/[^/\s]+?(?:\.git)?/?$`)
	apiReposRe        = regexp.MustCompile(`^/?repos/([^/{]+)/`)
	commentRe         = regexp.MustCompile(`(?s)<!--.*?-->`)
	headingRe         = regexp.MustCompile(`^#{1,6}\s`)
)

var repoArgCmds = valueSet("clone", "view", "create", "fork", "sync", "edit", "delete", "archive", "unarchive", "rename", "set-default")

// flagValues returns the values of a flag given as -X v, -Xv, --long v or --long=v.
func flagValues(argv []string, short, long string) []string {
	var out []string
	for i, a := range argv {
		switch {
		case a == long || (short != "" && a == short):
			if i+1 < len(argv) {
				out = append(out, argv[i+1])
			}
		case strings.HasPrefix(a, long+"="):
			out = append(out, strings.SplitN(a, "=", 2)[1])
		case short != "" && strings.HasPrefix(a, short) && len(a) > len(short) && !strings.HasPrefix(a, "--"):
			out = append(out, a[len(short):])
		}
	}
	return out
}

// CheckHTTPClient applies GH001: no curl/wget/Invoke-RestMethod calls to
// the GitHub API hosts; use gh api so the checked account is used.
func CheckHTTPClient(inv shell.Invocation, gh *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	r := gh.Rule("GH001")
	if r.Severity == verdict.Off || !shell.IsHTTPClient(inv.Program) {
		return v
	}
	hosts := policy.Strings(r.Params, "hosts")
	for _, a := range inv.Argv() {
		for _, h := range hosts {
			if mentionsHost(a, h) {
				v.Addf("GH001", r.Severity, "%s call to %s: use 'gh api' instead so the checked gh account is used", inv.Program, h)
				return v
			}
		}
	}
	return v
}

// mentionsHost is re.search(r"(^|[/@.])host(?=$|[/:?])", arg, re.I) without
// the lookahead RE2 lacks.
func mentionsHost(arg, host string) bool {
	a, h := strings.ToLower(arg), strings.ToLower(host)
	if h == "" {
		return false
	}
	for from := 0; from <= len(a)-len(h); {
		k := strings.Index(a[from:], h)
		if k < 0 {
			return false
		}
		k += from
		end := k + len(h)
		before := k == 0 || strings.IndexByte("/@.", a[k-1]) >= 0
		after := end == len(a) || strings.IndexByte("/:?", a[end]) >= 0
		if before && after {
			return true
		}
		from = k + 1
	}
	return false
}

func ownerOf(ref string) (string, bool) {
	m := ownerRe.FindStringSubmatch(pyStrip(ref))
	if m == nil {
		return "", false
	}
	return m[1], true
}

// GhInput bundles what CheckGh needs besides the invocation.
type GhInput struct {
	GH       *policy.Contract // github-cli contract
	Commit   *policy.Contract // commit-message contract (PR titles, AI attribution)
	ReadFile ReadFile         // reads --body-file
}

// CheckGh applies GH002–GH009 to a gh invocation.
func CheckGh(inv shell.Invocation, in GhInput) verdict.Verdict {
	var v verdict.Verdict
	path := shell.GhPath(inv)
	joined := strings.Join(path, " ")
	v.Extend(checkCommandPolicy(path, in.GH))
	v.Extend(checkAPIMutation(inv, path, in.GH))
	if joined == "pr create" || joined == "pr edit" {
		v.Extend(checkPRFlags(inv, joined, in))
		v.Extend(checkPRBody(inv, joined, in))
	}
	v.Extend(checkOwnerScope(inv, path, in.GH))
	v.Extend(checkRepoVisibility(inv, joined, in.GH))
	return v
}

// GH002 command policy.
func checkCommandPolicy(path []string, gh *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	r := gh.Rule("GH002")
	if r.Severity == verdict.Off {
		return v
	}
	entries, _ := r.Params["commands"].([]any)
	for _, raw := range entries {
		entry, _ := raw.(map[string]any)
		match := policy.Str(entry, "match", "")
		want := strings.Fields(match)
		if len(want) == 0 || !hasPrefixWords(path, want) {
			continue
		}
		if decision := policy.Str(entry, "decision", "deny"); decision != "allow" {
			sev, err := verdict.ParseSeverity(decision)
			if err != nil {
				sev = verdict.Deny
			}
			v.Addf("GH002", sev, "gh %s: %s", match, policy.Str(entry, "reason", "restricted command"))
		}
		break
	}
	return v
}

// GH003 api mutations.
func checkAPIMutation(inv shell.Invocation, path []string, gh *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	r := gh.Rule("GH003")
	if r.Severity == verdict.Off || len(path) == 0 || path[0] != "api" {
		return v
	}
	argv := inv.Argv()
	endpoint := ""
	if len(path) > 1 {
		endpoint = path[1]
	}
	methods := flagValues(argv, "-X", "--method")
	fields := append(flagValues(argv, "-f", "--raw-field"), flagValues(argv, "-F", "--field")...)
	hasInput := hasFlag(argv, "--input")
	if endpoint == "graphql" {
		for _, f := range fields {
			if graphqlMutationRe.MatchString(f) {
				hasInput = true
			}
		}
		if hasInput {
			v.Add("GH003", r.Severity, "gh api graphql mutation")
		}
		return v
	}
	method := "GET"
	switch {
	case len(methods) > 0:
		method = strings.ToUpper(methods[len(methods)-1])
	case len(fields) > 0 || hasInput:
		method = "POST"
	}
	switch method {
	case "GET":
	case "DELETE":
		v.Addf("GH003", policy.Sev(r.Params, "delete_severity", r.Severity), "gh api DELETE %s", endpoint)
	default:
		v.Addf("GH003", r.Severity, "gh api %s %s", method, endpoint)
	}
	return v
}

// GH004 non-interactive pr create, GH005 PR title convention.
func checkPRFlags(inv shell.Invocation, path string, in GhInput) verdict.Verdict {
	var v verdict.Verdict
	argv := inv.Argv()
	titles := flagValues(argv, "-t", "--title")
	titleDynamic := false
	for i, w := range inv.Args {
		afterFlag := i > 0 && (inv.Args[i-1].Value == "-t" || inv.Args[i-1].Value == "--title")
		if w.Dynamic && (afterFlag || strings.HasPrefix(w.Value, "--title=") || strings.HasPrefix(w.Value, "-t")) {
			titleDynamic = true
		}
	}
	if r4 := in.GH.Rule("GH004"); path == "pr create" && r4.Severity != verdict.Off {
		filled := hasFlag(argv, "--fill", "-f", "--fill-first", "--fill-verbose", "--web", "-w")
		body := hasFlag(argv, "--body", "-b", "--body-file", "-F", "--template", "-T")
		for _, a := range argv {
			if len(a) > 2 && !strings.HasPrefix(a, "--") &&
				(strings.HasPrefix(a, "-b") || strings.HasPrefix(a, "-F") || strings.HasPrefix(a, "-T")) {
				body = true
			}
		}
		if !filled && !(len(titles) > 0 && body) {
			var missing []string
			if len(titles) == 0 {
				missing = append(missing, "--title")
			}
			if !body {
				missing = append(missing, "--body/--body-file")
			}
			v.Addf("GH004", r4.Severity, "gh pr create would prompt interactively; pass %s (or --fill)", strings.Join(missing, " and "))
		}
	}
	r5 := in.GH.Rule("GH005")
	switch {
	case r5.Severity == verdict.Off:
	case titleDynamic:
		v.Add("GH005", verdict.Ask, "PR title uses shell expansion and cannot be checked")
	default:
		for _, t := range titles {
			v.Extend(checkTitle(t, r5.Severity, in.Commit))
		}
	}
	return v
}

func checkTitle(title string, sev verdict.Severity, commit *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	for _, f := range CheckHeader(title, commit, sev).Findings {
		v.Addf("GH005", f.Severity, "PR title: %s (%s)", f.Message, f.RuleID)
	}
	return v
}

// GH006 repository owner scope.
func checkOwnerScope(inv shell.Invocation, path []string, gh *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	r := gh.Rule("GH006")
	allowed := nonEmpty(policy.Strings(r.Params, "allowed_owners"))
	if r.Severity == verdict.Off || len(allowed) == 0 { // no profile owners -> nothing to scope against
		return v
	}
	argv := inv.Argv()
	refs := flagValues(argv, "-R", "--repo")
	if len(path) == 2 && path[0] == "repo" && repoArgCmds[path[1]] {
		for _, a := range argv[2:] {
			if !strings.HasPrefix(a, "-") {
				if strings.Contains(a, "/") {
					refs = append(refs, a)
				}
				break
			}
		}
	}
	if len(path) > 1 && path[0] == "api" {
		if m := apiReposRe.FindStringSubmatch(path[1]); m != nil {
			refs = append(refs, m[1]+"/_")
		}
	}
	lowered := lowerAll(allowed)
	for _, ref := range refs {
		if owner, ok := ownerOf(ref); ok && !contains(lowered, strings.ToLower(owner)) {
			v.Addf("GH006", r.Severity, "targets repository '%s' outside allowed owners %s", ref, pyList(allowed))
		}
	}
	return v
}

// GH007 repo create visibility.
func checkRepoVisibility(inv shell.Invocation, path string, gh *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	r := gh.Rule("GH007")
	if r.Severity == verdict.Off || path != "repo create" {
		return v
	}
	argv := inv.Argv()
	if hasFlag(argv, "--public") {
		v.Add("GH007", policy.Sev(r.Params, "public_severity", verdict.Ask), "creates a PUBLIC repository")
	} else if !hasFlag(argv, "--private", "--internal") {
		v.Add("GH007", r.Severity, "gh repo create must pass --private, --public or --internal")
	}
	return v
}

// ---------------------------------------------------------------- PR body (GH008 / GH009)

// prBody returns the kind ("text", "none", "dynamic", "unreadable") and
// text-or-detail of a gh pr create/edit body.
func prBody(inv shell.Invocation, read ReadFile) (MessageKind, string) {
	words := inv.Args
	kind, text := MsgNone, ""
	for i, w := range words {
		a := w.Value
		var nxt *shell.Word
		if i+1 < len(words) {
			nxt = &words[i+1]
		}
		switch {
		case (a == "-b" || a == "--body") && nxt != nil:
			kind, text = bodyValue(nxt.Value, nxt.Dynamic)
		case strings.HasPrefix(a, "--body="):
			kind, text = bodyValue(strings.SplitN(a, "=", 2)[1], w.Dynamic)
		case a == "-F" || a == "--body-file" || strings.HasPrefix(a, "--body-file="):
			path := ""
			if strings.Contains(a, "=") {
				path = strings.SplitN(a, "=", 2)[1]
			} else if nxt != nil {
				path = nxt.Value
			}
			kind, text = bodyFile(inv, path, read)
		}
	}
	argv := inv.Argv()
	if kind == MsgNone && hasFlag(argv, "--fill", "-f", "--fill-first", "--fill-verbose") {
		return MsgUnreadable, "--fill builds the body from commits, without the required sections"
	}
	if kind == MsgNone && hasFlag(argv, "--web", "-w", "--template", "-T") {
		return MsgUnreadable, "the body is written in the browser/template and cannot be checked"
	}
	return kind, text
}

func bodyValue(val string, dynamic bool) (MessageKind, string) {
	if dynamic {
		return MsgDynamic, "body uses shell expansion"
	}
	return MsgText, val
}

func bodyFile(inv shell.Invocation, path string, read ReadFile) (MessageKind, string) {
	if path == "-" {
		if inv.HasStdin {
			return MsgText, inv.Stdin
		}
		return MsgDynamic, "body is read from stdin"
	}
	text, ok := read.read(absUnder(inv.Cwd, path))
	if !ok {
		return MsgUnreadable, "body file not found: " + path
	}
	return MsgText, text
}

type prSection struct {
	name, heading string
	mustMention   []string
}

func sectionsOf(params map[string]any) []prSection {
	raw, _ := params["sections"].([]any)
	var out []prSection
	for _, x := range raw {
		m, _ := x.(map[string]any)
		out = append(out, prSection{
			name:        policy.Str(m, "name", ""),
			heading:     policy.Str(m, "heading", ""),
			mustMention: policy.Strings(m, "must_mention"),
		})
	}
	return out
}

func headingLevel(line string) int { return len(line) - len(strings.TrimLeft(line, "#")) }

// BodySectionProblems lists missing or empty required sections of a PR body
// (rule GH008). params are GH008's params.
func BodySectionProblems(body string, params map[string]any) []string {
	lines := strings.Split(strings.ReplaceAll(commentRe.ReplaceAllString(body, ""), "\r\n", "\n"), "\n")
	var headings []int
	for i, ln := range lines {
		if headingRe.MatchString(ln) {
			headings = append(headings, i)
		}
	}
	placeholders := policy.Strings(params, "placeholders")
	var problems []string
	for _, sec := range sectionsOf(params) {
		idx := -1
		for _, i := range headings {
			if reSearch(sec.heading, pyStrip(lines[i]), true) {
				idx = i
				break
			}
		}
		if idx < 0 {
			problems = append(problems, fmt.Sprintf("missing section '## %s'", sec.name))
			continue
		}
		end := len(lines)
		for _, i := range headings {
			if i > idx && headingLevel(lines[i]) <= headingLevel(lines[idx]) {
				end = i
				break
			}
		}
		problems = append(problems, sectionProblems(sec, lines[idx+1:end], placeholders)...)
	}
	return problems
}

func sectionProblems(sec prSection, section, placeholders []string) []string {
	var body []string
	hasText := false
	for _, ln := range section {
		if pyStrip(ln) == "" || anySearch(placeholders, ln, true) {
			continue
		}
		body = append(body, ln)
		hasText = hasText || !headingRe.MatchString(ln)
	}
	if !hasText {
		return []string{fmt.Sprintf("section '%s' is empty", sec.name)}
	}
	joined := strings.Join(body, "\n")
	var out []string
	for _, word := range sec.mustMention {
		if !reSearch(`\b`+regexp.QuoteMeta(word), joined, true) {
			out = append(out, fmt.Sprintf("section '%s' must report '%s' results", sec.name, word))
		}
	}
	return out
}

func checkPRBody(inv shell.Invocation, path string, in GhInput) verdict.Verdict {
	var v verdict.Verdict
	kind, text := prBody(inv, in.ReadFile)
	r8 := in.GH.Rule("GH008")
	if kind == MsgNone && path == "pr edit" {
		return v // not changing the body
	}
	if kind != MsgText {
		if r8.Severity != verdict.Off {
			detail, sev := text, policy.Sev(r8.Params, "unreadable_severity", verdict.Ask)
			if kind == MsgNone {
				detail, sev = "gh pr create has no --body/--body-file", r8.Severity
			}
			v.Addf("GH008", sev, "PR body cannot be checked: %s", detail)
		}
		return v
	}
	v.Extend(bodyFindings(text, in.GH, in.Commit))
	return v
}

// bodyFindings applies GH008 (sections) and GH009 (no AI attribution) to a known body.
func bodyFindings(body string, gh, commit *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	if r8 := gh.Rule("GH008"); r8.Severity != verdict.Off {
		for _, prob := range BodySectionProblems(body, r8.Params) {
			v.Add("GH008", r8.Severity, "PR body: "+prob)
		}
	}
	if r9 := gh.Rule("GH009"); r9.Severity != verdict.Off {
		for _, prob := range AIAttribution(strings.Split(body, "\n"), commit.Rule("CM016").Params) {
			v.Add("GH009", r9.Severity, "PR body: "+prob)
		}
	}
	return v
}

// CheckPR checks a pull request title (GH005: the commit header rules) and
// body (GH008 required sections, GH009 no AI attribution) before it is
// opened. It backs `mygit lint pr` and `mygit pr check`; combine it with
// PRTargetProblem for BR005.
func CheckPR(title, body string, gh, commit *policy.Contract) verdict.Verdict {
	var v verdict.Verdict
	if r5 := gh.Rule("GH005"); r5.Severity != verdict.Off {
		v.Extend(checkTitle(title, r5.Severity, commit))
	}
	v.Extend(bodyFindings(body, gh, commit))
	return v
}
