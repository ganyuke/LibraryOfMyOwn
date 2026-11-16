package main

import (
	"crypto/subtle"
	"log"
	"net/http"
	"strings"
	"sync"

	"github.com/alexedwards/scs/v2"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

type Server struct {
	repo        Repository
	snapshot    Snapshot
	snapshotErr error

	templ    Templates
	sessions *scs.SessionManager
	secrets  Secrets

	antiRefreshMutex  sync.RWMutex
	refreshWriteMutex sync.Mutex
}

// must hold the refreshWriteMutex as well before using this function
func (s *Server) refreshSnapshot() error {
	s.antiRefreshMutex.RLock()
	currentRepo := s.repo
	s.antiRefreshMutex.RUnlock()

	newSnapshot, err := onPush(currentRepo)
	if err != nil {
		return err
	}

	s.antiRefreshMutex.Lock()
	s.snapshot = newSnapshot
	s.snapshotErr = nil
	s.antiRefreshMutex.Unlock()

	return nil
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	s.antiRefreshMutex.RLock()
	currentSnapshot := s.snapshot
	currentSnapshotErr := s.snapshotErr
	s.antiRefreshMutex.RUnlock()

	if currentSnapshotErr != nil {
		log.Println("failed to start web service:", currentSnapshotErr)
		http.Error(w, "Failed to start web service. See logs for details.", http.StatusInternalServerError)
		return
	}

	renderTemplate(w, s.templ.Index, http.StatusOK, currentSnapshot.Index)
}

func (s *Server) handleStories(w http.ResponseWriter, r *http.Request) {
	s.antiRefreshMutex.RLock()
	currentSnapshot := s.snapshot
	currentRepo := s.repo
	currentSnapshotErr := s.snapshotErr
	s.antiRefreshMutex.RUnlock()

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

		renderTemplate(w, s.templ.Story, http.StatusOK, storyRendered)
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

			renderTemplate(w, s.templ.History, http.StatusOK, historyPgData)
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

			revisionPath := matched.OldPath
			if matched.NewPath != "" {
				revisionPath = matched.NewPath
			}

			renderCommit(revisionPath, commit)
			return
		}

		// not a special page
		http.NotFound(w, r)
		return
	}

	entry := entries[0]

	renderCommit(entry.SourcePath, currentSnapshot.Commit)
}

func (s *Server) handleKaboom(w http.ResponseWriter, r *http.Request) {
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

	result := subtle.ConstantTimeCompare([]byte(token), []byte(s.secrets.SystemHookPush))
	if result == 0 {
		log.Println("rejected invalid token on endpoint /internal/kaboom")
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	// prevent refreshing from stepping on each other by serializing
	s.refreshWriteMutex.Lock()
	pushErr := s.refreshSnapshot()
	s.refreshWriteMutex.Unlock()

	if pushErr != nil {
		log.Println("failed to refresh repository:", pushErr)
		http.Error(w, "Failed to refresh repository", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleAdmin(w http.ResponseWriter, r *http.Request) {
	if !s.sessions.GetBool(r.Context(), "authenticated") {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}

	renderTemplate(w, s.templ.Admin, http.StatusOK, nil)
}

func (s *Server) handleAdminSiteSettings(w http.ResponseWriter, r *http.Request) {
	if !s.sessions.GetBool(r.Context(), "authenticated") {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}

	s.antiRefreshMutex.RLock()
	currentRepo := s.repo
	s.antiRefreshMutex.RUnlock()

	data, err := makeSitePageData(currentRepo.Repository)
	if err != nil {
		log.Println("failed to hydrate page:", err)
		http.Error(w, "Failed to hydrate page. See logs for details.", http.StatusInternalServerError)
		return
	}

	renderTemplate(w, s.templ.Site, http.StatusOK, data)
}

func (s *Server) handAdminSiteSettingsPOST(w http.ResponseWriter, r *http.Request) {
	if !s.sessions.GetBool(r.Context(), "authenticated") {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}

	s.antiRefreshMutex.RLock()
	currentRepo := s.repo
	s.antiRefreshMutex.RUnlock()

	chosenBranch := r.PostFormValue("branch")

	s.refreshWriteMutex.Lock()
	branchErr := setPublishedBranch(currentRepo.Repository, chosenBranch)

	var refreshErr error
	if branchErr == nil {
		refreshErr = s.refreshSnapshot()
	}
	s.refreshWriteMutex.Unlock()

	// happy path everything worked out okay
	if branchErr == nil && refreshErr == nil {
		http.Redirect(w, r, "/admin/site", http.StatusSeeOther)
		return
	}

	// something went wrong and we need to
	// regenerate the page with all the data we had earlier
	data, siteSettingsErr := makeSitePageData(currentRepo.Repository)
	if siteSettingsErr != nil {
		log.Println("failed to hydrate page:", siteSettingsErr)
		http.Error(w, "Failed to load site settings. See logs for details.", http.StatusInternalServerError)
		return
	}

	if branchErr != nil {
		log.Println("failed to change published branch:", branchErr)
		data.Error = "Unable to change branch to " + chosenBranch
	} else {
		log.Println("changed HEAD but snapshot refresh failed:", refreshErr)
		data.Error = "Changed branch to " + chosenBranch + ", but failed to refresh snapshot. See logs for details."
	}

	renderTemplate(w, s.templ.Site, http.StatusInternalServerError, data)
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if s.sessions.GetBool(r.Context(), "authenticated") {
		http.Redirect(w, r, "/admin", http.StatusSeeOther)
		return
	}

	renderTemplate(w, s.templ.Login, http.StatusOK, LoginPageData{})
}

func (s *Server) handleLoginPOST(w http.ResponseWriter, r *http.Request) {
	password := r.PostFormValue("password")

	if !VerifySecret(password, s.secrets.AdministratorPassword) {
		log.Println("rejected login attempt on endpoint /login")
		renderTemplate(w, s.templ.Login, http.StatusUnauthorized, LoginPageData{
			Error: "Your password is wrong.",
		})
		return
	}

	if err := s.sessions.RenewToken(r.Context()); err != nil {
		log.Println("failed to renew session:", err)
		http.Error(w, "Could not create session", http.StatusInternalServerError)
		return
	}

	s.sessions.Put(r.Context(), "authenticated", true)
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) handleLogoutPOST(w http.ResponseWriter, r *http.Request) {
	if err := s.sessions.Destroy(r.Context()); err != nil {
		log.Println("failed to destroy session:", err)
		http.Error(w, "Could not log out", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, "/login", http.StatusSeeOther)
}
