"""Loading heimaskjar's settings file and writing the site index for the web pages."""
import json
import os
import tomllib
from pathlib import Path

# looked for in this order when --config isn't given
SEARCH = [
    os.environ.get("HEIMASKJAR_CONFIG"),
    "/etc/heimaskjar/heimaskjar.toml",
    Path(__file__).resolve().parents[2] / "config" / "heimaskjar.toml",
]


class Config(dict):
    """The parsed settings file. Relative paths are relative to the file itself."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        with open(self.path, "rb") as fh:
            super().__init__(tomllib.load(fh))
        self.setdefault("heimaskjar", {})

    def path_of(self, value):
        """A path from the settings, made absolute; None for empty values."""
        if not value:
            return None
        p = Path(os.path.expanduser(str(value)))
        return p if p.is_absolute() else (self.path.parent / p).resolve()

    @property
    def data_dir(self):
        return self.path_of(self["heimaskjar"].get("data_dir", "../data"))

    @property
    def cache_dir(self):
        return self.path_of(self["heimaskjar"].get("cache_dir", "../cache"))

    def module(self, name):
        return self.get(name, {})


def load(path=None):
    for candidate in [path] + ([] if path else SEARCH):
        if candidate and Path(candidate).exists():
            return Config(candidate)
    raise SystemExit("No settings file found. Copy config/heimaskjar.example.toml to "
                     "/etc/heimaskjar/heimaskjar.toml (or config/heimaskjar.toml) and edit it, "
                     "or pass --config.")


# Every module the overview page knows about. A module listed here appears on the
# overview once it is enabled in the settings and has written its summary.json.
MODULES = {
    "vedur": {"title": "Veður", "page": "vedur/"},
}


def write_site(cfg):
    """data/site.json: title, family members and enabled modules, for the web pages."""
    site = {
        "title": cfg["heimaskjar"].get("title", "Heimaskjár"),
        "people": [{"id": p["id"], "name": p.get("name", p["id"]), "colour": p.get("colour")}
                   for p in cfg.get("people", [])],
        "modules": [dict(id=m, summary=f"{m}/summary.json", **info)
                    for m, info in MODULES.items() if cfg.module(m).get("enabled", True)
                    and m in cfg],
    }
    out = cfg.data_dir / "site.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(site, ensure_ascii=False, indent=1))
    tmp.replace(out)
    return out
