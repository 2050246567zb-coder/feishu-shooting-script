"""Install this local skill folder without changing Feishu configuration."""
import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path


def install(destination=None, replace=False, dry_run=False):
    source = Path(__file__).resolve().parent.parent
    codex_root = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser()
    root = Path(destination).expanduser().resolve() if destination else (codex_root/'skills').resolve()
    target = root/'feishu-shooting-script'
    if target == source:
        return {'status': 'already_installed', 'path': str(target)}
    if source in root.parents or root == source:
        raise ValueError('The installation destination cannot be inside the source skill folder')
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError('The resolved target is outside the intended skills directory')
    if target.exists() and not replace:
        raise ValueError('The skill already exists; use --replace to back it up and replace it')
    result = {'status': 'dry_run' if dry_run else 'installed', 'path': str(target)}
    if dry_run:
        return result
    root.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup_root = (root.parent/'skill-backups').resolve()
        backup_root.mkdir(exist_ok=True)
        backup = backup_root/('feishu-shooting-script-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        if target.resolve().parent != root or backup.resolve().parent != backup_root:
            raise ValueError('Refusing to move outside the verified installation/backup directories')
        shutil.move(str(target), str(backup))
        result['backup'] = str(backup)
    try:
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.tmp'))
    except Exception:
        if target.exists() and target.resolve().parent == root and not target.is_symlink():
            shutil.rmtree(target)
        if backup is not None:
            shutil.move(str(backup), str(target))
        raise
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', help='Skills directory; defaults to CODEX_HOME/skills or ~/.codex/skills')
    parser.add_argument('--replace', action='store_true', help='Back up an existing installation outside skills/ before replacing')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.destination, args.replace, args.dry_run), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error)+'\n')
