package runner

import (
	"strings"
	"testing"
)

func TestCleanChat2NoteIntermediate_DropsSuperfluous(t *testing.T) {
	in := `## Primary Decisions and Action Items
Use PKCE for OAuth.

## Concepts and Information
PKCE is a code-challenge extension for public clients.

## Relevant Files and Sections
- src/auth.go

## Superfluous Information
Considered implicit flow, rejected.`

	got := CleanChat2NoteIntermediate(in)
	if strings.Contains(got, "Superfluous Information") {
		t.Errorf("Superfluous Information section should be dropped, got:\n%s", got)
	}
	if !strings.Contains(got, "PKCE") || !strings.Contains(got, "src/auth.go") {
		t.Errorf("retained sections should be preserved, got:\n%s", got)
	}
}

func TestCleanChat2NoteIntermediate_DropsEmptySections(t *testing.T) {
	in := `## Primary Decisions and Action Items
Use PKCE.

## Concepts and Information
(none)

## Relevant Files and Sections


## Superfluous Information
(none)`

	got := CleanChat2NoteIntermediate(in)
	if strings.Contains(got, "Concepts and Information") {
		t.Errorf("(none) section should be dropped, got:\n%s", got)
	}
	if strings.Contains(got, "Relevant Files and Sections") {
		t.Errorf("empty-body section should be dropped, got:\n%s", got)
	}
	if !strings.Contains(got, "PKCE") {
		t.Errorf("non-empty section should be retained, got:\n%s", got)
	}
}

func TestCleanChat2NoteIntermediate_NonePlaceholderVariants(t *testing.T) {
	cases := []string{"(none)", "None", "n/a", "N/A", "[empty]", "nothing"}
	for _, placeholder := range cases {
		in := "## Concepts and Information\n" + placeholder + "\n"
		got := CleanChat2NoteIntermediate(in)
		if strings.Contains(got, "Concepts and Information") {
			t.Errorf("placeholder %q should drop the section, got: %q", placeholder, got)
		}
	}
}

func TestCleanChat2NoteIntermediate_PreservesSubHeadings(t *testing.T) {
	in := `## Concepts and Information
### OAuth
PKCE is a code-challenge extension.

### JWT
Tokens are signed.`

	got := CleanChat2NoteIntermediate(in)
	if !strings.Contains(got, "### OAuth") || !strings.Contains(got, "### JWT") {
		t.Errorf("sub-headings should survive, got:\n%s", got)
	}
}

func TestCleanChat2NoteIntermediate_CaseInsensitiveSuperfluousMatch(t *testing.T) {
	in := `## Primary Decisions and Action Items
Decision.

## SUPERFLUOUS information
junk here`

	got := CleanChat2NoteIntermediate(in)
	if strings.Contains(strings.ToLower(got), "junk") {
		t.Errorf("case-insensitive match should drop Superfluous Information regardless of casing, got:\n%s", got)
	}
}
