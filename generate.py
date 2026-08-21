#!/usr/bin/env python3
"""
Hike Map Generator - Creates interactive static HTML pages from GPX + Immich albums.
Merges the original hike_map generator with Chart.js HR chart and waypoint features.

Usage:
    python3 generate.py          # Generate all hikes

Directory structure:
    hike_routes/
        config.json         # Hike definitions (add more hikes here)
        data/               # GPX files
        templates/          # HTML templates
        output/             # Generated HTML files
        generate.py         # This script
"""

import json
import xml.etree.ElementTree as ET
import os
import math
import urllib.request
import ssl
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ============================================================
# CONFIG
# ============================================================
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def load_template(name):
    path = os.path.join(TEMPLATE_DIR, name + '.html')
    with open(path, 'r') as f:
        return f.read()


LOCAL_TZ = ZoneInfo('Europe/Bucharest')

def utc_to_local(iso_str, tz=LOCAL_TZ):
    """Convert an ISO 8601 UTC string to the given timezone (default Europe/Bucharest)."""
    dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    return dt.astimezone(tz)


def fetch_immich_album(album_id, api_key, base_url):
    """Fetch album data (including all assets) from Immich."""
    ctx = ssl.create_default_context()
    url = base_url + "/api/albums/" + album_id
    req = urllib.request.Request(url, headers={
        'x-api-key': api_key,
        'Content-Type': 'application/json'
    })
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        data = json.loads(resp.read().decode())
    return data


def parse_gpx(gpx_path):
    """Parse GPX file and extract track points, stats."""
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

    name_elem = root.find('.//gpx:name', ns)
    track_name = name_elem.text if name_elem is not None else os.path.basename(gpx_path)

    all_points = []
    segments = []
    for trk in root.findall('.//gpx:trk', ns):
        for seg in trk.findall('gpx:trkseg', ns):
            points = []
            for pt in seg.findall('gpx:trkpt', ns):
                lat = float(pt.attrib['lat'])
                lon = float(pt.attrib['lon'])
                ele_elem = pt.find('gpx:ele', ns)
                time_elem = pt.find('gpx:time', ns)

                hr = None
                ext = pt.find('gpx:extensions', ns)
                if ext is not None:
                    tpx = ext.find(
                        '{http://www.garmin.com/xmlschemas/TrackPointExtension/v1}TrackPointExtension',
                        ns
                    )
                    if tpx is not None:
                        hr_elem = tpx.find('{http://www.garmin.com/xmlschemas/TrackPointExtension/v1}hr')
                        if hr_elem is not None:
                            hr = int(hr_elem.text)

                points.append({
                    'lat': lat, 'lon': lon,
                    'ele': float(ele_elem.text) if ele_elem is not None else None,
                    'time': time_elem.text if time_elem is not None else None,
                    'hr': hr
                })
            segments.append(points)
            all_points.extend(points)

    return {
        'name': track_name,
        'segments': segments,
        'all_points': all_points
    }


def haversine(lat1, lon1, lat2, lon2):
    """Distance between two points in meters."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def rdp_simplify(points, epsilon=0.00008):
    """Ramer-Douglas-Peucker simplification."""
    if len(points) <= 2:
        return points[:]

    dmax = 0
    idx = 0
    end = len(points) - 1
    p1 = (points[0]['lat'], points[0]['lon'])
    p2 = (points[end]['lat'], points[end]['lon'])

    for i in range(1, end):
        pt = (points[i]['lat'], points[i]['lon'])
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        mag = math.sqrt(dx*dx + dy*dy)
        if mag == 0:
            dist = math.sqrt((pt[0]-p1[0])**2 + (pt[1]-p1[1])**2)
        else:
            u = ((pt[0]-p1[0])*dx + (pt[1]-p1[1])*dy) / (mag*mag)
            x = p1[0] + u * dx
            y = p1[1] + u * dy
            dist = math.sqrt((pt[0]-x)**2 + (pt[1]-y)**2)
        if dist > dmax:
            dmax = dist
            idx = i

    if dmax > epsilon:
        left = rdp_simplify(points[:idx+1], epsilon)
        right = rdp_simplify(points[idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[end]]


def smooth_elevation(elevs, window=21):
    """Smooth elevation data with a moving average to remove GPS noise.

    GPS elevation is very noisy — tiny jitters between consecutive points
    add up massively. Moving average (like Strava) filters this out.
    """
    if len(elevs) < window:
        return elevs
    half = window // 2
    smoothed = []
    for i in range(len(elevs)):
        start = max(0, i - half)
        end = min(len(elevs), i + half + 1)
        smoothed.append(sum(elevs[start:end]) / (end - start))
    return smoothed


def calculate_stats(points):
    """Calculate hike statistics."""
    if not points:
        return {}

    # Downsample for elevation stats — GPS elevation is extremely noisy
    # with one-second intervals. Downsample to ~2000 points before computing
    # elevation gain/loss (similar to what Strava does).
    max_pts = min(2000, len(points))
    step = max(1, len(points) // max_pts)
    pts_for_elev = points[::step]

    total_dist = 0
    elev_gain = 0
    elev_loss = 0

    # Extract raw elevations and smooth them (GPS elevation is very noisy)
    raw_elevs = [p['ele'] for p in pts_for_elev]
    elevs = smooth_elevation(raw_elevs, window=21)

    for i in range(1, len(pts_for_elev)):
        total_dist += haversine(
            pts_for_elev[i-1]['lat'], pts_for_elev[i-1]['lon'],
            pts_for_elev[i]['lat'], pts_for_elev[i]['lon']
        )
        if elevs[i] is not None and elevs[i-1] is not None:
            diff = elevs[i] - elevs[i-1]
            if diff > 0:
                elev_gain += diff
            else:
                elev_loss += abs(diff)

    times = [p['time'] for p in points if p['time']]
    duration = 0
    if len(times) >= 2:
        t0 = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
        t1 = datetime.fromisoformat(times[-1].replace('Z', '+00:00'))
        duration = (t1 - t0).total_seconds()

    hrs = [p['hr'] for p in points if p['hr'] is not None]
    avg_hr = round(sum(hrs) / len(hrs)) if hrs else None
    max_hr = max(hrs) if hrs else None
    min_hr = min(hrs) if hrs else None

    elevations = [p['ele'] for p in points if p['ele'] is not None]
    min_elev = round(min(elevations)) if elevations else None
    max_elev = round(max(elevations)) if elevations else None

    speed = round(total_dist / 1000 / (duration / 3600), 1) if duration > 0 else None

    return {
        'distance_km': round(total_dist / 1000, 2),
        'elevation_gain_m': round(elev_gain),
        'elevation_loss_m': round(elev_loss),
        'min_elevation_m': min_elev,
        'max_elevation_m': max_elev,
        'duration_h': round(duration / 3600, 1),
        'duration_str': str(int(duration//3600)) + "h " + str(int((duration%3600)//60)) + "m" if duration else "—",
        'avg_hr': avg_hr,
        'max_hr': max_hr,
        'min_hr': min_hr,
        'speed_kmh': speed,
        'total_points': len(points)
    }


def _measure_offset_seconds(photos_with_gps, trail_points):
    """Median (photo wall-clock − spatially-snapped track UTC) in seconds, or None.
    Spatial snapping is fine for the measurement itself: the median discards the
    minority of photos that snap to the wrong pass of a self-intersecting track."""
    offsets = []
    if not trail_points:
        return None
    lats = [p['lat'] for p in trail_points]
    lons = [p['lon'] for p in trail_points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    for photo in photos_with_gps:
        raw = photo.get('localDateTime', '')
        if not raw:
            continue
        try:
            wall = datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            continue
        lat, lon = photo['latitude'], photo['longitude']
        if lat < min_lat - 0.02 or lat > max_lat + 0.02 or \
           lon < min_lon - 0.02 or lon > max_lon + 0.02:
            continue
        best_idx, best_dist = 0, float('inf')
        for i, pt in enumerate(trail_points):
            d = haversine(lat, lon, pt['lat'], pt['lon'])
            if d < best_dist:
                best_dist, best_idx = d, i
        if best_dist > 300 or not trail_points[best_idx]['time']:
            continue
        try:
            utc = datetime.fromisoformat(
                trail_points[best_idx]['time'].replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            continue
        offsets.append((wall - utc).total_seconds())

    if not offsets:
        return None
    return sorted(offsets)[len(offsets) // 2]


def snap_photos_to_trail(photos, trail_points, offset_s=None):
    """Place each photo on the trail. Tracks may be loops or out-and-backs where
    the same coordinates occur at several times/distances, so snap by TIME when
    possible (photo clock minus measured UTC offset) and fall back to the
    nearest spatial point only when the clock is unusable."""
    markers = []
    if not trail_points:
        return markers

    track_times = []
    for pt in trail_points:
        try:
            track_times.append(datetime.fromisoformat(
                pt['time'].replace('Z', '+00:00')).replace(tzinfo=None) if pt['time'] else None)
        except ValueError:
            track_times.append(None)

    lats = [p['lat'] for p in trail_points]
    lons = [p['lon'] for p in trail_points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    for photo in photos:
        if photo.get('latitude') is None or photo.get('longitude') is None:
            continue

        lat, lon = photo['latitude'], photo['longitude']
        if lat < min_lat - 0.02 or lat > max_lat + 0.02 or \
           lon < min_lon - 0.02 or lon > max_lon + 0.02:
            continue

        # Time-based snap: unique even where the track crosses itself.
        # Gate: clock must agree with some track point within 10 min, and the
        # matched point must lie within 2km of the photo's own GPS.
        best_idx, best_dist, limit = None, float('inf'), 300
        raw = photo.get('localDateTime', '')
        if offset_s is not None and raw:
            try:
                wall = datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=None)
                tgt = wall - timedelta(seconds=offset_s)
                t_best_i, t_best_dt = None, None
                for i, tt in enumerate(track_times):
                    if tt is None:
                        continue
                    dd = abs((tt - tgt).total_seconds())
                    if t_best_dt is None or dd < t_best_dt:
                        t_best_dt, t_best_i = dd, i
                if t_best_i is not None and t_best_dt <= 1800:
                    d = haversine(lat, lon,
                                  trail_points[t_best_i]['lat'], trail_points[t_best_i]['lon'])
                    if d <= 2000:
                        best_idx, best_dist, limit = t_best_i, d, 2000
            except ValueError:
                pass

        # Spatial fallback (no clock / clock off / too far off-track)
        if best_idx is None:
            best_idx, best_dist = 0, float('inf')
            for i, pt in enumerate(trail_points):
                d = haversine(lat, lon, pt['lat'], pt['lon'])
                if d < best_dist:
                    best_dist, best_idx = d, i

        if best_dist > limit:
            continue

        pt = trail_points[best_idx]

        cum_dist = 0
        for i in range(1, best_idx + 1):
            cum_dist += haversine(
                trail_points[i-1]['lat'], trail_points[i-1]['lon'],
                trail_points[i]['lat'], trail_points[i]['lon']
            )

        dt_str = ''
        local = photo.get('localDateTime', '')
        if local:
            try:
                # Immich localDateTime is camera wall-clock (naive, Z-suffixed) — never re-convert
                dt_str = datetime.fromisoformat(local.replace('Z', '+00:00')).strftime('%H:%M')
            except ValueError:
                pass

        markers.append({
            'photo_id': photo['id'],
            'filename': photo.get('originalFileName', ''),
            'lat': pt['lat'],
            'lon': pt['lon'],
            'ele': round(pt['ele']) if pt['ele'] else None,
            'time': dt_str,
            'distance_m': round(cum_dist),
            'dist_from_trail_m': round(best_dist),
        })

    markers.sort(key=lambda m: m['distance_m'])
    return markers


def infer_tz(photos_with_gps, trail_points):
    """Derive the hike's UTC offset from its own data: camera wall-clock
    (Immich localDateTime) minus the nearest track point's UTC time.
    Returns a fixed-offset tzinfo, or None if no GPS photos lie near the trail.
    Median + 15-min rounding makes it robust to camera clock drift and DST."""
    offset_s = _measure_offset_seconds(photos_with_gps, trail_points)
    if offset_s is None:
        return None
    return timezone(timedelta(seconds=round(offset_s / 900) * 900))


def escape_html(s):
    """Escape a string for safe embedding in HTML attribute values."""
    if s is None:
        return ''
    s = str(s)
    s = s.replace('&', '&amp;')
    s = s.replace('"', '&quot;')
    s = s.replace("'", '&#39;')
    s = s.replace('<', '&lt;')
    s = s.replace('>', '&gt;')
    return s


def get_hr_zone_color(hr):
    """Get color class for heart rate zone."""
    if hr is None:
        return None
    if hr < 100:
        return '#4ecca3'      # Resting - green
    elif hr < 130:
        return '#7ee8a0'      # Fat burn - light green
    elif hr < 155:
        return '#f0c040'      # Cardio - yellow
    else:
        return '#e06040'      # Peak - red


def generate_hike_html(config, hike_key):
    """Generate a single hike's HTML page."""
    hike = config['hikes'][hike_key]

    gpx_path = os.path.join(DATA_DIR, hike['gpx_file'])
    gpx = parse_gpx(gpx_path)
    stats = calculate_stats(gpx['all_points'])

    # Simplify GPX for web using RDP
    trail_points = gpx['all_points']
    if len(trail_points) > 4000:
        trail_points = rdp_simplify(trail_points)
        print(f"  Simplified {len(gpx['all_points'])} -> {len(trail_points)} points")

    # Fetch Immich photos
    print("  Fetching Immich album...")
    album_data = fetch_immich_album(
        hike['immich_album_id'],
        hike['immich_api_key'],
        hike['immich_base_url']
    )
    album_name = album_data.get('albumName', hike_key)

    photos_with_gps = []
    for asset in album_data.get('assets', []):
        exif = asset.get('exifInfo', {})
        if exif.get('latitude') and exif.get('longitude'):
            photos_with_gps.append({
                'id': asset['id'],
                'latitude': exif['latitude'],
                'longitude': exif['longitude'],
                'localDateTime': asset.get('localDateTime', ''),
                'originalFileName': asset.get('originalFileName', ''),
            })

    print(f"  Found {len(photos_with_gps)} photos with GPS")

    # Measured UTC offset (photo clocks vs track UTC); explicit config timezone wins
    offset_s = _measure_offset_seconds(photos_with_gps, trail_points)
    if hike.get('timezone'):
        tz = ZoneInfo(hike['timezone'])
    elif offset_s is not None:
        # 15-min rounding, same as infer_tz — avoids baking camera-clock drift into pages
        tz = timezone(timedelta(seconds=round(offset_s / 900) * 900))
    else:
        tz = LOCAL_TZ

    # Snap to trail — time-based where possible (coordinates recur on loops)
    markers = snap_photos_to_trail(photos_with_gps, trail_points, offset_s)
    print(f"  Placed {len(markers)} photos along trail")

    # Prepare elevation data (downsampled for chart)
    elev_data = []
    if trail_points:
        step = max(1, len(trail_points) // 800)
        for i in range(0, len(trail_points), step):
            pt = trail_points[i]
            if pt['ele'] is not None:
                elev_data.append(pt['ele'])

    # Build trail coords for JS (downsampled to max 4000)
    # Leaflet expects [lat, lng], NOT [lon, lat] like GeoJSON
    trail_coords = []
    for pt in gpx['all_points']:
        trail_coords.append([pt['lat'], pt['lon']])
    if len(trail_coords) > 4000:
        step = len(trail_coords) // 4000
        trail_coords = trail_coords[::step]

    # Heart rate data for Chart.js (match trail_points count, interpolate missing)
    hr_values = []
    for i in range(len(trail_points)):
        hr = trail_points[i]['hr']
        hr_values.append(hr)

    # Interpolate missing HR values
    for i in range(len(hr_values)):
        if hr_values[i] is None:
            prev = next = None
            for j in range(i-1, -1, -1):
                if hr_values[j] is not None:
                    prev = hr_values[j]
                    break
            for j in range(i+1, len(hr_values)):
                if hr_values[j] is not None:
                    next = hr_values[j]
                    break
            if prev is not None and next is not None:
                hr_values[i] = round((prev + next) / 2)
            elif prev is not None:
                hr_values[i] = prev
            elif next is not None:
                hr_values[i] = next

    # Times — convert from UTC to local
    times = [p['time'] for p in gpx['all_points'] if p['time']]
    start_time = utc_to_local(times[0], tz).strftime('%H:%M') if times else ''
    end_time = utc_to_local(times[-1], tz).strftime('%H:%M') if len(times) > 1 else ''

    # Build photo grid HTML (thumbs proxied via serve.py — no key in page)
    photo_grid_html = ''
    for i, m in enumerate(markers):
        thumb = "/immich/api/assets/" + m['photo_id'] + "/thumbnail"
        time_tag = '<span class="time-tag">' + m['time'] + '</span>' if m['time'] else ''
        fname_esc = escape_html(m['filename'])
        photo_grid_html += '<div class="photo-thumb" data-idx="' + str(i) + '" onclick="showPhoto(' + str(i) + ')" title="' + fname_esc + '">'
        photo_grid_html += '<img src="' + thumb + '" loading="lazy" />'
        photo_grid_html += time_tag
        photo_grid_html += '</div>'

    # Build waypoints for the sidebar (key elevation change points + HR)
    waypoints_json = []
    num_waypoints = min(20, len(trail_points))
    step = max(1, len(trail_points) // num_waypoints)
    for i in range(0, len(trail_points), step):
        pt = trail_points[i]
        if pt['time']:
            try:
                dt = datetime.fromisoformat(pt['time'].replace('Z', '+00:00'))
                time_str = dt.astimezone(tz).strftime('%H:%M')
            except:
                time_str = ''
        else:
            time_str = ''

        waypoints_json.append({
            'time': time_str,
            'lat': round(pt['lat'], 4),
            'lon': round(pt['lon'], 4),
            'ele': round(pt['ele']) if pt['ele'] else None,
            'hr': pt['hr'],
            'elev_pct': round((pt['ele'] - stats['min_elevation_m']) / (stats['max_elevation_m'] - stats['min_elevation_m'] * 1.0 if stats['max_elevation_m'] and stats['min_elevation_m'] else 1) * 100, 1) if pt['ele'] else None
        })

    # Build replacements dict
    replacements = {
        '{{TITLE}}': hike.get('title', album_name),
        '{{DATE}}': hike.get('date', ''),
        '{{START_TIME}}': start_time,
        '{{END_TIME}}': end_time,
        '{{PHOTO_COUNT}}': str(len(markers)),
        '{{TOTAL_PHOTOS}}': str(len(photos_with_gps)),
        '{{DISTANCE}}': str(stats.get('distance_km', '—')),
        '{{ELEV_GAIN}}': str(stats.get('elevation_gain_m', '—')),
        '{{ELEV_LOSS}}': str(stats.get('elevation_loss_m', '—')),
        '{{MIN_ELEV}}': str(stats.get('min_elevation_m', '—')),
        '{{MAX_ELEV}}': str(stats.get('max_elevation_m', '—')),
        '{{DURATION}}': str(stats.get('duration_str', '—')),
        '{{AVG_HR}}': str(stats.get('avg_hr', '—')),
        '{{MAX_HR}}': str(stats.get('max_hr', '—')),
        '{{SPEED}}': str(stats.get('speed_kmh', '—')),
        '{{MARKER_JSON}}': json.dumps(markers),
        '{{ELEV_JSON}}': json.dumps(elev_data),
        '{{HR_JSON}}': json.dumps(hr_values),
        '{{TRAIL_JSON}}': json.dumps(trail_coords),
        '{{PHOTO_GRID}}': photo_grid_html,
        '{{WAYPOINTS_JSON}}': json.dumps(waypoints_json),
    }

    # Load template and do replacements
    template = load_template('page')
    for key, value in replacements.items():
        template = template.replace(key, str(value))

    return template


def generate_index(config):
    """Generate index page listing all hikes."""
    cards = ''
    for key, hike in config['hikes'].items():
        gpx_path = os.path.join(DATA_DIR, hike['gpx_file'])
        stats = ''
        photo_count = '?'
        if os.path.exists(gpx_path):
            gpx = parse_gpx(gpx_path)
            stats = calculate_stats(gpx['all_points'])
            try:
                album = fetch_immich_album(
                    hike['immich_album_id'],
                    hike['immich_api_key'],
                    hike['immich_base_url']
                )
                photo_count = sum(1 for a in album.get('assets', [])
                                if a.get('exifInfo', {}).get('latitude'))
            except:
                photo_count = '?'

        title = escape_html(hike.get('title', hike.get('gpx_file', key)))
        cards += '<a href="' + key + '.html" class="card">'
        cards += '<div class="card-title">' + title + '</div>'
        cards += '<div class="card-meta"><span>' + hike.get('date', '') + '</span></div>'
        cards += '<div class="card-stats">'
        cards += '<div><span class="v">' + str(stats.get('distance_km', '—')) + '</span> <span class="l">km</span></div>'
        cards += '<div><span class="v">' + str(stats.get('duration_str', '—')) + '</span> <span class="l">time</span></div>'
        cards += '<div><span class="v">' + str(stats.get('elevation_gain_m', '—')) + '</span> <span class="l">gain</span></div>'
        cards += '<div><span class="v">' + str(photo_count) + '</span> <span class="l">photos</span></div>'
        cards += '</div>'
        cards += '<div class="card-desc">' + escape_html(hike.get('description', '')) + '</div>'
        cards += '</a>'

    template = load_template('index')
    template = template.replace('{{CARDS}}', cards)
    return template


def main():
    print("=" * 60)
    print("Hike Route Generator")
    print("=" * 60)

    config = load_config()
    print("\nFound " + str(len(config['hikes'])) + " hike(s) in config")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for key in config['hikes']:
        print("\n" + "─" * 40)
        print("Generating: " + key)
        html = generate_hike_html(config, key)
        path = os.path.join(OUTPUT_DIR, key + ".html")
        with open(path, 'w') as f:
            f.write(html)
        print("  Written: " + path)

    print("\n" + "─" * 40)
    print("Generating index page...")
    html = generate_index(config)
    path = os.path.join(OUTPUT_DIR, 'index.html')
    with open(path, 'w') as f:
        f.write(html)
    print("  Written: " + path)

    print("\n" + "=" * 60)
    print("Open " + os.path.join(OUTPUT_DIR, 'index.html') + " in your browser")
    print("Or run: python3 -m http.server 8081 --directory " + OUTPUT_DIR)
    print("=" * 60)


if __name__ == '__main__':
    main()
