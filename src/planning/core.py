"""Weekly practice/school scenarios with explicit constraints and routed travel."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from itertools import combinations
from math import comb, isfinite
from typing import Callable

WIB = timezone(timedelta(hours=7))
DAYS = ('Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu')


def minute(value: str) -> int:
    try:
        parsed = time.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f'Jam tidak valid: {value}; gunakan HH:MM.') from exc
    if parsed.second or parsed.microsecond:
        raise ValueError('Gunakan jam dalam menit, tanpa detik.')
    return parsed.hour * 60 + parsed.minute


def clock(value: int) -> str:
    return f'{value // 60:02d}:{value % 60:02d}'


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    lat: float
    lon: float

    def __post_init__(self):
        if not self.id or not self.name or not isfinite(self.lat) or not isfinite(self.lon):
            raise ValueError('Nama dan koordinat lokasi wajib diisi.')
        if not (-90 <= self.lat <= 90 and -180 <= self.lon <= 180):
            raise ValueError('Koordinat lokasi di luar rentang.')


@dataclass(frozen=True)
class Slot:
    id: str
    place: Place
    day: int
    start: int
    end: int
    priority: float = 3
    locked: bool = False
    status: str = 'Usulan'

    def __post_init__(self):
        if not 0 <= self.day < 7 or not 0 <= self.start < self.end < 1440:
            raise ValueError('Hari/jam praktik tidak valid; satu sesi harus selesai pada hari yang sama.')
        if not 0 <= self.priority <= 5:
            raise ValueError('Bobot RS harus antara 0 dan 5.')


@dataclass(frozen=True)
class School:
    place: Place
    start: int
    end: int
    days: tuple[int, ...] = (0, 1, 2, 3, 4)
    curriculum: str | None = None
    level: str | None = None
    admission: float | None = None
    monthly: float | None = None
    annual: float | None = None
    other_first_year: float | None = None
    academic_year: str = ''
    source: str = ''

    def __post_init__(self):
        if not 0 < self.start < self.end < 1430 or not self.days or any(d not in range(7) for d in self.days):
            raise ValueError('Jam/hari sekolah tidak valid.')
        for amount in (self.admission, self.monthly, self.annual, self.other_first_year):
            if amount is not None and (not isfinite(amount) or amount < 0):
                raise ValueError('Biaya sekolah harus angka positif atau belum diketahui.')

    @property
    def first_year(self):
        parts = (self.admission, self.monthly, self.annual, self.other_first_year)
        return None if any(v is None for v in parts) else self.admission + 12 * self.monthly + self.annual + self.other_first_year

    @property
    def recurring_year(self):
        return None if self.monthly is None or self.annual is None else 12 * self.monthly + self.annual


@dataclass(frozen=True)
class Travel:
    minutes: float
    km: float
    source: str = 'TomTom historical'

    def __post_init__(self):
        if not isfinite(self.minutes) or not isfinite(self.km) or self.minutes < 0 or self.km < 0:
            raise ValueError('Waktu/jarak perjalanan tidak valid.')


@dataclass(frozen=True)
class Event:
    day: int
    start: int
    end: int
    place: Place
    label: str
    status: str


@dataclass(frozen=True)
class Leg:
    day: int
    origin: Place
    destination: Place
    departure: datetime
    arrival: datetime
    travel: Travel


@dataclass(frozen=True)
class Constraints:
    sessions: int = 4
    max_days: int = 5
    max_hours: float = 40
    max_leg_minutes: float = 120
    earliest_departure: int = 360
    latest_home: int = 1320
    buffer: int = 15
    school_service: int = 10
    school_mode: str = 'Antar dan jemput'
    max_school_km: float = 20
    first_year_budget: float | None = None
    preferred_curricula: tuple[str, ...] = ()
    preferred_level: str = ''


@dataclass(frozen=True)
class Weights:
    commute: float = 40
    hospital: float = 40
    hours: float = 20
    school_cost: float = 0
    curriculum: float = 0


@dataclass
class Plan:
    slots: tuple[Slot, ...]
    school: School | None
    events: list[Event]
    legs: list[Leg]
    commute_minutes: float
    extra_school_minutes: float
    school_home_km: float | None
    hours: float
    score: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class PlanningResult:
    plans: list[Plan]
    rejected: dict[str, int]
    evaluated: int


# Router signature: origin, destination, local datetime, arrive_by.
Router = Callable[[Place, Place, datetime, bool], Travel]


def local_time(week: date, day: int, minutes: int) -> datetime:
    return datetime.combine(week + timedelta(days=day), time(), tzinfo=WIB) + timedelta(minutes=minutes)


def events_for(slots: tuple[Slot, ...], school: School | None, cfg: Constraints) -> list[Event]:
    events = [Event(s.day, s.start, s.end, s.place, 'Praktik', s.status) for s in slots]
    if school:
        for day in school.days:
            if cfg.school_mode in ('Antar dan jemput', 'Antar saja'):
                events.append(Event(day, school.start - cfg.school_service, school.start, school.place, 'Antar sekolah', 'Skenario masa depan'))
            if cfg.school_mode in ('Antar dan jemput', 'Jemput saja'):
                events.append(Event(day, school.end, school.end + cfg.school_service, school.place, 'Jemput sekolah', 'Skenario masa depan'))
    return sorted(events, key=lambda e: (e.day, e.start, e.end))


def route_week(home: Place, events: list[Event], week: date, cfg: Constraints, route: Router) -> list[Leg] | None:
    legs: list[Leg] = []
    for day in range(7):
        daily = [e for e in events if e.day == day]
        if not daily:
            continue
        # On school-only days, return home between drop-off and pickup.
        tours = [[e] for e in daily] if all(e.label != 'Praktik' for e in daily) else [daily]
        previous_home = None
        for tour in tours:
            first = tour[0]
            deadline = local_time(week, day, first.start - cfg.buffer)
            travel = route(home, first.place, deadline, True)
            departure = deadline - timedelta(minutes=travel.minutes)
            if departure < local_time(week, day, cfg.earliest_departure) or (previous_home and departure < previous_home):
                return None
            if travel.minutes > cfg.max_leg_minutes:
                return None
            legs.append(Leg(day, home, first.place, departure, deadline, travel))
            for previous, current in zip(tour, tour[1:]):
                departure = local_time(week, day, previous.end)
                travel = route(previous.place, current.place, departure, False)
                arrival = departure + timedelta(minutes=travel.minutes)
                if travel.minutes > cfg.max_leg_minutes or arrival + timedelta(minutes=cfg.buffer) > local_time(week, day, current.start):
                    return None
                legs.append(Leg(day, previous.place, current.place, departure, arrival, travel))
            last = tour[-1]
            departure = local_time(week, day, last.end)
            travel = route(last.place, home, departure, False)
            arrival = departure + timedelta(minutes=travel.minutes)
            if travel.minutes > cfg.max_leg_minutes or arrival > local_time(week, day, cfg.latest_home):
                return None
            legs.append(Leg(day, last.place, home, departure, arrival, travel))
            previous_home = arrival
    return legs


def optimize(home: Place, slots: list[Slot], schools: list[School], week: date,
             cfg: Constraints, weights: Weights, route: Router, limit: int = 5) -> PlanningResult:
    if week.weekday() != 0:
        raise ValueError('Awal minggu harus hari Senin.')
    if not 1 <= cfg.sessions <= 8 or not 1 <= cfg.max_days <= 7 or cfg.max_hours <= 0:
        raise ValueError('Target sesi/hari/jam praktik tidak valid.')
    if not 0 <= cfg.buffer <= 120 or not 0 <= cfg.earliest_departure < cfg.latest_home <= 1439:
        raise ValueError('Batas perjalanan atau buffer tidak valid.')
    if cfg.max_leg_minutes <= 0 or cfg.max_school_km <= 0 or not 0 <= cfg.school_service <= 60:
        raise ValueError('Batas perjalanan/jarak sekolah atau waktu antar/jemput tidak valid.')
    if cfg.first_year_budget is not None and (not isfinite(cfg.first_year_budget) or cfg.first_year_budget < 0):
        raise ValueError('Anggaran sekolah tidak valid.')
    if cfg.school_mode not in ('Antar dan jemput', 'Antar saja', 'Jemput saja', 'Transportasi lain'):
        raise ValueError('Pilih skenario antar/jemput yang tersedia.')
    if any(not isfinite(w) or w < 0 for w in vars(weights).values()) or sum(vars(weights).values()) <= 0:
        raise ValueError('Setidaknya satu bobot harus positif.')
    if len(slots) > 16 or len(schools) > 5 or len({s.id for s in slots}) != len(slots):
        raise ValueError('Maksimum 16 kandidat sesi dan 5 sekolah; ID sesi harus unik.')
    locked = tuple(s for s in slots if s.locked)
    optional = [s for s in slots if not s.locked]
    needed = cfg.sessions - len(locked)
    if needed < 0 or needed > len(optional):
        raise ValueError('Target sesi harus mencakup semua slot terkunci dan tidak melebihi kandidat.')
    if comb(len(optional), needed) * max(len(schools), 1) > 25000:
        raise ValueError('Terlalu banyak kombinasi. Kurangi kandidat sesi/sekolah atau kunci beberapa sesi.')
    rejected = Counter()
    plans = []
    evaluated = 0
    # Per-run memoization only; personal locations never enter the hospital DB.
    memo = {}
    def cached_route(a, b, when, arrive_by):
        if (a.lat, a.lon) == (b.lat, b.lon):
            return Travel(0, 0)
        key = (a.lat, a.lon, b.lat, b.lon, when.isoformat(), arrive_by)
        if key not in memo:
            memo[key] = route(a, b, when, arrive_by)
        return memo[key]
    school_options = schools or [None]
    for school in school_options:
        home_km = None
        if school:
            if cfg.preferred_level and (school.level or '').casefold() != cfg.preferred_level.casefold():
                rejected['Jenjang sekolah tidak cocok/belum diketahui'] += 1
                continue
            if cfg.first_year_budget is not None:
                if school.first_year is None:
                    rejected['Biaya belum lengkap untuk memeriksa anggaran'] += 1
                    continue
                if school.first_year > cfg.first_year_budget:
                    rejected['Melebihi anggaran tahun pertama'] += 1
                    continue
            when = local_time(week, min(school.days or (0,)), school.start - cfg.school_service - cfg.buffer)
            home_km = cached_route(home, school.place, when, True).km
            if home_km > cfg.max_school_km:
                rejected['Sekolah terlalu jauh dari rumah'] += 1
                continue
        for choice in combinations(optional, needed):
            selected = locked + choice
            evaluated += 1
            hours = sum((s.end - s.start) / 60 for s in selected)
            if len({s.day for s in selected}) > cfg.max_days or hours > cfg.max_hours:
                rejected['Batas hari/jam praktik'] += 1
                continue
            events = events_for(selected, school, cfg)
            if any(a.day == b.day and a.end > b.start for a, b in zip(events, events[1:])):
                rejected['Benturan jadwal'] += 1
                continue
            legs = route_week(home, events, week, cfg, cached_route)
            if legs is None:
                rejected['Waktu perjalanan/buffer tidak mencukupi'] += 1
                continue
            commute = sum(leg.travel.minutes for leg in legs)
            baseline_legs = route_week(home, events_for(selected, None, cfg), week, cfg, cached_route) if school else legs
            if baseline_legs is None:
                rejected['Jadwal RS dasar tidak memenuhi batas perjalanan'] += 1
                continue
            baseline = sum(leg.travel.minutes for leg in baseline_legs)
            plan = Plan(selected, school, events, legs, commute, commute-baseline, home_km, hours)
            if any(s.status == 'Usulan' for s in selected):
                plan.notes.append('Mengandung usulan slot yang belum dikonfirmasi RS.')
            if school and school.first_year is None:
                plan.notes.append('Biaya tahun pertama belum lengkap.')
            if school and (not school.academic_year or not school.source):
                plan.notes.append('Tahun ajaran/sumber biaya sekolah belum lengkap; perlu verifikasi.')
            plans.append(plan)
    if plans:
        max_commute = max(p.commute_minutes for p in plans) or 1
        costs = [p.school.first_year for p in plans if p.school and p.school.first_year is not None]
        max_cost = max(costs, default=1) or 1
        for plan in plans:
            school = plan.school
            if school and ((weights.school_cost and school.first_year is None) or
                           (weights.curriculum and cfg.preferred_curricula and not school.curriculum)):
                plan.notes.append('Belum diberi peringkat: lengkapi data sekolah atau nolkan bobot terkait.')
                continue
            scores = [(weights.commute, 1-plan.commute_minutes/max_commute),
                      (weights.hospital, sum(s.priority*(s.end-s.start) for s in plan.slots) / sum(s.end-s.start for s in plan.slots) / 5),
                      (weights.hours, min(plan.hours/cfg.max_hours, 1))]
            if school:
                scores.append((weights.school_cost, 1-school.first_year/max_cost if school.first_year is not None else 0))
                if cfg.preferred_curricula:
                    match = (school.curriculum or '').casefold() in {c.casefold() for c in cfg.preferred_curricula}
                    scores.append((weights.curriculum, float(match)))
            total = sum(weight for weight, _ in scores)
            plan.score = round(100*sum(w*s for w,s in scores)/total, 2) if total else None
        plans.sort(key=lambda p: (p.score is None, -(p.score or 0), p.commute_minutes,
                                  tuple(s.id for s in p.slots), p.school.place.id if p.school else ''))
    # Keep the best schedule per school before filling remaining alternatives.
    chosen, seen = [], set()
    for plan in plans:
        key = plan.school.place.id if plan.school else ''
        if key not in seen:
            chosen.append(plan)
            seen.add(key)
    for plan in plans:
        if len(chosen) >= limit:
            break
        if all(plan is not existing for existing in chosen):
            chosen.append(plan)
    chosen.sort(key=lambda p: (p.score is None, -(p.score or 0), p.commute_minutes))
    return PlanningResult(chosen[:limit], dict(rejected), evaluated)
