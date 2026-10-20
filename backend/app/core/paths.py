"""Translate host paths at I/O boundaries; keep persisted Windows paths portable."""

from pathlib import Path, PureWindowsPath

from .config import Settings, get_settings


class UnsharedPath(ValueError):
    pass


def local_path(value: str | Path, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    text = str(value)
    if not settings.path_mappings:
        return Path(text)
    windows = PureWindowsPath(text)
    if windows.drive:
        for host, mounted in sorted(settings.path_mappings.items(), key=lambda pair: -len(pair[0])):
            try:
                suffix = windows.relative_to(PureWindowsPath(host))
            except ValueError:
                continue
            if ".." in suffix.parts:
                raise UnsharedPath("Host path cannot escape its shared folder")
            return Path(mounted).joinpath(*suffix.parts)
        raise UnsharedPath(f"Windows folder is not shared with ReelMaker: {text}")
    return Path(text)


def source_path(value: str | Path, settings: Settings) -> Path:
    path = local_path(value, settings).resolve()
    if settings.container_mode and not any(
        path.is_relative_to(Path(mount).resolve()) for mount in settings.media_mounts.values()
    ):
        raise UnsharedPath("Choose a folder shared with ReelMaker in Docker settings")
    return path


def host_path(value: Path, settings: Settings) -> str:
    resolved = value.resolve()
    for host, mounted in sorted(settings.path_mappings.items(), key=lambda pair: -len(pair[1])):
        try:
            suffix = resolved.relative_to(Path(mounted).resolve())
        except ValueError:
            continue
        return str(PureWindowsPath(host).joinpath(*suffix.parts))
    return str(resolved)
