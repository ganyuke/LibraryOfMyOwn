package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGitBackendUnavailable(t *testing.T) {
	backend := &GitBackendContext{}

	request := httptest.NewRequest(http.MethodGet, "/stories.git/info/refs", nil)
	response := httptest.NewRecorder()

	backend.handleGit(response, request)

	if response.Code != http.StatusServiceUnavailable {
		t.Errorf("status = %d; want %d", response.Code, http.StatusServiceUnavailable)
	}
}
