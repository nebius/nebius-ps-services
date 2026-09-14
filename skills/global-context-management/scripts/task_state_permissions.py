"""Shared task-state permission inspection and explicitly requested repair."""
from pathlib import Path
import stat

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def permission_audit(home: Path, *, repair: bool = False) -> dict[str, int]:
    task_root = home / "task-state"
    result = {"directories_checked": 0, "files_checked": 0, "repaired": 0, "unsafe": 0}
    if home.is_symlink():
        result["unsafe"] += 1
        return result
    if task_root.is_symlink():
        result["unsafe"] += 1
        return result
    if not task_root.exists():
        return result
    if not task_root.is_dir():
        result["unsafe"] += 1
        return result

    directories = [task_root]
    files: list[Path] = []
    for workspace in task_root.iterdir():
        if workspace.is_symlink():
            result["unsafe"] += 1
            continue
        if not workspace.is_dir():
            result["unsafe"] += 1
            continue
        directories.append(workspace)
        for session in workspace.iterdir():
            if session.is_symlink():
                result["unsafe"] += 1
                continue
            if not session.is_dir():
                result["unsafe"] += 1
                continue
            directories.append(session)
            state_file = session / "current.md"
            if state_file.is_symlink():
                result["unsafe"] += 1
            elif state_file.is_file():
                files.append(state_file)
            elif state_file.exists():
                result["unsafe"] += 1

    for directory in directories:
        result["directories_checked"] += 1
        if _mode(directory) != PRIVATE_DIR_MODE:
            if repair:
                directory.chmod(PRIVATE_DIR_MODE)
                result["repaired"] += 1
            else:
                result["unsafe"] += 1
    for state_file in files:
        result["files_checked"] += 1
        if _mode(state_file) != PRIVATE_FILE_MODE:
            if repair:
                state_file.chmod(PRIVATE_FILE_MODE)
                result["repaired"] += 1
            else:
                result["unsafe"] += 1
    return result
