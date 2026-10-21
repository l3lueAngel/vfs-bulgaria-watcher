# VFS Bulgaria Watcher v3.2 draft

This package combines VisaTour monitoring for the two biometric categories with a cautious, independent check of the public VFS Bulgaria page.

## Important limitations

- The public VFS landing page may not expose appointment availability. This implementation intentionally does **not** infer a slot from arbitrary dates or from the page merely loading. The VFS check only reports a candidate when it sees explicit availability wording; the live VFS appointment calendar must still confirm it.
- The workflow requires `TG_BOT_TOKEN` and `TG_CHAT_ID` in GitHub Actions Secrets.
- The workflow is `workflow_dispatch` only because the existing external cron-job.org is expected to dispatch it every 2 minutes. Confirm that external trigger is still enabled.
- Existing `state.json` is preserved when this package is applied over the current repository. Do not replace it with an empty file.
- This is a prepared package, not a deployment to GitHub. Review and merge it into the repository before it becomes active.

## Files

- `checker.py`: VisaTour dual-category checks and independent public VFS check.
- `.github/workflows/vfs_cron.yml`: Actions workflow, tests, Playwright install, state commit only on changes.
- `tests/test_checker.py`: unit tests including protection against treating an arbitrary date as a slot.

## Apply

Copy these files into the existing repository, preserving its current `state.json`. Ensure Telegram secrets are configured, then run the workflow manually once and inspect the logs before relying on it.