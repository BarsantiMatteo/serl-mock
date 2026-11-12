# serl-mock — instructions for Claude sessions

Generates dummy SERL Observatory datasets for pipeline development and testing. Start with
[README.md](README.md); the docs index is its "Documentation" table.

## Tasks (GitHub issues)

- Open tasks are public GitHub issues: <https://github.com/BarsantiMatteo/serl-mock/issues>.
  `gh` is at `C:\Program Files\GitHub CLI\gh.exe` if not on PATH.
- Milestones group them: *Realistic data quality*, *Edition09*, *Edition comparison mock*, *Cleanup*,
  *pip package (PyPI)*. An issue with no milestone is backlog.
- Board: <https://github.com/users/BarsantiMatteo/projects/2> (project "serl-mock", Status
  Todo → In Progress → Done; closed issues move to Done automatically).
- At the start of a session, list the open issues of the milestone being worked on
  (`gh issue list --milestone "<title>"`), or read the issue named in the request. Set the issue
  being worked on to In Progress on the board, so parallel sessions don't pick the same one.
- New issues: add `--project "serl-mock"` to `gh issue create` so they appear on the board.
- Close issues from commits: `Closes #N` (closes when the commit reaches `master`); `Refs #N` for
  partial progress.
- Open an issue for any follow-up found during work, rather than a TODO in code or notes.
  Keep it short:
  ```markdown
  **Goal:** one or two lines.
  **Done when:** the concrete result.
  **Links:** docs page, file, related issues.
  ```
  Title as an action. Labels: one of `bug` / `enhancement` / `cleanup` / `documentation`, plus
  `waiting-on-serl` if it needs SERL documentation, a data release or an answer from SERL. Set a
  milestone if one fits.
- How something works goes in `docs/`, not in issue comments; the issue links to it.
- Don't create or edit milestones and labels without asking.

## Local notes

`docs/notes/` is gitignored (personal working notes and step plans, e.g.
`plan_edition_comparison.md`). Plans there may refer to issues; the issues are the task list.

## Working on the code

- Environment: `uv sync`; run with `uv run ...`; tests with `uv run python -m pytest` (on this
  machine `pytest.exe` is blocked by Windows Application Control) — ~3 minutes, see
  [docs/06_testing.md](docs/06_testing.md).
- GitHub Actions runs the same tests on every push and pull request
  (`.github/workflows/tests.yml`). Run them locally before pushing; after pushing, check the run
  (`gh run list -L 3`, `gh run watch`).
- When behaviour or config options change, update the matching page in `docs/` in the same commit
  (configuration options: [docs/02_configuration.md](docs/02_configuration.md); layout and modules:
  [docs/01_structure.md](docs/01_structure.md)).
