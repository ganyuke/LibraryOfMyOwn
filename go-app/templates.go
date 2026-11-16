package main

import (
	"bytes"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"slices"
	"strings"
	"time"

	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
)

type Templates struct {
	Index   *template.Template
	Story   *template.Template
	History *template.Template
	Login   *template.Template
	Admin   *template.Template
	Site    *template.Template
	Publish *template.Template
}

type HistoryPageData struct {
	Entry     StoryIndexEntry
	Revisions []FileRevision
}

type LoginPageData struct {
	Error string
}

type SitePageData struct {
	Error         string
	Branches      []string
	IsMissing     bool
	CurrentBranch string
}

type PublishOption struct {
	Title     string
	Path      string
	Published bool
	Missing   bool
}

type PublishPageData struct {
	Options []PublishOption
	Error   string
}

// mediawiki style date
func FormatTimestamp(date time.Time) string {
	return date.UTC().Format("15:04, 2 January 2006 (MST)")
}

// mediawiki style date without the timezone or time
func FormatSimpleDate(date time.Time) string {
	return date.UTC().Format("2 January 2006")
}

func isShortSha(hex string) bool {
	if hex == "" || len(hex) != 7 {
		return false
	}

	for _, char := range hex {
		if !(char >= 'a' && char <= 'f') &&
			!(char >= '0' && char <= '9') {
			return false
		}
	}

	return true
}

func makeSitePageData(repo *git.Repository) (SitePageData, error) {
	branches, err := listBranches(repo)
	if err != nil {
		return SitePageData{}, fmt.Errorf("list branches: %w", err)
	}

	head, err := repo.Reference(plumbing.HEAD, false)
	if err != nil {
		return SitePageData{}, fmt.Errorf("read HEAD: %w", err)
	}

	currentBranch := head.Target().Short()

	return SitePageData{
		Branches:      branches,
		CurrentBranch: currentBranch,
		IsMissing:     !slices.Contains(branches, currentBranch),
	}, nil
}

func makePublishPageData(index []StoryIndexEntry, settings SiteSettings) PublishPageData {
	var data PublishPageData
	currentPaths := make(map[string]bool)

	// add currently recognized paths to the list
	for _, story := range index {
		currentPaths[story.SourcePath] = true // to tell if a path in site settings is stale
		data.Options = append(data.Options, PublishOption{
			Title:     story.Title,
			Path:      story.SourcePath,
			Published: settings.PublishedPaths[story.SourcePath],
		})
	}

	// add nonexistent paths still marked in the site settings as published
	// i.e. stale/missing published paths
	for path, published := range settings.PublishedPaths {
		if published && !currentPaths[path] {
			data.Options = append(data.Options, PublishOption{
				Path:      path,
				Published: true,
				Missing:   true,
			})
		}
	}

	// since maps don't guarantee order and
	// having options jumping around is really annoying
	slices.SortFunc(data.Options, func(a, b PublishOption) int {
		return strings.Compare(a.Path, b.Path)
	})

	return data
}

// generic HTTP writer handler to buffer responses before sending them over the wire
// returns over the HTTP API either 500 or 200 and on Go side, returns err for failed
// template execution and failed buffer write to the HTTP writer
func renderTemplate(w http.ResponseWriter, templ *template.Template, status int, data any) error {
	// I don't want to send partial responses if the template fails
	// so we will use a buffer here to generate the response fully before we
	// send it over the wire to be either 200 or, what this gives us, 500
	var buf bytes.Buffer

	if err := templ.Execute(&buf, data); err != nil {
		log.Println("Could not render template:", err)
		http.Error(w, "Could not render template", http.StatusInternalServerError)
		return err
	}

	w.WriteHeader(status)
	_, err := buf.WriteTo(w)
	if err != nil {
		log.Println("Could not send response:", err)
		return err
	}

	return nil
}

func makeTemplates() Templates {
	funcs := template.FuncMap{
		"FormatTimestamp":  FormatTimestamp,
		"FormatSimpleDate": FormatSimpleDate,
		"ShortHash": func(s string) string {
			return s[:7]
		},
	}

	parse := func(name string) *template.Template {
		return template.Must(
			template.New(name).Funcs(funcs).ParseFiles("templates/" + name),
		)
	}

	return Templates{
		Index:   parse("index.gohtml"),
		Story:   parse("story.gohtml"),
		History: parse("history.gohtml"),
		Login:   parse("login.gohtml"),
		Admin:   parse("admin.gohtml"),
		Site:    parse("site.gohtml"),
		Publish: parse("publish.gohtml"),
	}
}
