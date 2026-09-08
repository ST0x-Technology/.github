# .github

Org-wide reusable workflows for the app repos. Each app keeps a ten-line
caller; the logic lives here so a flow change is one PR in one place.

| workflow | what | identity |
|---|---|---|
| `release-label.yml` | tag `vX.Y.Z` labels the digests CI built and attested for that commit, cuts the GitHub release | `<app>-labeler` |
| `app-release.yml` | deploys image + config to one plane: staging on merge, production behind a PAM grant (`app-deploy` entitlement in the env project) | `<app>-deployer` (staging), `<app>-releaser` (production) |
| `config-check.yml` | PR guard: candidate `deploy/config/*.toml` files parsed by the validator inside the newest released image | `<app>-labeler` |

Identities and gates are Terraform in `T0Trade/t0.devops`
(`modules/app-releaser`, `modules/app-release-gate`, `modules/ar-labeler`).
Callers pin these workflows by commit SHA; bump via PR.

Run the release gate tests with `python3 -m unittest discover -s tests -v`
(Python 3, Bash, and jq required). They run the workflow shell with fake
cloud responses, including denied access, moved tags, and missing attestations.
