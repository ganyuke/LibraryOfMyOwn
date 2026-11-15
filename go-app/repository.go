package main

import (
	"bytes"
	"fmt"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"

	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing/object"
)

type FileRevision struct {
	Hash         string
	Date         time.Time
	Author       string
	Summary      string
	RenameStatus string
	OldPath      string
	NewPath      string
}

type RevisionHistory = map[string][]FileRevision

func openRepository(path string) (*git.Repository, error) {
	return git.PlainOpen(path)
}

func getHeadCommit(repo *git.Repository) (*object.Commit, error) {
	head, err := repo.Head()
	if err != nil {
		return nil, err
	}

	return repo.CommitObject(head.Hash())
}

func listFilesPathsAtCommit(commit *object.Commit) ([]string, error) {
	files, err := commit.Files()
	if err != nil {
		return nil, err
	}
	defer files.Close()

	var paths []string

	err = files.ForEach(func(file *object.File) error {
		if filepath.Ext(file.Name) == ".md" || filepath.Ext(file.Name) == ".fountain" || filepath.Ext(file.Name) == ".txt" {
			paths = append(paths, file.Name)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}

	return paths, nil
}

func readFileAtCommit(commit *object.Commit, path string) ([]byte, error) {
	file, err := commit.File(path)
	if err != nil {
		return nil, err
	}

	content, err := file.Contents()
	if err != nil {
		return nil, err
	}

	return []byte(content), nil
}

func walkHistoryInRepo(repo Repository) (RevisionHistory, error) {
	return walkHistoryAtCommit(repo.RepositoryPath, repo.HeadCommit.Hash.String(), repo.CurrentFilePaths)
}

func walkHistoryAtCommit(repoPath string, commitHash string, paths []string) (RevisionHistory, error) {
	revisionList := make(RevisionHistory)

	for _, path := range paths {
		// apparently Gitea just shells out to git log --follow
		// and go-git itself doesn't support that directly out-of-the-box
		// and I'm a lazy bum so I'll just do what Gitea does
		command := exec.Command(
			"git", "--no-pager", "log",
			"--follow",
			"-z",
			"--format=%x00%H%x00%ct%x00%an%x00%s",
			"--name-status",
			commitHash,
			"--",
			path,
		)
		command.Dir = repoPath

		output, err := command.Output()
		if err != nil {
			return nil, err
		}

		// git log output format:
		// 0 empty field
		// 1 commit hash
		// 2 commit date (in Unix seconds)
		// 3 author
		// 4 commit message summary
		// 5 commit status (R, C, A etc. beginning with \n)
		// 6 old path
		// 7 new path (optionally) / 7 empty field (next record)
		// 8 (if 7 exists) empty field (next record)
		parts := bytes.Split(output, []byte{0})

		for i := 0; i < len(parts)-1; i += 7 {
			if i+7 >= len(parts) {
				return nil, fmt.Errorf("incomplete git history record for %q", path)
			}

			hash := parts[i+1]
			timestamp := parts[i+2]
			author := parts[i+3]
			commitSummary := parts[i+4]
			commitStatus := parts[i+5]
			oldPath := parts[i+6]
			newPath := parts[i+7]

			commitStatus = bytes.TrimSpace(commitStatus) // strip the newline

			if len(newPath) != 0 {
				i++
			}

			unix, err := strconv.ParseInt(string(timestamp), 10, 64)
			if err != nil {
				return nil, err
			}
			t := time.Unix(unix, 0)

			revisionList[path] = append(revisionList[path], FileRevision{
				Hash:         string(hash),
				Date:         t,
				Author:       string(author),
				Summary:      string(commitSummary),
				RenameStatus: string(commitStatus),
				OldPath:      string(oldPath),
				NewPath:      string(newPath),
			})

			// stop looking for revisions past the file's creation commit
			// reasoning: shouldn't expose the history of previously files
			// on that path, which git log will do if we don't stop it
			// scenario: you use draft.txt for your stories and delete it after
			// you make it into a real story and you don't want to expose that
			if bytes.Equal(commitStatus, []byte("A")) {
				break
			}
		}
	}

	return revisionList, nil
}
