package main

import (
	"bytes"
	"html/template"
	"log"
	"path/filepath"
	"strings"

	"github.com/yuin/goldmark"
)

type FrontmatterField struct {
	Field string
	Value string
}

type StoryMetadata struct {
	Title         string
	Frontmatter   []FrontmatterField
	CharacterList []FrontmatterField
}

type StoryRawData struct {
	Metadata StoryMetadata
	Body     []byte
}

type StoryPageData struct {
	Metadata StoryMetadata
	Body     template.HTML
}

func splitFrontmatter(content []byte) (frontmatter []byte, body []byte) {
	// I don't want to deal with Windows-style carriage returns
	content = bytes.ReplaceAll(content, []byte("\r\n"), []byte("\n"))

	hlStr := "\n---\n"
	hl := []byte(hlStr)

	// case: opening delimiter for standard greymatter
	opening := hl[1:]
	rest := content
	if bytes.HasPrefix(content, opening) {
		rest = content[len(opening):]

		// case: empty frontmatter
		if bytes.HasPrefix(rest, opening) {
			return nil, rest[len(opening):]
		}
	}

	// case: closing delimiter
	frontmatter, body, found := bytes.Cut(rest, hl)

	// case: no frontmatter
	if !found {
		// case: pure frontmatter
		if bytes.HasSuffix(rest, hl[:len(hl)-1]) {
			return rest[:len(rest)-(len(hl)-1)], nil
		}

		return nil, content
	}

	return frontmatter, body
}

func parseStoryMetadata(frontmatter []byte) StoryMetadata {
	var frontmatterList []FrontmatterField
	var characterList []FrontmatterField
	var title string

	lines := bytes.Split(frontmatter, []byte("\n"))
	colon := []byte(":")
	titleFieldName := []byte("Title")
	characterListHeading := []byte("Characters:")
	characterPrefixes := [][]byte{
		[]byte("- "),
		[]byte("– "),
	}

	checkIsCharacter := false

	for lineIdx := range lines {
		line := lines[lineIdx]

		if bytes.HasPrefix(line, characterListHeading) {
			checkIsCharacter = true
			continue
		}

		fieldName, fieldValue, found := bytes.Cut(line, colon)
		if !found {
			continue
		}

		fieldName = bytes.TrimSpace(fieldName)
		fieldValue = bytes.TrimSpace(fieldValue)

		if checkIsCharacter {
			isCharacter := false
			for _, prefix := range characterPrefixes {
				if bytes.HasPrefix(fieldName, prefix) {
					fieldName = bytes.TrimPrefix(fieldName, prefix)
					isCharacter = true
					break
				}
			}

			if isCharacter {
				characterList = append(characterList, FrontmatterField{
					Field: string(fieldName),
					Value: string(fieldValue),
				})
				continue
			}

			checkIsCharacter = false
		}

		if bytes.Equal(fieldName, titleFieldName) {
			title = string(fieldValue)
			continue
		}

		frontmatterList = append(frontmatterList, FrontmatterField{
			Field: string(fieldName),
			Value: string(fieldValue),
		})
	}

	return StoryMetadata{
		Title:         title,
		Frontmatter:   frontmatterList,
		CharacterList: characterList,
	}
}

func stripExtension(filename string) string {
	return strings.TrimSuffix(filename, filepath.Ext(filename))
}

func parseStory(content []byte, path string) StoryRawData {
	front, content := splitFrontmatter(content)
	metadata := parseStoryMetadata(front)

	// case: no title metadata
	// use file's name instead
	if metadata.Title == "" {
		filename := filepath.Base(path)
		metadata.Title = stripExtension(filename)
	}

	return StoryRawData{
		Metadata: metadata,
		Body:     content,
	}
}

func renderStory(story StoryRawData) (StoryPageData, error) {
	// render markdown
	var buf bytes.Buffer
	if err := goldmark.Convert(story.Body, &buf); err != nil {
		log.Println("Could not convert Markdown:", err)
		return StoryPageData{}, err
	}

	return StoryPageData{
		Metadata: story.Metadata,
		Body:     template.HTML(buf.String()),
	}, nil
}
