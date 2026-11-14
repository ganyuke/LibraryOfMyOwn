package main

import (
	"testing"
)

func TestSlugGeneration(t *testing.T) {
	tests := []struct {
		path string
		want string
	}{
		{"Mainline/My Story.md", "mainline/my-story"},
		{"Workshop/Hello -- World!.md", "workshop/hello-world"},
		{"Café.md", "café"},
	}

	for _, tt := range tests {
		got := createStorySlug(tt.path)
		if got != tt.want {
			t.Errorf("createStorySlug(%q) = %q; want %q", tt.path, got, tt.want)
		}
	}
}
