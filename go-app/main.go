package main

import (
	"crypto/subtle"
	"log"
	"net/http"
	"strings"
	"sync"

	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

type HistoryPageData struct {
	Entry     StoryIndexEntry
	Revisions []FileRevision
}

func main() {
	secrets, err := generateSecrets()
	if err != nil {
		log.Fatal("failed to generate/read secrets.txt: %w", err)
	}
	mux := http.NewServeMux()
	templ := makeTemplates()
	repo := loadRepo("../examples/private/")
	err = deployPostRecieveHook(repo.RepositoryPath)
	if err != nil {
		log.Println("warning: degraded functionality; failed to write post-recieve hook:", err)
	}
	snapshot, snapshotErr := makeSnapshot(repo)

	var antiRefreshMutex sync.RWMutex
	var refreshWriteMutex sync.Mutex

	// root index route (/)
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		antiRefreshMutex.RLock()
		currentSnapshot := snapshot
		currentSnapshotErr := snapshotErr
		antiRefreshMutex.RUnlock()

		if currentSnapshotErr != nil {
			log.Println("failed to start web service:", currentSnapshotErr)
			http.Error(w, "Failed to start web service. See logs for details.", http.StatusInternalServerError)
			return
		}

		renderTemplate(w, templ.Index, currentSnapshot.Index)
	})

	// wildcard stories routes
	// /stories /stories/X/history /stories/X/r/Y
	mux.HandleFunc("GET /stories/{path...}", func(w http.ResponseWriter, r *http.Request) {
		antiRefreshMutex.RLock()
		currentSnapshot := snapshot
		currentRepo := repo
		currentSnapshotErr := snapshotErr
		antiRefreshMutex.RUnlock()

		if currentSnapshotErr != nil {
			log.Println("failed to start web service:", currentSnapshotErr)
			http.Error(w, "Failed to start web service. See logs for details.", http.StatusInternalServerError)
			return
		}

		path := r.PathValue("path")

		entries, found := currentSnapshot.IndexSlugs[path]

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
				entries, found := currentSnapshot.IndexSlugs[truncatedPath]

				if !found {
					http.NotFound(w, r)
					return
				}

				entry := entries[0]
				entryHistory := currentSnapshot.History[entry.SourcePath]

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
				entries, found := currentSnapshot.IndexSlugs[truncatedPath]

				if !found {
					http.NotFound(w, r)
					return
				}

				entry := entries[0]
				entryHistory := currentSnapshot.History[entry.SourcePath]

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

				hash, err := currentRepo.Repository.ResolveRevision(plumbing.Revision(matched.Hash))
				if err != nil {
					log.Println("Could not resolve revision:", err)
					return
				}

				commit, err := currentRepo.Repository.CommitObject(*hash)
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

		renderCommit(entry.SourcePath, currentRepo.HeadCommit)
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

			// prevent refreshing from stepping on each other by serializing
			refreshWriteMutex.Lock()

			antiRefreshMutex.RLock()
			currentRepo := repo
			antiRefreshMutex.RUnlock()

			// no lock on reads yet so we can continue serving
			// the current snapshot while building the next
			nRepo, nSnapshot, pushErr := onPush(currentRepo)
			if pushErr == nil {
				// acquire write lock while swapping out the old snapshot
				antiRefreshMutex.Lock()
				repo = nRepo
				snapshot = nSnapshot
				snapshotErr = nil
				antiRefreshMutex.Unlock()
			}
			refreshWriteMutex.Unlock()

			if pushErr != nil {
				log.Println("failed to refresh repository:", pushErr)
				http.Error(w, "Failed to refresh repository", http.StatusInternalServerError)
				return
			}

			w.WriteHeader(http.StatusNoContent)
		},
	)

	log.Println("Listening at http://localhost:8080")
	log.Fatal(http.ListenAndServe(":8080", mux))

}
