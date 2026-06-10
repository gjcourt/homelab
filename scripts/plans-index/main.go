// Command plans-index validates docs/plans frontmatter and (re)generates the
// status-grouped Document Index in docs/plans/README.md between the
// BEGIN/END PLANS INDEX markers.
//
// Usage:
//
//	go run . -write   # regenerate the index block in README.md
//	go run . -check   # exit non-zero on frontmatter violations or index drift
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

const (
	beginMarker = "<!-- BEGIN PLANS INDEX -->"
	endMarker   = "<!-- END PLANS INDEX -->"
)

var (
	validStatuses = map[string]bool{
		"planned":     true,
		"in-progress": true,
		"complete":    true,
		"superseded":  true,
		"abandoned":   true,
	}
	fileNameRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$`)
	dateRe     = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
)

type plan struct {
	File         string
	Status       string
	LastModified string
	Summary      string
	BlockedOn    string
	SupersededBy string
}

func main() {
	plansDir := flag.String("plans", "../../docs/plans", "path to the docs/plans directory")
	write := flag.Bool("write", false, "rewrite the index block in README.md")
	check := flag.Bool("check", false, "verify frontmatter and that the index block is up to date")
	flag.Parse()

	if *write == *check {
		fmt.Fprintln(os.Stderr, "exactly one of -write or -check is required")
		os.Exit(2)
	}

	plans, errs := loadPlans(*plansDir)
	if len(errs) > 0 {
		for _, e := range errs {
			fmt.Fprintf(os.Stderr, "frontmatter error: %v\n", e)
		}
		os.Exit(1)
	}

	readmePath := filepath.Join(*plansDir, "README.md")
	readme, err := os.ReadFile(readmePath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "read %s: %v\n", readmePath, err)
		os.Exit(1)
	}

	updated, err := replaceBlock(string(readme), renderIndex(plans))
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", readmePath, err)
		os.Exit(1)
	}

	if *check {
		if updated != string(readme) {
			fmt.Fprintln(os.Stderr, "docs/plans/README.md index is out of date; run `make plans-index`")
			os.Exit(1)
		}
		fmt.Printf("ok: %d plans, index up to date\n", len(plans))
		return
	}

	if err := os.WriteFile(readmePath, []byte(updated), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "write %s: %v\n", readmePath, err)
		os.Exit(1)
	}
	fmt.Printf("wrote index for %d plans\n", len(plans))
}

func loadPlans(dir string) ([]plan, []error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, []error{err}
	}
	var plans []plan
	var errs []error
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || name == "README.md" || !strings.HasSuffix(name, ".md") {
			continue
		}
		if !fileNameRe.MatchString(name) {
			errs = append(errs, fmt.Errorf("%s: filename does not match YYYY-MM-DD-<slug>.md", name))
			continue
		}
		p, err := parsePlan(dir, name)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		plans = append(plans, p)
	}
	sort.Slice(plans, func(i, j int) bool { return plans[i].File > plans[j].File })
	return plans, errs
}

func parsePlan(dir, name string) (plan, error) {
	p := plan{File: name}
	raw, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		return p, err
	}
	text := string(raw)
	if !strings.HasPrefix(text, "---\n") {
		return p, fmt.Errorf("%s: missing YAML frontmatter", name)
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return p, fmt.Errorf("%s: unterminated frontmatter", name)
	}
	for _, line := range strings.Split(text[4:4+end], "\n") {
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		value = strings.TrimSpace(value)
		value = strings.Trim(value, `"`)
		switch strings.TrimSpace(key) {
		case "status":
			p.Status = value
		case "last_modified":
			p.LastModified = value
		case "summary":
			p.Summary = value
		case "blocked_on":
			p.BlockedOn = value
		case "superseded_by":
			p.SupersededBy = value
		}
	}
	if !validStatuses[p.Status] {
		return p, fmt.Errorf("%s: invalid status %q (want planned|in-progress|complete|superseded|abandoned)", name, p.Status)
	}
	if !dateRe.MatchString(p.LastModified) {
		return p, fmt.Errorf("%s: invalid or missing last_modified %q", name, p.LastModified)
	}
	if p.Summary == "" {
		return p, fmt.Errorf("%s: missing summary", name)
	}
	if p.Status == "superseded" && p.SupersededBy == "" {
		return p, fmt.Errorf("%s: status superseded requires superseded_by", name)
	}
	if p.SupersededBy != "" {
		target := strings.TrimPrefix(p.SupersededBy, "docs/plans/")
		if _, err := os.Stat(filepath.Join(dir, target)); err != nil {
			return p, fmt.Errorf("%s: superseded_by target %q not found", name, target)
		}
	}
	return p, nil
}

func renderIndex(plans []plan) string {
	var b strings.Builder
	group := func(title string, statuses ...string) {
		var rows []plan
		for _, p := range plans {
			for _, s := range statuses {
				if p.Status == s {
					rows = append(rows, p)
				}
			}
		}
		fmt.Fprintf(&b, "### %s (%d)\n\n", title, len(rows))
		if len(rows) == 0 {
			b.WriteString("_None._\n\n")
			return
		}
		b.WriteString("| File | Last modified | Summary |\n| :--- | :--- | :--- |\n")
		for _, p := range rows {
			summary := escapeCell(p.Summary)
			if p.BlockedOn != "" {
				summary += fmt.Sprintf(" — **blocked:** %s", escapeCell(p.BlockedOn))
			}
			if p.SupersededBy != "" {
				target := strings.TrimPrefix(p.SupersededBy, "docs/plans/")
				summary += fmt.Sprintf(" — superseded by [%s](%s)", target, target)
			}
			fmt.Fprintf(&b, "| [%s](%s) | %s | %s |\n", p.File, p.File, p.LastModified, summary)
		}
		b.WriteString("\n")
	}
	group("In progress", "in-progress")
	group("Planned", "planned")
	group("Complete", "complete")
	group("Superseded / abandoned", "superseded", "abandoned")
	return strings.TrimSuffix(b.String(), "\n")
}

func escapeCell(s string) string {
	return strings.ReplaceAll(s, "|", `\|`)
}

func replaceBlock(readme, index string) (string, error) {
	start := strings.Index(readme, beginMarker)
	end := strings.Index(readme, endMarker)
	if start < 0 || end < 0 || end < start {
		return "", fmt.Errorf("BEGIN/END PLANS INDEX markers not found")
	}
	return readme[:start+len(beginMarker)] + "\n\n" + index + "\n" + readme[end:], nil
}
