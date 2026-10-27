#!/usr/bin/env python3
"""Apply a profile's overlay from config/profiles/<name>/ to a Hermes home.

  SOUL.md        copied to $HERMES_HOME/SOUL.md; a `<!-- hermes_installer:host begin/end -->` block
                 already in the target is kept (install.sh re-renders it afterwards anyway)
  overlay.yaml   deep-merged into $HERMES_HOME/config.yaml: mappings merge, lists are unioned
                 (existing order kept, new items appended), scalars are replaced.
                 `local_skills` is not a Hermes key: each name is a directory under
                 SKILL_Processed/ copied to $HERMES_HOME/skills/<name>/ (replaced if present).

Run with the Hermes venv Python (it has ruamel.yaml, which keeps comments and order).
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parent.parent
BEGIN, END = "<!-- hermes_installer:host begin -->", "<!-- hermes_installer:host end -->"


def deep_merge(base, new):
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        elif isinstance(value, list) and isinstance(base.get(key), list):
            for item in value:
                if item not in base[key]:
                    base[key].append(item)
        else:
            base[key] = value


def apply_soul(src: Path, dst: Path) -> str:
    text = src.read_text()
    if dst.is_file():
        old = dst.read_text()
        if BEGIN in old and END in old:
            block = old[old.index(BEGIN): old.index(END) + len(END)]
            text = text.rstrip("\n") + "\n\n" + block + "\n"
    if dst.is_file() and dst.read_text() == text:
        return "unchanged"
    dst.write_text(text)
    return "written"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--hermes-home", required=True)
    ap.add_argument("--skills-source", default=str(REPO / "SKILL_Processed"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = REPO / "config" / "profiles" / args.name
    if not src.is_dir():
        sys.exit(f"!! no overlay for profile {args.name}: {src}")
    home = Path(args.hermes_home).expanduser()
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    overlay = yaml.load((src / "overlay.yaml").read_text()) if (src / "overlay.yaml").is_file() else {}
    local_skills = list(overlay.pop("local_skills", []) or [])

    config_path = home / "config.yaml"
    config = yaml.load(config_path.read_text()) if config_path.is_file() else {}
    before = dict(config)
    deep_merge(config, overlay)
    if args.dry_run:
        print(f"would merge {src / 'overlay.yaml'} into {config_path}; local skills: {local_skills}")
        return
    if config_path.is_file():
        shutil.copy2(config_path, home / f"config.yaml.bak-profile-{args.name}")
    with config_path.open("w") as fh:
        yaml.dump(config, fh)
    print(f"    config.yaml: overlay {src.name}/overlay.yaml applied ({', '.join(overlay.keys())})")

    if (src / "SOUL.md").is_file():
        print(f"    SOUL.md: {apply_soul(src / 'SOUL.md', home / 'SOUL.md')}")

    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name in local_skills:
        source = Path(args.skills_source) / name
        if not (source / "SKILL.md").is_file():
            print(f"    !! local skill {name}: no SKILL.md under {source}; skipped")
            continue
        target = skills_dir / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        print(f"    skill {name}: copied to {target}")


if __name__ == "__main__":
    main()
