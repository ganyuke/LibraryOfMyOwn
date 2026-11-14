package main

import (
	"bytes"
	"log"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/go-git/go-git/v5/plumbing/object"
)

type StoryIndexEntry struct {
	SourcePath  string
	Slug        string
	Title       string
	Author      string
	WordCount   int
	LastUpdated time.Time
}

// strip extension, lowercase, kebab
func createStorySlug(sourcePath string) string {
	path := stripExtension(sourcePath)
	segments := strings.Split(path, "/")

	for i, segment := range segments {
		var slug strings.Builder
		lastWasHyphen := false

		for _, char := range strings.ToLower(segment) {
			switch {
			case unicode.IsLetter(char) || unicode.IsDigit(char):
				slug.WriteRune(char)
				lastWasHyphen = false

			case char == ' ' || char == '_' || char == '-':
				// try to avoid long strings of hyphens if we caused that
				if slug.Len() > 0 && !lastWasHyphen {
					slug.WriteByte('-')
					lastWasHyphen = true
				}
			}
		}

		segments[i] = strings.TrimSuffix(slug.String(), "-")
	}

	return strings.Join(segments, "/")
}

func calculateWordCount(body []byte) int {
	return len(bytes.Fields(body))
}

func buildStoryIndex(commit *object.Commit, history RevisionHistory) (entries []StoryIndexEntry, err error) {
	files, err := listFilesPathsAtCommit(commit)
	if err != nil {
		return nil, err
	}

	for _, path := range files {
		revisions := history[path]

		// in theory, this should never happen and if it does happen if I
		// did my code right. and if the git history somehow does happen
		// in a way that triggers this... I have no clue how it would happen.
		// it's simply an invariant violation.
		if len(revisions) == 0 {
			log.Println("Skipped path; found no revisions for path:", path)
			continue
		}

		// latest commits first (timewise), earliest commits last
		sort.Slice(revisions, func(i, j int) bool {
			return revisions[i].Date.After(revisions[j].Date)
		})

		latestTime := revisions[0].Date
		earliestAuthor := revisions[len(revisions)-1].Author

		file, err := readFileAtCommit(commit, path)
		if err != nil {
			return nil, err
		}
		story := parseStory(file, path)

		entries = append(entries, StoryIndexEntry{
			SourcePath:  path,
			Slug:        createStorySlug(path),
			Title:       story.Metadata.Title,
			WordCount:   calculateWordCount(story.Body),
			LastUpdated: latestTime,
			Author:      earliestAuthor,
		})
	}

	return entries, nil
}
