#!/bin/bash

set -euxo pipefail

# This script records the enterprise-server image built for the PR in its
# description. The image needs a database and Keycloak to run, so the reference
# is what is useful here — not a standalone `docker run` command.

IMAGE_REF="ghcr.io/openhands/enterprise-server:sha-${SHORT_SHA}"

# Matches the section this script adds, plus the wording it used previously, so
# an existing section is replaced instead of duplicated.
SECTION_MARKERS="Enterprise server image for this PR:|To run this PR locally, use the following command:"

# Get the current PR body
PR_BODY=$(gh pr view "$PR_NUMBER" --json body --jq .body)

# Prepare the new PR body
if echo "$PR_BODY" | grep -qE "$SECTION_MARKERS"; then
  # Drop everything from the existing section onwards and re-add it, so repeated
  # pushes refresh the image tag rather than stacking sections up.
  BEFORE_SECTION=$(echo "$PR_BODY" | sed -E "/$SECTION_MARKERS/,\$d")
  NEW_PR_BODY=$(cat <<EOF
${BEFORE_SECTION}

Enterprise server image for this PR:

\`\`\`
${IMAGE_REF}
\`\`\`
EOF
)
else
  # For new PR descriptions: use heredoc safely without indentation
  NEW_PR_BODY=$(cat <<EOF
$PR_BODY

---

Enterprise server image for this PR:

\`\`\`
${IMAGE_REF}
\`\`\`
EOF
)
fi

# Update the PR description
echo "Updating PR description with the enterprise-server image reference"
gh pr edit "$PR_NUMBER" --body "$NEW_PR_BODY"
