# Releasing

Everything below is driven by one tag push. Read this before the first release
you do; the ordering constraints in it are not guessable.

## What publishes where

| Channel | Workflow | Trigger |
|---|---|---|
| GitHub release with the installer | `release.yml` | tag push `v*` |
| Docker Hub image | `docker.yml` | tag push `v*` |
| winget | `winget.yml` | run by hand from the Actions tab |

The two tag-driven ones run in parallel from the same push. winget is the only
manual step, and the reason is in the comment at the top of `winget.yml`:
GitHub does not start a workflow from an event raised with the default
`GITHUB_TOKEN`, so the `release: published` trigger is never delivered.

## Secrets

Set once, under Settings > Secrets and variables > Actions. Each workflow
checks the ones it needs in its first step and stops with a message naming
them, so a missing secret costs seconds rather than a failed build.

| Secret | Used by | What it is |
|---|---|---|
| `DOCKERHUB_USERNAME` | `docker.yml` | the Docker Hub account name |
| `DOCKERHUB_TOKEN` | `docker.yml` | a Docker Hub access token with Read & Write - not the password |
| `WINGET_TOKEN` | `winget.yml` | a classic GitHub PAT with `public_repo`, for the fork of microsoft/winget-pkgs |

## Steps

1. **Bump the version.** `installer/Sada.iss`, the `#define AppVersion` line.
   That one define feeds the installer filename, the uninstall registry entry
   and `VersionInfoVersion`, which is what `winget upgrade` compares against.

2. **Update the winget manifests** under `winget/`: `PackageVersion` in all
   four files, and in the installer manifest `DisplayVersion`, `InstallerUrl`
   and `ReleaseDate`. Leave `InstallerSha256` alone for now - see step 5.
   `python tools/validate_winget.py` runs in CI and will fail on a mismatch.

3. **Write the release notes** in `winget/MohammedKhaled.Sada.locale.en-US.yaml`
   under `ReleaseNotes`, and point `ReleaseNotesUrl` at the new tag. Write them
   for someone deciding whether to upgrade, not as a commit log.

4. **Tag and push.**

   ```bash
   git tag -a v1.0.2 -m "Sada 1.0.2"
   git push origin v1.0.2
   ```

   `release.yml` builds, packages and publishes; `docker.yml` builds and pushes
   `1.0.2`, `1.0`, `1` and `latest`. Watch both in the Actions tab.

5. **Fill in the installer hash.** Take it from `SHA256SUMS.txt` on the
   published release, put it in `InstallerSha256`, and commit.

   Do not use the hash of a local build. It looks correct, passes
   `winget validate`, and then fails at install time for every user, because
   the file they download is the one CI built. This happened during 1.0.1.

6. **Submit to winget.** Actions > Publish to WinGet > Run workflow, with the
   tag. It opens a pull request on microsoft/winget-pkgs for you.

## Ordering constraints

- **The first winget submission cannot be automated.** `winget-releaser` adds a
  version to a package that already exists; it cannot create one. The first
  submission is a pull request opened by hand, labelled `New-Package`, and it
  waits for a volunteer moderator - measured at a median of about four weeks.
  Later versions of an existing package merge in about an hour and a half.
  Do not run `winget.yml` until that first pull request has merged.

- **Do not move a tag that has a published release.** Deleting and re-pushing a
  tag is safe only while `GET /releases/tags/<tag>` returns 404. Once people
  have downloaded an asset, the bytes behind a version must not change; release
  a new patch version instead.

## What a release does not touch

`engine/`, `docker/` and the `Dockerfile` are what the container image holds -
`app/` is the Windows desktop program and is deliberately excluded. A release
that changes only `app/` produces a byte-identical image. It is still tagged
with the new version so that "which image goes with my version" has an answer,
and the push is cheap because every large layer is already on the registry.

Check with:

```bash
git diff --stat v1.0.1 v1.0.2 -- engine/ docker/ Dockerfile .dockerignore
```

## Known gaps

- **The binaries are not code signed.** On a machine with the Defender attack
  surface reduction rule "Block executable files from running unless they meet
  a prevalence, age, or trusted list criterion" enabled, a newly built unsigned
  installer is blocked outright, with no prompt to override. Many of the people
  this application is for are on managed work machines where that rule is on.

- **The application does not report its own version.** There is no version
  string in `app/` or `engine/`, so a user cannot read back what they are
  running, and a bug report cannot say. The installed version is only in the
  uninstall registry entry.

- **`winget/` drifts once winget-releaser takes over.** From the second
  submission on, the manifests that reach microsoft/winget-pkgs are generated
  from the previous version there, not from this folder. Keep it updated as the
  record of what was submitted, or delete it - but do not let it sit stale and
  half-true.
