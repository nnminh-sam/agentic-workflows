package rules

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

var (
	headerRe      = regexp.MustCompile(`^(?P<type>[^\s(:!]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<subject>.*)$`)
	breakingAnyRe = regexp.MustCompile(`(?i)^breaking[ -]change\s*:`)
	breakingOKRe  = regexp.MustCompile(`^BREAKING[ -]CHANGE: \S`)
	noSpaceHint   = regexp.MustCompile(`^\w+(\([^)]*\))?!?:\S`)
	scopeSpace    = regexp.MustCompile(`^\w+ \(`)
)

// Cleanup applies git's default "whitespace" cleanup: strip trailing
// spaces, squeeze and trim blank lines.
func Cleanup(message string) string {
	var out []string
	for _, ln := range strings.Split(strings.ReplaceAll(message, "\r\n", "\n"), "\n") {
		ln = strings.TrimRightFunc(ln, unicode.IsSpace)
		if ln == "" && (len(out) == 0 || out[len(out)-1] == "") {
			continue
		}
		out = append(out, ln)
	}
	for len(out) > 0 && out[len(out)-1] == "" {
		out = out[:len(out)-1]
	}
	return strings.Join(out, "\n")
}

// CheckMessage validates a full commit message against the commit-message
// contract (CM003–CM016). It backs `mygit lint msg` and the commit-msg hook.
func CheckMessage(text string, c *policy.Contract) verdict.Verdict {
	return checkMessage(text, c, false, verdict.Off)
}

// CheckHeader validates only the header (a PR title). A non-Off override
// replaces the severity of every deny/ask finding, as GH005 does.
func CheckHeader(text string, c *policy.Contract, override verdict.Severity) verdict.Verdict {
	return checkMessage(text, c, true, override)
}

type msgCheck struct {
	c        *policy.Contract
	override verdict.Severity
	v        verdict.Verdict
}

func (m *msgCheck) sev(id string) verdict.Severity {
	s := m.c.Rule(id).Severity
	if m.override != verdict.Off && (s == verdict.Deny || s == verdict.Ask) {
		return m.override
	}
	return s
}

func (m *msgCheck) params(id string) map[string]any { return m.c.Rule(id).Params }

func (m *msgCheck) add(id, format string, args ...any) { m.v.Addf(id, m.sev(id), format, args...) }

func checkMessage(message string, c *policy.Contract, headerOnly bool, override verdict.Severity) verdict.Verdict {
	m := &msgCheck{c: c, override: override}
	msg := Cleanup(message)
	lines := []string{""}
	if msg != "" {
		lines = strings.Split(msg, "\n")
	}
	header := lines[0]
	for _, pat := range policy.Strings(settings(c), "exempt_header_patterns") {
		if reSearch(pat, header, false) {
			return m.v
		}
	}
	if pyStrip(header) == "" {
		m.add("CM003", "message is empty")
		return m.v
	}
	m.checkHeader(header)
	if !headerOnly {
		m.checkBody(lines)
	}
	return m.v
}

func (m *msgCheck) checkHeader(header string) {
	if mx := policy.Int(m.params("CM007"), "max", 72); runeLen(header) > mx {
		m.add("CM007", "header is %d chars, max %d", runeLen(header), mx)
	}
	for _, pat := range policy.Strings(m.params("CM014"), "patterns") {
		if reSearch(pat, header, true) {
			m.add("CM014", "header matches forbidden pattern /%s/", pat)
			break
		}
	}
	types := policy.Strings(settings(m.c), "types")
	idx := headerRe.FindStringSubmatchIndex(header)
	if idx == nil {
		m.add("CM003", "header '%s' is not '<type>(<scope>)!: <subject>'%s", header, headerHint(header, types))
		return
	}
	typ, subject := header[idx[2]:idx[3]], header[idx[8]:idx[9]]
	if !contains(types, typ) {
		m.add("CM004", "type '%s' is not one of: %s", typ, strings.Join(types, ", "))
	}
	if idx[4] >= 0 { // a (scope) group was present, possibly empty
		m.checkScope(header[idx[4]:idx[5]])
	}
	if mn := policy.Int(m.params("CM006"), "min", 3); runeLen(pyStrip(subject)) < mn {
		m.add("CM006", "subject '%s' is shorter than %d chars", subject, mn)
	}
	if strings.HasSuffix(strings.TrimRightFunc(subject, unicode.IsSpace), ".") {
		m.add("CM008", "subject ends with a period")
	}
	if r := []rune(subject); len(r) > 0 && unicode.IsUpper(r[0]) && !(len(r) > 1 && unicode.IsUpper(r[1])) {
		m.add("CM009", "subject should start lower case: '%s'", subject)
	}
	first := strings.ToLower(strings.SplitN(subject, " ", 2)[0])
	if contains(lowerAll(policy.Strings(m.params("CM010"), "banned_first_words")), first) {
		m.add("CM010", "use the imperative mood, not '%s'", first)
	}
}

func headerHint(header string, types []string) string {
	switch {
	case noSpaceHint.MatchString(header):
		return " (add one space after the colon)"
	case scopeSpace.MatchString(header):
		return " (no space between type and scope)"
	case !strings.Contains(header, ":"):
		first := "feat"
		if len(types) > 0 {
			first = types[0]
		}
		return fmt.Sprintf(" (start with a type, e.g. '%s: ...')", first)
	}
	return ""
}

func (m *msgCheck) checkScope(scope string) {
	p := m.params("CM005")
	pat := policy.Str(p, "scope_pattern", `^.+$`)
	allowed := policy.Strings(p, "allowed_scopes")
	if scope == "" || !reMatch(pat, scope, false) {
		m.add("CM005", "scope '%s' does not match %s", scope, pat)
	} else if len(allowed) > 0 && !contains(allowed, scope) {
		m.add("CM005", "scope '%s' is not one of: %s", scope, strings.Join(allowed, ", "))
	}
}

func (m *msgCheck) checkBody(lines []string) {
	body := lines[1:]
	if len(body) > 0 && pyStrip(body[0]) != "" {
		m.add("CM011", "the second line must be blank (separate header and body)")
	}
	mx := policy.Int(m.params("CM012"), "max", 100)
	for n, ln := range body {
		if runeLen(ln) > mx && !strings.Contains(ln, "://") {
			m.add("CM012", "line %d is %d chars, max %d", n+2, runeLen(ln), mx)
			break
		}
	}
	for _, ln := range body {
		if breakingAnyRe.MatchString(ln) && !breakingOKRe.MatchString(ln) {
			m.add("CM013", "write 'BREAKING CHANGE: <description>', not '%s'", runePrefix(ln, 40))
		}
	}
	for _, key := range policy.Strings(m.params("CM015"), "trailers") {
		if !anyLine(body, `^`+regexp.QuoteMeta(key)+`:\s*\S`) {
			m.add("CM015", "missing trailer '%s: ...'", key)
		}
	}
	for _, problem := range AIAttribution(body, m.params("CM016")) {
		m.add("CM016", "%s", problem)
	}
}

func anyLine(lines []string, pat string) bool {
	re := compile(pat, true)
	for _, ln := range lines {
		if re != nil && re.MatchString(ln) {
			return true
		}
	}
	return false
}

// AIAttribution lists lines that credit an AI assistant (rule CM016; also
// used for PR bodies by GH009). params are CM016's params.
func AIAttribution(lines []string, params map[string]any) []string {
	keys := lowerAll(policy.Strings(params, "trailer_keys"))
	who := policy.Strings(params, "coauthor_patterns")
	linePats := policy.Strings(params, "line_patterns")
	var out []string
	for _, ln := range lines {
		key, val, sep := strings.Cut(ln, ":")
		short := runePrefix(pyStrip(ln), 80)
		if sep && contains(keys, strings.ToLower(pyStrip(key))) && anySearch(who, val, true) {
			out = append(out, fmt.Sprintf("AI co-author/attribution trailer is not allowed: '%s'", short))
		} else if anySearch(linePats, ln, true) {
			out = append(out, fmt.Sprintf("AI attribution line is not allowed: '%s'", short))
		}
	}
	return out
}

// ---------------------------------------------------------------- extraction

// MessageKind classifies the message a `git commit` would use.
type MessageKind string

// Message kinds.
const (
	MsgText       MessageKind = "text"       // the message is known
	MsgNone       MessageKind = "none"       // no -m/-F: git would open an editor
	MsgReuse      MessageKind = "reuse"      // -C/-c/--fixup/--squash/--no-edit reuse a message
	MsgDynamic    MessageKind = "dynamic"    // depends on shell expansion or unseen stdin
	MsgUnreadable MessageKind = "unreadable" // -F file that cannot be read
)

// Message is the extracted commit message.
type Message struct {
	Kind   MessageKind
	Text   string
	Detail string
}

// ExtractMessage finds the message a `git commit` would use, from -m,
// --message, -F/--file (read through readFile, relative to the call's cwd)
// or a heredoc on stdin (-F -).
func ExtractMessage(call *shell.GitCall, readFile ReadFile) Message {
	x := extractor{call: call, read: readFile}
	return x.run()
}

type extractor struct {
	call    *shell.GitCall
	read    ReadFile
	parts   []string
	dynamic bool
	fileMsg *Message
	reuse   bool
	noEdit  bool
}

func (x *extractor) run() Message {
	words := x.call.Args
	for i := 0; i < len(words); {
		if words[i].Value == "--" {
			break
		}
		i += x.option(words, i)
	}
	switch {
	case len(x.parts) > 0 && x.dynamic:
		return Message{Kind: MsgDynamic, Text: strings.Join(x.parts, "\n\n"), Detail: "message uses shell variables or command output"}
	case len(x.parts) > 0:
		return Message{Kind: MsgText, Text: strings.Join(x.parts, "\n\n")}
	case x.fileMsg != nil:
		return *x.fileMsg
	case x.reuse || x.noEdit:
		return Message{Kind: MsgReuse}
	}
	return Message{Kind: MsgNone}
}

// option consumes the option at i and returns how many words it used.
func (x *extractor) option(words []shell.Word, i int) int {
	w := words[i]
	a := w.Value
	var nxt *shell.Word
	if i+1 < len(words) {
		nxt = &words[i+1]
	}
	nextVal := func() string {
		if nxt == nil {
			return ""
		}
		return nxt.Value
	}
	step := func(inline bool) int {
		if inline {
			return 1
		}
		return 2
	}
	inline := strings.Contains(a, "=")
	switch {
	case a == "--message" || strings.HasPrefix(a, "--message="):
		if inline {
			x.addPart(strings.SplitN(a, "=", 2)[1], w.Dynamic)
		} else {
			x.addPart(nextVal(), nxt != nil && nxt.Dynamic)
		}
		return step(inline)
	case a == "--file" || strings.HasPrefix(a, "--file="):
		path := nextVal()
		if inline {
			path = strings.SplitN(a, "=", 2)[1]
		}
		x.readFile(path)
		return step(inline)
	}
	if a == "--no-edit" {
		x.noEdit = true
	}
	for _, f := range []string{"--reuse-message", "--reedit-message", "--fixup", "--squash"} {
		if a == f || strings.HasPrefix(a, f+"=") {
			x.reuse = true
			return step(inline)
		}
	}
	if has, val, ok := shortOptValue(a, 'm'); has {
		if !ok {
			x.addPart(nextVal(), nxt != nil && nxt.Dynamic)
			return 2
		}
		x.addPart(val, w.Dynamic)
		return 1
	}
	if has, val, ok := shortOptValue(a, 'F'); has {
		if !ok {
			val = nextVal()
		}
		x.readFile(val)
		return step(ok)
	}
	for _, letter := range []byte{'C', 'c'} {
		if has, _, ok := shortOptValue(a, letter); has {
			x.reuse = true
			return step(ok)
		}
	}
	return 1
}

func (x *extractor) addPart(text string, dynamic bool) {
	x.parts = append(x.parts, text)
	x.dynamic = x.dynamic || dynamic
}

func (x *extractor) readFile(path string) {
	m := readMessageFile(path, x.call, x.read)
	x.fileMsg = &m
}

func readMessageFile(path string, call *shell.GitCall, read ReadFile) Message {
	if path == "-" {
		if !call.HasStdin {
			return Message{Kind: MsgDynamic, Detail: "message is read from stdin that the hook cannot see"}
		}
		return Message{Kind: MsgText, Text: call.Stdin}
	}
	text, ok := read.read(absUnder(call.Cwd, path))
	if !ok {
		return Message{Kind: MsgUnreadable, Detail: "message file not found yet: " + path}
	}
	return Message{Kind: MsgText, Text: text}
}

// CheckCommitMessage checks the message of a `git commit` call: CM001 when
// git would open an editor, CM002 when the message cannot be known, and the
// convention rules otherwise.
func CheckCommitMessage(call *shell.GitCall, c *policy.Contract, readFile ReadFile) verdict.Verdict {
	var v verdict.Verdict
	msg := ExtractMessage(call, readFile)
	switch msg.Kind {
	case MsgNone:
		v.Add("CM001", c.Rule("CM001").Severity,
			"git commit has no -m/--message or -F/--file; an editor cannot be used here")
	case MsgDynamic, MsgUnreadable:
		v.Add("CM002", c.Rule("CM002").Severity, "commit message cannot be checked: "+msg.Detail)
	case MsgText:
		v.Extend(CheckMessage(msg.Text, c))
	}
	return v
}
