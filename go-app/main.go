package main

import (
	"bytes"
	"crypto/rand"
	"crypto/subtle"
	"errors"
	"fmt"
	"html/template"
	"io"
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

func refreshRepo(repo Repository) Repository {
	headCommit, err := getHeadCommit(repo.Repository)
	if err != nil {
		log.Fatal("Could not resolve stories HEAD:", err)
	}

	files, err := listFilesPathsAtCommit(headCommit)
	if err != nil {
		log.Fatal("Could not resolve stories HEAD:", err)
	}

	return Repository{
		RepositoryPath:   repo.RepositoryPath,
		RepositoryRoot:   repo.RepositoryRoot,
		Repository:       repo.Repository,
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

func onPush(repo Repository) (Repository, Snapshot, error) {
	repo = refreshRepo(repo)
	snapshot, err := makeSnapshot(repo)
	if err != nil {
		return Repository{}, Snapshot{}, err
	}

	return repo, snapshot, nil
}

type Secrets struct {
	SystemHookPush        string
	AdministratorPassword string
	GitPassword           string
}

func generateSecrets() (Secrets, error) {
	secrets, err := os.OpenFile("secrets.txt", os.O_RDONLY, 0)
	if errors.Is(err, os.ErrNotExist) {
		// generate new secrets
		hook, admin, git := rand.Text(), rand.Text(), rand.Text()

		adminHash, err := HashSecret(admin)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to hash admin password: %w", err)
		}

		gitHash, err := HashSecret(git)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to hash git password: %w", err)
		}

		err = os.WriteFile("secrets.txt", []byte(strings.Join([]string{hook, adminHash, gitHash}, "\n")), 0600)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to write secrets.txt: %w", err)
		}

		log.Println("---- START OF GENERATED SECRETS ----")
		log.Println("Admin password:", admin)
		log.Println("Git password:", git)
		log.Println("---- END OF GENERATED SECRETS ----")
		log.Println("Make sure you CHANGE these when you log in and SAVE them into your password manager! They will not be shown again!")

		return Secrets{
			SystemHookPush:        hook,
			AdministratorPassword: adminHash,
			GitPassword:           gitHash,
		}, nil
	} else if err != nil {
		// could not read file for some reason
		return Secrets{}, fmt.Errorf("failed to open/create secrest file: %w", err)
	} else {
		// normal operation
		defer secrets.Close()

		content, err := io.ReadAll(secrets)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to read secrets file: %w", err)
		}

		lines := bytes.Split(content, []byte("\n"))
		lineCount := len(lines)
		if lineCount != 3 {
			return Secrets{}, fmt.Errorf(
				"malformed secrets file: expected 3 lines of secrets, got %d",
				lineCount,
			)
		}

		return Secrets{
			SystemHookPush:        string(lines[0]),
			AdministratorPassword: string(lines[1]),
			GitPassword:           string(lines[2]),
		}, nil
	}
}

func main() {
	secrets, err := generateSecrets()
	if err != nil {
		log.Fatal("failed to generate/read secrets.txt: %w", err)
	}
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

	mux.HandleFunc("POST /internal/kaboom",
		func(w http.ResponseWriter, r *http.Request) {
			token, ok := strings.CutPrefix(
				r.Header.Get("Authorization"),
				"Bearer ",
			)

			if !ok || token == "" {
				log.Println("rejected refresh attempt on endpoint /internal/kaboom")
				http.Error(w, "Unauthorized", http.StatusUnauthorized)
				return
			}

			clientType := r.Header.Get("X-Client-Type")

			if clientType != "system" {
				log.Println("invalid X-Client-Type tried to access endpoint /internal/kaboom: ", clientType)
				http.Error(w, "invalid client type", 400)
				return
			}

			result := subtle.ConstantTimeCompare([]byte(token), []byte(secrets.SystemHookPush))
			if result == 0 {
				log.Println("rejected invalid token on endpoint /internal/kaboom")
				http.Error(w, "Unauthorized", http.StatusUnauthorized)
				return
			}

			repo, snapshot, err = onPush(repo)
			if err != nil {
				log.Println("failed to refresh repository:", err)
				http.Error(w, "Failed to refresh repository", http.StatusInternalServerError)
				return
			}

			w.WriteHeader(http.StatusNoContent)
		},
	)

	log.Println("Listening at http://localhost:8080")
	log.Fatal(http.ListenAndServe(":8080", mux))

}
