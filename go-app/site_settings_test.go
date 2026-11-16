package main

import (
	"path/filepath"
	"testing"
)

func TestSettingsSaveAndLoad(t *testing.T) {
	path := filepath.Join(t.TempDir(), "site_settings.json")

	settings := SiteSettings{
		PublishedPaths: map[string]bool{
			"Hololive/Micomet.md": true,
		},
	}

	if err := saveSettings(path, settings); err != nil {
		t.Fatalf("save settings: %v", err)
	}

	loaded, err := generateSettings(path)
	if err != nil {
		t.Fatalf("load settings: %v", err)
	}

	if !loaded.PublishedPaths["Hololive/Micomet.md"] {
		t.Error("expected Hololive/Micomet.md to remain published")
	}
}

func TestSettingsMissingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "site_settings.json")

	settings, err := generateSettings(path)
	if err != nil {
		t.Fatalf("load missing settings: %v", err)
	}

	if settings.PublishedPaths == nil {
		t.Fatal("expected initialized published paths map")
	}

	if len(settings.PublishedPaths) != 0 {
		t.Errorf("expected no published paths, got %v", settings.PublishedPaths)
	}
}
