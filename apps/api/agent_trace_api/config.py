import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

DeploymentProfile = Literal["local", "public-preview"]
DisabledCapability = Literal[
    "codex-cli",
    "external-network",
    "filesystem-access",
    "process-execution",
    "scenario-import-export",
    "persistent-memory-writes",
    "anonymous-learning-writes",
]

PUBLIC_PREVIEW_DISABLED_CAPABILITIES: tuple[DisabledCapability, ...] = (
    "codex-cli",
    "external-network",
    "filesystem-access",
    "process-execution",
    "scenario-import-export",
    "persistent-memory-writes",
    "anonymous-learning-writes",
)


@dataclass(frozen=True)
class RuntimePolicy:
    deployment_profile: DeploymentProfile
    cors_origins: tuple[str, ...]
    max_request_body_bytes: int = 524_288
    general_write_limit_per_minute: int = 90
    expensive_write_limit_per_minute: int = 8
    sse_max_lifetime_seconds: int = 300

    @property
    def public_preview(self) -> bool:
        return self.deployment_profile == "public-preview"

    @property
    def display_mode(self) -> Literal["LOCAL WORKSTATION", "PUBLIC PREVIEW / SAFE LAB MODE"]:
        if self.public_preview:
            return "PUBLIC PREVIEW / SAFE LAB MODE"
        return "LOCAL WORKSTATION"

    @property
    def disabled_capabilities(self) -> tuple[DisabledCapability, ...]:
        if self.public_preview:
            return PUBLIC_PREVIEW_DISABLED_CAPABILITIES
        return ()

    @property
    def trusted_hosts(self) -> tuple[str, ...]:
        hosts = {"127.0.0.1", "localhost"}
        hosts.update(urlsplit(origin).hostname or "" for origin in self.cors_origins)
        hosts.discard("")
        return tuple(sorted(hosts))

    def external_base_url(self, request_base_url: str) -> str:
        if self.public_preview:
            return self.cors_origins[0].rstrip("/")
        return request_base_url.rstrip("/")


def _bounded_integer(
    environment: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _cors_origins(environment: Mapping[str, str], profile: DeploymentProfile) -> tuple[str, ...]:
    default = (
        "http://127.0.0.1:4173,http://127.0.0.1:5173"
        if profile == "local"
        else "http://127.0.0.1:47800"
    )
    raw_origins = environment.get("AGENT_TRACE_CORS_ORIGINS", default)
    origin_parts = raw_origins.split(",")
    if any(not part or part != part.strip() for part in origin_parts):
        raise RuntimeError("AGENT_TRACE_CORS_ORIGINS must contain canonical HTTP(S) origins")
    origins = tuple(origin_parts)
    if not origins or len(origins) > 8 or len(set(origins)) != len(origins):
        raise RuntimeError("AGENT_TRACE_CORS_ORIGINS must contain between one and eight origins")
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            parsed_port = parsed.port
        except ValueError as error:
            raise RuntimeError(
                "AGENT_TRACE_CORS_ORIGINS must contain canonical HTTP(S) origins"
            ) from error
        hostname = parsed.hostname
        canonical_host = f"[{hostname}]" if hostname is not None and ":" in hostname else hostname
        canonical_authority = canonical_host
        if canonical_authority is not None and parsed_port is not None:
            canonical_authority = f"{canonical_authority}:{parsed_port}"
        canonical_origin = (
            f"{parsed.scheme}://{canonical_authority}" if canonical_authority is not None else None
        )
        default_port = 80 if parsed.scheme == "http" else 443 if parsed.scheme == "https" else None
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or hostname.endswith(".")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != ""
            or parsed.query
            or parsed.fragment
            or origin != canonical_origin
            or parsed_port == default_port
            or "*" in origin
            or "%" in origin
            or "\\" in origin
            or any(
                character.isspace() or ord(character) < 33 or ord(character) == 127
                for character in origin
            )
        ):
            raise RuntimeError("AGENT_TRACE_CORS_ORIGINS must be an exact HTTP(S) origin allowlist")
    if profile == "public-preview" and len(origins) != 1:
        raise RuntimeError(
            "AGENT_TRACE_CORS_ORIGINS must contain exactly one canonical origin in public preview"
        )
    return origins


def runtime_policy_from_environment(
    environment: Mapping[str, str] = os.environ,
) -> RuntimePolicy:
    raw_profile = environment.get("AGENT_TRACE_DEPLOYMENT_PROFILE", "local")
    if raw_profile not in {"local", "public-preview"}:
        raise RuntimeError("AGENT_TRACE_DEPLOYMENT_PROFILE must be local or public-preview")
    profile = cast(DeploymentProfile, raw_profile)
    return RuntimePolicy(
        deployment_profile=profile,
        cors_origins=_cors_origins(environment, profile),
        max_request_body_bytes=_bounded_integer(
            environment,
            "AGENT_TRACE_MAX_REQUEST_BODY_BYTES",
            default=524_288,
            minimum=16_384,
            maximum=1_048_576,
        ),
        general_write_limit_per_minute=_bounded_integer(
            environment,
            "AGENT_TRACE_GENERAL_WRITE_LIMIT_PER_MINUTE",
            default=90,
            minimum=1,
            maximum=600,
        ),
        expensive_write_limit_per_minute=_bounded_integer(
            environment,
            "AGENT_TRACE_EXPENSIVE_WRITE_LIMIT_PER_MINUTE",
            default=8,
            minimum=1,
            maximum=60,
        ),
        sse_max_lifetime_seconds=_bounded_integer(
            environment,
            "AGENT_TRACE_SSE_MAX_LIFETIME_SECONDS",
            default=300,
            minimum=30,
            maximum=900,
        ),
    )


def local_database_relative_path(*, repository_root: Path = REPOSITORY_ROOT) -> Path:
    config_path = repository_root / "config/local-database-path.txt"
    relative = Path(config_path.read_text(encoding="utf-8").strip())
    if relative.is_absolute() or relative.parent != Path(".local") or ".." in relative.parts:
        raise RuntimeError("local database path must be one file directly inside .local")
    if relative.suffix != ".sqlite3":
        raise RuntimeError("local database path must use the .sqlite3 suffix")
    return relative


def default_database_path(*, repository_root: Path = REPOSITORY_ROOT) -> Path:
    relative = local_database_relative_path(repository_root=repository_root)
    local_directory = repository_root / ".local"
    if local_directory.is_symlink():
        raise RuntimeError("repository .local directory must not be a symbolic link")
    try:
        local_directory.mkdir(parents=False, exist_ok=True)
    except (FileExistsError, NotADirectoryError) as error:
        raise RuntimeError("repository .local path must be a real directory") from error
    physical_local_directory = repository_root.resolve() / ".local"
    if not local_directory.is_dir() or local_directory.resolve() != physical_local_directory:
        raise RuntimeError("repository .local directory must resolve inside the repository")

    database_path = repository_root / relative
    protected_paths = (
        database_path,
        Path(f"{database_path}-shm"),
        Path(f"{database_path}-wal"),
    )
    if any(path.is_symlink() for path in protected_paths):
        raise RuntimeError("local database files must not be symbolic links")
    if database_path.parent.resolve() != local_directory.resolve():
        raise RuntimeError("local database path must resolve inside repository .local")
    return database_path
