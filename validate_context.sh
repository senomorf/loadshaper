#!/bin/bash
set -e

echo "🎯 WORKTREE CONTEXT VALIDATION"
echo "=============================="

# Verify worktree directory
EXPECTED_PATH="/Users/arsenio/IdeaProjects/loadshaper-pr-38"
CURRENT_PATH="$(pwd)"

if [[ "$CURRENT_PATH" != "$EXPECTED_PATH" ]]; then
    echo "❌ ERROR: Wrong directory!"
    echo "Current: $CURRENT_PATH"
    echo "Expected: $EXPECTED_PATH"
    exit 1
fi

# Verify branch
EXPECTED_BRANCH="fix/pr-38"
CURRENT_BRANCH=$(git branch --show-current)

if [[ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" ]]; then
    echo "❌ ERROR: Wrong branch!"
    echo "Current: $CURRENT_BRANCH"
    echo "Expected: $EXPECTED_BRANCH"
    exit 1
fi

# Verify git status
GIT_STATUS=$(git status --porcelain 2>/dev/null || echo "ERROR")
if [[ "$GIT_STATUS" == "ERROR" ]]; then
    echo "❌ ERROR: Git repository corrupted!"
    exit 1
fi

echo "✅ CONTEXT VALIDATED"
echo "📁 Directory: $CURRENT_PATH"
echo "🌿 Branch: $CURRENT_BRANCH"
echo "📊 Status: $(echo "$GIT_STATUS" | wc -l) files changed"
echo "🎯 Task: pr_fix - 38"
echo ""
echo "🚀 READY FOR DEVELOPMENT!"