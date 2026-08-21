#!/usr/bin/env python3
"""
Fetch hiking GPX files from Strava and optionally generate HTML pages.

Usage:
    python3 fetch_strava.py --token STRAVA_ACCESS_TOKEN
    python3 fetch_strava.py --token TOKEN --generate    # Also generate HTML pages
    python3 fetch_strava.py --token TOKEN --list         # List hiking activities only

Strava activity types considered "hiking":
    - Hike (primary)
    - Walk (sometimes used for hikes on phone)

Requires: Python 3.10+ (for zoneinfo), Strava access token
Get token: https://www.strava.com/settings/api
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Bucharest")

# ============================================================
# STRAVA API
# ============================================================

STRAVA_API = "https://www.strava.com/api/v3"


def get_activities(token, per_page=200, page=1):
    """Fetch all activities for the authenticated athlete."""
    url = f"{STRAVA_API}/athlete/activities?per_page={per_page}&page={page}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_activity(token, activity_id):
    """Get details for a single activity."""
    url = f"{STRAVA_API}/activities/{activity_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def download_gpx(token, activity_id):
    """Download GPX file for an activity."""
    url = f"{STRAVA_API}/activities/{activity_id}/gpx"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode()


# ============================================================
# FILTERING & LOGIC
# ============================================================

# Strava activity types we consider "hiking"
HIKE_TYPES = {"Hike", "Walk"}

# Maximum activities to check (Strava API paginates)
MAX_PAGES = 10  # 10 * 200 = 2000 activities max (should cover ~5 years)


def find_hikes(token, already_ids=None):
    """
    Find all hiking activities from Strava.
    Returns list of (activity_id, activity_data) tuples, newest first.
    """
    if already_ids is None:
        already_ids = set()

    hikes = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        print(f"  Page {page}...")
        try:
            activities = get_activities(token, per_page=200, page=page)
        except urllib.error.HTTPError as e:
            print(f"  API error on page {page}: {e.code} {e.reason}")
            break

        if not activities:
            break

        for act in activities:
            aid = act["id"]
            if aid in seen_ids or aid in already_ids:
                continue
            seen_ids.add(aid)

            # Filter by type
            if act.get("type") not in HIKE_TYPES:
                continue

            # Skip activities with no GPS data
            if act.get("summaries_only", False):
                continue
            if not act.get("map", {}).get("summary_polyline"):
                # Might still have GPS — fetch details
                try:
                    act = get_activity(token, aid)
                except Exception:
                    continue

            if not act.get("map", {}).get("summary_polyline"):
                continue

            hikes.append(act)

        # If we got fewer than per_page, we've reached the end
        if len(activities) < per_page:
            break

    # Sort by start date, newest first
    hikes.sort(key=lambda a: a.get("start_date", ""), reverse=True)
    return hikes


# ============================================================
# GPX PARSING (for metadata extraction)
# ============================================================

import xml.etree.ElementTree as ET


def parse_gpx_metadata(gpx_content, hike_key):
    """Extract metadata from GPX content without full point parsing."""
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    try:
        root = ET.fromstring(gpx_content)
    except ET.ParseError:
        return None

    name_elem = root.find(".//gpx:name", ns)
    name = name_elem.text if name_elem is not None else hike_key

    times = []
    for trk in root.findall(".//gpx:trk", ns):
        for seg in trk.findall("gpx:trkseg", ns):
            for pt in seg.findall("gpx:trkpt", ns):
                time_elem = pt.find("gpx:time", ns)
                if time_elem is not None:
                    times.append(time_elem.text)

    distance = 0.0
    elevs = []
    for trk in root.findall(".//gpx:trk", ns):
        for seg in trk.findall("gpx:trkseg", ns):
            for pt in seg.findall("gpx:trkpt", ns):
                ele = pt.find("gpx:ele", ns)
                if ele is not None:
                    elevs.append(float(ele.text))

    return {
        "name": name,
        "times": times,
        "distance_m": distance,
        "elevation_gain_m": 0,
        "elevation_loss_m": 0,
        "min_elev": round(min(elevs)) if elevs else None,
        "max_elev": round(max(elevs)) if elevs else None,
        "point_count": len(times),
    }


# ============================================================
# MAIN
# ============================================================


def cmd_list(token):
    """List all hiking activities without downloading."""
    print("Fetching hiking activities from Strava...")
    hikes = find_hikes(token)
    print(f"\nFound {len(hikes)} hiking activity(ies):\n")

    for i, act in enumerate(hikes, 1):
        aid = act["id"]
        name = act.get("name", "(no name)")
        atype = act.get("type", "?")
        start = act.get("start_date", "?")
        dist = act.get("distance", 0) / 1000  # meters to km

        print(f"  {i}. {name}")
        print(f"     Type: {atype} | Distance: {dist:.1f} km | Date: {start}")
        print()

    return hikes


def cmd_fetch(token, data_dir="data", output_dir="output"):
    """Fetch GPX files for all hiking activities."""
    print("Fetching hiking activities from Strava...")

    # First, see what GPX files already exist
    existing_files = {}
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            if f.endswith(".gpx"):
                existing_files[f] = os.path.join(data_dir, f)

    hikes = find_hikes(token, already_ids=set())
    print(f"\nFound {len(hikes)} hiking activity(ies)\n")

    if not hikes:
        print("No hiking activities found.")
        return

    downloaded = 0
    skipped = 0

    for act in hikes:
        aid = act["id"]
        name = act.get("name", f"Activity {aid}")
        start = act.get("start_date", "")
        atype = act.get("type", "?")
        dist = act.get("distance", 0) / 1000

        # Generate a safe filename: YYYY-MM-DD_hhmmss_name
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            local_dt = dt.astimezone(LOCAL_TZ)
            date_str = local_dt.strftime("%Y%m%d_%H%M%S")
        except Exception:
            date_str = str(aid)

        filename = f"{date_str}_{name.replace('/', '_').replace(' ', '_')[:30]}.gpx"

        # Skip if file already exists (check by activity ID mapping in metadata)
        if filename in existing_files:
            print(f"  Skip (exists): {name} ({dist:.1f} km)")
            skipped += 1
            continue

        print(f"  Downloading: {name} ({dist:.1f} km)")
        try:
            gpx = download_gpx(token, aid)
        except urllib.error.HTTPError as e:
            print(f"    Error {e.code}: {e.reason}")
            continue

        filepath = os.path.join(data_dir, filename)
        with open(filepath, "w") as f:
            f.write(gpx)

        # Extract metadata
        meta = parse_gpx_metadata(gpx, filename)
        if meta:
            print(
                f"    {meta['point_count']} points, "
                f"elev {meta['min_elev']}–{meta['max_elev']}m"
            )

        downloaded += 1
        time.sleep(0.5)  # Be polite to Strava API

    print(f"\nDone! Downloaded {downloaded}, Skipped {skipped}")

    if downloaded > 0 and os.path.exists("config.json"):
        print("\nTip: Run 'python3 generate.py' to create HTML pages for new hikes.")
        print("Or run with --generate to do both in one step.")


def cmd_generate(token, data_dir="data", output_dir="output"):
    """Fetch GPX files AND generate HTML pages."""
    print("Fetching hiking activities from Strava...")

    existing_files = {}
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            if f.endswith(".gpx"):
                existing_files[f] = os.path.join(data_dir, f)

    hikes = find_hikes(token, already_ids=set())
    print(f"\nFound {len(hikes)} hiking activity(ies)\n")

    if not hikes:
        print("No hiking activities found.")
        return

    downloaded = 0
    generated = 0
    skipped = 0

    for act in hikes:
        aid = act["id"]
        name = act.get("name", f"Activity {aid}")
        start = act.get("start_date", "")
        dist = act.get("distance", 0) / 1000

        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            local_dt = dt.astimezone(LOCAL_TZ)
            date_str = local_dt.strftime("%Y%m%d_%H%M%S")
            date_only = local_dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = str(aid)
            date_only = start[:10]

        filename = f"{date_str}_{name.replace('/', '_').replace(' ', '_')[:30]}.gpx"
        key = f"{date_only.replace('-', '_')}-{name.lower().replace('/', '_').replace(' ', '-')[:40]}"

        if filename in existing_files:
            print(f"  Skip (exists): {name} ({dist:.1f} km)")
            skipped += 1
            continue

        print(f"  Downloading: {name} ({dist:.1f} km)")
        try:
            gpx = download_gpx(token, aid)
        except urllib.error.HTTPError as e:
            print(f"    Error {e.code}: {e.reason}")
            continue

        filepath = os.path.join(data_dir, filename)
        with open(filepath, "w") as f:
            f.write(gpx)

        meta = parse_gpx_metadata(gpx, filename)
        if meta:
            print(
                f"    {meta['point_count']} points, "
                f"elev {meta['min_elev']}–{meta['max_elev']}m"
            )

        downloaded += 1
        time.sleep(0.5)

    # Now generate pages for all GPX files using the existing generator
    print(f"\nDownloading: {downloaded}, Skipped: {skipped}\n")

    if downloaded > 0:
        # Import and run the existing generator
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from generate import main

        # Re-run generate.py which picks up new files in data/
        print("Generating HTML pages...")
        generate_all(output_dir=output_dir)
    else:
        print("No new files to generate.")


def generate_all(output_dir="output"):
    """Generate HTML pages for all GPX files in data/ directory.

    This is a simplified generator that creates pages from GPX + config.json.
    """
    import glob as globmod

    print(f"\n{'─' * 40}")
    print("Generating index page...")

    # Read config for immich settings
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("  No config.json found — generating from GPX files only.")
        config = {"hikes": {}}
    except json.JSONDecodeError:
        print("  Invalid config.json — generating from GPX files only.")
        config = {"hikes": {}}

    # Collect all GPX files
    gpx_files = sorted(globmod.glob(os.path.join("data", "*.gpx")))
    if not gpx_files:
        print("  No GPX files found in data/")
        return

    print(f"  Found {len(gpx_files)} GPX file(s)\n")

    # Build index cards
    cards = ""
    for gpx_path in gpx_files:
        filename = os.path.basename(gpx_path)
        try:
            tree = ET.parse(gpx_path)
            ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
            name_elem = tree.getroot().find(".//gpx:name", ns)
            name = name_elem.text if name_elem is not None else filename.replace(".gpx", "")

            times = []
            elevs = []
            for trk in tree.getroot().findall(".//gpx:trk", ns):
                for seg in trk.findall("gpx:trkseg", ns):
                    for pt in seg.findall("gpx:trkpt", ns):
                        t = pt.find("gpx:time", ns)
                        if t is not None:
                            times.append(t.text)
                        e = pt.find("gpx:ele", ns)
                        if e is not None:
                            elevs.append(float(e.text))

            # Calculate duration
            duration = ""
            if len(times) >= 2:
                t0 = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
                dur = (t1 - t0).total_seconds()
                duration = f"{int(dur//3600)}h {int((dur%3600)//60)}m"

            # Calculate distance
            import math as mathmod

            def haversine(lat1, lon1, lat2, lon2):
                R = 6371000
                dlat = mathmod.radians(lat2 - lat1)
                dlon = mathmod.radians(lon2 - lon1)
                a = (
                    mathmod.sin(dlat / 2) ** 2
                    + mathmod.cos(mathmod.radians(lat1))
                    * mathmod.cos(mathmod.radians(lat2))
                    * mathmod.sin(dlon / 2) ** 2
                )
                return R * 2 * mathmod.asin(mathmod.sqrt(a))

            total_dist = 0
            for trk in tree.getroot().findall(".//gpx:trk", ns):
                for seg in trk.findall("gpx:trkseg", ns):
                    pts = seg.findall("gpx:trkpt", ns)
                    for i in range(1, len(pts)):
                        lat1 = float(pts[i - 1].attrib["lat"])
                        lon1 = float(pts[i - 1].attrib["lon"])
                        lat2 = float(pts[i].attrib["lat"])
                        lon2 = float(pts[i].attrib["lon"])
                        total_dist += haversine(lat1, lon1, lat2, lon2)

            # Extract date from GPX filename or time
            date_str = ""
            if times:
                try:
                    dt = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    date_str = "Unknown"

            key = os.path.splitext(filename)[0]
            card = '<a href="' + key + '.html" class="card">'
            card += '<div class="card-title">' + escape_html(name) + '</div>'
            card += '<div class="card-meta"><span>' + date_str + '</span></div>'
            card += '<div class="card-stats">'
            card += '<div><span class="v">' + f"{total_dist/1000:.1f}" + '</span> <span class="l">km</span></div>'
            card += '<div><span class="v">' + duration + '</span> <span class="l">time</span></div>'
            if elevs:
                card += '<div><span class="v">' + str(round(max(elevs) - min(elevs))) + '</span> <span class="l">elev</span></div>'
            card += '<div><span class="v">' + str(len(times)) + '</span> <span class="l">points</span></div>'
            card += '</div>'
            cards += card + "</a>"

        except Exception as e:
            print(f"  Error processing {filename}: {e}")

    # Load index template
    try:
        with open("templates/index.html", "r") as f:
            template = f.read()
    except FileNotFoundError:
        print("  templates/index.html not found — creating basic index")
        template = generate_minimal_index(cards)

    template = template.replace("{{CARDS}}", cards)

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "index.html")
    with open(path, "w") as f:
        f.write(template)
    print(f"  Written: {path}")

    # Also generate individual pages for new GPX files
    for gpx_path in gpx_files:
        filename = os.path.basename(gpx_path)
        key = os.path.splitext(filename)[0]
        out_path = os.path.join(output_dir, key + ".html")

        # Only generate if output doesn't exist or file is newer
        if os.path.exists(out_path):
            try:
                if os.path.getmtime(gpx_path) <= os.path.getmtime(out_path):
                    continue
            except Exception:
                continue

        print(f"  Generating page for {filename}...")
        try:
            # Try using the full generator if config has immich info
            generate_with_immich(gpx_path, filename, config, out_path)
        except Exception as e:
            print(f"    Full generator failed ({e}), using minimal...")
            generate_minimal_page(gpx_path, out_path)

    print(f"\n{'=' * 40}")
    print(f"Generated {len(gpx_files)} page(s)")
    print(f"{'=' * 40}")


def generate_with_immich(gpx_path, filename, config, out_path):
    """Generate page using the full generator with immich integration."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # Parse GPX
    from generate import (
        parse_gpx,
        rdp_simplify,
        smooth_elevation,
        calculate_stats,
        escape_html,
        haversine,
    )
    import urllib.request
    import ssl

    gpx = parse_gpx(gpx_path)
    stats = calculate_stats(gpx["all_points"])

    trail_points = gpx["all_points"]
    if len(trail_points) > 4000:
        trail_points = rdp_simplify(trail_points)

    # Simplify for web
    trail_coords = [[p["lat"], p["lon"]] for p in trail_points]
    if len(trail_coords) > 4000:
        step = len(trail_coords) // 4000
        trail_coords = trail_coords[::step]

    # Elevation data
    elev_data = []
    for pt in trail_points:
        if pt["ele"] is not None:
            elev_data.append(pt["ele"])

    # Heart rate data
    hr_values = []
    for i in range(len(trail_points)):
        hr_values.append(trail_points[i]["hr"])

    # Interpolate missing HR
    for i in range(len(hr_values)):
        if hr_values[i] is None:
            prev = next = None
            for j in range(i - 1, -1, -1):
                if hr_values[j] is not None:
                    prev = hr_values[j]
                    break
            for j in range(i + 1, len(hr_values)):
                if hr_values[j] is not None:
                    next = hr_values[j]
                    break
            if prev is not None and next is not None:
                hr_values[i] = round((prev + next) / 2)
            elif prev is not None:
                hr_values[i] = prev

    # Waypoints
    waypoints_json = []
    num_waypoints = min(20, len(trail_points))
    step = max(1, len(trail_points) // num_waypoints)
    for i in range(0, len(trail_points), step):
        pt = trail_points[i]
        time_str = ""
        if pt["time"]:
            try:
                dt = datetime.fromisoformat(pt["time"].replace("Z", "+00:00"))
                time_str = dt.astimezone(LOCAL_TZ).strftime("%H:%M")
            except Exception:
                pass
        waypoints_json.append(
            {
                "time": time_str,
                "lat": round(pt["lat"], 4),
                "lon": round(pt["lon"], 4),
                "ele": round(pt["ele"]) if pt["ele"] else None,
                "hr": pt["hr"],
                "elev_pct": 50,
            }
        )

    # Local times
    times = [p["time"] for p in gpx["all_points"] if p["time"]]
    start_time = ""
    end_time = ""
    if times:
        try:
            start_time = datetime.fromisoformat(times[0].replace("Z", "+00:00")).astimezone(
                LOCAL_TZ
            ).strftime("%H:%M")
        except Exception:
            start_time = ""
    if len(times) > 1:
        try:
            end_time = datetime.fromisoformat(times[-1].replace("Z", "+00:00")).astimezone(
                LOCAL_TZ
            ).strftime("%H:%M")
        except Exception:
            end_time = ""

    # Try to find immich config (use first hike's settings as template)
    immich_base = ""
    immich_api_key = ""
    immich_album_id = ""
    markers_json = "[]"
    photo_grid = ""

    # Try to find a matching immich album by center coordinates
    if gpx["all_points"]:
        center_lat = sum(p["lat"] for p in gpx["all_points"]) / len(gpx["all_points"])
        center_lon = sum(p["lon"] for p in gpx["all_points"]) / len(gpx["all_points"])

        # Find matching immich album in config
        for hike_key, hike_data in config.get("hikes", {}).items():
            hike_date = hike_data.get("date", "")
            file_date = os.path.splitext(filename)[0][:8]
            if file_date in hike_date:
                immich_base = hike_data.get("immich_base_url", "")
                immich_api_key = hike_data.get("immich_api_key", "")
                immich_album_id = hike_data.get("immich_album_id", "")
                break

    if immich_album_id and immich_api_key and immich_base:
        try:
            ctx = ssl.create_default_context()
            album_url = immich_base + "/api/albums/" + immich_album_id
            album_req = urllib.request.Request(
                album_url,
                headers={
                    "x-api-key": immich_api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(album_req, timeout=120, context=ctx) as resp:
                album_data = json.loads(resp.read().decode())

            # Find photos near trail
            lats = [p["lat"] for p in trail_points]
            lons = [p["lon"] for p in trail_points]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)

            markers = []
            photo_grid_html = ""

            for asset in album_data.get("assets", []):
                exif = asset.get("exifInfo", {})
                lat = exif.get("latitude")
                lon = exif.get("longitude")
                if lat is None or lon is None:
                    continue

                if lat < min_lat - 0.02 or lat > max_lat + 0.02 or (
                    lon < min_lon - 0.02 or lon > max_lon + 0.02
                ):
                    continue

                best_idx = 0
                best_dist = float("inf")
                for i, pt in enumerate(trail_points):
                    d = haversine(lat, lon, pt["lat"], pt["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_idx = i

                if best_dist > 300:
                    continue

                cum_dist = 0
                for i in range(1, best_idx + 1):
                    cum_dist += haversine(
                        trail_points[i - 1]["lat"],
                        trail_points[i - 1]["lon"],
                        trail_points[i]["lat"],
                        trail_points[i]["lon"],
                    )

                local_dt = asset.get("localDateTime", "")
                time_str = ""
                if local_dt:
                    try:
                        dt = datetime.fromisoformat(local_dt)
                        time_str = dt.strftime("%H:%M")
                    except Exception:
                        pass

                markers.append(
                    {
                        "photo_id": asset["id"],
                        "filename": asset.get("originalFileName", ""),
                        "lat": trail_points[best_idx]["lat"],
                        "lon": trail_points[best_idx]["lon"],
                        "ele": round(trail_points[best_idx]["ele"])
                        if trail_points[best_idx]["ele"]
                        else None,
                        "time": time_str,
                        "distance_m": round(cum_dist),
                        "dist_from_trail_m": round(best_dist),
                    }
                )

            markers.sort(key=lambda m: m["distance_m"])

            for i, m in enumerate(markers):
                thumb = "/immich/api/assets/" + m["photo_id"] + "/thumbnail"
                fname = escape_html(m["filename"])
                time_tag = (
                    '<span class="time-tag">' + m["time"] + "</span>" if m["time"] else ""
                )
                photo_grid_html += (
                    '<div class="photo-thumb" data-idx="'
                    + str(i)
                    + '" onclick="showPhoto('
                    + str(i)
                    + ')" title="'
                    + fname
                    + '">'
                )
                photo_grid_html += '<img src="' + thumb + '" loading="lazy" />'
                photo_grid_html += time_tag + "</div>"

            markers_json = json.dumps(markers)
            photo_grid = photo_grid_html
        except Exception as e:
            print(f"    Immich fetch failed: {e}")

    # Build HTML
    template = load_template("page")

    start_date = os.path.splitext(filename)[0][:8]
    if len(start_date) == 8:
        date_display = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
    else:
        date_display = start_date

    replacements = {
        "{{TITLE}}": os.path.splitext(filename)[0].replace("_", " "),
        "{{DATE}}": date_display,
        "{{START_TIME}}": start_time,
        "{{END_TIME}}": end_time,
        "{{PHOTO_COUNT}}": str(len(markers)),
        "{{TOTAL_PHOTOS}}": str(len(markers)),
        "{{DISTANCE}}": str(stats.get("distance_km", "—")),
        "{{ELEV_GAIN}}": str(stats.get("elevation_gain_m", "—")),
        "{{ELEV_LOSS}}": str(stats.get("elevation_loss_m", "—")),
        "{{MIN_ELEV}}": str(stats.get("min_elevation_m", "—")),
        "{{MAX_ELEV}}": str(stats.get("max_elevation_m", "—")),
        "{{DURATION}}": str(stats.get("duration_str", "—")),
        "{{AVG_HR}}": str(stats.get("avg_hr", "—")),
        "{{MAX_HR}}": str(stats.get("max_hr", "—")),
        "{{SPEED}}": str(stats.get("speed_kmh", "—")),
        "{{MARKER_JSON}}": markers_json,
        "{{ELEV_JSON}}": json.dumps(elev_data),
        "{{HR_JSON}}": json.dumps(hr_values),
        "{{TRAIL_JSON}}": json.dumps(trail_coords),
        "{{PHOTO_GRID}}": photo_grid,
        "{{WAYPOINTS_JSON}}": json.dumps(waypoints_json),
    }

    for key, value in replacements.items():
        template = template.replace(key, str(value))

    with open(out_path, "w") as f:
        f.write(template)


def load_template(name):
    """Load HTML template."""
    path = os.path.join("templates", name + ".html")
    with open(path, "r") as f:
        return f.read()


def escape_html(s):
    """Escape HTML special characters."""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace('"', "&quot;")
    s = s.replace("'", "&#39;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def generate_minimal_index(cards):
    """Generate a basic index page without template."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hike Routes</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9}}
.header{{background:linear-gradient(180deg,#161b22 0%,#0d1117 100%);border-bottom:1px solid #21262d;padding:24px 32px}}
.header h1{{font-size:1.6rem;font-weight:600;color:#f0f6fc}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;padding:24px 32px}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:20px;text-decoration:none;transition:all .2s}}
.card:hover{{border-color:#58a6ff;transform:translateY(-2px)}}
.card-title{{font-size:1.1rem;font-weight:600;color:#f0f6fc;margin-bottom:8px}}
.card-meta{{color:#8b94e8;font-size:0.8rem;margin-bottom:12px}}
.card-stats{{display:flex;gap:16px;flex-wrap:wrap}}
.card-stats .v{{font-size:1.1rem;font-weight:700;color:#58a6ff}}
.card-stats .l{{font-size:0.7rem;color:#8b94e8;text-transform:uppercase}}
</style></head><body>
<div class="header"><h1>Hike Routes</h1></div>
<div class="grid">{cards}</div>
</body></html>"""


def generate_minimal_page(gpx_path, out_path):
    """Generate a minimal HTML page from GPX without immich."""
    # Load template
    try:
        template = load_template("page")
    except FileNotFoundError:
        return

    # Parse GPX
    tree = ET.parse(gpx_path)
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}

    points = []
    for trk in tree.getroot().findall(".//gpx:trk", ns):
        for seg in trk.findall("gpx:trkseg", ns):
            for pt in seg.findall("gpx:trkpt", ns):
                lat = float(pt.attrib["lat"])
                lon = float(pt.attrib["lon"])
                ele = pt.find("gpx:ele", ns)
                time_elem = pt.find("gpx:time", ns)
                points.append(
                    {
                        "lat": lat,
                        "lon": lon,
                        "ele": float(ele.text) if ele is not None else None,
                        "time": time_elem.text if time_elem is not None else None,
                        "hr": None,
                    }
                )

    if not points:
        return

    # Stats
    import math as mathmod

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        dlat = mathmod.radians(lat2 - lat1)
        dlon = mathmod.radians(lon2 - lon1)
        a = mathmod.sin(dlat / 2) ** 2 + mathmod.cos(mathmod.radians(lat1)) * mathmod.cos(
            mathmod.radians(lat2)
        ) * mathmod.sin(dlon / 2) ** 2
        return R * 2 * mathmod.asin(mathmod.sqrt(a))

    total_dist = sum(
        haversine(points[i - 1]["lat"], points[i - 1]["lon"], points[i]["lat"], points[i]["lon"])
        for i in range(1, len(points))
    )

    elevations = [p["ele"] for p in points if p["ele"] is not None]
    min_elev = round(min(elevations)) if elevations else None
    max_elev = round(max(elevations)) if elevations else None

    # Downsample elevations for chart
    step = max(1, len(points) // 800)
    elev_data = [p["ele"] for i, p in enumerate(points) if i % step == 0 and p["ele"] is not None]

    # Trail coords
    trail_coords = [[p["lat"], p["lon"]] for p in points]
    if len(trail_coords) > 4000:
        trail_coords = trail_coords[::4]

    # Times
    times = [p["time"] for p in points if p["time"]]
    start_time = ""
    end_time = ""
    if times:
        try:
            start_time = datetime.fromisoformat(times[0].replace("Z", "+00:00")).astimezone(
                LOCAL_TZ
            ).strftime("%H:%M")
        except Exception:
            pass
    if len(times) > 1:
        try:
            end_time = datetime.fromisoformat(times[-1].replace("Z", "+00:00")).astimezone(
                LOCAL_TZ
            ).strftime("%H:%M")
        except Exception:
            pass

    dur = (
        datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
        - datetime.fromisoformat(times[0].replace("Z", "+00:00"))
    ).total_seconds() if len(times) >= 2 else 0
    dur_str = f"{int(dur//3600)}h {int((dur%3600)//60)}m" if dur else "—"

    date_str = os.path.splitext(os.path.basename(gpx_path))[0][:8]
    if len(date_str) == 8:
        date_display = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    else:
        date_display = date_str

    waypoints_json = []
    num_wp = min(20, len(points))
    wp_step = max(1, len(points) // num_wp)
    for i in range(0, len(points), wp_step):
        pt = points[i]
        time_str = ""
        if pt["time"]:
            try:
                dt = datetime.fromisoformat(pt["time"].replace("Z", "+00:00"))
                time_str = dt.astimezone(LOCAL_TZ).strftime("%H:%M")
            except Exception:
                pass
        waypoints_json.append(
            {
                "time": time_str,
                "lat": round(pt["lat"], 4),
                "lon": round(pt["lon"], 4),
                "ele": round(pt["ele"]) if pt["ele"] else None,
                "hr": None,
                "elev_pct": 50,
            }
        )

    replacements = {
        "{{TITLE}}": os.path.splitext(os.path.basename(gpx_path))[0].replace("_", " "),
        "{{DATE}}": date_display,
        "{{START_TIME}}": start_time,
        "{{END_TIME}}": end_time,
        "{{PHOTO_COUNT}}": "0",
        "{{TOTAL_PHOTOS}}": "0",
        "{{DISTANCE}}": f"{total_dist / 1000:.1f}",
        "{{ELEV_GAIN}}": str(max_elev - min_elev) if max_elev and min_elev else "—",
        "{{ELEV_LOSS}}": str(max_elev - min_elev) if max_elev and min_elev else "—",
        "{{MIN_ELEV}}": str(min_elev),
        "{{MAX_ELEV}}": str(max_elev),
        "{{DURATION}}": dur_str,
        "{{AVG_HR}}": "—",
        "{{MAX_HR}}": "—",
        "{{SPEED}}": f"{total_dist / 1000 / (dur / 3600):.1f}" if dur > 0 else "—",
        "{{MARKER_JSON}}": "[]",
        "{{ELEV_JSON}}": json.dumps(elev_data),
        "{{HR_JSON}}": json.dumps([None] * len(points)),
        "{{TRAIL_JSON}}": json.dumps(trail_coords),
        "{{PHOTO_GRID}}": "",
        "{{WAYPOINTS_JSON}}": json.dumps(waypoints_json),
    }

    for key, value in replacements.items():
        template = template.replace(key, str(value))

    with open(out_path, "w") as f:
        f.write(template)


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="Fetch hiking GPX from Strava")
    parser.add_argument("--token", help="Strava access token")
    parser.add_argument(
        "--list", action="store_true", help="List hiking activities only"
    )
    parser.add_argument(
        "--fetch", action="store_true", help="Fetch GPX files (default action)"
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Fetch GPX AND generate HTML pages",
    )
    parser.add_argument("--data-dir", default="data", help="Directory for GPX files")
    parser.add_argument("--output-dir", default="output", help="Output directory")
    parser.add_argument("--config", default="config.json", help="Config file path")

    args = parser.parse_args()

    if not args.token:
        # Try to read from env or config
        token = os.environ.get("STRAVA_TOKEN")
        if not token:
            try:
                with open(args.config, "r") as f:
                    config = json.load(f)
                # Look for a strava_token in config
                token = config.get("strava_token")
            except Exception:
                pass
        if not token:
            print("Error: Strava access token required.")
            print("Usage: python3 fetch_strava.py --token YOUR_TOKEN")
            print("Or set STRAVA_TOKEN environment variable.")
            sys.exit(1)
    else:
        token = args.token

    if args.list:
        cmd_list(token)
    elif args.fetch or args.generate:
        if args.generate:
            cmd_generate(token, args.data_dir, args.output_dir)
        else:
            cmd_fetch(token, args.data_dir, args.output_dir)
    else:
        # Default: fetch + generate
        cmd_fetch(token)
        print("\nRun with --generate to create HTML pages too.")


if __name__ == "__main__":
    main()
