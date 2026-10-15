"""Explicit historical routing and school discovery; no fabricated travel times."""
from __future__ import annotations

from datetime import datetime
import logging
import re
from urllib.parse import quote

import httpx

from src.config import get_sources_config
from src.planning.core import Place, Travel


class RoutingError(RuntimeError):
    pass


class _RedactTomTomKey(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if 'tomtom.com' in message:
            record.msg = re.sub(r'([?&]key=)[^&\s\"\']+', r'\1[redacted]', message)
            record.args = ()
        return True


for _name in ('httpx', 'httpcore'):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, _RedactTomTomKey) for f in _logger.filters):
        _logger.addFilter(_RedactTomTomKey())


class TomTomHistoricalRouter:
    def __init__(self, api_key: str, *, client=None, max_calls: int = 180):
        if not api_key or not api_key.strip():
            raise RoutingError('Hubungkan TomTom API key untuk menghitung waktu tempuh historis.')
        self._key = api_key.strip()
        self._client = client or httpx.Client(timeout=15.0)
        self._owns_client = client is None
        self.max_calls = max_calls
        self.calls = 0

    def close(self):
        if self._owns_client:
            self._client.close()

    def _get(self, url, params):
        if self.calls >= self.max_calls:
            raise RoutingError('Batas perhitungan rute tercapai. Kurangi kandidat sekolah/sesi atau kunci beberapa sesi, lalu hitung ulang.')
        self.calls += 1
        try:
            response = self._client.get(url, params={**params, 'key': self._key})
            if response.status_code >= 400:
                raise RoutingError(f'Layanan TomTom mengembalikan HTTP {response.status_code}. Periksa key, kuota, dan waktu rute.')
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never expose exception URLs: query strings contain the private key.
            raise RoutingError('Layanan TomTom belum dapat dihubungi atau responsnya tidak valid. Coba lagi nanti.') from None

    def __call__(self, origin: Place, destination: Place, when: datetime, arrive_by: bool = False) -> Travel:
        if when.tzinfo is None:
            raise ValueError('Waktu rute harus menyertakan zona waktu.')
        points = f'{origin.lat},{origin.lon}:{destination.lat},{destination.lon}'
        data = self._get(f'https://api.tomtom.com/routing/1/calculateRoute/{points}/json', {
            'traffic': 'false', 'travelMode': 'car', 'routeType': 'fastest',
            'computeTravelTimeFor': 'all', 'routeRepresentation': 'summaryOnly',
            'arriveAt' if arrive_by else 'departAt': when.isoformat(),
        })
        try:
            summary = data['routes'][0]['summary']
            seconds = float(summary['historicTrafficTravelTimeInSeconds'])
            km = float(summary['lengthInMeters']) / 1000
            return Travel(seconds / 60, km)
        except (KeyError, IndexError, TypeError, ValueError):
            raise RoutingError('Estimasi historis rute ini tidak tersedia. Tidak diganti dengan estimasi jarak lurus.') from None

    def geocode(self, query: str):
        query = query.strip()
        if len(query) < 5:
            raise ValueError('Masukkan alamat yang lebih lengkap.')
        data = self._get(f'https://api.tomtom.com/search/2/geocode/{quote(query, safe="")}.json',
                         {'countrySet': 'ID', 'limit': 5})
        return [{'name': item.get('address', {}).get('freeformAddress', query),
                 'lat': item['position']['lat'], 'lon': item['position']['lon']}
                for item in data.get('results', []) if 'position' in item]


def discover_schools(home: Place, radius_km: float = 10):
    """OSM supplies candidate locations only, not verified curriculum or fees."""
    if not 1 <= radius_km <= 30:
        raise ValueError('Radius pencarian sekolah harus 1-30 km.')
    cfg = get_sources_config()
    source = cfg.registry_sources.get('overpass')
    if source is None or not source.base_url:
        raise RoutingError('Sumber pencarian sekolah belum dikonfigurasi.')
    query = f'[out:json][timeout:25];nwr["amenity"~"^(school|kindergarten)$"](around:{int(radius_km*1000)},{home.lat},{home.lon});out center tags;'
    try:
        with httpx.Client(timeout=35.0, headers={'User-Agent': cfg.crawl_policy.user_agent}) as client:
            response = client.post(source.base_url, data={'data': query})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise RoutingError('Pencarian sekolah belum tersedia. Kandidat tetap bisa dimasukkan manual.') from None
    rows = []
    for item in data.get('elements', []):
        tags = item.get('tags', {})
        point = item if 'lat' in item else item.get('center', {})
        if not tags.get('name') or 'lat' not in point or 'lon' not in point:
            continue
        rows.append({'Nama': tags['name'], 'Latitude': point['lat'], 'Longitude': point['lon'],
                     'Website': tags.get('website') or tags.get('contact:website') or '',
                     'Sumber lokasi': f'https://www.openstreetmap.org/{item["type"]}/{item["id"]}'})
    # Rank only for discovery by geographic proximity, not commute feasibility.
    rows.sort(key=lambda row: (row['Latitude']-home.lat)**2 + (row['Longitude']-home.lon)**2)
    return rows[:40]
