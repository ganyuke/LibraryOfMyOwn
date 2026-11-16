package main

import (
	"bytes"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

type Snapshot struct {
	Commit     *object.Commit
	Index      []StoryIndexEntry
	History    RevisionHistory
	IndexSlugs map[string][]StoryIndexEntry
}

type Repository struct {
	AbsoluteRepositoryPath string
	RepositoryPath         string
	RepositoryRoot         *os.Root
	Repository             *git.Repository
}

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

func loadRepo(repoPath string) (Repository, error) {
	absolutePath, err := filepath.Abs(repoPath)
	if err != nil {
		return Repository{}, fmt.Errorf("failed to resolve absolute repository path: %w", err)
	}

	repoRoot, err := os.OpenRoot(repoPath)
	if err != nil {
		return Repository{}, fmt.Errorf("failed to open repository directory: %w", err)
	}

	repo, err := openRepository(repoPath)
	if err != nil {
		repoRoot.Close()
		return Repository{}, fmt.Errorf("failed to open repository: %w", err)
	}

	return Repository{
		AbsoluteRepositoryPath: absolutePath,
		RepositoryPath:         repoPath,
		RepositoryRoot:         repoRoot,
		Repository:             repo,
	}, nil
}

func makeSnapshot(repo Repository, commit *object.Commit) (Snapshot, error) {
	files, err := listFilesPathsAtCommit(commit)
	if err != nil {
		return Snapshot{}, fmt.Errorf("failed to list files from HEAD: %w", err)
	}

	history, err := walkHistoryAtCommit(repo.RepositoryPath, commit.Hash.String(), files)
	if err != nil {
		return Snapshot{}, fmt.Errorf("failed to walk history: %w", err)
	}

	storyIndex, err := buildStoryIndex(commit, history)
	if err != nil {
		return Snapshot{}, fmt.Errorf("failed to build index: %w", err)
	}

	// slice so we can store collided names for
	// notice in the admin UI
	indexSlugs := make(map[string][]StoryIndexEntry)

	for _, story := range storyIndex {
		existing, found := indexSlugs[story.Slug]
		if found {
			log.Println("slug collision detected on:", existing[0].SourcePath, "for", story.SourcePath)
		}

		indexSlugs[story.Slug] = append(indexSlugs[story.Slug], story)
	}

	return Snapshot{
		Commit:     commit,
		Index:      storyIndex,
		History:    history,
		IndexSlugs: indexSlugs,
	}, nil
}

func onPush(repo Repository) (Snapshot, error) {
	head, err := getHeadCommit(repo.Repository)
	// swallow the refresh error. implication: server doesn't
	// get murdered but now there's stale data that you need to fix
	// case: admin decided to rename the branch that HEAD was on
	if err != nil {
		return Snapshot{}, err
	}

	snapshot, err := makeSnapshot(repo, head)
	if err != nil {
		return Snapshot{}, err
	}

	return snapshot, nil
}

func findGitInternalFilePath(repoPath string, file string) (string, error) {
	cmd := exec.Command("git", "rev-parse", "--git-path", file)
	cmd.Dir = repoPath

	output, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("failed to locate %s path: %w", file, err)
	}

	path := strings.TrimSuffix(string(output), "\n")
	if !filepath.IsAbs(path) {
		path = filepath.Join(repoPath, path)
	}

	return path, nil
}

func findPostReceivePath(repoPath string) (string, error) {
	return findGitInternalFilePath(repoPath, "hooks/post-receive")
}

func findGitDaemonExportOkPath(repoPath string) (string, error) {
	return findGitInternalFilePath(repoPath, "git-daemon-export-ok")
}

// find hook path, copy script into git repo hooks,
// if there is a file there compare checksums and complain about it
func deployPostRecieveHook(repoPath string) error {
	hookPath, err := findPostReceivePath(repoPath)
	if err != nil {
		return err
	}

	script, err := os.ReadFile("../examples/git/refresh-hook.sh")
	if err != nil {
		return fmt.Errorf("failed to read sample refresh hook: %w", err)
	}

	installed, err := os.ReadFile(hookPath)

	if err == nil {
		if !bytes.Equal(installed, script) {
			return fmt.Errorf("post-receive hook already exists and isn't ours: %s", hookPath)
		} else {
			if err := os.Chmod(hookPath, 0755); err != nil {
				return fmt.Errorf("failed to mark post-receive hook executable: %w", err)
			}
		}

		return nil
	}

	if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("failed to read post-recieve hook: %w", err)
	}

	hook, err := os.OpenFile(hookPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0755)
	if err != nil {
		return fmt.Errorf("failed to create post-receive hook: %w", err)
	}
	defer hook.Close()

	if _, err := hook.Write(script); err != nil {
		return fmt.Errorf("failed to write post-receive hook: %w", err)
	}

	// double checking that it worked
	if err := hook.Chmod(0755); err != nil {
		return fmt.Errorf("failed to mark new post-receive hook executable: %w", err)
	}

	return nil
}

func findGitHttpBackendPath() (string, error) {
	output, err := exec.Command("git", "--exec-path").Output()
	if err != nil {
		return "", fmt.Errorf("failed to locate git exec path: %w", err)
	}

	dir := strings.TrimSuffix(string(output), "\n")
	return filepath.Join(dir, "git-http-backend"), nil
}

func deployDaemonExportOk(repoPath string) error {
	exportPath, err := findGitDaemonExportOkPath(repoPath)
	if err != nil {
		return err
	}

	// touch the daemon file if it doesn't exist yet
	file, err := os.OpenFile(exportPath, os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("failed to touch git-daemon-export-ok: %w", err)
	}
	if err := file.Close(); err != nil {
		return fmt.Errorf("failed to close git-daemon-export-ok: %w", err)
	}

	return nil
}

// move the HEAD to the branch that the site should be publishing from
func setPublishedBranch(repo *git.Repository, branch string) error {
	branchRef := plumbing.NewBranchReferenceName(branch)

	if err := branchRef.Validate(); err != nil {
		return fmt.Errorf("invalid branch %q: %w", branch, err)
	}

	if _, err := repo.Reference(branchRef, true); err != nil {
		return fmt.Errorf("find branch %q: %w", branch, err)
	}

	head := plumbing.NewSymbolicReference(plumbing.HEAD, branchRef)
	if err := repo.Storer.SetReference(head); err != nil {
		return fmt.Errorf("set HEAD to branch %q: %w", branch, err)
	}

	return nil
}
