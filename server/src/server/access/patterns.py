from functools import cache


class InvalidAccessPattern(ValueError):
    pass


MAX_PATTERNS = 32
MAX_PATTERN_LENGTH = 512


def validate_patterns(patterns: list[str]) -> tuple[str, ...]:
    if not patterns:
        raise InvalidAccessPattern("At least one path pattern is required")
    if len(patterns) > MAX_PATTERNS:
        raise InvalidAccessPattern(f"At most {MAX_PATTERNS} path patterns are allowed")

    validated: list[str] = []
    for raw in patterns:
        pattern = raw.strip()
        if not pattern.startswith("/"):
            raise InvalidAccessPattern(f"Path pattern must start with '/': {raw}")
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise InvalidAccessPattern("Path pattern is too long")
        if any(character in pattern for character in ("?", "#", "%", "\\", "\0")):
            raise InvalidAccessPattern(f"Invalid path pattern: {raw}")

        if pattern != "/" and pattern.endswith("/"):
            pattern = pattern.rstrip("/")
        segments = [] if pattern == "/" else pattern.split("/")[1:]
        if any(not segment for segment in segments):
            raise InvalidAccessPattern(f"Path pattern contains an empty segment: {raw}")
        for segment in segments:
            if "*" in segment and segment not in {"*", "**"}:
                raise InvalidAccessPattern(
                    "Wildcards must occupy an entire path segment"
                )
            if segment in {".", ".."}:
                raise InvalidAccessPattern(f"Invalid path pattern: {raw}")

        if pattern not in validated:
            validated.append(pattern)

    if "/" in validated and len(validated) > 1:
        raise InvalidAccessPattern("'/' already protects the entire site")
    return tuple(validated)


def matches_pattern(pattern: str, path: str) -> bool:
    if pattern == "/":
        return True
    pattern_segments = tuple(pattern.split("/")[1:])
    path_segments = tuple(path.split("/")[1:]) if path != "/" else ()

    @cache
    def matches(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_segments):
            return path_index == len(path_segments)
        segment = pattern_segments[pattern_index]
        if segment == "**":
            return matches(pattern_index + 1, path_index) or (
                path_index < len(path_segments)
                and matches(pattern_index, path_index + 1)
            )
        if path_index == len(path_segments):
            return False
        if segment == "*" or segment == path_segments[path_index]:
            return matches(pattern_index + 1, path_index + 1)
        return False

    return matches(0, 0)


def matches_any(patterns: tuple[str, ...], path: str) -> bool:
    return any(matches_pattern(pattern, path) for pattern in patterns)
