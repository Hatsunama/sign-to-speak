"""Filesystem guards and publication for generated Android asset trees."""

import os
from pathlib import Path
import shutil
import stat
import tempfile


def is_linked(info):
    return (stat.S_ISLNK(info.st_mode) or
            getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def no_links(path):
    """Check the lexical ancestors before resolving the path."""
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if is_linked(info):
            raise ValueError(f'Linked or reparse-point path is not admitted: {component}')
    return path.resolve()


def managed_files(output, allowed_files):
    """Inspect an existing tree without descending into unmanaged entries."""
    if not output.exists():
        return set()
    if not output.is_dir():
        raise ValueError(f'Asset output must be a directory: {output}')
    files = {Path(name) for name in allowed_files}
    directories = {parent for file in files for parent in file.parents}
    volume = output.stat().st_dev
    found = set()

    def inspect(directory):
        for item in directory.iterdir():
            relative = item.relative_to(output)
            info = item.lstat()
            if is_linked(info) or info.st_dev != volume:
                raise ValueError(f'Linked or mounted asset output entry: {item}')
            if relative in files and stat.S_ISREG(info.st_mode):
                found.add(relative)
            elif relative in directories and stat.S_ISDIR(info.st_mode):
                inspect(item)
            else:
                raise ValueError(f'Unmanaged asset output entry: {item}')

    inspect(output)
    return found


def publish_directory(staged, output, check_existing):
    """Replace an owned tree with same-filesystem renames; restore on failure."""
    no_links(output)
    lock = output.with_name(f'.{output.name}.publish-lock')
    lock.mkdir()
    try:
        # Another preparer may have published since this process staged assets.
        no_links(output)
        check_existing()
        backup = Path(tempfile.mkdtemp(prefix=f'.{output.name}-backup-', dir=output.parent))
        previous = backup / 'previous'
        try:
            if output.exists():
                os.replace(output, previous)
            os.replace(staged, output)
        except BaseException:
            # If publication completed before an interruption, staged is gone
            # and the new verified output is already in place.
            if previous.exists() and staged.exists():
                try:
                    if output.exists() or output.is_symlink():
                        raise OSError(f'Publication destination reappeared: {output}')
                    os.replace(previous, output)
                except OSError as error:
                    raise OSError(f'Could not restore previous assets; preserved at {previous}') from error
            raise
        finally:
            # Leave the previous tree available if the filesystem also refused
            # rollback. Never let temporary staging cleanup remove that copy.
            if not previous.exists() or not staged.exists():
                shutil.rmtree(backup)
    finally:
        lock.rmdir()
