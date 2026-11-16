package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

type SiteSettings struct {
	PublishedPaths map[string]bool
}

func generateSettings(path string) (SiteSettings, error) {
	settings, err := os.OpenFile(path, os.O_RDONLY, 0)
	if errors.Is(err, os.ErrNotExist) {
		return SiteSettings{
			PublishedPaths: make(map[string]bool),
		}, nil
	} else if err != nil {
		// could not read file for some reason
		return SiteSettings{}, fmt.Errorf("failed to open/create %s file: %w", path, err)
	} else {
		// normal operation
		defer settings.Close()

		var loaded SiteSettings

		if err := json.NewDecoder(settings).Decode(&loaded); err != nil {
			return SiteSettings{}, fmt.Errorf("failed to decode site settings: %w", err)
		}

		if loaded.PublishedPaths == nil {
			loaded.PublishedPaths = make(map[string]bool)
		}

		return loaded, nil
	}
}

func saveSettings(path string, settings SiteSettings) error {
	data, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return fmt.Errorf("failed to encode site settings: %w", err)
	}

	// figured that it might not be a good thing to create a temporary file
	// that someone could mess with from a hardcoded path so let's use
	// a randomly generated file. CreateTemp uses 600 so it's also restricted
	// with Unix permisisons already
	tmp, err := os.CreateTemp(filepath.Dir(path), "site-settings-*.tmp")
	if err != nil {
		return fmt.Errorf("failed to create temporary settings file: %w", err)
	}
	defer os.Remove(tmp.Name())

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return fmt.Errorf("failed to write temporary settings: %w", err)
	}

	if err := tmp.Close(); err != nil {
		return fmt.Errorf("failed to close temporary settings: %w", err)
	}

	if err := os.Rename(tmp.Name(), path); err != nil {
		return fmt.Errorf("failed to replace site settings: %w", err)
	}

	return nil
}
