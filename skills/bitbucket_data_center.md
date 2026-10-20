---
name: bitbucket_data_center
type: knowledge
version: 1.0.0
agent: CodeActAgent
triggers:
- bitbucket_data_center
- bitbucket data center
---

You have access to an environment variable, `BITBUCKET_DATA_CENTER_TOKEN`, which holds your
Bitbucket Data Center credential. Use it to interact with Bitbucket Data Center's REST API:
- If its value has no colon, or starts with `x-token-auth:`, it is an access or OAuth token — use a Bearer header: `curl -H "Authorization: Bearer ${BITBUCKET_DATA_CENTER_TOKEN#x-token-auth:}" https://{domain}/rest/api/1.0/...`
- Otherwise it is in `username:token` form — use HTTP Basic auth: `curl -u "${BITBUCKET_DATA_CENTER_TOKEN}" https://{domain}/rest/api/1.0/...`

<IMPORTANT>
ALWAYS use the Bitbucket Data Center API for operations instead of a web browser.
ALWAYS use the `create_bitbucket_data_center_pr` tool to open a pull request
</IMPORTANT>

If you encounter authentication issues when pushing to Bitbucket Data Center (such as password prompts or permission errors) and the token is in `username:token` form, the old token may have expired. In such case, update the remote URL to include the current token: `git remote set-url origin https://${BITBUCKET_DATA_CENTER_TOKEN}@{domain}/scm/{project_lower}/{repo}.git`
Don't do this for an access or OAuth token: the git username it needs depends on the server, and the remote URL you were given already has the right one.

The repository format for Bitbucket Data Center is `PROJECT/repo_slug` (project key, slash, repo slug).

Here are some instructions for pushing, but ONLY do this if the user asks you to:
* NEVER push directly to the `main` or `master` branch
* Git config (username and email) is pre-set. Do not modify.
* You may already be on a branch starting with `openhands-workspace`. Create a new branch with a better name before pushing.
* Use the `create_bitbucket_data_center_pr` tool to create a pull request, if you haven't already
* Once you've created your own branch or a pull request, continue to update it. Do NOT create a new one unless you are explicitly asked to. Update the PR title and description as necessary, but don't change the branch name.
* Use the main branch as the base branch, unless the user requests otherwise
* After opening or updating a pull request, send the user a short message with a link to the pull request.
* Do NOT mark a pull request as ready to review unless the user explicitly says so
* Do all of the above in as few steps as possible. E.g. you could push changes with one step by running the following bash commands:
```bash
git remote -v && git branch # to find the current org, repo and branch
git checkout -b create-widget && git add . && git commit -m "Create widget" && git push -u origin create-widget
```
