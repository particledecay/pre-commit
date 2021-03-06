import argparse
import os.path
import subprocess
import sys
from typing import Optional
from typing import Sequence
from typing import Tuple

from pre_commit.commands.run import run
from pre_commit.envcontext import envcontext
from pre_commit.parse_shebang import normalize_cmd
from pre_commit.store import Store

Z40 = '0' * 40


def _run_legacy(
        hook_type: str,
        hook_dir: str,
        args: Sequence[str],
) -> Tuple[int, bytes]:
    if os.environ.get('PRE_COMMIT_RUNNING_LEGACY'):
        raise SystemExit(
            f"bug: pre-commit's script is installed in migration mode\n"
            f'run `pre-commit install -f --hook-type {hook_type}` to fix '
            f'this\n\n'
            f'Please report this bug at '
            f'https://github.com/pre-commit/pre-commit/issues',
        )

    if hook_type == 'pre-push':
        stdin = sys.stdin.buffer.read()
    else:
        stdin = b''

    # not running in legacy mode
    legacy_hook = os.path.join(hook_dir, f'{hook_type}.legacy')
    if not os.access(legacy_hook, os.X_OK):
        return 0, stdin

    with envcontext((('PRE_COMMIT_RUNNING_LEGACY', '1'),)):
        cmd = normalize_cmd((legacy_hook, *args))
        return subprocess.run(cmd, input=stdin).returncode, stdin


def _validate_config(
        retv: int,
        config: str,
        skip_on_missing_config: bool,
) -> None:
    if not os.path.isfile(config):
        if skip_on_missing_config or os.getenv('PRE_COMMIT_ALLOW_NO_CONFIG'):
            print(f'`{config}` config file not found. Skipping `pre-commit`.')
            raise SystemExit(retv)
        else:
            print(
                f'No {config} file was found\n'
                f'- To temporarily silence this, run '
                f'`PRE_COMMIT_ALLOW_NO_CONFIG=1 git ...`\n'
                f'- To permanently silence this, install pre-commit with the '
                f'--allow-missing-config option\n'
                f'- To uninstall pre-commit run `pre-commit uninstall`',
            )
            raise SystemExit(1)


def _ns(
        hook_type: str,
        color: bool,
        *,
        all_files: bool = False,
        origin: Optional[str] = None,
        source: Optional[str] = None,
        remote_name: Optional[str] = None,
        remote_url: Optional[str] = None,
        commit_msg_filename: Optional[str] = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        color=color,
        hook_stage=hook_type.replace('pre-', ''),
        origin=origin,
        source=source,
        remote_name=remote_name,
        remote_url=remote_url,
        commit_msg_filename=commit_msg_filename,
        all_files=all_files,
        files=(),
        hook=None,
        verbose=False,
        show_diff_on_failure=False,
    )


def _rev_exists(rev: str) -> bool:
    return not subprocess.call(('git', 'rev-list', '--quiet', rev))


def _pre_push_ns(
        color: bool,
        args: Sequence[str],
        stdin: bytes,
) -> Optional[argparse.Namespace]:
    remote_name = args[0]
    remote_url = args[1]

    for line in stdin.decode().splitlines():
        _, local_sha, _, remote_sha = line.split()
        if local_sha == Z40:
            continue
        elif remote_sha != Z40 and _rev_exists(remote_sha):
            return _ns(
                'pre-push', color,
                origin=local_sha, source=remote_sha,
                remote_name=remote_name, remote_url=remote_url,
            )
        else:
            # ancestors not found in remote
            ancestors = subprocess.check_output((
                'git', 'rev-list', local_sha, '--topo-order', '--reverse',
                '--not', f'--remotes={remote_name}',
            )).decode().strip()
            if not ancestors:
                continue
            else:
                first_ancestor = ancestors.splitlines()[0]
                cmd = ('git', 'rev-list', '--max-parents=0', local_sha)
                roots = set(subprocess.check_output(cmd).decode().splitlines())
                if first_ancestor in roots:
                    # pushing the whole tree including root commit
                    return _ns(
                        'pre-push', color,
                        all_files=True,
                        remote_name=remote_name, remote_url=remote_url,
                    )
                else:
                    rev_cmd = ('git', 'rev-parse', f'{first_ancestor}^')
                    source = subprocess.check_output(rev_cmd).decode().strip()
                    return _ns(
                        'pre-push', color,
                        origin=local_sha, source=source,
                        remote_name=remote_name, remote_url=remote_url,
                    )

    # nothing to push
    return None


def _run_ns(
        hook_type: str,
        color: bool,
        args: Sequence[str],
        stdin: bytes,
) -> Optional[argparse.Namespace]:
    if hook_type == 'pre-push':
        return _pre_push_ns(color, args, stdin)
    elif hook_type in {'prepare-commit-msg', 'commit-msg'}:
        return _ns(hook_type, color, commit_msg_filename=args[0])
    elif hook_type in {'pre-merge-commit', 'pre-commit'}:
        return _ns(hook_type, color)
    else:
        raise AssertionError(f'unexpected hook type: {hook_type}')


def hook_impl(
        store: Store,
        *,
        config: str,
        color: bool,
        hook_type: str,
        hook_dir: str,
        skip_on_missing_config: bool,
        args: Sequence[str],
) -> int:
    retv, stdin = _run_legacy(hook_type, hook_dir, args)
    _validate_config(retv, config, skip_on_missing_config)
    ns = _run_ns(hook_type, color, args, stdin)
    if ns is None:
        return retv
    else:
        return retv | run(config, store, ns)
