"""Where a changeset happened: geohash and country, computed at ingest.

Was a Postgres trigger (PostGIS) until migration 0057; moved here so every
analytics backend gets identical values from one implementation, without
needing PostGIS (see CLAUDE.md's "Location data is computed at ingest"). The
rules are unchanged:

- A changeset's location is the center of its bounding box, but only when
  the bbox is trustworthy: present, within +/-180/+/-90 (a few 2006-2009
  changesets carry +/-214.748, an old int-overflow artifact), and with a
  geodesic diagonal of at most BBOX_DIAG_THRESHOLD_KM (a stray far-away edit
  balloons an otherwise local bbox). Untrustworthy -> no geohash, no country.
- geohash: GEOHASH_PRECISION characters (geo.py).
- country: the country polygon containing the point (smallest ISO code when
  simplified polygons overlap), else the nearest one within
  NEAREST_COUNTRY_MAX_M, else none (open sea, Antarctica, ...). The polygons
  are simplified (~2km tolerance, changesets/data/README.md), which clipped
  coastal and border edits out of their own country before the 5km fallback
  existed (migration 0053).

Distances are geodesic on the WGS84 ellipsoid (pyproj, GeographicLib), the
same computation PostGIS's geography type uses.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from pyproj import Geod

from ..geo import BBOX_DIAG_THRESHOLD_KM, GEOHASH_PRECISION, geohash_encode

NEAREST_COUNTRY_MAX_M = 5000
# Candidate search box for the nearest-country fallback: ~5.5km of latitude,
# widened in longitude by 1/cos(lat) (capped near the poles).
_SEARCH_DEG = 0.05

_GEOD = Geod(ellps='WGS84')
_BOUNDARIES = Path(__file__).resolve().parent.parent / 'data' / 'country_boundaries.geojson'


def bbox_centers(min_lat, min_lon, max_lat, max_lon):
    """Trustworthy bbox centers, vectorized. Inputs are sequences (None or NaN
    for a missing coordinate); returns (lat, lon) float arrays, NaN where the
    bbox isn't trustworthy."""
    coords = [np.array([np.nan if v is None else v for v in seq], dtype=float)
              for seq in (min_lat, min_lon, max_lat, max_lon)]
    a_lat, a_lon, b_lat, b_lon = coords
    valid = np.isfinite(a_lat) & np.isfinite(a_lon) & np.isfinite(b_lat) & np.isfinite(b_lon)
    valid &= (np.abs(a_lon) <= 180) & (np.abs(b_lon) <= 180) & (np.abs(a_lat) <= 90) & (np.abs(b_lat) <= 90)
    if valid.any():
        _, _, diagonal_m = _GEOD.inv(a_lon[valid], a_lat[valid], b_lon[valid], b_lat[valid])
        ok = np.zeros(len(valid), dtype=bool)
        ok[valid] = np.asarray(diagonal_m) <= BBOX_DIAG_THRESHOLD_KM * 1000
        valid = ok
    lat = np.where(valid, (a_lat + b_lat) / 2.0, np.nan)
    lon = np.where(valid, (a_lon + b_lon) / 2.0, np.nan)
    return lat, lon


class CountryLocator:
    """Point -> ISO 3166-1 alpha-2 code, from changesets/data/country_boundaries.geojson."""

    def __init__(self, path=_BOUNDARIES):
        features = json.loads(Path(path).read_text())['features']
        self.codes = [f['properties']['iso_a2'].strip() for f in features]
        self.geoms = np.array([shapely.geometry.shape(f['geometry']) for f in features])
        shapely.prepare(self.geoms)
        self.tree = shapely.STRtree(self.geoms)

    def locate(self, lat, lon):
        """ISO codes (or None) for float arrays of lat/lon; NaN -> None."""
        result = [None] * len(lat)
        known = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
        if not len(known):
            return result
        points = shapely.points(lon[known], lat[known])
        point_idx, geom_idx = self.tree.query(points, predicate='within')
        for p, g in zip(point_idx, geom_idx):
            i, code = known[p], self.codes[g]
            if result[i] is None or code < result[i]:
                result[i] = code
        for i in known:
            if result[i] is None:
                result[i] = self._nearest_within(float(lat[i]), float(lon[i]))
        return result

    def _nearest_within(self, lat, lon):
        """Nearest country within NEAREST_COUNTRY_MAX_M of (lat, lon), or None.
        Candidates come from the index (search box), each clipped to that box
        so big countries stay cheap; the nearest point is found in a local
        equirectangular frame and the distance measured geodesically."""
        dlon = _SEARCH_DEG / max(math.cos(math.radians(lat)), 0.02)
        box = (lon - dlon, lat - _SEARCH_DEG, lon + dlon, lat + _SEARCH_DEG)
        candidates = self.tree.query(shapely.box(*box))
        if not len(candidates):
            return None
        scale = np.array([max(math.cos(math.radians(lat)), 1e-6), 1.0])
        point = shapely.points(lon * scale[0], lat)
        best, best_m = None, None
        for g in candidates:
            piece = shapely.clip_by_rect(self.geoms[g], *box)
            if piece.is_empty:
                continue
            local = shapely.transform(piece, lambda c: c * scale)
            nearest = shapely.shortest_line(local, point)
            n_lon, n_lat = shapely.get_coordinates(nearest)[0]
            _, _, dist_m = _GEOD.inv(lon, lat, n_lon / scale[0], n_lat)
            if dist_m <= NEAREST_COUNTRY_MAX_M and (best_m is None or dist_m < best_m):
                best, best_m = self.codes[g], dist_m
        return best


@lru_cache(maxsize=1)
def country_locator():
    return CountryLocator()


def locate(records):
    """Set 'geohash' and 'country_code' on parsed changeset dicts (None when
    the bbox isn't trustworthy). Works on a whole batch at once."""
    if not records:
        return records
    lat, lon = bbox_centers(*([r.get(k) for r in records] for k in ('min_lat', 'min_lon', 'max_lat', 'max_lon')))
    countries = country_locator().locate(lat, lon)
    for r, la, lo, country in zip(records, lat, lon, countries):
        if math.isnan(la):
            r['geohash'] = None
            r['country_code'] = None
        else:
            r['geohash'] = geohash_encode(float(la), float(lo), GEOHASH_PRECISION)
            r['country_code'] = country
    return records
