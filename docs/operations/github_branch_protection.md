# GitHub main branch protection

The single-maintainer repository uses an active `main-quality-gate` ruleset. A
pull request is still mandatory, but the required approving review count is zero
so the PR author can merge after the automated and conversation gates pass.

## GitHub UI

1. Open **Settings → Rules → Rulesets → main-quality-gate**.
2. Keep enforcement **Active** and the target set to the default branch.
3. Require a pull request, set required approvals to **0**, dismiss stale reviews,
   and require all conversations to be resolved.
4. Require the `quality-gate` status check and require branches to be up to date.
5. Keep force pushes and branch deletion blocked. Do not add bypass actors.

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
      "required_approving_review_count": 0,
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

gh api --method PUT repos/Daikisong/new_bot/rulesets/<RULESET_ID> `
  --input $env:TEMP\nslab-main-ruleset.json
```

Verify the server state:

```powershell
gh api repos/Daikisong/new_bot/rulesets
```

Do not report branch protection as applied until the server response shows all of
the following: active `main-quality-gate`, required approvals `0`, strict required
status check `quality-gate`, required conversation resolution, deletion protection,
non-fast-forward protection, and no bypass actor.
