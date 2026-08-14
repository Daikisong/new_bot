# GitHub main branch protection

Repository administrators must apply this ruleset after the `quality-gate` workflow has completed
once on `main`.

## GitHub UI

1. Open **Settings → Rules → Rulesets → New ruleset → New branch ruleset**.
2. Name it `main-quality-gate`, set enforcement to **Active**, and target the default branch.
3. Require a pull request before merging and require all conversations to be resolved.
4. Require status checks, select `quality-gate`, and require branches to be up to date.
5. Block force pushes and branch deletion. Do not add bypass actors except an audited break-glass team.

## GitHub CLI

Save the JSON below as a temporary file outside the repository, then run the API command as a
repository administrator. Replace the repository owner/name only when operating a fork.

```powershell
@'
{
  "name": "main-quality-gate",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": true,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": true
    }},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": true,
      "do_not_enforce_on_create": false,
      "required_status_checks": [{"context": "quality-gate"}]
    }}
  ]
}
'@ | Set-Content -Encoding utf8 $env:TEMP\nslab-main-ruleset.json

gh api --method POST repos/Daikisong/new_bot/rulesets `
  --input $env:TEMP\nslab-main-ruleset.json
```

Verify the server state:

```powershell
gh api repos/Daikisong/new_bot/rulesets
```

Do not report branch protection as applied until the server response shows an active ruleset with
required status check `quality-gate`.
