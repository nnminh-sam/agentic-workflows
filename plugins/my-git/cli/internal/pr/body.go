package pr

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
)

// Commit is one commit of the PR range.
type Commit struct {
	Hash    string
	Subject string
	Body    string
}

// LogFormat is the git log format ParseCommits reads.
const LogFormat = "--format=%h%x1f%s%x1f%b%x1e"

// ParseCommits parses `git log` output made with LogFormat.
func ParseCommits(out string) []Commit {
	var cs []Commit
	for _, rec := range strings.Split(strings.ReplaceAll(out, "\r\n", "\n"), "\x1e") {
		rec = strings.TrimLeft(rec, "\n")
		if strings.TrimSpace(rec) == "" {
			continue
		}
		f := strings.SplitN(rec, "\x1f", 3)
		for len(f) < 3 {
			f = append(f, "")
		}
		cs = append(cs, Commit{Hash: f[0], Subject: strings.TrimSpace(f[1]), Body: strings.TrimSpace(f[2])})
	}
	return cs
}

// CheckRun is the outcome of one local check (test or lint).
type CheckRun struct {
	Label      string // "Unit tests" or "Lint"
	Command    string // as configured; "" = none configured
	ExitCode   int
	Output     string
	StartError string // the command could not be started
}

// Passed reports a started command that exited 0.
func (r CheckRun) Passed() bool { return r.Command != "" && r.StartError == "" && r.ExitCode == 0 }

const outputTail = 10

// TestResults renders the "Test results" section content from the checks.
// Both labels always appear so rule GH008 (must mention unit and lint) holds.
func TestResults(runs []CheckRun) string {
	var b strings.Builder
	for i, r := range runs {
		if i > 0 {
			b.WriteString("\n")
		}
		switch {
		case r.Command == "":
			fmt.Fprintf(&b, "- %s: none configured (set pr.checks.%s)", r.Label, checkKey(r.Label))
		case r.StartError != "":
			fmt.Fprintf(&b, "- %s: `%s` → could not run: %s", r.Label, r.Command, r.StartError)
		case r.ExitCode == 0:
			fmt.Fprintf(&b, "- %s: `%s` → passed", r.Label, r.Command)
		default:
			fmt.Fprintf(&b, "- %s: `%s` → FAILED (exit %d)", r.Label, r.Command, r.ExitCode)
		}
		if tail := stableTail(r.Output, outputTail); tail != "" && r.Command != "" && r.StartError == "" {
			b.WriteString("\n\n  ```text\n")
			for _, l := range strings.Split(tail, "\n") {
				b.WriteString("  " + l + "\n")
			}
			b.WriteString("  ```")
		}
	}
	return b.String()
}

func checkKey(label string) string {
	if label == "Lint" {
		return "lint"
	}
	return "test"
}

// durationRe matches timings ("0.253s", "12ms") that differ on every run.
var durationRe = regexp.MustCompile(`\b\d+(?:\.\d+)?(?:ms|µs|ns|s)\b`)

// stableTail is the last n lines of a command's output with timings blanked
// (as "Ns"), so re-running the checks for `--apply <id>` renders the same
// body and the plan id stays valid.
func stableTail(s string, n int) string {
	return durationRe.ReplaceAllString(lastLines(s, n), "Ns")
}

func lastLines(s string, n int) string {
	s = strings.TrimSpace(strings.ReplaceAll(s, "\r\n", "\n"))
	if s == "" {
		return ""
	}
	lines := strings.Split(s, "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " \t")
	}
	return strings.Join(lines, "\n")
}

// CheckSummary is the one-line summary shown next to a check's read step.
func CheckSummary(res execx.Result) string {
	if res.ExitCode == 0 {
		return "passed"
	}
	return fmt.Sprintf("failed (exit %d)", res.ExitCode)
}

var shellMeta = regexp.MustCompile("[&|;<>`]|\\$[({]|^\\s*[A-Za-z_][A-Za-z0-9_]*=")

// CheckCmd builds the process that runs a configured check command line in
// dir. Plain commands run directly ("go test ./..."); a command line that
// needs a shell (&&, pipes, redirects, variables) runs through sh -c, or
// cmd /C on Windows.
func CheckCmd(line, dir, goos string) execx.Cmd {
	line = strings.TrimSpace(line)
	if !shellMeta.MatchString(line) {
		if words, ok := splitWords(line); ok && len(words) > 0 {
			return execx.Cmd{Name: words[0], Args: words[1:], Dir: dir}
		}
	}
	if goos == "windows" {
		return execx.Cmd{Name: "cmd", Args: []string{"/C", line}, Dir: dir}
	}
	return execx.Cmd{Name: "sh", Args: []string{"-c", line}, Dir: dir}
}

// splitWords splits a command line on spaces, honouring '…' and "…".
func splitWords(s string) ([]string, bool) {
	var words []string
	var cur strings.Builder
	var quote rune
	started := false
	for _, r := range s {
		switch {
		case quote != 0:
			if r == quote {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
		case r == '\'' || r == '"':
			quote, started = r, true
		case r == ' ' || r == '\t':
			if started || cur.Len() > 0 {
				words = append(words, cur.String())
				cur.Reset()
				started = false
			}
		default:
			cur.WriteRune(r)
		}
	}
	if quote != 0 {
		return nil, false
	}
	if started || cur.Len() > 0 {
		words = append(words, cur.String())
	}
	return words, true
}

// DeriveTitle proposes a PR title. A release uses chore(release): <version
// or date>. Otherwise a single commit lends its subject; several commits
// give "<type>: <name>" from the work branch name (develop/feat/login →
// "feat: login").
func DeriveTitle(t Target, commits []Commit, workTypes []string, version string, now time.Time) string {
	if t.Kind == tmpl.PRRelease {
		if version == "" {
			version = now.Format("2006-01-02")
		}
		return "chore(release): " + version
	}
	if len(commits) == 1 && commits[0].Subject != "" {
		return commits[0].Subject
	}
	segs := strings.Split(t.Head, "/")
	name := strings.NewReplacer("-", " ", "_", " ").Replace(segs[len(segs)-1])
	typ := "chore"
	for _, s := range segs[:len(segs)-1] {
		for _, wt := range workTypes {
			if s == wt {
				typ = wt
			}
		}
	}
	return typ + ": " + strings.TrimSpace(name)
}

var trailerLine = regexp.MustCompile(`(?i)^\s*(co-authored-by|signed-off-by|reviewed-by|acked-by|generated with)\b`)

// commitProse returns the human text of a commit body: no trailers and no
// AI-attribution lines (rule GH009 must never fire on text mygit copied).
func commitProse(body string) string {
	var out []string
	for _, l := range strings.Split(body, "\n") {
		if trailerLine.MatchString(l) || strings.Contains(l, "🤖") {
			continue
		}
		out = append(out, l)
	}
	return strings.TrimSpace(strings.Join(out, "\n"))
}

// AutoSections fills the sections mygit can fill from the PR range; the
// result is keyed by lower-case section name. Sections it cannot fill (an
// Explanation without commit bodies) stay empty so rule GH008 asks for them.
func AutoSections(t Target, commits []Commit, diffStat, version string, now time.Time) map[string]string {
	out := map[string]string{}
	var bullets, prose []string
	for _, c := range commits {
		bullets = append(bullets, "- "+c.Subject)
		if p := commitProse(c.Body); p != "" {
			prose = append(prose, p)
		}
	}
	list := strings.Join(bullets, "\n")
	if stat := lastLines(diffStat, 1); stat != "" {
		list += "\n\n" + stat
	}
	out["detailed changes"] = list
	out["included prs"] = strings.Join(bullets, "\n")
	out["explanation"] = strings.Join(prose, "\n\n")
	switch {
	case t.Kind == tmpl.PRRelease:
		if version == "" {
			version = now.Format("2006-01-02")
		}
		out["summary"] = fmt.Sprintf("Release %s: promote %s to %s (%d commit(s)).", version, t.Head, t.Base, len(commits))
	case len(commits) == 1:
		out["summary"] = commits[0].Subject
	default:
		var subs []string
		for _, c := range commits {
			subs = append(subs, c.Subject)
		}
		s := strings.Join(subs, "; ")
		if len(s) > 300 {
			s = s[:297] + "..."
		}
		out["summary"] = fmt.Sprintf("%d commits on %s: %s", len(commits), t.Head, s)
	}
	return out
}

// SectionNames returns the body sections: the configured pr.sections, else
// the transition's prTemplate sections, else the built-ins; then every
// section the GH008 rule requires and the list lacks is appended, so the
// rendered body can pass the rule that will check it.
func SectionNames(configured, templated []string, gh008 map[string]any) []string {
	names := configured
	if len(names) == 0 {
		names = templated
	}
	if len(names) == 0 {
		names = tmpl.DefaultSections()
	}
	names = append([]string(nil), names...)
	raw, _ := gh008["sections"].([]any)
	for _, x := range raw {
		m, _ := x.(map[string]any)
		name, heading := policy.Str(m, "name", ""), policy.Str(m, "heading", "")
		if name == "" || heading == "" {
			continue
		}
		re, err := regexp.Compile("(?i)" + heading)
		if err != nil {
			continue
		}
		covered := false
		for _, n := range names {
			if re.MatchString("## " + n) {
				covered = true
			}
		}
		if !covered {
			names = append(names, strings.TrimSpace(strings.TrimSuffix(name, "(local)")))
		}
	}
	return names
}
