package main

import (
	"crypto/subtle"
	"log"
	"net/http"
	"net/http/cgi"
	"path/filepath"
	"strings"
	"sync"

	"github.com/alexedwards/scs/v2"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

func main() {
	secrets, err := generateSecrets()
	if err != nil {
		log.Fatal("failed to generate/read secrets.txt: %w", err)
	}
	mux := http.NewServeMux()
	sessions := scs.New()
	templ := makeTemplates()
	repo, err := loadRepo("../examples/private/")
	if err != nil {
		log.Fatal("failed to load repository: %w", err)
	}
	repoName := filepath.Base(repo.AbsoluteRepositoryPath)

	err = deployDaemonExportOk(repo.RepositoryPath)
	if err != nil {
		log.Fatal("failed to create git-daemon-export-ok; unable to serve push functionality for repository:", err)
	}

	err = deployPostRecieveHook(repo.RepositoryPath)
	if err != nil {
		log.Println("warning: degraded functionality; failed to write post-recieve hook:", err)
	}

	backendPath, err := findGitHttpBackendPath()
	if err != nil {
		log.Fatal("failed to locate git-http-backend:", err)
	}

	gitBackend := &cgi.Handler{
		Path: backendPath,
		Env: []string{
			"GIT_PROJECT_ROOT=" + filepath.Dir(repo.AbsoluteRepositoryPath),
			"GIT_HOOK_SECRET=" + secrets.SystemHookPush,
			"REMOTE_USER=mina_ha",
		},
	}

	gitHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		username, password, ok := r.BasicAuth()

		if !ok || username != "git" || !VerifySecret(password, secrets.GitPassword) {
			w.Header().Set("WWW-Authenticate", `Basic realm="Git"`)
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}

		suffix := strings.TrimPrefix(r.URL.Path, "/stories.git")

		switch {
		case r.Method == http.MethodGet && suffix == "/info/refs":
		case r.Method == http.MethodPost && suffix == "/git-upload-pack":
		case r.Method == http.MethodPost && suffix == "/git-receive-pack":
		default:
			http.NotFound(w, r)
			return
		}

		// create and edit a copy of the original request
		gitRequest := r.Clone(r.Context())
		gitRequest.URL.Path = "/" + repoName + suffix
		gitRequest.URL.RawPath = ""

		gitBackend.ServeHTTP(w, gitRequest)
	})

	snapshot, snapshotErr := onPush(repo)

	var antiRefreshMutex sync.RWMutex
	var refreshWriteMutex sync.Mutex

	// must hold the refreshWriteMutex as well before using this function
	refreshSnapshot := func() error {
		antiRefreshMutex.RLock()
		currentRepo := repo
		antiRefreshMutex.RUnlock()

		newSnapshot, err := onPush(currentRepo)
		if err != nil {
			return err
		}

		antiRefreshMutex.Lock()
		snapshot = newSnapshot
		snapshotErr = nil
		antiRefreshMutex.Unlock()

		return nil
	}

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

		renderTemplate(w, templ.Index, http.StatusOK, currentSnapshot.Index)
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

			renderTemplate(w, templ.Story, http.StatusOK, storyRendered)
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

				renderTemplate(w, templ.History, http.StatusOK, historyPgData)
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

		renderCommit(entry.SourcePath, currentSnapshot.Commit)
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
			pushErr := refreshSnapshot()
			refreshWriteMutex.Unlock()

			if pushErr != nil {
				log.Println("failed to refresh repository:", pushErr)
				http.Error(w, "Failed to refresh repository", http.StatusInternalServerError)
				return
			}

			w.WriteHeader(http.StatusNoContent)
		},
	)

	mux.Handle("/stories.git/", gitHandler)

	mux.HandleFunc("GET /admin", func(w http.ResponseWriter, r *http.Request) {
		if !sessions.GetBool(r.Context(), "authenticated") {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		renderTemplate(w, templ.Admin, http.StatusOK, nil)
	})

	mux.HandleFunc("GET /admin/site", func(w http.ResponseWriter, r *http.Request) {
		if !sessions.GetBool(r.Context(), "authenticated") {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		antiRefreshMutex.RLock()
		currentRepo := repo
		antiRefreshMutex.RUnlock()

		data, err := makeSitePageData(currentRepo.Repository)
		if err != nil {
			log.Println("failed to hydrate page:", err)
			http.Error(w, "Failed to hydrate page. See logs for details.", http.StatusInternalServerError)
			return
		}

		renderTemplate(w, templ.Site, http.StatusOK, data)
	})

	mux.HandleFunc("POST /admin/site", func(w http.ResponseWriter, r *http.Request) {
		if !sessions.GetBool(r.Context(), "authenticated") {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		antiRefreshMutex.RLock()
		currentRepo := repo
		antiRefreshMutex.RUnlock()

		data, err := makeSitePageData(currentRepo.Repository)
		if err != nil {
			log.Println("failed to hydrate page:", err)
			http.Error(w, "Failed to hydrate page. See logs for details.", http.StatusInternalServerError)
			return
		}

		chosenBranch := r.PostFormValue("branch")

		refreshWriteMutex.Lock()
		err = setPublishedBranch(repo.Repository, chosenBranch)
		if err != nil {
			refreshWriteMutex.Unlock()
			data.Error = "Unable to change branch to " + chosenBranch
			renderTemplate(w, templ.Site, http.StatusInternalServerError, data)
			return
		}

		err = refreshSnapshot()
		refreshWriteMutex.Unlock()

		if err != nil {
			log.Println("changed HEAD but snapshot refresh failed:", err)
			data, err := makeSitePageData(currentRepo.Repository)
			if err != nil {
				log.Println("failed to hydrate page:", err)
				http.Error(w, "Failed to hydrate page. See logs for details.", http.StatusInternalServerError)
				return
			}
			data.Error = "Changed branch to " + chosenBranch + " successfully, but failed to refresh snapshot. See logs for details."
			renderTemplate(w, templ.Site, http.StatusInternalServerError, data)
			return
		}

		// this will refresh with new data instead of what we collected prior to the new snapshot
		http.Redirect(w, r, "/admin/site", http.StatusSeeOther)
	})

	mux.HandleFunc("GET /login", func(w http.ResponseWriter, r *http.Request) {
		if sessions.GetBool(r.Context(), "authenticated") {
			http.Redirect(w, r, "/admin", http.StatusSeeOther)
			return
		}

		renderTemplate(w, templ.Login, http.StatusOK, LoginPageData{})
	})

	mux.HandleFunc("POST /login", func(w http.ResponseWriter, r *http.Request) {
		password := r.PostFormValue("password")

		if !VerifySecret(password, secrets.AdministratorPassword) {
			log.Println("rejected login attempt on endpoint /login")
			renderTemplate(w, templ.Login, http.StatusUnauthorized, LoginPageData{
				Error: "Your password is wrong.",
			})
			return
		}

		if err := sessions.RenewToken(r.Context()); err != nil {
			log.Println("failed to renew session:", err)
			http.Error(w, "Could not create session", http.StatusInternalServerError)
			return
		}

		sessions.Put(r.Context(), "authenticated", true)
		http.Redirect(w, r, "/admin", http.StatusSeeOther)
	})

	mux.HandleFunc("POST /logout", func(w http.ResponseWriter, r *http.Request) {
		if err := sessions.Destroy(r.Context()); err != nil {
			log.Println("failed to destroy session:", err)
			http.Error(w, "Could not log out", http.StatusInternalServerError)
			return
		}

		http.Redirect(w, r, "/login", http.StatusSeeOther)
	})

	csrfProtection := http.NewCrossOriginProtection()

	log.Println("Listening at http://localhost:8080")
	handler := sessions.LoadAndSave(csrfProtection.Handler(mux))
	log.Fatal(http.ListenAndServe(":8080", handler))
}
