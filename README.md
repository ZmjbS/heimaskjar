# Heimaskjár

A home dashboard for the family: an overview screen with one card per *module*, each
opening a detailed view, filterable by family member. Runs on a Raspberry Pi on the
local network.

The first module is **Veður** (weather): the 07–19 forecast for the capital area
(today's in the morning, tomorrow's from the evening before)
as synchronised looping maps of cloud layers, precipitation, temperature and wind,
drawn from the HARMONIE IG weather model.

## How it works

Scheduled jobs write files; static web pages show them. There's no app server.

```
heimaskjar/
├── heimaskjar/                Python
│   ├── core/config.py         settings file, site.json
│   └── modules/vedur/         weather job (python -m heimaskjar.modules.vedur)
│       └── kort.py            download + map drawing; also a command-line tool
├── web/                       static pages (served at /)
│   ├── index.html             overview: one card per module, family filter
│   ├── vedur/index.html       weather maps player
│   └── shared/                common script (sun, dates, family filter) and styles
├── config/heimaskjar.example.toml
└── deploy/                    install/update scripts, systemd units, Caddy site
```

Output goes to the data folder (`/var/lib/heimaskjar/data` on the Pi), served at `/data/`:

```
data/site.json                 title, family members, enabled modules (from the settings)
data/vedur/summary.json        numbers for the overview card
data/vedur/manifest.json       hours, model run, areas
data/vedur/<area>/<panel>_<HH>.png
```

### Adding a module

A module is a job plus pages:

1. `heimaskjar/modules/<name>/__main__.py` writes `data/<name>/summary.json` (at least
   `module`, `title`, `updated`; add `"people": ["id", …]` if it belongs to certain family
   members) and whatever its detail page needs, then calls `config.write_site(cfg)`.
2. Register it in `MODULES` in `heimaskjar/core/config.py`, and give it a `[<name>]`
   section in the settings.
3. `web/<name>/index.html` for the detail view; a card function in `CARDS` in
   `web/index.html` (without one it gets a plain card).
4. `deploy/systemd/heimaskjar-<name>.service` and `.timer`.

## Running on the Raspberry Pi

Needs 64-bit Raspberry Pi OS (Bookworm); a Pi 4 or 5. All Python libraries have
prebuilt ARM64 wheels, so nothing is compiled.

```sh
sudo mkdir -p /opt/heimaskjar && sudo chown "$USER" /opt/heimaskjar
git clone https://github.com/<you>/heimaskjar.git /opt/heimaskjar
cd /opt/heimaskjar
sudo deploy/install.sh
```

`install.sh` installs Caddy and Python's venv, creates a `heimaskjar` system user,
`/var/lib/heimaskjar/` for data and `/etc/heimaskjar/heimaskjar.toml` for settings,
downloads the elevation file (118 MB), sets up the Python environment, installs and
starts the systemd timer (06:20 and 18:20 every day: today's maps in the morning,
tomorrow's in the evening), and serves the pages on port 8080.
For another port: `sudo HEIMASKJAR_PORT=80 deploy/install.sh` (after `sudo`, which
drops variables set before it). Later runs and updates keep the port used last time.

Then:

```sh
sudoedit /etc/heimaskjar/heimaskjar.toml     # family members, map areas
sudo systemctl start heimaskjar-vedur        # make today's maps now
journalctl -u heimaskjar-vedur -f            # follow it
```

and open `http://<pi-name>.local:8080/`.

**Updating:** after pushing changes to GitHub, on the Pi: `deploy/update.sh`
(git pull, then reinstall; keeps your settings).

**A screen:** a separate Pi with a display only needs a browser in kiosk mode
pointing at this one, e.g. `chromium-browser --kiosk http://leiftur.local:8080/`.

## Trying it on your own machine

```sh
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp config/heimaskjar.example.toml config/heimaskjar.toml     # then edit
venv/bin/python -m heimaskjar.modules.vedur
deploy/dev-server.sh                                         # http://localhost:8000/
```

## The weather module

- **Forecast:** HARMONIE IG (Iceland–Greenland, 2.5 km) from the
  [Danish Meteorological Institute's open data](https://opendatadocs.dmi.govcloud.dk/Data/Forecast_Data).
  Each forecast hour is a ~400 MB GRIB2 file; the job reads the files' headers with HTTP
  range requests and downloads only the ~12 fields it draws (~50 MB per hour).
- **Map areas:** `hofudborg` (capital area), `hofudborg_vitt` (wider), `sudvestur`
  (southwest), `island` (whole country).
- **Panels:** cloud by height (low red, mid blue, high grey), precipitation per hour
  (dots: mostly snow) with sea-level pressure, 2 m temperature, 10 m wind.
- **Player:** space pauses, ←/→ step an hour; the hour bar and page colour follow
  the sun's height over Reykjavík. Address options: `?panels=sky,urkoma&cols=1&area=…`.
- `python -m heimaskjar.modules.vedur.kort --help` for the stand-alone map tool
  (single maps, `--inventory` of a GRIB file, `--list` of available model runs).

## Data and credits

- Weather forecast: HARMONIE IG, Danish Meteorological Institute (DMI) open data.
- Coastline: IS 50V Strandlína; elevation: ÍslandsDEM v1.0 – Náttúrufræðistofnun
  (formerly Landmælingar Íslands), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Downloaded at run time, not stored in this repository; the credit is printed on the pages.

Code: MIT licence (see `LICENSE`).
