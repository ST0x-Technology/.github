# .github

Org-wide reusable workflows for the app repos. Each app keeps a ten-line
caller; the logic lives here so a flow change is one PR in one place.

| workflow | what | identity |
|---|---|---|
| `release-label.yml` | tag `vX.Y.Z` labels the digests CI built and attested for that commit, cuts the GitHub release | `<app>-labeler` |
| `app-release.yml` | deploys image + config to one plane: staging on merge, production behind a PAM grant (`app-deploy` entitlement in the env project) or inside an approved deploy window | `<app>-deployer` (staging), `<app>-releaser` (production) |
| `deploy-window.yml` | asks PAM for a `deploy-window` grant that opens at a chosen time (up to 7 days ahead) for 1 to 12 hours, started by the person who wants it with their own GitHub sign-in so the run names them; once two approvers approve it, every production release of the app inside the window rolls without a new approval while at least 30 minutes remain (`app-release.yml` finds it ACTIVE and leaves it open) | `<app>-releaser` |
| `externally-merged.yml` | labels PRs the Graphite merge queue closed (not merged) as `externally-merged`, so Linear marks their issues done | `GITHUB_TOKEN` |
| `config-check.yml` | PR guard: candidate `deploy/config/*.toml` files parsed by the validator inside the newest released image | `<app>-labeler` |
| `observability.yml` | the app's Grafana dashboards and alert rules (`observability/`): a throwaway Grafana provisions them on every PR and a scope check keeps the rules file to rule groups for that service; on the default branch they ship to the T0 observability box's config bucket | `<app>-obs-deploy` |

Identities and gates are Terraform in `T0Trade/t0.devops`
(`modules/app-releaser`, `modules/app-release-gate`, `modules/ar-labeler`).
Callers pin these workflows by commit SHA; bump via PR.

Run the release gate tests with `python3 -m unittest discover -s tests -v`
(Python 3, Bash, and jq required). They run the workflow shell with fake
cloud responses, including denied access, moved tags, and missing attestations.
