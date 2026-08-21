# Hike Routes

Interactive hiking route website with Strava-accurate elevation data, satellite maps, and photo integration.

## Features

- **Accurate elevation calculations** with GPS noise smoothing
- **Interactive Leaflet maps** with street/satellite toggle
- **Photo integration** from Immich albums along the trail
- **Strava GPX fetcher** for automatic imports (optional)
- **DST-aware local time** (Europe/Bucharest)

## Quick Start

```bash
# Generate pages from GPX files
python3 generate.py

# Serve the website
python3 -m http.server 8081 --directory output
```

## Setup

1. Place `.gpx` files in the `data/` directory
2. Copy `config.json.example` to `config.json` and fill in your Immich credentials
3. Run `python3 generate.py` to create HTML pages

### Strava Integration (Optional)

```bash
# Fetch hiking GPX files from Strava and generate pages
python3 fetch_strava.py --token YOUR_TOKEN --generate
```

## Structure

```
hike_routes/
├── data/              # GPX files go here
├── output/            # Generated HTML (gitignored)
├── templates/         # HTML templates
├── generate.py        # Main generator
├── fetch_strava.py    # Strava fetcher
├── config.json.example # Example config
└── start.sh           # Startup script
```

## Configuration

Add entries to `config.json` for each hike:

```json
{
  "hikes": {
    "hike-name": {
      "title": "Hike Name",
      "date": "2026-08-16",
      "gpx_file": "20260816_101401.gpx",
      "immich_album_id": "YOUR_ALBUM_ID",
      "immich_base_url": "https://immich.example.com",
      "immich_api_key": "YOUR_KEY"
    }
  }
}
```

Immich fields are optional — pages will still be generated without photo integration.

## License

MIT License — see [LICENSE](LICENSE)
