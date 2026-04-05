"""GitHub gateway — monitors repo activity (PRs, issues, commits) and posts to the bus."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None


def run_drifter(project_root: Path, *args: str) -> str:
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def fetch_prs(owner: str, repo: str, token: str, state: str = "open", hours: int = 24) -> list[dict]:
    """Fetch recent pull requests from GitHub."""
    if requests is None:
        print("[github] requests not installed, skipping", file=sys.stderr)
        return []

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        params={"state": state, "sort": "updated", "direction": "desc", "per_page": 20},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if resp.status_code != 200:
        print(f"[github] PR fetch failed: {resp.status_code}", file=sys.stderr)
        return []

    prs = []
    for pr in resp.json():
        updated = pr.get("updated_at", "")
        if updated > since:
            prs.append({
                "number": pr["number"],
                "title": pr["title"],
                "user": pr["user"]["login"],
                "state": pr["state"],
                "url": pr["html_url"],
                "updated": updated,
                "body": pr.get("body", "")[:500],
            })
    return prs


def fetch_issues(owner: str, repo: str, token: str, hours: int = 24) -> list[dict]:
    """Fetch recent issues from GitHub."""
    if requests is None:
        return []

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 20},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if resp.status_code != 200:
        print(f"[github] issue fetch failed: {resp.status_code}", file=sys.stderr)
        return []

    issues = []
    for issue in resp.json():
        if issue.get("pull_request"):
            continue  # Skip PRs listed as issues
        updated = issue.get("updated_at", "")
        if updated > since:
            issues.append({
                "number": issue["number"],
                "title": issue["title"],
                "user": issue["user"]["login"],
                "url": issue["html_url"],
                "updated": updated,
                "body": issue.get("body", "")[:500],
            })
    return issues


def post_activity(project_root: Path, items: list[dict], item_type: str, channel: str = "engineering") -> int:
    """Post GitHub activity to the bus."""
    posted = 0
    for item in items:
        body_preview = f"\n{item['body']}" if item.get("body") else ""
        msg = (
            f"GITHUB {item_type.upper()} #{item['number']}: {item['title']}\n"
            f"By: {item['user']}\n"
            f"Updated: {item['updated']}\n"
            f"URL: {item['url']}{body_preview}"
        )

        try:
            run_drifter(
                project_root, "post", channel, msg,
                "--agent", "github-gateway",
                "--metadata", '{"trigger":"manual","source":"github"}',
            )
            posted += 1
        except subprocess.CalledProcessError as exc:
            print(f"[github] failed to post: {exc.stderr}", file=sys.stderr)

    return posted


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub gateway — monitors repo activity")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPO", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--hours", type=int, default=24, help="Look back window in hours")
    parser.add_argument("--channel", default="engineering")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.repo:
        print("[github] GITHUB_REPO env var required (format: owner/repo)", file=sys.stderr)
        return 1
    if "/" not in args.repo:
        print("[github] GITHUB_REPO must be in format owner/repo", file=sys.stderr)
        return 1
    if not args.token:
        print("[github] GITHUB_TOKEN env var required", file=sys.stderr)
        return 1

    owner, repo = args.repo.split("/", 1)
    project_root = Path(__file__).resolve().parent.parent

    prs = fetch_prs(owner, repo, args.token, hours=args.hours)
    issues = fetch_issues(owner, repo, args.token, hours=args.hours)

    if not prs and not issues:
        print("[github] no recent activity")
        return 0

    print(f"[github] found {len(prs)} PR(s), {len(issues)} issue(s)")

    if args.dry_run:
        for pr in prs:
            print(f"  PR #{pr['number']}: {pr['title']} by {pr['user']}")
        for issue in issues:
            print(f"  Issue #{issue['number']}: {issue['title']} by {issue['user']}")
        return 0

    posted_prs = post_activity(project_root, prs, "PR", args.channel) if prs else 0
    posted_issues = post_activity(project_root, issues, "issue", args.channel) if issues else 0
    print(f"[github] posted {posted_prs} PR(s), {posted_issues} issue(s) to #{args.channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
