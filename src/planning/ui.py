"""Streamlit-native weekly planner. Network work happens only on explicit actions."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timedelta
import hashlib
import json
import math
import os

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
from sqlalchemy.orm import Session

from src.models import Hospital, HospitalPracticeMetrics, ScheduleSlot
from src.metrics.coverage import usable_slots_for_hospital
from src.planning.core import (DAYS, WIB, Constraints, Place, School, Slot, Weights,
                               clock, minute, optimize)
from src.planning.services import RoutingError, TomTomHistoricalRouter, discover_schools, normalize_api_key

SLOT_COLUMNS = ['RS', 'Hari', 'Mulai', 'Selesai', 'Status', 'Kunci']
SCHOOL_COLUMNS = ['Pilih', 'Nama', 'Latitude', 'Longitude', 'Jenjang', 'Kurikulum',
                  'Masuk', 'Pulang', 'Admission / uang pangkal', 'SPP bulanan',
                  'Biaya tahunan', 'Wajib lain tahun pertama', 'Tahun ajaran',
                  'Sumber biaya', 'Website', 'Sumber lokasi']
MONEY_COLUMNS = SCHOOL_COLUMNS[8:12]


def _secret():
    value = os.environ.get('TOMTOM_API_KEY')
    if value:
        return value
    try:
        return st.secrets.get('TOMTOM_API_KEY', '')
    except Exception:
        return ''


def _present(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value)) and str(value).strip() != ''


def _text(value):
    return str(value).strip() if _present(value) else ''


def _amount(value):
    return float(value) if _present(value) else None


def _rupiah(value):
    return f'Rp {value:,.0f}'.replace(',', '.') if value is not None else 'Belum diketahui'


def _school_rows(rows):
    frame = pd.DataFrame(rows).reindex(columns=SCHOOL_COLUMNS)
    frame['Pilih'] = frame['Pilih'].fillna(False).astype(bool)
    for column in ['Latitude', 'Longitude', *MONEY_COLUMNS]:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    for column in set(SCHOOL_COLUMNS) - {'Pilih', 'Latitude', 'Longitude', *MONEY_COLUMNS}:
        frame[column] = frame[column].fillna('').astype(str)
    return frame


def _gap_suggestions(engine, selected, duration):
    """Suggest observed gaps only where schedule coverage is usable, never vacancies."""
    per_hospital = []
    with Session(engine) as session:
        for hospital in selected:
            metrics = session.get(HospitalPracticeMetrics, hospital.id)
            if not metrics or metrics.schedule_completeness is None or metrics.schedule_completeness < .8:
                continue
            slots = usable_slots_for_hospital(session.query(ScheduleSlot).filter_by(hospital_id=hospital.id).all())
            if not slots:
                continue
            rows = []
            for day in range(6):
                daily_slots = [slot for slot in slots if slot.day_of_week == day]
                if any(slot.end_minutes is None for slot in daily_slots):
                    continue  # an open-ended session cannot prove a later gap
                occupied = [(slot.start_minutes, slot.end_minutes) for slot in daily_slots]
                for start in range(8*60, 21*60-duration+1, duration):
                    if all(start+duration <= a or start >= b for a,b in occupied):
                        rows.append({'RS': f'{hospital.display_alias or hospital.name} [#{hospital.id}]', 'Hari': DAYS[day],
                                     'Mulai': clock(start), 'Selesai': clock(start+duration),
                                     'Status': 'Usulan', 'Kunci': False})
                        break  # a small, editable set rather than an overwhelming list
            per_hospital.append(rows)
    return [rows[i] for i in range(6) for rows in per_hospital if i < len(rows)][:16]


def render_planner(engine):
    st.subheader('Rencana praktik & perjalanan')
    st.write('Bandingkan jadwal mingguan RS dan kandidat sekolah masa depan berdasarkan preferensi, waktu tempuh historis, dan anggaran.')
    st.caption('Slot usulan belum membuktikan ada lowongan praktik. Rencana ini tidak mengubah database RS atau membuat janji.')
    with Session(engine) as session:
        hospitals = session.query(Hospital).filter(Hospital.lat.is_not(None), Hospital.lon.is_not(None),
                                                  Hospital.duplicate_of_hospital_id.is_(None)).order_by(Hospital.name).all()
    if not hospitals:
        st.info('Belum ada RS dengan koordinat untuk direncanakan.')
        return
    by_id = {h.id: h for h in hospitals}
    revision = st.session_state.get('planner_revision', 0)

    with st.expander('Sumber waktu tempuh', expanded=not bool(_secret())):
        st.write('Mobil pribadi · pola historis menurut hari/jam · TomTom Routing. Perkiraan ini tidak menjamin kondisi lalu lintas pada hari perjalanan.')
        entered_key = st.text_input('TomTom API key (opsional jika sudah dikonfigurasi)', type='password', key='planner_key')
        api_key = normalize_api_key(entered_key) or normalize_api_key(_secret())
        key_id = hashlib.sha256(api_key.encode()).hexdigest()[:10] if api_key else ''
        if api_key:
            st.caption(f'ID key: {key_id}. Key sudah diisi; autentikasi belum diverifikasi sampai koneksi berhasil.')
        else:
            st.info('Pilihan dan pembobotan bisa disiapkan sekarang. Perhitungan historis membutuhkan TOMTOM_API_KEY di Streamlit Secrets atau key pada kolom ini.')
        if st.button('Uji koneksi TomTom', disabled=not bool(api_key)):
            router = None
            try:
                router = TomTomHistoricalRouter(api_key, max_calls=1)
                router.geocode('Jakarta Indonesia')
                st.session_state['planner_verified_key'] = key_id
                st.success('Autentikasi dan akses Geocoding TomTom berhasil.')
            except (ValueError, RoutingError) as exc:
                st.session_state.pop('planner_verified_key', None)
                st.error(str(exc))
            finally:
                if router:
                    router.close()
        if api_key and st.session_state.get('planner_verified_key') == key_id:
            st.caption('Key ini sudah lolos uji Geocoding dalam sesi ini.')
        st.caption('Koordinat dan waktu rute yang dihitung dikirim ke TomTom. Key tidak disertakan dalam ekspor skenario.')

    st.markdown('**1 · Rumah dan RS kandidat**')
    address = st.text_input('Alamat / nama lokasi rumah', key='planner_address', placeholder='Alamat lengkap untuk mencari lokasi')
    if st.button('Cari lokasi rumah', key='planner_geocode'):
        st.session_state.pop('planner_geocodes', None)
        router = None
        try:
            router = TomTomHistoricalRouter(api_key)
            st.session_state['planner_geocodes'] = router.geocode(address)
            st.session_state['planner_verified_key'] = key_id
        except (ValueError, RoutingError) as exc:
            st.error(str(exc))
        finally:
            if router:
                router.close()
    matches = st.session_state.get('planner_geocodes', [])
    if matches:
        match = st.selectbox('Hasil pencarian alamat', range(len(matches)), format_func=lambda i: matches[i]['name'])
        if st.button('Gunakan lokasi ini'):
            st.session_state['planner_home_lat'] = float(matches[match]['lat'])
            st.session_state['planner_home_lon'] = float(matches[match]['lon'])
    with st.expander('Pilih titik rumah di peta atau isi koordinat'):
        center = [st.session_state.get('planner_home_lat') or -6.25, st.session_state.get('planner_home_lon') or 106.85]
        map_ = folium.Map(location=center, zoom_start=11)
        if st.session_state.get('planner_home_lat') is not None and st.session_state.get('planner_home_lon') is not None:
            folium.Marker(center, tooltip='Lokasi rumah skenario').add_to(map_)
        event = st_folium(map_, height=260, use_container_width=True, returned_objects=['last_clicked'], key='planner_home_map')
        click = (event or {}).get('last_clicked')
        if click and click != st.session_state.get('planner_last_click'):
            st.session_state['planner_last_click'] = click
            st.session_state['planner_home_lat'] = float(click['lat'])
            st.session_state['planner_home_lon'] = float(click['lng'])
        left, right = st.columns(2)
        lat = left.number_input('Latitude rumah', min_value=-90.0, max_value=90.0, value=None, format='%.6f', key='planner_home_lat')
        lon = right.number_input('Longitude rumah', min_value=-180.0, max_value=180.0, value=None, format='%.6f', key='planner_home_lon')
        st.caption('Titik awal peta hanya area pandang Jabodetabek; lokasi rumah belum dipilih sampai koordinat diisi.')
    selected_ids = st.multiselect('RS kandidat planner', options=list(by_id),
                                  format_func=lambda i: f'{by_id[i].display_alias or by_id[i].name} · {by_id[i].kota_kab or "lokasi belum diketahui"}',
                                  max_selections=8, key='planner_hospitals')
    selected = [by_id[i] for i in selected_ids]
    names = [h.display_alias or h.name for h in selected]
    # Display names may repeat in the raw registry; use explicit city/ID labels for edits.
    labels = {f'{h.display_alias or h.name} [#{h.id}]': h for h in selected}
    priority_rows = [{'RS': label, 'Prioritas (0–5)': 3} for label in labels]
    priorities = st.data_editor(pd.DataFrame(priority_rows, columns=['RS', 'Prioritas (0–5)']),
                                hide_index=True, disabled=['RS'], key='planner_priorities_'+'_'.join(map(str, selected_ids)),
                                column_config={'Prioritas (0–5)': st.column_config.NumberColumn(min_value=0, max_value=5, step=1)},
                                width="stretch")

    st.markdown('**2 · Kandidat slot praktik**')
    st.caption('Masukkan slot yang ditawarkan RS atau usulan untuk diajukan. Praktik tetap selalu dikunci; kandidat lainnya bisa dikunci agar wajib masuk alternatif.')
    duration = st.selectbox('Durasi usulan celah jadwal (menit)', [120, 180, 240], key='planner_gap_duration')
    if st.button('Tambahkan usulan dari celah jadwal terdata', disabled=not selected):
        suggestions = _gap_suggestions(engine, selected, duration)
        if suggestions:
            existing = st.session_state.get('planner_slot_base', [])
            st.session_state['planner_slot_base'] = (existing + suggestions)[:16]
            st.session_state['planner_revision'] = revision+1
            st.rerun()
        else:
            st.info('Belum ada celah dari data yang cukup lengkap. Masukkan kandidat slot secara manual.')
    raw_slots = st.data_editor(pd.DataFrame(st.session_state.get('planner_slot_base', []), columns=SLOT_COLUMNS),
                               num_rows='dynamic', hide_index=True, width="stretch", key=f'planner_slots_{revision}',
                               column_config={'RS': st.column_config.SelectboxColumn(options=list(labels), required=True),
                                   'Hari': st.column_config.SelectboxColumn(options=list(DAYS), required=True),
                                   'Mulai': st.column_config.TextColumn(help='HH:MM, misalnya 16:00'),
                                   'Selesai': st.column_config.TextColumn(help='HH:MM, misalnya 19:00'),
                                   'Status': st.column_config.SelectboxColumn(options=['Usulan', 'Ditawarkan RS', 'Praktik tetap'], default='Usulan'),
                                   'Kunci': st.column_config.CheckboxColumn(default=False)})
    # Keep editable values when a discovery button refreshes the editor.
    st.session_state['planner_slot_base'] = raw_slots.to_dict('records')

    st.markdown('**3 · Kandidat sekolah · skenario masa depan**')
    school_enabled = st.checkbox('Bandingkan sekolah dalam rencana perjalanan', key='planner_schools_enabled')
    raw_schools = _school_rows(st.session_state.get('planner_school_base', []))
    school_days = list(DAYS[:5])
    school_mode = 'Antar dan jemput'
    curricula, level = [], ''
    max_school_km, school_budget = 20.0, 0.0
    if school_enabled:
        st.caption('Pencarian peta hanya menemukan kandidat lokasi. Kurikulum, jenjang, jam, dan biaya harus diverifikasi dari sumber sekolah atau dicatat sebagai asumsi.')
        a,b = st.columns(2)
        radius = a.slider('Radius pencarian sekolah dari rumah (km)', 1, 30, 10)
        if b.button('Cari kandidat sekolah sekitar rumah', disabled=lat is None or lon is None):
            try:
                found = discover_schools(Place('home', 'Rumah', lat, lon), radius)
                if not found:
                    st.info('Tidak ditemukan kandidat pada sumber peta. Tambahkan sekolah manual.')
                else:
                    old = st.session_state.get('planner_school_base', [])
                    seen = {(row.get('Nama'), row.get('Latitude'), row.get('Longitude')) for row in old}
                    for row in found:
                        if (row['Nama'], row['Latitude'], row['Longitude']) not in seen:
                            old.append({**row, 'Pilih': False})
                    st.session_state['planner_school_base'] = old
                    st.session_state['planner_revision'] = revision+1
                    st.rerun()
            except (ValueError, RoutingError) as exc:
                st.error(str(exc))
        raw_schools = st.data_editor(raw_schools, num_rows='dynamic', hide_index=True,
                                     width="stretch", key=f'planner_schools_{revision}',
                                     column_config={**{name: st.column_config.NumberColumn(min_value=0, format='%.0f') for name in MONEY_COLUMNS},
                                         'Pilih': st.column_config.CheckboxColumn(default=False),
                                         'Latitude': st.column_config.NumberColumn(format='%.6f'),
                                         'Longitude': st.column_config.NumberColumn(format='%.6f'),
                                         'Sumber biaya': st.column_config.LinkColumn(), 'Website': st.column_config.LinkColumn(),
                                         'Sumber lokasi': st.column_config.LinkColumn()})
        st.session_state['planner_school_base'] = raw_schools.to_dict('records')
        st.caption('Kosong = belum diketahui; isi 0 hanya jika memang tidak ada biaya. Tahun pertama = admission + 12×SPP + tahunan + wajib lain. Tahun berikutnya = 12×SPP + tahunan. Sumber lokasi: © OpenStreetMap contributors.')
        a,b,c = st.columns(3)
        school_mode = a.selectbox('Skenario transportasi sekolah', ['Antar dan jemput','Antar saja','Jemput saja','Transportasi lain'])
        school_days = b.multiselect('Hari sekolah', list(DAYS), default=list(DAYS[:5]))
        level = c.text_input('Jenjang wajib (kosong = semua)', placeholder='Contoh: SD')
        available_curricula = sorted({_text(v) for v in raw_schools['Kurikulum'] if _text(v)})
        curricula = st.multiselect('Kurikulum yang diprioritaskan', available_curricula)
        a,b = st.columns(2)
        max_school_km = a.number_input('Maksimum jarak rute rumah–sekolah (km)', 1.0, 100.0, 20.0)
        school_budget = b.number_input('Anggaran tahun pertama (Rp; 0 = tanpa batas)', 0.0, value=0.0, step=1000000.0)

    st.markdown('**4 · Batas mingguan dan pembobotan**')
    a,b,c = st.columns(3)
    target = a.number_input('Target sesi praktik per minggu', 1, 8, 4, key='planner_target')
    max_days = b.number_input('Maksimum hari praktik', 1, 7, 5)
    max_hours = c.number_input('Maksimum jam praktik per minggu', 1.0, 100.0, 40.0)
    a,b,c = st.columns(3)
    earliest = a.time_input('Paling awal berangkat dari rumah', value=time(6,0))
    latest = b.time_input('Paling lambat tiba kembali di rumah', value=time(22,0))
    max_leg = c.number_input('Maksimum waktu satu perjalanan (menit)', 5, 300, 120, step=5)
    a,b,c = st.columns(3)
    buffer = a.number_input('Buffer sebelum kegiatan (menit)', 0, 120, 15, step=5)
    service = b.number_input('Waktu antar/jemput di sekolah (menit)', 0, 60, 10, step=5)
    today = datetime.now(WIB).date()
    next_monday = today + timedelta(days=(7-today.weekday()) % 7 or 7)
    week = c.date_input('Minggu contoh (pilih Senin)', next_monday, min_value=next_monday)
    st.caption('Minggu contoh mewakili pola hari/jam. Biaya sekolah menggunakan tahun ajaran yang dicatat, bukan prediksi harga masa depan.')
    a,b,c = st.columns(3)
    w_commute = a.slider('Bobot perjalanan singkat', 0, 100, 40)
    w_hospital = b.slider('Bobot prioritas RS', 0, 100, 40)
    w_hours = c.slider('Bobot jam praktik', 0, 100, 20)
    a,b = st.columns(2)
    w_cost = a.slider('Bobot biaya sekolah', 0, 100, 0, disabled=not school_enabled)
    w_curriculum = b.slider('Bobot kecocokan kurikulum', 0, 100, 0, disabled=not school_enabled or not curricula)

    cfg = Constraints(int(target), int(max_days), float(max_hours), float(max_leg), minute(str(earliest)),
                      minute(str(latest)), int(buffer), int(service), school_mode, float(max_school_km),
                      school_budget or None, tuple(curricula), level.strip())
    weights = Weights(w_commute,w_hospital,w_hours,w_cost if school_enabled else 0,w_curriculum if curricula and school_enabled else 0)
    scenario = {'version':1, 'home':{'name':address,'lat':lat,'lon':lon}, 'hospital_ids':selected_ids,
                'priorities':priorities.to_dict('records'), 'slots':raw_slots.to_dict('records'),
                'schools_enabled':school_enabled, 'schools':raw_schools.to_dict('records'),
                'school_days':school_days, 'week':str(week), 'constraints':asdict(cfg), 'weights':asdict(weights)}
    # pandas JSON serialization converts NaN to null and never includes API keys.
    exported = pd.Series(scenario).to_json(force_ascii=False, indent=2)
    signature = hashlib.sha256(exported.encode()).hexdigest()
    st.download_button('Simpan input skenario (JSON)', exported, 'rencana-mingguan.json', 'application/json')
    st.caption('Lokasi rumah dan input sekolah hanya digunakan dalam sesi ini. Ekspor skenario mengandung lokasi yang kamu isi.')
    if st.button('Susun alternatif mingguan', type='primary', key='planner_optimize'):
        router = None
        try:
            if lat is None or lon is None:
                raise ValueError('Pilih lokasi rumah terlebih dahulu.')
            if not selected:
                raise ValueError('Pilih setidaknya satu RS kandidat.')
            if raw_slots.empty:
                raise ValueError('Tambahkan kandidat slot praktik terlebih dahulu.')
            priority = {row['RS']:float(row['Prioritas (0–5)']) for row in priorities.to_dict('records')}
            slots = []
            for i,row in enumerate(raw_slots.to_dict('records')):
                if row.get('RS') not in labels:
                    raise ValueError(f'Baris slot {i+1}: pilih RS yang masih ada dalam kandidat.')
                h = labels[row['RS']]
                status = _text(row.get('Status')) or 'Usulan'
                slots.append(Slot(str(i), Place(f'rs-{h.id}', h.display_alias or h.name, h.lat,h.lon),
                                  DAYS.index(row['Hari']), minute(row['Mulai']),minute(row['Selesai']),
                                  priority[row['RS']], bool(row.get('Kunci')) if _present(row.get('Kunci')) else False, status))
                if status == 'Praktik tetap':
                    slots[-1] = Slot(**{**vars(slots[-1]), 'locked':True})
            schools = []
            if school_enabled:
                for i,row in enumerate(raw_schools.to_dict('records')):
                    if not row.get('Pilih'):
                        continue
                    if not school_days:
                        raise ValueError('Pilih hari sekolah untuk skenario ini.')
                    schools.append(School(Place(f'school-{i}',_text(row['Nama']),float(row['Latitude']),float(row['Longitude'])),
                        minute(row['Masuk']),minute(row['Pulang']),tuple(DAYS.index(day) for day in school_days),
                        _text(row['Kurikulum']) or None,_text(row['Jenjang']) or None,
                        *[_amount(row[name]) for name in MONEY_COLUMNS],_text(row['Tahun ajaran']),_text(row['Sumber biaya'])))
                if not schools:
                    raise ValueError('Centang setidaknya satu kandidat sekolah, atau matikan perbandingan sekolah.')
            router = TomTomHistoricalRouter(api_key)
            with st.spinner('Menyusun alternatif dengan waktu tempuh historis…'):
                result = optimize(Place('home','Rumah',lat,lon),slots,schools,week,cfg,weights,router)
            st.session_state['planner_result'] = (signature,result)
        except (ValueError, TypeError, KeyError, RoutingError) as exc:
            st.error(str(exc))
        finally:
            if router:
                router.close()
    saved = st.session_state.get('planner_result')
    if saved and saved[0] != signature:
        st.info('Input berubah. Susun ulang alternatif untuk melihat hasil yang sesuai.')
        return
    if saved:
        _render_results(saved[1])


def _render_results(result):
    st.markdown('**Alternatif rencana mingguan**')
    st.caption('Peringkat hanya membandingkan kandidat yang dimasukkan, berdasarkan bobot pilihanmu. Skor bukan probabilitas ketepatan waktu atau penilaian kualitas sekolah.')
    if not result.plans:
        st.warning('Belum ada kombinasi yang memenuhi seluruh batas. Tambah kandidat slot atau sesuaikan batas perjalanan/jadwal.')
    rows = []
    for i,plan in enumerate(result.plans):
        school = plan.school
        rows.append({'Alternatif':i+1,'Sekolah':school.place.name if school else 'Tanpa skenario sekolah',
                     'Skor':plan.score,'Jam praktik':plan.hours,'Perjalanan/minggu (menit)':round(plan.commute_minutes),
                     'Tambahan sekolah (menit)':round(plan.extra_school_minutes),
                     'Rumah–sekolah (km)':round(plan.school_home_km,1) if plan.school_home_km is not None else None,
                     'Tahun pertama':_rupiah(school.first_year) if school else '—',
                     'Rutin/tahun berikutnya':_rupiah(school.recurring_year) if school else '—',
                     'Kurikulum':school.curriculum or 'Belum diketahui' if school else '—',
                     'Jenjang':school.level or 'Belum diketahui' if school else '—',
                     'Tahun ajaran':school.academic_year or 'Belum diketahui' if school else '—'})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        selected = st.selectbox('Lihat rincian alternatif', range(len(rows)), format_func=lambda i:f'Alternatif {i+1}')
        plan = result.plans[selected]
        for note in plan.notes:
            st.warning(note)
        if plan.school:
            st.write('Sumber biaya sekolah:',plan.school.source or 'Belum dicatat; data perlu verifikasi.')
        calendar = [{'Hari':DAYS[e.day],'Mulai':clock(e.start),'Selesai':clock(e.end),
                     'Kegiatan':e.label,'Lokasi':e.place.name,'Status':e.status} for e in plan.events]
        st.markdown('**Kalender kegiatan**')
        st.dataframe(pd.DataFrame(calendar), hide_index=True, width="stretch")
        journeys = [{'Hari':DAYS[l.day],'Dari':l.origin.name,'Ke':l.destination.name,
                     'Berangkat (WIB)':l.departure.strftime('%H:%M'),'Tiba (WIB)':l.arrival.strftime('%H:%M'),
                     'Menit':round(l.travel.minutes,1),'Km':round(l.travel.km,1),'Sumber':l.travel.source} for l in plan.legs]
        st.markdown('**Rencana perjalanan**')
        st.dataframe(pd.DataFrame(journeys), hide_index=True, width="stretch")
        st.caption('Pada hari tanpa praktik, skenario antar dan jemput menghitung pulang ke rumah di antaranya. Pada hari praktik, rute mengikuti urutan kegiatan; waktu jeda tidak otomatis dianggap perjalanan pulang.')
        st.download_button('Unduh kalender alternatif (CSV)',pd.DataFrame(calendar).to_csv(index=False).encode('utf-8-sig'),
                           'kalender-praktik.csv','text/csv')
    if result.rejected:
        with st.expander('Mengapa kombinasi lain tidak lolos?'):
            st.dataframe(pd.DataFrame([{'Alasan':reason,'Kombinasi / sekolah':count} for reason,count in result.rejected.items()]),hide_index=True)
