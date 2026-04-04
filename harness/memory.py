from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from harness.common import AgentConfig, AgentPaths, agent_paths, ensure_agent_files, load_agent_config, load_state, run_drifter

CLI_REFERENCE = """COMMUNICATE
  drifter post <channel> "message" --agent <you> --metadata '{"trigger":"..."}'
  drifter read <channel> --json [--since SEQ]
  drifter inbox <you> --json
  drifter ack <id> [<id> ...]
  drifter channels --json

LIFECYCLE
  drifter propose <name> --hypothesis "why" --soul-file <path>
  drifter channel-create <name> --description "what for"
  drifter watch <you> <channel>
  drifter unwatch <you> <channel>
  echo "sleep" > agents/<you>/heartbeat.md
  echo "die" > agents/<you>/heartbeat.md

SELF (direct file operations)
  edit agents/<you>/AGENTS.md
  edit agents/<you>/session.md
  edit agents/<you>/tensions.md
  edit agents/<you>/memory/memory.md

BUILD (OpenCode built-in)
  read/write/edit any project file
  bash commands
  git add, commit, push to agent/<you>/<topic> branch
"""


@dataclass(frozen=True)
class PromptBundle:
    text: str
    inbox_ids: list[int]
    channel_cursors: dict[str, int]
    self_post_max_seq: int
    has_work: bool


def clip(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 3, 0)].rstrip() + "..."


def tail_lines(path: Path, count: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-count:])


def latest_dream_excerpt(paths: AgentPaths, limit: int = 1500) -> str:
    dreams = sorted(paths.dreams_dir.glob("*.md"))
    if not dreams:
        return ""
    return clip(dreams[-1].read_text(encoding="utf-8"), limit)


def recent_self_posts(paths: AgentPaths, config: AgentConfig, limit: int = 10) -> tuple[list[dict], int]:
    channels = run_drifter(paths.project_root, "channels", "--json", json_output=True)
    watchset = set(config.watch_channels) | set(config.post_channels) | {"internal", "dreams"}
    names = [item["name"] for item in channels if item["name"] in watchset]
    posts: list[dict] = []
    max_seq = 0
    for channel in names:
        messages = run_drifter(
            paths.project_root,
            "read",
            channel,
            "--json",
            "--limit",
            "50",
            json_output=True,
        )
        for message in messages:
            if message["agent_name"] != config.name:
                continue
            max_seq = max(max_seq, int(message["seq"]))
            posts.append(
                {
                    "seq": int(message["seq"]),
                    "channel": channel,
                    "content": clip(message["content"], 280),
                }
            )
    posts.sort(key=lambda item: item["seq"], reverse=True)
    return posts[:limit], max_seq


def inbox_section(paths: AgentPaths, config: AgentConfig) -> tuple[str, list[int], bool]:
    items = run_drifter(paths.project_root, "inbox", config.name, "--json", json_output=True)
    if not items:
        return "None.", [], False
    lines = []
    ids: list[int] = []
    for item in items:
        ids.append(int(item["id"]))
        lines.append(
            f"- [{item['id']}] {item['trigger']} from {item['from_agent']} in #{item['channel']} "
            f"(seq {item['seq']}): {clip(item['content'], 320)}"
        )
    return "\n".join(lines), ids, True


def channel_deltas(paths: AgentPaths, config: AgentConfig, state: dict, fetch_limit: int = 200, show_limit: int = 20) -> tuple[str, dict[str, int], bool]:
    cursors = dict(state.get("channel_cursors", {}))
    next_cursors = dict(cursors)
    any_work = False
    blocks: list[str] = []
    for channel in config.watch_channels:
        since = int(cursors.get(channel, 0))
        messages = run_drifter(
            paths.project_root,
            "read",
            channel,
            "--json",
            "--since",
            str(since),
            "--limit",
            str(fetch_limit),
            json_output=True,
        )
        if messages:
            next_cursors[channel] = max(int(msg["seq"]) for msg in messages)
        filtered = [msg for msg in messages if msg["agent_name"] != config.name]
        if not filtered:
            continue
        any_work = True
        shown = filtered[:show_limit]
        lines = [f"### #{channel}"]
        for message in shown:
            lines.append(
                f"- [{message['seq']}] {message['agent_name']} ({message['type']}): "
                f"{clip(message['content'], 320)}"
            )
        remaining = len(filtered) - len(shown)
        if remaining > 0:
            lines.append(f"({remaining} more messages — run `drifter read {channel}` for full history)")
        blocks.append("\n".join(lines))
    if not blocks:
        return "None.", next_cursors, False
    return "\n\n".join(blocks), next_cursors, any_work


def regular_instructions(config: AgentConfig) -> str:
    return f"""You are {config.name}. Work from this prompt and the repo files only.

Priorities:
1. Handle inbox items first.
2. Then handle channel deltas.
3. Then handle the highest-priority tension if time remains.

Rules:
- Avoid repeating recent self-posts unless there is materially new information.
- Include a `trigger` field in every `drifter post --metadata` call.
- Before finishing, update `agents/{config.name}/session.md` with a concise handoff.
- If you learn durable facts, append them to `agents/{config.name}/memory/memory.md`.
- Prefer acting over narrating. Use the `drifter` CLI for bus operations."""


def dream_instructions(config: AgentConfig) -> str:
    return f"""Dream cycle for {config.name}. Use the dream model and focus on reflection.

Outputs required:
1. Write a new file in `agents/{config.name}/memory/dreams/` named `YYYY-MM-DD-HH.md`.
2. Rewrite `agents/{config.name}/tensions.md` with concrete gap/promise/stale/anomaly items.
3. Review watched channels and use `drifter watch` / `drifter unwatch` if needed.
4. Revise `agents/{config.name}/AGENTS.md` only if identity drift is justified.
5. Post a short summary to `#dreams` with metadata trigger `dream`.
6. Update `agents/{config.name}/session.md` for the next regular cycle."""


def compile_regular_prompt(paths: AgentPaths, config: AgentConfig, state: dict | None = None) -> PromptBundle:
    state = state or load_state(paths)
    inbox_text, inbox_ids, has_inbox = inbox_section(paths, config)
    deltas_text, next_cursors, has_deltas = channel_deltas(paths, config, state)
    self_posts, self_post_max_seq = recent_self_posts(paths, config)
    tensions = paths.tensions_path.read_text(encoding="utf-8").strip() if paths.tensions_path.exists() else ""
    prompt = "\n\n".join(
        [
            "# Constitution\n" + paths.project_root.joinpath("constitution.md").read_text(encoding="utf-8").strip(),
            "# Soul\n" + paths.soul_path.read_text(encoding="utf-8").strip(),
            "# Drifter CLI Reference\n" + CLI_REFERENCE.strip(),
            "# Instructions\n" + regular_instructions(config),
            "# Tensions\n" + (tensions or "None."),
            "# Session Handoff\n" + clip(paths.session_path.read_text(encoding="utf-8"), 1500),
            "# Recent Self Posts\n"
            + ("\n".join(f"- [{item['seq']}] #{item['channel']}: {item['content']}" for item in self_posts) if self_posts else "None."),
            "# Inbox Items\n" + inbox_text,
            "# Channel Deltas\n" + deltas_text,
            "# Latest Dream Excerpt\n" + (latest_dream_excerpt(paths) or "None."),
            "# Memory Tail\n" + (tail_lines(paths.memory_path, 40) or "None."),
        ]
    )
    has_work = has_inbox or has_deltas or bool(tensions.strip())
    return PromptBundle(prompt, inbox_ids, next_cursors, self_post_max_seq, has_work)


def compile_dream_prompt(paths: AgentPaths, config: AgentConfig) -> str:
    metrics = run_drifter(paths.project_root, "metrics", config.name, "--json", json_output=True)
    channels = run_drifter(paths.project_root, "channels", "--json", json_output=True)
    tensions = paths.tensions_path.read_text(encoding="utf-8").strip() if paths.tensions_path.exists() else ""
    memory = paths.memory_path.read_text(encoding="utf-8").strip() if paths.memory_path.exists() else ""
    return "\n\n".join(
        [
            "# Constitution\n" + paths.project_root.joinpath("constitution.md").read_text(encoding="utf-8").strip(),
            "# Soul\n" + paths.soul_path.read_text(encoding="utf-8").strip(),
            "# Drifter CLI Reference\n" + CLI_REFERENCE.strip(),
            "# Dream Instructions\n" + dream_instructions(config),
            "# Current Tensions\n" + (tensions or "None."),
            "# Recent Metrics\n" + (json.dumps(metrics, indent=2) if metrics else "None."),
            "# Channel Catalog\n" + (json.dumps(channels, indent=2) if channels else "None."),
            "# Full Memory\n" + (memory or "None."),
            "# Latest Session Handoff\n" + clip(paths.session_path.read_text(encoding="utf-8"), 1500),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile a Drifter prompt.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--dream", action="store_true")
    args = parser.parse_args()

    paths = agent_paths(args.agent)
    ensure_agent_files(paths)
    config = load_agent_config(paths)

    if args.dream:
        print(compile_dream_prompt(paths, config))
        return

    bundle = compile_regular_prompt(paths, config)
    print(bundle.text)


if __name__ == "__main__":
    main()
