package main

import (
	"log"
	"net/http"

	"github.com/alexedwards/scs/v2"
)

func main() {
	secrets, err := generateSecrets()
	if err != nil {
		log.Fatalf("failed to generate/read secrets.txt: %v", err)
	}
	mux := http.NewServeMux()
	sessions := scs.New()
	templ := makeTemplates()
	repo, err := loadRepo("../examples/private/")
	if err != nil {
		log.Fatalf("failed to load repository: %v", err)
	}

	gitBackend, issues := setupGitBackend(repo, secrets)
	if len(issues) > 0 {
		// errors from the git backend setup aren't all
		// showstoppers though they do deprive key functionality
		for _, issue := range issues {
			log.Println(issue)
		}
	}

	snapshot, snapshotErr := onPush(repo)

	server := &Server{
		repo:        repo,
		snapshot:    snapshot,
		snapshotErr: snapshotErr,
		templ:       templ,
		sessions:    sessions,
		secrets:     secrets,
	}

	// root index route (/)
	mux.HandleFunc("GET /{$}", server.handleIndex)

	// wildcard stories routes
	// /stories /stories/X/history /stories/X/r/Y
	mux.HandleFunc("GET /stories/{path...}", server.handleStories)

	mux.HandleFunc("POST /internal/kaboom", server.handleKaboom)

	mux.HandleFunc("/stories.git/", gitBackend.handleGit)

	mux.HandleFunc("GET /admin", server.handleAdmin)

	mux.HandleFunc("GET /admin/site", server.handleAdminSiteSettings)

	mux.HandleFunc("POST /admin/site", server.handAdminSiteSettingsPOST)

	mux.HandleFunc("GET /login", server.handleLogin)

	mux.HandleFunc("POST /login", server.handleLoginPOST)

	mux.HandleFunc("POST /logout", server.handleLogoutPOST)

	csrfProtection := http.NewCrossOriginProtection()

	log.Println("Listening at http://localhost:8080")
	handler := sessions.LoadAndSave(csrfProtection.Handler(mux))
	log.Fatal(http.ListenAndServe(":8080", handler))
}
