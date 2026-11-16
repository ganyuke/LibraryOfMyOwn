package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/config"
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
	paths, err := listFilesPathsAtCommit(head)
	if err != nil {
		t.Fatal(err)
	}
	history, err := walkHistoryAtCommit(dir, head.Hash.String(), paths)
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

	paths, err := listFilesPathsAtCommit(head)
	if err != nil {
		t.Fatal(err)
	}
	history, err := walkHistoryAtCommit(dir, head.Hash.String(), paths)
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

func TestSetPublishedBranch(t *testing.T) {
	dir := t.TempDir()

	repo, err := git.PlainInit(dir, false)
	if err != nil {
		t.Fatal(err)
	}

	worktree, err := repo.Worktree()
	if err != nil {
		t.Fatal(err)
	}

	if err := os.WriteFile(filepath.Join(dir, "story.txt"), []byte("Hello\n"), 0644); err != nil {
		t.Fatal(err)
	}

	if _, err := worktree.Add("story.txt"); err != nil {
		t.Fatal(err)
	}

	hash, err := worktree.Commit("First story", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	mainRef := plumbing.NewBranchReferenceName("main")
	if err := repo.Storer.SetReference(plumbing.NewHashReference(mainRef, hash)); err != nil {
		t.Fatal(err)
	}

	if err := setPublishedBranch(repo, "main"); err != nil {
		t.Fatal(err)
	}

	head, err := repo.Reference(plumbing.HEAD, false) // false to prevent resolving HEAD's reference
	if err != nil {
		t.Fatal(err)
	}

	if head.Target() != mainRef {
		t.Errorf("HEAD points to %s; want %s", head.Target(), mainRef)
	}

	// test failure case: nonexistent branch
	if err := setPublishedBranch(repo, "missing"); err == nil {
		t.Error("expected an error for a missing branch")
	}

	head, err = repo.Reference(plumbing.HEAD, false)
	if err != nil {
		t.Fatal(err)
	}

	if head.Target() != mainRef {
		t.Errorf("HEAD changed to %s; want it to remain %s", head.Target(), mainRef)
	}

	// test failure case: invalid branch
	if err := setPublishedBranch(repo, "/feature"); err == nil {
		t.Error("expected an error for an invalid-named branch")
	}

	head, err = repo.Reference(plumbing.HEAD, false)
	if err != nil {
		t.Fatal(err)
	}

	if head.Target() != mainRef {
		t.Errorf("HEAD changed to %s; want it to remain %s", head.Target(), mainRef)
	}
}

// situation on startup where push happens but HEAD is now pointing at nothing
// expected behavior: return error (and for app side, don't take down application)
func TestSnapshotWithUnbornHead(t *testing.T) {
	dir := t.TempDir()

	_, err := git.PlainInit(dir, true)
	if err != nil {
		t.Fatal(err)
	}

	repo, err := loadRepo(dir)
	if err != nil {
		t.Fatalf("open empty bare repo: %v", err)
	}
	defer repo.RepositoryRoot.Close()

	_, err = onPush(repo)
	if err == nil {
		t.Error("expected error: HEAD has no commit")
	}
}

// HEAD is on master but you are actually a main fan
// expected behavior: moving the HEAD to an existing branch
// should regenerate the snapshot correctly
func TestSnapshotWithRecoveredHead(t *testing.T) {
	dir := t.TempDir()

	_, err := git.PlainInit(dir, true)
	if err != nil {
		t.Fatal(err)
	}

	repo, err := loadRepo(dir)
	if err != nil {
		t.Fatalf("open empty bare repo: %v", err)
	}
	defer repo.RepositoryRoot.Close()

	_, err = onPush(repo)
	if err == nil {
		t.Error("expected error: HEAD has no commit")
	}

	// second repo to push a commit into bare repo
	workDir := t.TempDir()

	workRepo, err := git.PlainInit(workDir, false)
	if err != nil {
		t.Fatal(err)
	}

	worktree, err := workRepo.Worktree()
	if err != nil {
		t.Fatal(err)
	}

	if err := os.WriteFile(filepath.Join(workDir, "story.md"), []byte("# Hello\n"), 0644); err != nil {
		t.Fatal(err)
	}

	if _, err := worktree.Add("story.md"); err != nil {
		t.Fatal(err)
	}

	hash, err := worktree.Commit("First story", &git.CommitOptions{
		Author: &object.Signature{
			Name:  "Original Author",
			Email: "original@example.com",
			When:  time.Now(),
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	mainRef := plumbing.NewBranchReferenceName("main")
	if err := workRepo.Storer.SetReference(plumbing.NewHashReference(mainRef, hash)); err != nil {
		t.Fatal(err)
	}

	_, err = workRepo.CreateRemote(&config.RemoteConfig{
		Name: "origin",
		URLs: []string{dir},
	})
	if err != nil {
		t.Fatalf("create remote: %v", err)
	}

	err = workRepo.Push(&git.PushOptions{
		RemoteName: "origin",
		RefSpecs: []config.RefSpec{
			"refs/heads/main:refs/heads/main",
		},
	})
	if err != nil {
		t.Fatalf("push main to bare repo: %v", err)
	}

	// check that HEAD is pointing to a non-existent master still
	if _, err := onPush(repo); err == nil {
		t.Fatal("expected snapshot to fail before switching HEAD to main")
	}

	// update the HEAD to point at newly-existing main branch
	// and check if that now works
	if err := setPublishedBranch(repo.Repository, "main"); err != nil {
		t.Fatalf("set published branch: %v", err)
	}

	snapshot, err := onPush(repo)
	if err != nil {
		t.Fatalf("build snapshot after switching to main: %v", err)
	}

	if snapshot.Commit.Hash.String() != hash.String() {
		t.Fatalf("snapshot hash does not meet commit hash: expected %s, got %s", hash.String(), snapshot.Commit.Hash.String())
	}

}
