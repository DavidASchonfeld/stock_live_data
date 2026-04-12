# Terraform Auth Strategy

## Why Option B (SSO) instead of Option A (Static Keys)

**Option A** means running `aws configure` and storing a long-lived Access Key ID + Secret on disk in `~/.aws/credentials`. That key never expires — if it leaks (laptop stolen, accidentally committed, etc.), it's a live credential until you manually rotate it.

**Option B (AWS IAM Identity Center / SSO)** issues short-lived credentials that expire after ~8 hours. There is no static key to lose, rotate, or accidentally commit. You log in once via a browser, the session lasts the workday, and the script handles re-authentication automatically.

For a solo developer running Terraform manually a few times a month, Option B is the right tradeoff: marginally more setup once, meaningfully better security forever.

---

## Future: OIDC for GitHub Actions

### What is OIDC?

OIDC (OpenID Connect) is a standard that lets one system prove its identity to another without exchanging passwords or keys. In the GitHub Actions context: when a workflow runs, GitHub generates a short-lived cryptographically signed token that says *"this is a run from repo X, branch Y"*. AWS verifies that token directly with GitHub — no credentials ever need to be stored.

### Why it's used in CI/CD

GitHub Actions has no browser and no human, so SSO doesn't work there. OIDC is the equivalent for automation: temporary credentials, no stored secrets, and the trust is scoped to a specific repo and branch.

### How to set it up (when ready)

**Step 1 — Create an OIDC identity provider in AWS IAM**
- AWS Console → IAM → Identity Providers → Add provider
- Provider type: OpenID Connect
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

**Step 2 — Create an IAM Role for GitHub Actions**
- AWS Console → IAM → Roles → Create role
- Trusted entity: Web identity → select the provider above
- Condition: limit to your repo (`repo:YOUR_GITHUB_USERNAME/YOUR_REPO:*`)
- Attach the permissions Terraform needs (e.g. EC2, IAM, ECR full access)
- Note the Role ARN — you'll need it in the workflow

**Step 3 — Add the workflow file**

`.github/workflows/terraform.yml`:
```yaml
name: Terraform

on:
  workflow_dispatch:  # manual trigger only

permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  terraform:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials via OIDC
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::YOUR_12_DIGIT_ACCOUNT_ID:role/github-actions-terraform
          aws-region: us-east-1

      - name: Terraform apply
        run: ./scripts/deploy/terraform.sh apply
```

No secrets stored in GitHub. The `configure-aws-credentials` action exchanges the GitHub OIDC token for temporary AWS credentials automatically.
