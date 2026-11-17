package workflow

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"path"
	"sort"
	"strings"
)

// Builtin is the origin label of templates embedded in the binary.
const Builtin = "built-in"

// Source is one template document as found in one scope.
type Source struct {
	Name   string
	Origin string // "built-in" or the file path it came from
	Doc    map[string]any
}

// Catalog holds the known templates. A name may be defined in several
// scopes; the most recently added definition (the most specific scope)
// shadows the earlier ones, and a template that extends its own name
// inherits from the shadowed definition.
type Catalog struct {
	byName map[string][]Source
}

// NewCatalog returns an empty catalog.
func NewCatalog() *Catalog { return &Catalog{byName: map[string][]Source{}} }

// Add registers a template. Add sources from the least to the most specific
// scope: built-in, global, project, local.
func (c *Catalog) Add(s Source) {
	c.byName[s.Name] = append(c.byName[s.Name], s)
}

// AddJSON parses a template file and adds it. The name is the document's
// "name" field, or fallback (typically the file's base name) when absent.
func (c *Catalog) AddJSON(data []byte, origin, fallback string) error {
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		return fmt.Errorf("%s: invalid workflow template: %w", origin, err)
	}
	if doc == nil {
		return fmt.Errorf("%s: workflow template must be a JSON object", origin)
	}
	name, _ := doc["name"].(string)
	if name == "" {
		name = fallback
	}
	if name == "" {
		return fmt.Errorf("%s: workflow template needs a name", origin)
	}
	c.Add(Source{Name: name, Origin: origin, Doc: doc})
	return nil
}

// Lookup returns the most specific definition of name.
func (c *Catalog) Lookup(name string) (Source, bool) {
	stack := c.byName[name]
	if len(stack) == 0 {
		return Source{}, false
	}
	return stack[len(stack)-1], true
}

// Names returns every known template name, sorted.
func (c *Catalog) Names() []string {
	out := make([]string, 0, len(c.byName))
	for n := range c.byName {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

// Builtins loads workflows/*.json from fsys (normally defaults.FS).
func Builtins(fsys fs.FS) (*Catalog, error) {
	c := NewCatalog()
	files, err := fs.Glob(fsys, "workflows/*.json")
	if err != nil {
		return nil, err
	}
	sort.Strings(files)
	for _, f := range files {
		data, err := fs.ReadFile(fsys, f)
		if err != nil {
			return nil, err
		}
		base := strings.TrimSuffix(path.Base(f), ".json")
		if err := c.AddJSON(data, Builtin, base); err != nil {
			return nil, err
		}
	}
	return c, nil
}

// Resolved is a template with its extends chain applied.
type Resolved struct {
	Template Template
	// Origin is where the selected (leaf) template was defined.
	Origin string
	// Chain lists "name (origin)" from the leaf to the root of the extends chain.
	Chain []string
}

// Resolve looks up name in cat and applies its extends chain: objects
// deep-merge, transitions merge by id (same id deep-merges onto the
// inherited transition, a new id is appended, "remove": true drops the inherited one). Cycles are an error.
func Resolve(name string, cat *Catalog) (*Resolved, error) {
	stack := cat.byName[name]
	if len(stack) == 0 {
		return nil, fmt.Errorf("unknown workflow %q (known: %s)", name, strings.Join(cat.Names(), ", "))
	}
	r := &resolver{cat: cat, seen: map[string]bool{}}
	doc, err := r.resolve(name, len(stack)-1)
	if err != nil {
		return nil, err
	}
	doc["name"] = name
	delete(doc, "extends")
	dropRemoved(doc)
	leaf := stack[len(stack)-1]
	t, err := decode(doc)
	if err != nil {
		return nil, fmt.Errorf("workflow %q (%s): %w", name, leaf.Origin, err)
	}
	return &Resolved{Template: t, Origin: leaf.Origin, Chain: r.chain}, nil
}

type resolver struct {
	cat   *Catalog
	seen  map[string]bool
	chain []string
	path  []string
}

func (r *resolver) resolve(name string, level int) (map[string]any, error) {
	key := fmt.Sprintf("%s#%d", name, level)
	r.path = append(r.path, name)
	if r.seen[key] {
		return nil, fmt.Errorf("workflow extends cycle: %s", strings.Join(r.path, " → "))
	}
	r.seen[key] = true
	src := r.cat.byName[name][level]
	r.chain = append(r.chain, fmt.Sprintf("%s (%s)", name, src.Origin))
	parent, _ := src.Doc["extends"].(string)
	if parent == "" {
		return cloneMap(src.Doc), nil
	}
	plevel := len(r.cat.byName[parent]) - 1
	if parent == name {
		plevel = level - 1
	}
	if plevel < 0 {
		return nil, fmt.Errorf("workflow %q (%s) extends unknown workflow %q", name, src.Origin, parent)
	}
	base, err := r.resolve(parent, plevel)
	if err != nil {
		return nil, err
	}
	return MergeDocs(base, src.Doc), nil
}

// MergeDocs merges over onto base (both raw template documents) and returns
// a new document: objects deep-merge, other values replace, and
// "transitions" merge by id (a same-id transition deep-merges onto the
// inherited one; a new id is appended). Removal markers are kept so a later layer can
// still see them; Resolve drops them at the end.
func MergeDocs(base, over map[string]any) map[string]any {
	out := cloneMap(base)
	for k, v := range over {
		switch {
		case k == "transitions":
			out[k] = mergeTransitions(out[k], v)
		default:
			bm, bok := out[k].(map[string]any)
			om, ook := v.(map[string]any)
			if bok && ook {
				out[k] = deepMerge(bm, om)
			} else {
				out[k] = cloneValue(v)
			}
		}
	}
	return out
}

func mergeTransitions(base, over any) any {
	bl, _ := base.([]any)
	ol, ok := over.([]any)
	if !ok {
		return cloneValue(over) // let decode report the type error
	}
	out := make([]any, 0, len(bl)+len(ol))
	for _, t := range bl {
		out = append(out, cloneValue(t))
	}
	for _, t := range ol {
		id := transitionID(t)
		idx := -1
		for i, b := range out {
			if id != "" && transitionID(b) == id {
				idx = i
				break
			}
		}
		if idx >= 0 {
			out[idx] = mergeTransition(out[idx], t)
		} else {
			out = append(out, cloneValue(t))
		}
	}
	return out
}

// mergeTransition deep-merges an overriding transition onto the inherited
// one, so fields it leaves out (deleteHead, prTemplate, …) are kept. An
// inherited removal marker is replaced outright, so a later layer can
// re-add a transition an earlier one removed.
func mergeTransition(base, over any) any {
	bm, bok := base.(map[string]any)
	om, ook := over.(map[string]any)
	if !bok || !ook || bm["remove"] == true {
		return cloneValue(over)
	}
	return deepMerge(bm, om)
}

func transitionID(t any) string {
	m, _ := t.(map[string]any)
	id, _ := m["id"].(string)
	return id
}

// dropRemoved deletes transitions marked "remove": true.
func dropRemoved(doc map[string]any) {
	l, ok := doc["transitions"].([]any)
	if !ok {
		return
	}
	out := l[:0]
	for _, t := range l {
		if m, ok := t.(map[string]any); ok && m["remove"] == true {
			continue
		}
		out = append(out, t)
	}
	doc["transitions"] = out
}

func decode(doc map[string]any) (Template, error) {
	data, err := json.Marshal(doc)
	if err != nil {
		return Template{}, err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var t Template
	if err := dec.Decode(&t); err != nil {
		return Template{}, err
	}
	return t, nil
}

func deepMerge(base, over map[string]any) map[string]any {
	out := cloneMap(base)
	for k, v := range over {
		bm, bok := out[k].(map[string]any)
		om, ook := v.(map[string]any)
		if bok && ook {
			out[k] = deepMerge(bm, om)
		} else {
			out[k] = cloneValue(v)
		}
	}
	return out
}

func cloneMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = cloneValue(v)
	}
	return out
}

func cloneValue(v any) any {
	switch t := v.(type) {
	case map[string]any:
		return cloneMap(t)
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = cloneValue(x)
		}
		return out
	}
	return v
}
