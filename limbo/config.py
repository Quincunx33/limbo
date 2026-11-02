"""Typed, dependency-free project configuration for Limbo."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

class ConfigError(ValueError):
    """Actionable configuration error; never includes secret values."""

@dataclass(frozen=True)
class Section:
    values: dict[str, Any] = field(default_factory=dict)
    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

@dataclass(frozen=True)
class LimboConfig:
    path: Path | None
    app: Section = field(default_factory=Section)
    server: Section = field(default_factory=Section)
    frontend: Section = field(default_factory=Section)
    build: Section = field(default_factory=Section)
    static: Section = field(default_factory=Section)
    security: Section = field(default_factory=Section)
    database: Section = field(default_factory=Section)
    tunnel: Section = field(default_factory=Section)
    sources: dict[str, str] = field(default_factory=dict, repr=False)
    def section(self, name: str) -> Section:
        if name not in _SCHEMA: raise KeyError(name)
        return getattr(self, name)
    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: dict(getattr(self, name).values) for name in _SCHEMA}

_SCHEMA = {
 "app": {"name":str,"debug":bool,"environment":str},
 "server": {"host":str,"port":int,"workers":int,"backlog":int,"request_timeout":(int,float),"max_body_bytes":int},
 "frontend": {"directory":str,"entry":str,"framework":str,"compiler":str,"source_maps":bool},
 "build": {"out_dir":str,"minify":bool,"sourcemap":bool,"clean":bool,"target":str},
 "static": {"url_prefix":str,"directory":str,"spa_fallback":bool,"directory_indexes":bool,"max_age":int},
 "security": {"trusted_hosts":list,"csrf":bool,"secure_cookies":bool,"max_upload_bytes":int,"max_upload_files":int},
 "database": {"url":str,"path":str,"timeout":(int,float)},
 "tunnel": {"enabled":bool,"name":str,"hostname":str,"protocol":str,"cloudflared":str},
}
_ENV = {
 "LIMBO_APP_NAME":("app","name",str),"LIMBO_APP_DEBUG":("app","debug","bool"),
 "LIMBO_SERVER_HOST":("server","host",str),"LIMBO_SERVER_PORT":("server","port",int),
 "LIMBO_SERVER_WORKERS":("server","workers",int),"LIMBO_FRONTEND_DIRECTORY":("frontend","directory",str),
 "LIMBO_BUILD_OUT_DIR":("build","out_dir",str),"LIMBO_STATIC_DIRECTORY":("static","directory",str),
 "LIMBO_DATABASE_PATH":("database","path",str),"LIMBO_TUNNEL_ENABLED":("tunnel","enabled","bool"),
}

def _toml():
    try:
        import tomllib
        return tomllib
    except ImportError:
        try:
            import tomli
            return tomli
        except ImportError as exc:
            raise ConfigError("Python 3.10 requires optional 'tomli' for TOML support; install with: python -m pip install tomli") from exc

def _env_value(raw, kind, name):
    try:
        if kind == "bool":
            value=raw.strip().lower()
            if value in ("1","true","yes","on"): return True
            if value in ("0","false","no","off"): return False
            raise ValueError("expected true/false")
        if kind is int: return int(raw)
        if kind is str: return raw
    except ValueError as exc:
        raise ConfigError(f"Invalid environment override {name}: {exc}") from exc
    raise ConfigError(f"Unsupported environment type for {name}")

def _validate(section,key,value,kind):
    types=kind if isinstance(kind,tuple) else (kind,)
    valid=isinstance(value,types) and (bool in types or not isinstance(value,bool))
    if not valid:
        raise ConfigError(f"[{section}].{key} must be {'/'.join(t.__name__ for t in types)}, got {type(value).__name__}")
    if key=="port" and not 1<=value<=65535: raise ConfigError("[server].port must be between 1 and 65535")
    if key in ("workers","backlog","max_upload_files") and value<=0: raise ConfigError(f"[{section}].{key} must be positive")
    if key in ("max_body_bytes","max_upload_bytes","max_age") and value<0: raise ConfigError(f"[{section}].{key} cannot be negative")
    if key in ("request_timeout","timeout") and value<=0: raise ConfigError(f"[{section}].{key} must be greater than zero")

def load_config(path="limbo.toml", *, environ=None, overrides=None):
    """Precedence: CLI overrides > environment > TOML > safe defaults."""
    file=Path(path).expanduser()
    raw={}
    if file.exists():
        try: raw=_toml().loads(file.read_text(encoding="utf-8"))
        except ConfigError: raise
        except (OSError,UnicodeError) as exc: raise ConfigError(f"Cannot read {file}: {exc}") from exc
        except Exception as exc:
            where=f" at line {exc.lineno}, column {getattr(exc,'colno','?')}" if getattr(exc,"lineno",None) else ""
            raise ConfigError(f"Invalid TOML in {file}{where}: {exc}") from exc
    if not isinstance(raw,dict): raise ConfigError(f"{file} must contain TOML tables")
    unknown=set(raw)-set(_SCHEMA)
    if unknown: raise ConfigError("Unknown configuration section(s): "+", ".join(sorted(unknown)))
    values={name:{} for name in _SCHEMA}; sources={}
    for section,items in raw.items():
        if not isinstance(items,dict): raise ConfigError(f"[{section}] must be a TOML table")
        unknown=set(items)-set(_SCHEMA[section])
        if unknown: raise ConfigError(f"Unknown key(s) in [{section}]: "+", ".join(sorted(unknown)))
        for key,value in items.items():
            _validate(section,key,value,_SCHEMA[section][key]); values[section][key]=value; sources[f"{section}.{key}"]="config"
    env=os.environ if environ is None else environ
    for name,(section,key,kind) in _ENV.items():
        if name in env:
            value=_env_value(env[name],kind,name); _validate(section,key,value,_SCHEMA[section][key])
            values[section][key]=value; sources[f"{section}.{key}"]="environment"
    for section,items in (overrides or {}).items():
        if section not in _SCHEMA or not isinstance(items,dict): raise ConfigError(f"Invalid CLI override section: {section}")
        for key,value in items.items():
            if key not in _SCHEMA[section]: raise ConfigError(f"Unknown CLI override: {section}.{key}")
            if value is None: continue
            _validate(section,key,value,_SCHEMA[section][key]); values[section][key]=value; sources[f"{section}.{key}"]="cli"
    hosts=values["security"].get("trusted_hosts")
    if hosts is not None and any(not isinstance(x,str) or not x.strip() for x in hosts):
        raise ConfigError("[security].trusted_hosts must contain non-empty strings")
    for section,key in (("frontend","directory"),("static","directory"),("build","out_dir")):
        value=values[section].get(key)
        if value is not None and not value.strip(): raise ConfigError(f"[{section}].{key} must not be empty")
    sections={name:Section(values[name]) for name in _SCHEMA}
    return LimboConfig(file.resolve() if file.exists() else None,**sections,sources=sources)
