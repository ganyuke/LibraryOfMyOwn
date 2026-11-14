package main

import (
	"context"
	"path/filepath"
	"time"

	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing/object"
)

type FileRevision struct {
	Hash   string
	Author string
	Date   time.Time
	Path   string
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

// for each commit in branch history:
//    find paths changed by this commit
//    for each changed story:
//        add this commit to that story's history
// for each file
// 	  find earliest recorded commit
//    set file author as earliest commit's author
//    set file update date as latest commit's update date

func walkHistory(repo *git.Repository, head *object.Commit) (RevisionHistory, error) {
	revisionList := make(RevisionHistory)
	tracked := make(map[string]string)

	commits, err := repo.Log(&git.LogOptions{
		From: head.Hash,
	})
	if err != nil {
		return nil, err
	}
	defer commits.Close()

	paths, err := listFilesPathsAtCommit(head)
	if err != nil {
		return nil, err
	}

	for _, path := range paths {
		tracked[path] = path
	}

	err = commits.ForEach(func(commit *object.Commit) error {
		// root commit
		if commit.NumParents() == 0 {
			files, err := listFilesPathsAtCommit(commit)
			if err != nil {
				return err
			}

			for _, path := range files {
				currentPath, found := tracked[path]
				if !found {
					continue
				}

				revisionList[currentPath] = append(revisionList[currentPath], FileRevision{
					Hash:   commit.Hash.String(),
					Author: commit.Author.Name,
					Date:   commit.Committer.When,
					Path:   path,
				})
			}

			return nil
		}

		parent, err := commit.Parent(0)
		if err != nil {
			return err
		}

		parentTree, err := parent.Tree()
		if err != nil {
			return err
		}

		currentTree, err := commit.Tree()
		if err != nil {
			return err
		}

		changes, err := object.DiffTreeWithOptions(
			context.Background(),
			parentTree,
			currentTree,
			object.DefaultDiffTreeOptions,
		)
		if err != nil {
			return err
		}

		for _, change := range changes {
			// "" -> to - file creation
			// from -> to - file modification
			// from -> "" - file deletion
			oldPath := change.From.Name
			newPath := change.To.Name
			if newPath == "" {
				continue
			}

			currentPath, trackedFile := tracked[change.To.Name]
			if !trackedFile {
				continue
			}

			switch {
			case oldPath == "":
				// found file's original file path
				// stop tracking the history for this file
				delete(tracked, newPath)

			case oldPath != newPath:
				// add to the HEAD's current path
				delete(tracked, newPath)
				tracked[oldPath] = currentPath
			}

			revisionList[currentPath] = append(revisionList[currentPath], FileRevision{
				Hash:   commit.Hash.String(),
				Author: commit.Author.Name,
				Date:   commit.Committer.When,
				Path:   newPath,
			})
		}

		return nil
	})
	if err != nil {
		return nil, err
	}

	return revisionList, err
}
