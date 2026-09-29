"""Reproduce the pinned Echo Live comparison. Requires only Python and Git."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(".reference/Echo-Live"))
    parser.add_argument("--base", default="1.6.6")
    parser.add_argument("--head", default="1.8.12")
    parser.add_argument("--output", type=Path, default=Path(".reference/audit"))
    args = parser.parse_args()

    def git(*cmd: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(args.repo), *cmd], encoding="utf-8"
        )

    def read(ref: str, path: str) -> str:
        return git("show", f"{ref}:{path}")

    def flatten(value: dict, prefix: str = "") -> dict:
        result = {}
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(item, dict):
                result.update(flatten(item, name))
            else:
                result[name] = item
        return result

    versions = {}
    for ref in (args.base, args.head):
        source = read(ref, "config.js")
        # These pinned upstream configs are JSON object literals, not arbitrary JS.
        config = json.loads(source.split("=", 1)[1].strip().removesuffix(";"))
        broadcast = read(ref, "res/class/EchoLiveBroadcast.js")
        versions[ref] = {
            "commit": git("rev-parse", f"{ref}^{{commit}}").strip(),
            "config_data_version": config["data_version"],
            "config": flatten(config),
            "actions": sorted(set(re.findall(r"API_NAME_\w+:\s*'([^']+)'", broadcast))),
            "roles": sorted(set(re.findall(r"TYPE_\w+:\s*'([^']+)'", broadcast))),
        }
    base, head = versions[args.base], versions[args.head]
    old, new = base["config"], head["config"]
    report = {
        "source": "https://github.com/sheep-realms/Echo-Live",
        "versions": versions,
        "diff_stat": git("diff", "--shortstat", args.base, args.head).strip(),
        "actions_added": sorted(set(head["actions"]) - set(base["actions"])),
        "actions_removed": sorted(set(base["actions"]) - set(head["actions"])),
        "config_added": {key: new[key] for key in sorted(new.keys() - old.keys())},
        "config_removed": {key: old[key] for key in sorted(old.keys() - new.keys())},
        "config_changed": {
            key: {"before": old[key], "after": new[key]}
            for key in sorted(old.keys() & new.keys()) if old[key] != new[key]
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "upstream.patch").write_text(
        git("diff", "--no-ext-diff", args.base, args.head), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "versions"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
