package main

import (
	"testing"
)

func TestSlugGeneration(t *testing.T) {
	tests := []struct {
		path string
		want string
	}{
		{"Hololive/Micomet.md", "hololive/micomet"},
		{"Elaina/Sou -- watashi desu!.md", "elaina/sou-watashi-desu"},
		{"Café.md", "café"},
	}

	for _, tt := range tests {
		got := createStorySlug(tt.path)
		if got != tt.want {
			t.Errorf("createStorySlug(%q) = %q; want %q", tt.path, got, tt.want)
		}
	}
}
