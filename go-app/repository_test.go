package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

func TestHistoryAfterPathReuse(t *testing.T) {
	dir := t.TempDir()

	repo, err := git.PlainInit(dir, false)
	if err != nil {
		t.Fatal(err)
	}

	worktree, err := repo.Worktree()
	if err != nil {
		t.Fatal(err)
	}

	err = os.WriteFile(filepath.Join(dir, "A.md"), []byte("Original story\n"), 0644)
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("A.md")
	if err != nil {
		t.Fatal(err)
	}

	originalHash, err := worktree.Commit("Create original A", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	err = os.Rename(filepath.Join(dir, "A.md"), filepath.Join(dir, "B.md"))
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("A.md")
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("B.md")
	if err != nil {
		t.Fatal(err)
	}

	renameHash, err := worktree.Commit("Rename A to B", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	err = os.WriteFile(filepath.Join(dir, "A.md"), []byte("New story\n"), 0644)
	if err != nil {
		t.Fatal(err)
	}
	_, err = worktree.Add("A.md")
	if err != nil {
		t.Fatal(err)
	}

	newHash, err := worktree.Commit("Create new A", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	head, err := getHeadCommit(repo)
	if err != nil {
		t.Fatal(err)
	}
	history, err := walkHistory(repo, head)
	if err != nil {
		t.Fatal(err)
	}
	if got := len(history["A.md"]); got != 1 {
		t.Errorf("A.md has %d revisions; want 1", got)
	}

	if got := len(history["B.md"]); got != 2 {
		t.Errorf("B.md has %d revisions; want 2", got)
	}

	hasRevision := func(revisions []FileRevision, hash string) bool {
		for _, revision := range revisions {
			if revision.Hash == hash {
				return true
			}
		}
		return false
	}

	if !hasRevision(history["A.md"], newHash.String()) {
		t.Error("A.md is missing new commit")
	}
	if !hasRevision(history["B.md"], originalHash.String()) {
		t.Error("B.md is missing original creation commit")
	}
	if !hasRevision(history["B.md"], renameHash.String()) {
		t.Error("B.md is missing rename commit")
	}
}

func TestHistoryAfterMerge(t *testing.T) {
	dir := t.TempDir()

	repo, err := git.PlainInit(dir, false)
	if err != nil {
		t.Fatal(err)
	}

	worktree, err := repo.Worktree()
	if err != nil {
		t.Fatal(err)
	}

	err = os.WriteFile(filepath.Join(dir, "README.txt"), []byte("Base\n"), 0644)
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("README.txt")
	if err != nil {
		t.Fatal(err)
	}

	baseHash, err := worktree.Commit("Create new A", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	err = worktree.Checkout(&git.CheckoutOptions{
		Branch: plumbing.NewBranchReferenceName("dev"),
		Create: true,
	})
	if err != nil {
		t.Fatal(err)
	}

	err = os.WriteFile(filepath.Join(dir, "Story.md"), []byte("First version\n"), 0644)
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("Story.md")
	if err != nil {
		t.Fatal(err)
	}

	creationHash, err := worktree.Commit("Create story on dev", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	err = os.WriteFile(filepath.Join(dir, "Story.md"), []byte("Second version\n"), 0644)
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("Story.md")
	if err != nil {
		t.Fatal(err)
	}

	editHash, err := worktree.Commit("Edit dev draft", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	err = worktree.Checkout(&git.CheckoutOptions{
		Branch: plumbing.NewBranchReferenceName("master"),
	})
	if err != nil {
		t.Fatal(err)
	}

	// merge dev back into main
	err = os.WriteFile(filepath.Join(dir, "Story.md"), []byte("Second version\n"), 0644)
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Add("Story.md")
	if err != nil {
		t.Fatal(err)
	}

	_, err = worktree.Commit("Merge dev", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
		Parents: []plumbing.Hash{baseHash, editHash},
	})
	if err != nil {
		t.Fatal(err)
	}

	head, err := getHeadCommit(repo)
	if err != nil {
		t.Fatal(err)
	}

	history, err := walkHistory(repo, head)
	if err != nil {
		t.Fatal(err)
	}

	hasRevision := func(hash string) bool {
		for _, revision := range history["Story.md"] {
			if revision.Hash == hash {
				return true
			}
		}
		return false
	}

	if !hasRevision(creationHash.String()) {
		t.Error("Story.md is missing its creation commit")
	}

	if !hasRevision(editHash.String()) {
		t.Error("Story.md is missing its edit commit")
	}
}
