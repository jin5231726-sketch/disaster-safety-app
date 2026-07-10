import math
import json
import urllib.request

import streamlit as st
import folium
from streamlit.components.v1 import html as st_html
from geopy.geocoders import Nominatim


# -----------------------------
# 1. 주소 -> 좌표 변환
# -----------------------------
@st.cache_data(show_spinner=False)
def get_coordinates(address):
    geolocator = Nominatim(user_agent="disaster_safety_webapp_v1")
    location = geolocator.geocode(address)
    if location:
        return location.latitude, location.longitude
    return None, None


# -----------------------------
# 2. 해발고도 자동 조회
# -----------------------------
@st.cache_data(show_spinner=False)
def get_automated_elevation(lat, lon):
    try:
        url = f"https://api.open-elevation.com/v1/by-locations?locations={lat},{lon}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return float(data['results'][0]['elevation'])
    except Exception:
        return 25.0


# -----------------------------
# 3. 실제 주변 시설(병원/경찰서/대피소) 카카오 로컬 API 조회
# -----------------------------
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0  # 지구 반지름(m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(a)))


def _kakao_headers():
    api_key = st.secrets.get("KAKAO_API_KEY", "")
    return {"Authorization": f"KakaoAK {api_key}"}


def _kakao_category_search(category_group_code, lat, lon, radius):
    """카카오 로컬 API - 카테고리 검색 (병원: HP8, 경찰서: PO3)"""
    url = (
        "https://dapi.kakao.com/v2/local/search/category.json"
        f"?category_group_code={category_group_code}&x={lon}&y={lat}&radius={radius}&sort=distance&size=15"
    )
    req = urllib.request.Request(url, headers=_kakao_headers())
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
    return [
        {"이름": doc["place_name"], "위도": float(doc["y"]), "경도": float(doc["x"])}
        for doc in data.get("documents", [])
    ]


def _kakao_keyword_search(keyword, lat, lon, radius):
    """카카오 로컬 API - 키워드 검색 (대피소는 전용 카테고리 코드가 없어 키워드로 조회)"""
    from urllib.parse import quote
    url = (
        "https://dapi.kakao.com/v2/local/search/keyword.json"
        f"?query={quote(keyword)}&x={lon}&y={lat}&radius={radius}&sort=distance&size=15"
    )
    req = urllib.request.Request(url, headers=_kakao_headers())
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
    return [
        {"이름": doc["place_name"], "위도": float(doc["y"]), "경도": float(doc["x"])}
        for doc in data.get("documents", [])
    ]


@st.cache_data(show_spinner=False, ttl=300)  # 실패 결과가 오래 캐싱되지 않도록 5분 TTL
def find_all_nearby_facilities(lat, lon, radius=3000):
    """병원/경찰서/대피소를 카카오 로컬 API로 조회"""
    if not st.secrets.get("KAKAO_API_KEY"):
        st.error("⚠️ 카카오 API 키가 설정되지 않았습니다. Streamlit Secrets에 KAKAO_API_KEY를 추가해주세요.")
        return [], [], []

    hospitals, police, shelters = [], [], []

    try:
        hospitals = _kakao_category_search("HP8", lat, lon, radius)
    except Exception as e:
        st.warning(f"병원 조회 실패: {e}")

    try:
        police = _kakao_category_search("PO3", lat, lon, radius)
    except Exception as e:
        st.warning(f"경찰서 조회 실패: {e}")

    try:
        shelters = _kakao_keyword_search("대피소", lat, lon, radius)
    except Exception as e:
        st.warning(f"대피소 조회 실패: {e}")

    return hospitals, police, shelters


def google_maps_search_url(query, lat, lon):
    """API 키 없이도 항상 작동하는 구글맵 검색 링크 (Overpass 실패 시 대체용)"""
    from urllib.parse import quote
    return f"https://www.google.com/maps/search/{quote(query)}/@{lat},{lon},16z"


def find_closest(user_lat, user_lon, facilities):
    if not facilities:
        return None
    best = min(facilities, key=lambda f: haversine_m(user_lat, user_lon, f["위도"], f["경도"]))
    dist = haversine_m(user_lat, user_lon, best["위도"], best["경도"])
    return {"이름": best["이름"], "거리": round(dist, 1), "위도": best["위도"], "경도": best["경도"]}


# -----------------------------
# 4. 지진 시나리오 텍스트
# -----------------------------
def predict_earthquake_scenario(score):
    if score >= 85:
        grade = "A등급 (상대적 안전 우수)"
        before = "입력하신 조건상 구조·연식·경사 요인의 감점이 적은 편입니다."
        after = ("다른 등급 대비 붕괴 위험이 낮게 추정되나, 이는 몇 가지 변수로 계산한 참고용 점수일 뿐 "
                 "전문 구조기술사의 내진성능평가를 대체하지 않습니다. 실제 강진 발생 시에도 반드시 대피 매뉴얼에 따라 행동해야 합니다.")
    elif score >= 60:
        grade = "B등급 (양호/주의)"
        before = "노후화가 다소 진행되었거나 과거 내진 기준이 적용되어 보완이 필요할 수 있는 상태입니다."
        after = ("강한 흔들림 발생 시 천장 마감재, 조명 기구의 낙하 위험이 있습니다. 외벽 균열이 발생할 수 있으므로, "
                 "지진 직후 책상 밑으로 신속히 대피한 뒤 계단을 통해 공터로 즉시 탈출해야 합니다.")
    else:
        grade = "X등급 (붕괴 위험 고조)"
        before = "지진에 취약한 구조(조적조 등)이거나 내진 설계 기준 적용 이전 건물로 추정됩니다."
        after = ("구조부(기둥, 보)에 손상이 생겨 붕괴로 이어질 위험이 상대적으로 높게 추정됩니다. 탈출로가 차단될 수 있으므로, "
                 "대피 경보 즉시 머리를 보호하며 건물 밖으로 대피하는 것을 우선 고려해야 합니다.")
    return grade, before, after


# -----------------------------
# 5. 종합 안전성 점수 계산 (2017년 내진기준 강화 반영)
# -----------------------------
def evaluate_comprehensive_safety(structure, year, floors, elevation, slope, river_dist):
    eq_score = 100
    if structure in ["벽돌조", "조적조", "블록조"]:
        eq_score -= 30
    elif structure in ["목조", "황토구조"]:
        eq_score -= 15

    if year < 1988:
        eq_score -= 30
    elif year < 2000:
        eq_score -= 20
    elif year < 2017:
        eq_score -= 10

    if slope >= 25:
        eq_score -= 20
    elif slope >= 10:
        eq_score -= 10
    if floors >= 16:
        eq_score -= 20
    elif floors >= 6:
        eq_score -= 10

    flood_score = 100
    if elevation < 15:
        flood_score -= 40
    elif elevation < 30:
        flood_score -= 20
    if river_dist < 100:
        flood_score -= 30
    elif river_dist < 500:
        flood_score -= 15
    if floors == 1:
        flood_score -= 30
    elif floors <= 3:
        flood_score -= 15

    typhoon_score = 100
    if floors >= 16:
        typhoon_score -= 40
    elif floors >= 4:
        typhoon_score -= 20
    if slope >= 15:
        typhoon_score -= 30
    if (2026 - year) >= 15:
        typhoon_score -= 30

    eq_score, flood_score, typhoon_score = max(0, eq_score), max(0, flood_score), max(0, typhoon_score)
    return {
        "지진점수": eq_score, "홍수점수": flood_score, "태풍점수": typhoon_score,
        "종합점수": min(eq_score, flood_score, typhoon_score)
    }


# =========================================================
# 🚀 Streamlit 화면 구성
# =========================================================
st.set_page_config(page_title="재난 안전성 평가 시스템", page_icon="🚨", layout="centered")
st.title("🚨 건물 재난 안전성 평가 및 구호 기관 안내")
st.caption("이 점수는 참고용 추정치이며 전문 구조기술사의 내진성능평가를 대체하지 않습니다.")

address = st.text_input("1. 분석할 건물 주소를 입력하세요", placeholder="예: 서울특별시 종로구 세종대로 1")

if address:
    with st.spinner("주소 확인 및 지형 정보 수집 중..."):
        lat, lon = get_coordinates(address)

    if lat is None:
        st.error("❌ 주소를 찾을 수 없습니다. 다른 형식으로 다시 입력해보세요.")
    else:
        st.success(f"주소 확인 완료! (위도 {lat:.4f}, 경도 {lon:.4f})")
        elevation = get_automated_elevation(lat, lon)
        slope, river_dist = 8.5, 350.0  # 임시값: 실서비스 배포 시 실제 GIS API로 교체 필요
        st.info(f"해발고도: {elevation:.1f}m  ｜  경사도·하천거리는 현재 임시값입니다 ({slope}°, {river_dist}m)")

        st.subheader("2. 건축물 정보 입력")
        col1, col2, col3 = st.columns(3)
        with col1:
            structure = st.selectbox("건물 구조", ["철근콘크리트", "벽돌조", "조적조", "블록조", "목조", "황토구조"])
        with col2:
            year = st.number_input("준공 연도", min_value=1900, max_value=2026, value=2010, step=1)
        with col3:
            floors = st.number_input("층수", min_value=1, max_value=100, value=4, step=1)

        if st.button("평가하기", type="primary"):
            scores = evaluate_comprehensive_safety(structure, int(year), int(floors), elevation, slope, river_dist)
            grade, before, after = predict_earthquake_scenario(scores["종합점수"])

            st.subheader("📊 평가 결과")
            c1, c2, c3 = st.columns(3)
            c1.metric("지진 점수", f"{scores['지진점수']}점")
            c2.metric("홍수 점수", f"{scores['홍수점수']}점")
            c3.metric("태풍 점수", f"{scores['태풍점수']}점")

            st.markdown(f"### ⭐ 종합 등급: {grade} (종합 점수 {scores['종합점수']}점)")
            st.write(f"**현재 상태 추정**: {before}")
            st.write(f"**지진 시 상황 예측**: {after}")

            st.subheader("🏃 주변 구호 기관 (카카오맵 데이터 기반)")
            with st.spinner("주변 병원/경찰서/대피소 실시간 조회 중..."):
                hospitals, police, shelters = find_all_nearby_facilities(lat, lon)

            closest_hospital = find_closest(lat, lon, hospitals)
            closest_police = find_closest(lat, lon, police)
            closest_shelter = find_closest(lat, lon, shelters)

            hospital_text = f"{closest_hospital['이름']} ({closest_hospital['거리']}m)" if closest_hospital else "반경 내 조회 결과 없음"
            police_text = f"{closest_police['이름']} ({closest_police['거리']}m)" if closest_police else "반경 내 조회 결과 없음"
            shelter_text = f"{closest_shelter['이름']} ({closest_shelter['거리']}m)" if closest_shelter else "반경 내 조회 결과 없음 (지자체 공식 자료 확인 권장)"

            st.write(f"🏥 응급 의료원: {hospital_text}")
            if not closest_hospital:
                st.caption(f"↳ 실시간 조회가 불안정할 수 있습니다. [구글맵에서 주변 병원 직접 확인]({google_maps_search_url('병원', lat, lon)})")

            st.write(f"🚔 치안/구조처: {police_text}")
            if not closest_police:
                st.caption(f"↳ 실시간 조회가 불안정할 수 있습니다. [구글맵에서 주변 경찰서 직접 확인]({google_maps_search_url('경찰서', lat, lon)})")

            st.write(f"🚨 지정 대피소: {shelter_text}")
            if not closest_shelter:
                st.caption(f"↳ [구글맵에서 주변 대피소/공터 직접 확인]({google_maps_search_url('대피소', lat, lon)})")

            b_color = "green" if scores["종합점수"] >= 85 else ("orange" if scores["종합점수"] >= 60 else "red")
            m = folium.Map(location=[lat, lon], zoom_start=15)
            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(f"<b>대상 건물 안전 추정 점수: {scores['종합점수']}점</b><br>{grade}", max_width=250),
                icon=folium.Icon(color=b_color, icon="home"),
            ).add_to(m)
            for f in shelters:
                folium.Marker([f["위도"], f["경도"]], popup=f["이름"], icon=folium.Icon(color="blue", icon="info-sign")).add_to(m)
            for f in hospitals:
                folium.Marker([f["위도"], f["경도"]], popup=f"🏥 {f['이름']}", icon=folium.Icon(color="red", icon="medical")).add_to(m)
            for f in police:
                folium.Marker([f["위도"], f["경도"]], popup=f"🚔 {f['이름']}", icon=folium.Icon(color="cadetblue", icon="shield")).add_to(m)

            st_html(m._repr_html_(), height=500)
