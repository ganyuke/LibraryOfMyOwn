package main

import (
	"bytes"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

type Snapshot struct {
	Commit     *object.Commit
	Index      []StoryIndexEntry
	History    RevisionHistory
	IndexSlugs map[string][]StoryIndexEntry
}

type Repository struct {
	RepositoryPath   string
	RepositoryRoot   *os.Root
	Repository       *git.Repository
	HeadCommit       *object.Commit
	CurrentFilePaths []string
}

type HistoryPageData struct {
	Entry     StoryIndexEntry
	Revisions []FileRevision
}

type Templates struct {
	Index   *template.Template
	Story   *template.Template
	History *template.Template
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

// generic HTTP writer handler to buffer responses before sending them over the wire
// returns over the HTTP API either 500 or 200 and on Go side, returns err for failed
// template execution and failed buffer write to the HTTP writer
func renderTemplate(w http.ResponseWriter, templ *template.Template, data any) error {
	// I don't want to send partial responses if the template fails
	// so we will use a buffer here to generate the response fully before we
	// send it over the wire to be either 200 or, what this gives us, 500
	var buf bytes.Buffer

	if err := templ.Execute(&buf, data); err != nil {
		log.Println("Could not render template:", err)
		http.Error(w, "Could not render template", http.StatusInternalServerError)
		return err
	}

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

	return Templates{
		Index:   template.Must(template.New("index.gohtml").Funcs(funcs).ParseFiles("templates/index.gohtml")),
		Story:   template.Must(template.New("story.gohtml").Funcs(funcs).ParseFiles("templates/story.gohtml")),
		History: template.Must(template.New("history.gohtml").Funcs(funcs).ParseFiles("templates/history.gohtml")),
	}
}

func loadRepo(repoPath string) Repository {
	repoRoot, err := os.OpenRoot(repoPath)
	if err != nil {
		log.Fatal("Could not open stories directory:", err)
	}

	repo, err := openRepository(repoPath)
	if err != nil {
		log.Fatal("Could not open stories repository:", err)
	}

	headCommit, err := getHeadCommit(repo)
	if err != nil {
		log.Fatal("Could not resolve stories HEAD:", err)
	}

	files, err := listFilesPathsAtCommit(headCommit)
	if err != nil {
		log.Fatal("Could not resolve stories HEAD:", err)
	}

	return Repository{
		RepositoryPath:   repoPath,
		RepositoryRoot:   repoRoot,
		Repository:       repo,
		HeadCommit:       headCommit,
		CurrentFilePaths: files,
	}
}

func makeSnapshot(repo Repository) (Snapshot, error) {
	history, err := walkHistoryInRepo(repo)
	if err != nil {
		return Snapshot{}, fmt.Errorf("failed to walk history: %w", err)
	}

	storyIndex, err := buildStoryIndex(repo.HeadCommit, history)
	if err != nil {
		return Snapshot{}, fmt.Errorf("failed to build index: %w", err)
	}

	// slice so we can store collided names for
	// notice in the admin UI
	indexSlugs := make(map[string][]StoryIndexEntry)

	for _, story := range storyIndex {
		existing, found := indexSlugs[story.Slug]
		if found {
			log.Println("slug collision detected on:", existing[0].SourcePath, "for", story.SourcePath)
		}

		indexSlugs[story.Slug] = append(indexSlugs[story.Slug], story)
	}

	return Snapshot{
		Commit:     repo.HeadCommit,
		Index:      storyIndex,
		History:    history,
		IndexSlugs: indexSlugs,
	}, nil
}

func main() {
	mux := http.NewServeMux()
	templ := makeTemplates()
	repo := loadRepo("../examples/private/")
	snapshot, err := makeSnapshot(repo)

	// root index route (/)
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		if err != nil {
			log.Println("failed to start web service:", err)
			http.Error(w, "Failed to start web service. See logs for details.", http.StatusInternalServerError)
			return
		}

		renderTemplate(w, templ.Index, snapshot.Index)
	})

	// wildcard stories routes
	// /stories /stories/X/history /stories/X/r/Y
	mux.HandleFunc("GET /stories/{path...}", func(w http.ResponseWriter, r *http.Request) {
		if err != nil {
			log.Println("failed to start web service:", err)
			http.Error(w, "Failed to start web service. See logs for details.", http.StatusInternalServerError)
			return
		}

		path := r.PathValue("path")

		entries, found := snapshot.IndexSlugs[path]

		renderCommit := func(src string, commit *object.Commit) {
			file, err := readFileAtCommit(commit, src)
			if err != nil {
				log.Println("Could not read story:", err)
				http.Error(w, "Could not read story", http.StatusInternalServerError)
				return
			}

			story := parseStory(file, src)
			storyRendered, err := renderStory(story)
			if err != nil {
				http.Error(w, "Could not render story", http.StatusInternalServerError)
				return
			}

			renderTemplate(w, templ.Story, storyRendered)
		}

		if !found {
			// handle history path
			pathSplit := strings.Split(path, "/")
			if pathSplit[len(pathSplit)-1] == "history" {
				truncatedPath := path[:len(path)-len("/history")]
				entries, found := snapshot.IndexSlugs[truncatedPath]

				if !found {
					http.NotFound(w, r)
					return
				}

				entry := entries[0]
				entryHistory := snapshot.History[entry.SourcePath]

				historyPgData := HistoryPageData{
					Entry:     entry,
					Revisions: entryHistory,
				}

				renderTemplate(w, templ.History, historyPgData)
				return
			}

			shortHash := pathSplit[len(pathSplit)-1]
			if len(pathSplit) >= 2 && pathSplit[len(pathSplit)-2] == "r" && isShortSha(shortHash) {
				truncatedPath := path[:len(path)-len("/r/")-7]
				entries, found := snapshot.IndexSlugs[truncatedPath]

				if !found {
					http.NotFound(w, r)
					return
				}

				entry := entries[0]
				entryHistory := snapshot.History[entry.SourcePath]

				var matched FileRevision
				matches := 0

				for _, revision := range entryHistory {
					if strings.HasPrefix(revision.Hash, shortHash) {
						matched = revision
						matches += 1
					}
				}

				if matches == 0 {
					http.NotFound(w, r)
					return
				}

				if matches > 1 {
					http.Error(w, "Short SHA collision detected; unable to show commit.", http.StatusConflict)
					return
				}

				hash, err := repo.Repository.ResolveRevision(plumbing.Revision(matched.Hash))
				if err != nil {
					log.Println("Could not resolve revision:", err)
					return
				}

				commit, err := repo.Repository.CommitObject(*hash)
				if err != nil {
					log.Println("Could not load commit:", err)
					return
				}

				renderCommit(matched.OldPath, commit)
				return
			}

			// not a special page
			http.NotFound(w, r)
			return
		}

		entry := entries[0]

		renderCommit(entry.SourcePath, repo.HeadCommit)
	})

	log.Println("Listening at http://localhost:8080")
	log.Fatal(http.ListenAndServe(":8080", mux))

}
