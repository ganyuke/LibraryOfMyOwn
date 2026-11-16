package main

import (
	"fmt"
	"net/http"
	"net/http/cgi"
	"path/filepath"
	"strings"
)

type GitBackendContext struct {
	secrets  Secrets
	handler  *cgi.Handler
	repoName string
}

// deploy every needed for the backend git manipulation to work
// errors returned by this function are nonfatal
func setupGitBackend(repo Repository, secrets Secrets) (*GitBackendContext, []error) {
	repoName := filepath.Base(repo.AbsoluteRepositoryPath)

	var errorList []error

	err := deployDaemonExportOk(repo.RepositoryPath)
	if err != nil {
		errorList = append(errorList,
			fmt.Errorf("failed to create git-daemon-export-ok; unable to serve push functionality for repository: %w", err),
		)
	}

	err = deployPostRecieveHook(repo.RepositoryPath)
	if err != nil {
		errorList = append(errorList,
			fmt.Errorf("warning: degraded functionality; failed to write post-recieve hook: %w", err),
		)
	}

	handler, err := initBackend(repo.AbsoluteRepositoryPath, secrets.SystemHookPush)
	if err != nil {
		errorList = append(errorList, err)
	}

	return &GitBackendContext{
		secrets:  secrets,
		handler:  handler,
		repoName: repoName,
	}, errorList
}

func initBackend(absRepoPath string, hookSecret string) (*cgi.Handler, error) {
	backendPath, err := findGitHttpBackendPath()
	if err != nil {
		return nil, fmt.Errorf("failed to locate git-http-backend: %w", err)
	}

	return &cgi.Handler{
		Path: backendPath,
		Env: []string{
			"GIT_PROJECT_ROOT=" + filepath.Dir(absRepoPath),
			"GIT_HOOK_SECRET=" + hookSecret,
			"REMOTE_USER=mina_ha",
		},
	}, nil
}

func (c *GitBackendContext) handleGit(w http.ResponseWriter, r *http.Request) {
	if c.handler == nil {
		http.Error(w, "Git service unavailable. See server logs.", http.StatusServiceUnavailable)
		return
	}

	username, password, ok := r.BasicAuth()

	if !ok || username != "git" || !VerifySecret(password, c.secrets.GitPassword) {
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
	gitRequest.URL.Path = "/" + c.repoName + suffix
	gitRequest.URL.RawPath = ""

	c.handler.ServeHTTP(w, gitRequest)
}
