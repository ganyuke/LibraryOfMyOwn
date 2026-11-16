package main

import (
	"bytes"
	"crypto/rand"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
)

type Secrets struct {
	SystemHookPush        string
	AdministratorPassword string
	GitPassword           string
}

func generateSecrets(path string) (Secrets, error) {
	secrets, err := os.OpenFile(path, os.O_RDONLY, 0)
	if errors.Is(err, os.ErrNotExist) {
		// generate new secrets
		hook, admin, git := rand.Text(), rand.Text(), rand.Text()

		adminHash, err := HashSecret(admin)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to hash admin password: %w", err)
		}

		gitHash, err := HashSecret(git)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to hash git password: %w", err)
		}

		err = os.WriteFile(path, []byte(strings.Join([]string{hook, adminHash, gitHash}, "\n")), 0600)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to write %s: %w", path, err)
		}

		log.Println("---- START OF GENERATED SECRETS ----")
		log.Println("Admin password:", admin)
		log.Println("Git password:", git)
		log.Println("---- END OF GENERATED SECRETS ----")
		log.Println("Make sure you CHANGE these when you log in and SAVE them into your password manager! They will not be shown again!")

		return Secrets{
			SystemHookPush:        hook,
			AdministratorPassword: adminHash,
			GitPassword:           gitHash,
		}, nil
	} else if err != nil {
		// could not read file for some reason
		return Secrets{}, fmt.Errorf("failed to open/create secrest file: %w", err)
	} else {
		// normal operation
		defer secrets.Close()

		content, err := io.ReadAll(secrets)
		if err != nil {
			return Secrets{}, fmt.Errorf("failed to read secrets file: %w", err)
		}

		lines := bytes.Split(content, []byte("\n"))
		lineCount := len(lines)
		if lineCount != 3 {
			return Secrets{}, fmt.Errorf(
				"malformed secrets file: expected 3 lines of secrets, got %d",
				lineCount,
			)
		}

		return Secrets{
			SystemHookPush:        string(lines[0]),
			AdministratorPassword: string(lines[1]),
			GitPassword:           string(lines[2]),
		}, nil
	}
}
