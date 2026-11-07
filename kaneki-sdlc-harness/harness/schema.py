"""JSON Schema validation without dependencies.

Uses the `jsonschema` package when it is installed; otherwise a bundled subset of draft 2020-12 that covers every
keyword the harness contracts use: type, enum, const, required, properties, additionalProperties, items,
minItems, uniqueItems, minLength, pattern, minimum, allOf, anyOf, if/then.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import HarnessError

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(value: Any, typ: str) -> bool:
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[typ])


def _errors(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    out: list[str] = []
    if not isinstance(schema, dict):
        return out
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            return [f"{path or '$'}: expected {' or '.join(types)}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path or '$'}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path or '$'}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path or '$'}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path or '$'}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        out.append(f"{path or '$'}: below {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path or '$'}: needs at least {schema['minItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            out.append(f"{path or '$'}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                out += _errors(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path or '$'}: missing '{key}'")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                out += _errors(value[key], sub, f"{path}.{key}" if path else key)
        extra = schema.get("additionalProperties", True)
        for key in value:
            if key in props:
                continue
            if extra is False:
                out.append(f"{path or '$'}: unknown key '{key}'")
            elif isinstance(extra, dict):
                out += _errors(value[key], extra, f"{path}.{key}" if path else key)
    for sub in schema.get("allOf", []):
        out += _errors(value, sub, path)
    if "anyOf" in schema and not any(not _errors(value, sub, path) for sub in schema["anyOf"]):
        out.append(f"{path or '$'}: matches none of the allowed shapes")
    if "if" in schema and not _errors(value, schema["if"], path):
        out += _errors(value, schema.get("then", {}), path)
    return out


@lru_cache(maxsize=None)
def load_contract(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def errors(value: Any, contract: str) -> list[str]:
    schema = load_contract(contract)
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _errors(value, schema, "")
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'.'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}" for e in validator.iter_errors(value)]


def validate(value: Any, contract: str, what: str) -> None:
    errs = errors(value, contract)
    if errs:
        shown = "; ".join(errs[:8]) + (f"; … {len(errs) - 8} more" if len(errs) > 8 else "")
        raise HarnessError(f"G7: {what} does not match {contract}: {shown}")
