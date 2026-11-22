"""Official mainland Chinese undergraduate taxonomy, separate from job criteria."""

from dataclasses import dataclass
from difflib import get_close_matches
from importlib.resources import files
import json
import re
import unicodedata


def normalize_major_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


@dataclass(frozen=True)
class Major:
    code: str
    name: str
    discipline: str
    group: str | None


_CATALOG = json.loads(files("boss_agent").joinpath("undergraduate_majors.json").read_text(encoding="utf-8"))
CATALOG_VERSION = _CATALOG["version"]
CATALOG_SOURCE = _CATALOG["source"]
CATALOG_SOURCE_SHA256 = _CATALOG["source_sha256"]
_BY_CODE = {
    code: Major(code, name, _CATALOG["disciplines"][code[:2]], _CATALOG["groups"].get(code[:4]))
    for code, name in _CATALOG["majors"].items()
}
_BY_NAME = {normalize_major_name(item.name): item for item in _BY_CODE.values()}
_ALIASES = {normalize_major_name(alias): _BY_NAME[normalize_major_name(name)]
            for alias, name in _CATALOG["aliases"].items()}
_GROUPS = set(_CATALOG["groups"].values())


def find_major(value: str) -> Major | None:
    key = normalize_major_name(value)
    exact = _BY_NAME.get(key) or _ALIASES.get(key) or _BY_CODE.get(key.upper())
    if exact:
        return exact
    base = _direction_base(key)
    return _BY_NAME.get(base) if base else None


def _direction_base(key: str) -> str | None:
    match = re.fullmatch(r"([^()]+)\([^()]{1,24}方向\)", key)
    return match[1] if match else None


def suggest_majors(value: str) -> list[str]:
    key = normalize_major_name(value)
    if len(key) < 4:
        return []
    return [_BY_NAME[name].name for name in get_close_matches(key, _BY_NAME, n=3, cutoff=.8)]


def known_major_group(value: str) -> bool:
    return normalize_major_name(value) in _GROUPS


@dataclass(frozen=True)
class BusinessMajorScope:
    name: str
    version: str
    major_codes: frozenset[str]
    rationale: str

    def __post_init__(self):
        if not all(value.strip() for value in (self.name, self.version, self.rationale)) or not (
            self.major_codes and self.major_codes <= _BY_CODE.keys()
        ):
            raise ValueError("invalid_business_major_scope")


@dataclass(frozen=True)
class BusinessMajorAlias:
    raw_name: str
    target_code: str
    version: str
    rationale: str

    def __post_init__(self):
        if self.target_code not in _BY_CODE or not all(value.strip() for value in (self.raw_name, self.version, self.rationale)):
            raise ValueError("invalid_business_major_alias")


def describe_major(value: str | None, *, scopes: tuple[BusinessMajorScope, ...] = (),
                   aliases: tuple[BusinessMajorAlias, ...] = ()) -> dict:
    """Report official taxonomy and explicit business scopes independently."""
    result = dict(raw_major=value, normalized_major=None, major_code=None, major_category=None,
                  discipline=None, catalog_version=CATALOG_VERSION, catalog_source=CATALOG_SOURCE,
                  mapping_method=None, status="MISSING", business_scopes=[])
    if not value:
        return result
    key = normalize_major_name(value)
    configured = [alias for alias in aliases if normalize_major_name(alias.raw_name) == key]
    business = configured[0] if len(configured) == 1 and not known_major_group(value) else None
    for method, major in (("exact_code", _BY_CODE.get(key.upper())), ("exact_name", _BY_NAME.get(key)),
                          ("verified_alias", _ALIASES.get(key)),
                          ("business_alias", _BY_CODE[business.target_code] if business else None)):
        if major is not None:
            result.update(normalized_major=major.name, major_code=major.code, major_category=major.group,
                          discipline=major.discipline, mapping_method=method, status="MATCHED",
                          business_scopes=[dict(name=scope.name, version=scope.version, rationale=scope.rationale,
                                                matches=major.code in scope.major_codes) for scope in scopes])
            if method == "business_alias":
                result["business_mapping"] = {"version": business.version, "rationale": business.rationale}
            return result
    base = _direction_base(key)
    if base in _BY_NAME:
        result = describe_major(_BY_NAME[base].name, scopes=scopes)
        return {**result, "raw_major": value, "mapping_method": "explicit_direction_suffix"}
    if known_major_group(value):
        result.update(major_category=key, mapping_method="explicit_group", status="GROUP_ONLY")
    else:
        result.update(status="UNMAPPED")
        if len(configured) > 1:
            result["mapping_issue"] = "conflicting_business_aliases"
    return result
