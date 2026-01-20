# Spec Processor Research: Efficient Git Cloning and Merging

## Objective

Design and implement a processor (`spec_processor.py`) that:
- Takes a `ProjectSpec` containing multiple `ModuleSpec`s
- Each `ModuleSpec` has multiple `ModuleOrigin` entries (with `remote`, `origin`, and `type`)
- For each `ModuleSpec`, clone/fetch each origin and merge them together
- Minimize data transfer from Git repositories (bandwidth, storage, latency)

## Key Considerations

### What "Minimal Data" Means

1. **Shallow clones**: Use `--depth <n>` to limit history fetched
2. **Single branch clones**: Only fetch required branches/refs, not all branches
3. **Pull request handling**: Fetch PR refs (`refs/pull/{pr_id}/head`) without cloning entire forks
4. **Merge base requirements**: Need enough common history to compute merge-base for proper merging
5. **Remote reuse**: If multiple `ModuleOrigin`s use the same remote, reuse the same clone

## Git Techniques for Minimal Data Transfer

### 1. Shallow Clones

**Command**: `git clone --depth=1 --single-branch -b <branch> <remote_url>`

- `--depth=1`: Fetch only the latest commit (no history)
- `--single-branch`: Only fetch the specified branch
- `-b <branch>`: Clone specific branch

**Use case**: When you only need the latest state of a branch and don't need history.

**Limitations**: 
- Cannot perform operations requiring history (blame, log, etc.)
- May not have enough history for merge-base calculations
- Can be deepened later with `git fetch --deepen=<n>` if needed

### 2. Fetching Specific Branches

**Command**: `git fetch origin <branch>`

- Only fetches the specified branch
- Can be combined with `--depth` for shallow fetch
- More efficient than fetching all branches

### 3. Pull Request References

**GitHub PR Refs**: `refs/pull/{pr_id}/head`

**Fetching PRs**:
```bash
# Fetch a specific PR
git fetch origin refs/pull/123/head:pr/123

# Or configure remote to fetch all PRs
git config --add remote.origin.fetch "+refs/pull/*/head:refs/remotes/origin/pr/*"
git fetch origin
```

**Advantages**:
- Don't need to clone contributor's fork
- PR refs are stable even if branch is deleted
- Can fetch multiple PRs from same remote efficiently

### 4. Sparse Checkout (Directory Filtering)

**Sparse checkout** allows checking out only specific directories/paths from a repository, dramatically reducing working tree size.

**Basic Setup**:
```bash
# After cloning, enable sparse checkout
git sparse-checkout init --cone
git sparse-checkout set <path1> <path2> <path3>
```

**Cone Mode** (recommended):
- `--cone`: Uses pattern matching optimized for directory trees
- Automatically includes parent directories
- More efficient for large repositories

**Non-Cone Mode**:
- More flexible pattern matching
- Can use glob patterns
- Better for scattered paths

**Combined with Shallow Clone**:
```bash
# Clone shallowly with no checkout
git clone --no-checkout --depth=1 --single-branch -b <branch> <url> <path>
cd <path>
# Enable sparse checkout
git sparse-checkout init --cone
# Set only the modules we need
git sparse-checkout set <module1> <module2> <module3>
# Now checkout
git checkout <branch>
```

**Use Case**: When `ModuleSpec.modules` contains a list like `['account', 'sale', 'stock']`, only these directories are checked out.

**Advantages**:
- Minimal working tree size
- Faster checkout operations
- Reduces disk I/O
- Can be combined with shallow clones and partial clones

**Limitations**:
- Still downloads full object database (unless combined with partial clone)
- History operations may still access all objects
- Some Git operations may fail if paths aren't checked out

### 5. Partial Clones (Advanced)

**Blobless clone**: `git clone --filter=blob:none <repo>`
- Downloads commits and trees but defers blob downloads
- Useful for very large repositories
- **Combined with sparse checkout**: Only fetches blobs for checked-out paths

**Tree filtering**: `git clone --filter=tree:<depth> <repo>`
- Limits tree depth fetched

**Combined Approach** (Most Efficient):
```bash
# Clone with blob filter and sparse checkout
git clone --no-checkout --filter=blob:none --depth=1 --single-branch -b <branch> <url> <path>
cd <path>
git sparse-checkout init --cone
git sparse-checkout set <module1> <module2>
git checkout <branch>
```

**Note**: Requires Git 2.19+ and server support

### 6. Increasing Shallow Depth for Merges

**Problem**: Shallow clones (depth=1) may not have enough history to compute merge-base

**Solution**: 
```bash
git fetch --deepen=<n>  # Increase depth by n commits
git fetch --unshallow    # Convert to full clone (if needed)
```

## Proposed Processor Architecture

### High-Level Workflow

```
For each ModuleSpec in ProjectSpec:
  1. Group origins by remote URL
  2. For each unique remote:
     - If not cloned: Clone shallowly (depth=1, single-branch, --no-checkout)
     - If already cloned: Reuse existing clone
  3. Enable sparse checkout with ModuleSpec.modules paths
  4. For each origin in ModuleSpec:
     - Fetch the required ref (branch or PR)
     - Store reference locally
  5. Merge all origins:
     - Use first origin as base branch
     - Merge subsequent origins in order
     - Handle conflicts appropriately
  6. Result: Single merged workspace per module with only needed directories
```

### Detailed Steps

#### Step 1: Clone/Fetch Remotes Minimally with Sparse Checkout

**For Branch Origins**:
```bash
# Clone without checkout, with blob filter for efficiency
git clone --no-checkout --filter=blob:none --depth=1 --single-branch -b <branch> <remote_url> <cache_path>
cd <cache_path>
# Enable sparse checkout with modules from ModuleSpec.modules
git sparse-checkout init --cone
git sparse-checkout set <module1> <module2> <module3>  # From ModuleSpec.modules
# Now checkout only the sparse paths
git checkout <branch>
```

**For PR Origins**:
```bash
# Clone default branch shallowly without checkout
git clone --no-checkout --filter=blob:none --depth=1 --single-branch -b main <remote_url> <cache_path>
cd <cache_path>
# Fetch the PR ref
git fetch origin refs/pull/{pr_id}/head:pr/{pr_id}
# Enable sparse checkout
git sparse-checkout init --cone
git sparse-checkout set <module1> <module2> <module3>  # From ModuleSpec.modules
# Checkout the PR
git checkout pr/{pr_id}
```

**Reusing Existing Clones**:
- Check if remote URL already cloned
- If yes, fetch additional branches/PRs into existing clone
- Update sparse checkout paths if new modules needed: `git sparse-checkout add <new-module>`
- Avoids redundant data transfer

**Key Point**: The `ModuleSpec.modules` list determines which directories are checked out via sparse checkout. This dramatically reduces working tree size.

#### Step 2: Fetch All Required Refs

For each `ModuleOrigin`:
- If `type == BRANCH`: `git fetch origin <branch>:<local-branch>`
- If `type == PR`: `git fetch origin refs/pull/{pr_id}/head:pr/{pr_id}`

**Note**: When fetching into a sparse checkout, only the paths specified in sparse checkout will be updated in the working tree. The full ref is still fetched, but working tree remains minimal.

#### Step 3: Merge Origins

```bash
# Start with first origin as base
git checkout <base-branch>

# Merge each subsequent origin
for origin in remaining_origins:
    git merge <origin-local-ref>
    # Handle conflicts if needed
```

**Merge Strategy Options**:
- `--ff-only`: Only fast-forward merges (fails if not possible)
- `--no-ff`: Always create merge commit
- `-X ours` / `-X theirs`: Conflict resolution strategy

**Sparse Checkout During Merge**:
- Merges work normally with sparse checkout
- Only files in sparse checkout paths are updated in working tree
- Conflicts are only visible for checked-out paths
- Full merge history is preserved in Git database

## Implementation Considerations

### Folder Structure

```
<workdir>/
  ├── _cache/              # Cached remote clones
  │   ├── <remote-hash>/   # One clone per unique remote URL
  │   └── ...
  └── modules/             # Processed modules
      ├── <module-name>/   # Merged workspace per module
      └── ...
```

### Remote Caching Strategy

- Hash remote URL to create unique cache key
- Check if cache exists before cloning
- Reuse cache for multiple origins from same remote
- Optional: Cache expiration/cleanup mechanism

### Handling Merge Base Issues

**Problem**: Shallow clones may not have common ancestor for merge

**Solutions**:
1. Start with `--depth=10` instead of `--depth=1` (more conservative)
2. Dynamically deepen if merge-base fails: `git fetch --deepen=10`
3. Use `git merge-base --is-ancestor` to check if merge is possible

### Conflict Handling

**Options**:
1. **Fail fast**: Abort on any conflict (safest)
2. **Prefer base**: Use `-X ours` to prefer base branch
3. **Prefer latest**: Use `-X theirs` to prefer merged branch
4. **Interactive**: Prompt user for resolution (not suitable for automation)

**Recommendation**: Start with fail-fast, make configurable later

### Ordering of Origins

- First origin typically becomes the base branch
- Subsequent origins merged in order
- PRs usually merged after base branches
- Order matters for conflict resolution

## Git Commands Reference

| Scenario | Command |
|----------|---------|
| Clone branch shallow | `git clone --depth=1 --single-branch -b <branch> <url>` |
| Clone with sparse checkout | `git clone --no-checkout <url> && git sparse-checkout init --cone && git sparse-checkout set <paths>` |
| Clone optimized (shallow + sparse + blobless) | `git clone --no-checkout --filter=blob:none --depth=1 --single-branch -b <branch> <url>` |
| Enable sparse checkout | `git sparse-checkout init --cone` |
| Set sparse paths | `git sparse-checkout set <path1> <path2> <path3>` |
| Add sparse path | `git sparse-checkout add <path>` |
| Fetch specific branch | `git fetch origin <branch>:<local-branch>` |
| Fetch PR ref | `git fetch origin refs/pull/{id}/head:pr/{id}` |
| Increase shallow depth | `git fetch --deepen=<n>` |
| Check merge-base | `git merge-base <ref1> <ref2>` |
| Merge branches | `git merge <branch>` |
| Fast-forward only | `git merge --ff-only <branch>` |

## Edge Cases & Challenges

### 1. Merge Base Unavailable

**Problem**: Shallow clone doesn't have common ancestor

**Solution**: 
- Increase depth: `git fetch --deepen=20`
- Or start with deeper clone: `--depth=10`

### 2. PR Ref Not Found

**Problem**: PR may have been closed/deleted

**Solution**:
- Check if ref exists before fetching
- Provide clear error message
- Consider fallback to branch name if available

### 3. Multiple Origins from Same Remote

**Opportunity**: Reuse single clone, fetch multiple refs

**Implementation**: Group origins by remote URL, clone once, fetch all needed refs

### 4. Large Repositories

**Problem**: Even shallow clones can be large

**Solutions**:
- Use partial clones (`--filter=blob:none`)
- **Use sparse checkout** to only checkout directories in `ModuleSpec.modules`
- Combine shallow clone + sparse checkout + blob filter for maximum efficiency
- Consider Git LFS handling

**Example**: For a repo with 1000 modules, if `ModuleSpec.modules` only lists 5 modules, sparse checkout reduces working tree by ~99.5%

### 5. Network Failures

**Problem**: Partial fetches leave inconsistent state

**Solution**:
- Use atomic operations where possible
- Clean up on failure
- Retry mechanism with exponential backoff

## Python Implementation Approach

### Using subprocess

```python
import subprocess
import os
from pathlib import Path
from typing import List

def clone_shallow_with_sparse(
    remote_url: str, 
    branch: str, 
    target_path: Path,
    modules: List[str]
):
    """Clone a branch shallowly with sparse checkout for specific modules."""
    # Clone without checkout, with optimizations
    subprocess.run([
        'git', 'clone',
        '--no-checkout',
        '--filter=blob:none',
        '--depth', '1',
        '--single-branch',
        '--branch', branch,
        remote_url,
        str(target_path)
    ], check=True)
    
    # Enable sparse checkout
    subprocess.run([
        'git', 'sparse-checkout', 'init', '--cone'
    ], cwd=target_path, check=True)
    
    # Set the modules to checkout
    subprocess.run([
        'git', 'sparse-checkout', 'set'
    ] + modules, cwd=target_path, check=True)
    
    # Now checkout
    subprocess.run([
        'git', 'checkout', branch
    ], cwd=target_path, check=True)

def fetch_pr(remote_url: str, pr_id: int, local_path: Path):
    """Fetch a PR ref."""
    subprocess.run([
        'git', 'fetch',
        remote_url,
        f'refs/pull/{pr_id}/head:pr/{pr_id}'
    ], cwd=local_path, check=True)

def update_sparse_checkout(local_path: Path, modules: List[str]):
    """Update sparse checkout paths."""
    subprocess.run([
        'git', 'sparse-checkout', 'set'
    ] + modules, cwd=local_path, check=True)
```

### Using GitPython Library

```python
from git import Repo
from typing import List

def clone_shallow_with_sparse(
    remote_url: str, 
    branch: str, 
    target_path: Path,
    modules: List[str]
):
    """Clone using GitPython with sparse checkout."""
    # Clone without checkout
    repo = Repo.clone_from(
        remote_url,
        str(target_path),
        branch=branch,
        depth=1,
        single_branch=True,
        no_checkout=True
    )
    
    # Enable sparse checkout (GitPython may need subprocess for this)
    import subprocess
    subprocess.run([
        'git', 'sparse-checkout', 'init', '--cone'
    ], cwd=target_path, check=True)
    
    subprocess.run([
        'git', 'sparse-checkout', 'set'
    ] + modules, cwd=target_path, check=True)
    
    # Checkout
    repo.heads[branch].checkout()
    
    return repo
```

**Note**: GitPython doesn't have native sparse checkout support, so subprocess is needed for sparse checkout commands.

**Advantages of GitPython**:
- Better error handling
- More Pythonic API
- Easier to inspect repo state

**Disadvantages**:
- Additional dependency
- May be slower for some operations
- Sparse checkout still requires subprocess calls

## Performance Optimizations

1. **Parallel Processing**: Clone multiple remotes in parallel
2. **Incremental Updates**: Only fetch new commits if cache exists
3. **Compression**: Use `--compression=0` for faster transfers (if bandwidth > CPU)
4. **Protocol**: Prefer SSH for authenticated access, HTTPS for public repos

## Testing Strategy

1. **Unit Tests**: Mock git commands, test logic
2. **Integration Tests**: Use small test repositories
3. **Performance Tests**: Measure data transfer for various scenarios
4. **Edge Case Tests**: Test PR refs, shallow merge-base issues, conflicts

## Recommendations

1. **Default to shallow clones** (`--depth=1`) for branch origins
2. **Use single-branch mode** when cloning branches
3. **Always use sparse checkout** with paths from `ModuleSpec.modules` to minimize working tree
4. **Combine techniques**: Use `--no-checkout --filter=blob:none --depth=1` + sparse checkout for maximum efficiency
5. **Fetch PR refs explicitly** rather than cloning forks
6. **Reuse clones** for multiple origins from same remote
7. **Update sparse checkout** when adding new modules to existing clone: `git sparse-checkout add <new-module>`
8. **Make depth configurable** (allow deeper clones if merge-base fails)
9. **Fail fast on conflicts** initially, make resolution configurable later
10. **Cache remote clones** to avoid redundant operations
11. **Consider GitPython** for better error handling and Pythonic API (but use subprocess for sparse checkout)

## Sparse Checkout Best Practices

### Path Mapping from ModuleSpec.modules

The `ModuleSpec.modules` list contains module names (e.g., `['account', 'sale', 'stock']`). These typically map to directories in the repository:

- **Odoo-style repos**: Modules are usually in root or `addons/` directory
- **OCA repos**: Modules often in root directory
- **Custom repos**: May have different structure

**Implementation Strategy**:
```python
def get_module_paths(modules: List[str], repo_structure: str = "root") -> List[str]:
    """Convert module names to repository paths."""
    if repo_structure == "root":
        return modules  # Modules are in root
    elif repo_structure == "addons":
        return [f"addons/{m}" for m in modules]
    # Add other patterns as needed
```

### Handling Module Path Discovery

**Option 1**: Assume modules are in root (most common for OCA repos)
**Option 2**: Auto-detect structure by checking if `addons/` exists
**Option 3**: Make it configurable per `ModuleSpec` or `ProjectSpec`

### Sparse Checkout Patterns

**Cone Mode** (recommended):
- Automatically includes parent directories
- More efficient for Git
- Use when modules are in known directory structure

**Non-Cone Mode**:
- More flexible with glob patterns
- Use when modules are scattered or need complex patterns
- Example: `git sparse-checkout set 'addons/*' 'other/path/*'`

## References

- [Git Shallow Clone Documentation](https://git-scm.com/docs/git-clone)
- [Git Sparse Checkout Documentation](https://git-scm.com/docs/git-sparse-checkout)
- [GitHub: Checking out Pull Requests Locally](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/checking-out-pull-requests-locally)
- [Atlassian: Pull Request Proficiency](https://www.atlassian.com/git/articles/pull-request-proficiency-fetching-abilities-unlocked)
- [Stack Overflow: Shallow Clone Merge Base](https://stackoverflow.com/questions/27059840/how-to-fetch-enough-commits-to-do-a-merge-in-a-shallow-clone)
- [Git Tower: Sparse Checkout Guide](https://www.git-tower.com/learn/git/faq/git-sparse-checkout)