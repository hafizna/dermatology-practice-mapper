from datetime import date, datetime
from dataclasses import replace
import logging

import httpx
import pytest

from src.planning.core import (Constraints, Place, School, Slot, Travel, Weights,
                               WIB, events_for, minute, optimize, route_week)
from src.planning.services import RoutingError, TomTomHistoricalRouter

HOME = Place('home','Rumah',-6.2,106.8)
A = Place('a','RS A',-6.21,106.81)
B = Place('b','RS B',-6.22,106.82)
SCHOOL = Place('school','Sekolah',-6.205,106.805)
WEEK = date(2026,10,19)


def router(a,b,when,arrive_by=False):
    return Travel(15,5,'Fixture historical')


def slot(id='a', place=A, day=0, start=960, end=1080, priority=3, locked=False):
    return Slot(id,place,day,start,end,priority,locked)


def cfg(**kwargs):
    return replace(Constraints(sessions=1),**kwargs)


def test_locked_slot_is_kept_even_when_another_rs_has_higher_weight():
    fixed=slot(locked=True,priority=0)
    result=optimize(HOME,[fixed,slot('b',B,priority=5)],[],WEEK,cfg(),Weights(),router)
    assert len(result.plans)==1
    assert result.plans[0].slots==(fixed,)


def test_hospital_preference_changes_suggestion():
    result=optimize(HOME,[slot(priority=1),slot('b',B,priority=5)],[],WEEK,cfg(),Weights(0,100,0),router)
    assert result.plans[0].slots[0].place==B


def test_overlapping_and_unreachable_transfers_are_rejected():
    slots=[slot(locked=True),slot('b',B,start=1085,end=1200,locked=True)]
    result=optimize(HOME,slots,[],WEEK,cfg(sessions=2),Weights(),router)
    assert not result.plans
    assert result.rejected['Waktu perjalanan/buffer tidak mencukupi']==1
    slots[1]=slot('b',B,start=1000,end=1200,locked=True)
    result=optimize(HOME,slots,[],WEEK,cfg(sessions=2),Weights(),router)
    assert not result.plans
    assert result.rejected['Benturan jadwal']==1


def test_route_time_uses_actual_previous_end_and_first_arrival_deadline():
    calls=[]
    def timed(a,b,when,arrive_by):
        calls.append((a.id,b.id,when,arrive_by))
        return router(a,b,when,arrive_by)
    result=optimize(HOME,[slot(),slot('b',B,start=1140,end=1260)],[],WEEK,cfg(sessions=2),Weights(),timed)
    assert result.plans
    assert any(a=='a' and b=='b' and when.hour==18 and not arrive for a,b,when,arrive in calls)
    assert any(a=='home' and when.hour==15 and when.minute==45 and arrive for a,b,when,arrive in calls)
    assert all(when.utcoffset().total_seconds()==7*3600 for _,_,when,_ in calls)


def test_school_pickup_conflict_is_a_hard_constraint():
    school=School(SCHOOL,450,840)
    result=optimize(HOME,[slot(start=780,end=900)],[school],WEEK,cfg(),Weights(),router)
    assert not result.plans
    assert result.rejected['Benturan jadwal']==1


def test_school_only_days_return_home_between_dropoff_and_pickup():
    school=School(SCHOOL,450,840,days=(0,))
    events=events_for((),school,cfg())
    legs=route_week(HOME,events,WEEK,cfg(),router)
    assert [(l.origin.id,l.destination.id) for l in legs]==[
        ('home','school'),('school','home'),('home','school'),('school','home')]


def test_fee_totals_and_unknown_are_different_from_zero():
    unknown=School(SCHOOL,450,840)
    assert unknown.first_year is None
    known=replace(unknown,admission=10,monthly=2,annual=3,other_first_year=4)
    assert known.first_year==41
    assert known.recurring_year==27
    assert replace(known,admission=0,monthly=0,annual=0,other_first_year=0).first_year==0


def test_unknown_fees_cannot_pass_a_hard_budget():
    result=optimize(HOME,[slot()],[School(SCHOOL,450,840)],WEEK,cfg(first_year_budget=100),Weights(),router)
    assert not result.plans
    assert 'Biaya belum lengkap untuk memeriksa anggaran' in result.rejected


def test_unknown_fees_are_not_ranked_when_cost_is_weighted():
    result=optimize(HOME,[slot()],[School(SCHOOL,450,840)],WEEK,cfg(),Weights(school_cost=10),router)
    assert result.plans and result.plans[0].score is None


def test_cost_priority_favors_cheaper_school_with_same_travel():
    first=School(SCHOOL,450,840,admission=100,monthly=10,annual=10,other_first_year=0)
    cheaper=replace(first,place=Place('school2','Sekolah B',-6.206,106.806),admission=10)
    result=optimize(HOME,[slot()],[first,cheaper],WEEK,cfg(),Weights(0,0,0,100,0),router)
    assert result.plans[0].school.place.id=='school2'


def test_curriculum_and_level_preferences_are_respected():
    school=School(SCHOOL,450,840,level='SD')
    result=optimize(HOME,[slot()],[school],WEEK,cfg(preferred_curricula=('IB',)),Weights(curriculum=10),router)
    assert result.plans[0].score is None
    result=optimize(HOME,[slot()],[school],WEEK,cfg(preferred_level='SMP'),Weights(),router)
    assert not result.plans


def test_school_distance_and_earliest_departure_limits():
    result=optimize(HOME,[slot()],[School(SCHOOL,450,840)],WEEK,cfg(max_school_km=1),Weights(),router)
    assert not result.plans
    result=optimize(HOME,[slot(start=360,end=480)],[],WEEK,cfg(),Weights(),router)
    assert not result.plans


def test_max_working_days_and_hours_are_hard_limits():
    result=optimize(HOME,[slot(),slot('b',day=1)],[],WEEK,cfg(sessions=2,max_days=1),Weights(),router)
    assert not result.plans
    result=optimize(HOME,[slot()],[],WEEK,cfg(max_hours=1),Weights(),router)
    assert not result.plans


def test_router_failure_never_becomes_a_zero_minute_trip():
    def unavailable(*args):
        raise RoutingError('Unavailable')
    with pytest.raises(RoutingError):
        optimize(HOME,[slot()],[],WEEK,cfg(),Weights(),unavailable)


@pytest.mark.parametrize('value',['25:00','17.30','abc','10:15:30'])
def test_bad_times_are_rejected(value):
    with pytest.raises(ValueError): minute(value)


def test_historical_api_parameters_and_private_key_redaction(caplog):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={'routes':[{'summary':{'historicTrafficTravelTimeInSeconds':1800,'lengthInMeters':12000}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        api=TomTomHistoricalRouter('SECRET_KEY',client=client)
        with caplog.at_level(logging.INFO,logger='httpx'):
            result=api(HOME,A,datetime(2026,10,19,16,tzinfo=WIB),True)
        assert result.minutes==30 and result.km==12
        query=seen[0].url.params
        assert query['traffic']=='false'
        assert query['travelMode']=='car'
        assert query['arriveAt'].endswith('+07:00') and 'departAt' not in query
        assert 'SECRET_KEY' not in caplog.text


def test_missing_historical_field_and_http_error_have_no_fallback():
    for response in [httpx.Response(200,json={'routes':[{'summary':{'travelTimeInSeconds':60,'lengthInMeters':100}}]}),httpx.Response(403)]:
        with httpx.Client(transport=httpx.MockTransport(lambda request:response)) as client:
            api=TomTomHistoricalRouter('private',client=client)
            with pytest.raises(RoutingError): api(HOME,A,datetime(2026,10,19,16,tzinfo=WIB))


def test_api_call_budget_stops_further_requests():
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'results':[]}))) as client:
        api=TomTomHistoricalRouter('private',client=client,max_calls=1)
        api.geocode('Alamat lengkap')
        with pytest.raises(RoutingError,match='Batas perhitungan'):
            api.geocode('Alamat lengkap lain')


def test_gap_suggestions_do_not_conflate_same_named_branches(in_memory_engine):
    from sqlalchemy.orm import Session
    from src.models import Hospital, HospitalPracticeMetrics, Doctor, ScheduleSlot, ParseConfidence
    from src.planning.ui import _gap_suggestions
    with Session(in_memory_engine) as session:
        hospitals=[]
        for index in range(2):
            hospital=Hospital(name='RS Nama Sama',name_normalized='nama sama')
            session.add(hospital)
            session.flush()
            doctor=Doctor(hospital_id=hospital.id,raw_name='dr. Contoh, Sp.DVE')
            session.add(doctor)
            session.flush()
            session.add(HospitalPracticeMetrics(hospital_id=hospital.id,schedule_completeness=1))
            session.add(ScheduleSlot(hospital_id=hospital.id,doctor_id=doctor.id,day_of_week=0,
                start_time='08:00',end_time='10:00',raw_text='Senin 08:00-10:00',parse_confidence=ParseConfidence.HIGH))
            hospitals.append(hospital)
        session.commit()
        rows=_gap_suggestions(in_memory_engine,hospitals,120)
        assert {row['RS'] for row in rows} == {f'RS Nama Sama [#{h.id}]' for h in hospitals}
        assert all(row['Status']=='Usulan' for row in rows)



@pytest.mark.parametrize('copied', ['fixture-key', '  fixture-key  ', '"fixture-key"', "'fixture-key'", 'TOMTOM_API_KEY = "fixture-key"', 'fixture-\nkey', '\u200bfixture-key\ufeff'])
def test_key_copy_artifacts_are_removed(copied):
    from src.planning.services import normalize_api_key
    assert normalize_api_key(copied)=='fixture-key'


def test_401_is_reported_as_authentication_not_an_address_error():
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(401))) as client:
        api=TomTomHistoricalRouter('fixture-key',client=client)
        with pytest.raises(RoutingError,match='menolak API key') as error:
            api.geocode('Alamat contoh')
        assert 'bukan kesalahan alamat' in str(error.value)
