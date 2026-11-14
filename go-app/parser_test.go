package main

import (
	"bytes"
	"slices"
	"testing"
)

//
// FRONTMATTER SPLITTING
//

// case: unfinished frontmatter with opening but no closing
// expected behavior: keep rest of body intact
func TestMissingClosingFrontmatterSplit(t *testing.T) {
	content := []byte("---\nTitle: Hello\nStory starts here")

	frontmatter, body := splitFrontmatter(content)

	if frontmatter != nil {
		t.Errorf("frontmatter = %q; want nil", frontmatter)
	}
	if !bytes.Equal(body, content) {
		t.Errorf("body = %q; want %q", body, content)
	}
}

// case: standard markdown frontmatter w/ both open and close tripe
// expected behavior: cut triples and keep rest of body intact
func TestStandardFrontmatterSplit(t *testing.T) {
	content := []byte("---\nTitle: Hello\n---\nStory starts here")

	frontmatter, body := splitFrontmatter(content)

	wantFrontmatter := []byte("Title: Hello")
	wantBody := []byte("Story starts here")

	if !bytes.Equal(frontmatter, wantFrontmatter) {
		t.Errorf("frontmatter = %q; want %q", frontmatter, wantFrontmatter)
	}
	if !bytes.Equal(body, wantBody) {
		t.Errorf("body = %q; want %q", body, wantBody)
	}
}

// case: someone decided that they wanted windows line endings instead of the superior unix
// expected behavior: parser converts to unix line endings
func TestNormalizeWindowsFrontmatterSplit(t *testing.T) {
	content := []byte("---\r\nTitle: Hello\r\n---\r\nFirst line\r\nSecond line\r")

	frontmatter, body := splitFrontmatter(content)

	wantFrontmatter := []byte("Title: Hello")
	wantBody := []byte("First line\nSecond line\r")

	if !bytes.Equal(frontmatter, wantFrontmatter) {
		t.Errorf("frontmatter = %q; want %q", frontmatter, wantFrontmatter)
	}
	if !bytes.Equal(body, wantBody) {
		t.Errorf("body = %q; want %q", body, wantBody)
	}
}

// case: frontmatter missing open triple
// expected behavior: frontmatter + body
func TestMissingOpenFrontmatterSplit(t *testing.T) {
	content := []byte("Title: Hello\n---\nStory starts here")

	frontmatter, body := splitFrontmatter(content)

	wantFrontmatter := []byte("Title: Hello")
	wantBody := []byte("Story starts here")

	if !bytes.Equal(frontmatter, wantFrontmatter) {
		t.Errorf("frontmatter = %q; want %q", frontmatter, wantFrontmatter)
	}
	if !bytes.Equal(body, wantBody) {
		t.Errorf("body = %q; want %q", body, wantBody)
	}
}

// case: missing frontmatter
// expected behavior: just the body
func TestMissingFrontmatterSplit(t *testing.T) {
	content := []byte("Story starts here")

	frontmatter, body := splitFrontmatter(content)

	if frontmatter != nil {
		t.Errorf("frontmatter = %q; want nil", frontmatter)
	}
	if !bytes.Equal(body, content) {
		t.Errorf("body = %q; want %q", body, content)
	}
}

// case: empty frontmatter
// expected behavior: just the body
func TestEmptyFrontmatterSplit(t *testing.T) {
	content := []byte("---\n---\nStory starts here")

	frontmatter, body := splitFrontmatter(content)
	wantBody := []byte("Story starts here")

	if frontmatter != nil {
		t.Errorf("frontmatter = %q; want nil", frontmatter)
	}
	if !bytes.Equal(body, wantBody) {
		t.Errorf("body = %q; want %q", body, wantBody)
	}
}

// case: frontmatter at the end of file
// expected behavior: just the frontmatter
func TestEofFrontmatterSplit(t *testing.T) {
	content := []byte("---\nTitle: Hello\n---")

	frontmatter, body := splitFrontmatter(content)

	wantFrontmatter := []byte("Title: Hello")

	if !bytes.Equal(frontmatter, wantFrontmatter) {
		t.Errorf("frontmatter = %q; want %q", frontmatter, wantFrontmatter)
	}
	if body != nil {
		t.Errorf("body = %q; want nil", body)
	}
}

//
// FRONTMATTER PARSING
//

// case: standard frontmater
// expected behavior: everything to work
func TestParseFrontmatter(t *testing.T) {
	content := []byte("Title: Hololive\nDescription: Shipping\nCharacters:\n- Hoshimachi Suisei: The super cute comet idol\n– Sakura Miko: Her best friend\nCategory: Original Works")
	metadata := parseStoryMetadata(content)
	fields := metadata.Frontmatter
	characters := metadata.CharacterList

	if metadata.Title != "Hololive" {
		t.Errorf("frontmatter = %s; want %s", metadata.Title, "Hololive")
	}

	wantFields := []FrontmatterField{
		{Field: "Description", Value: "Shipping"},
		{Field: "Category", Value: "Original Works"},
	}

	if !slices.Equal(fields, wantFields) {
		t.Errorf("fields = %+v; want %+v", fields, wantFields)
	}

	wantCharacters := []FrontmatterField{
		{Field: "Hoshimachi Suisei", Value: "The super cute comet idol"},
		{Field: "Sakura Miko", Value: "Her best friend"},
	}

	if !slices.Equal(characters, wantCharacters) {
		t.Errorf("characters = %+v; want %+v", characters, wantCharacters)
	}
}

// case: missing title
// expected behavior: title returns as an empty string
func TestEmptyTitle(t *testing.T) {
	content := []byte("Description: Shipping\nCharacters:\n- Hoshimachi Suisei: The super cute comet idol\n– Sakura Miko: Her best friend\nCategory: Original Works")
	metadata := parseStoryMetadata(content)
	fields := metadata.Frontmatter
	characters := metadata.CharacterList

	if metadata.Title != "" {
		t.Errorf("title = %s; want \"\"", metadata.Title)
	}

	wantFields := []FrontmatterField{
		{Field: "Description", Value: "Shipping"},
		{Field: "Category", Value: "Original Works"},
	}

	if !slices.Equal(fields, wantFields) {
		t.Errorf("fields = %+v; want %+v", fields, wantFields)
	}

	wantCharacters := []FrontmatterField{
		{Field: "Hoshimachi Suisei", Value: "The super cute comet idol"},
		{Field: "Sakura Miko", Value: "Her best friend"},
	}

	if !slices.Equal(characters, wantCharacters) {
		t.Errorf("characters = %+v; want %+v", characters, wantCharacters)
	}
}

// case: missing frontmatter
// expected behavior: empty structs
func TestParseFrontmatterEmpty(t *testing.T) {
	metadata := parseStoryMetadata(nil)
	fields := metadata.Frontmatter
	characters := metadata.CharacterList

	if metadata.Title != "" {
		t.Errorf("title = %s; want \"\"", metadata.Title)
	}

	if len(fields) != 0 {
		t.Errorf("fields = %+v; want empty", fields)
	}
	if len(characters) != 0 {
		t.Errorf("characters = %+v; want empty", characters)
	}
}

//
// STORY PARSING
//

// The render function tested later is primarily glue around Goldmark
// and I don't think it's my job to test that so I will instead
// just test the title fallback here

// case: missing title
// expected behavior: fallback to file's name
func TestEmptyTitleFallback(t *testing.T) {
	content := []byte("Description: Shipping\nCharacters:\n- Hoshimachi Suisei: The super cute comet idol\n– Sakura Miko: Her best friend\nCategory: Original Works\n---\nsuichan wa kyou mo kawaii")
	story := parseStory(content, "Hello/World/Sekai-de.md")

	if story.Metadata.Title != "Sekai-de" {
		t.Errorf("title = %s; want %s", story.Metadata.Title, "Sekai-de")
	}
}
