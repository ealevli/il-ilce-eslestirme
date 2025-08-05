import pandas as pd
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from time import sleep
import openrouteservice
import re

# 1. Excel dosyasını oku
df = pd.read_excel("test.xlsx")
print("✅ Excel dosyası okundu:", df.columns)

# 2. Geopy ayarı
geolocator = Nominatim(user_agent="adres_bulucu_test")

# 3. OpenRouteService istemcisi
ors_client = openrouteservice.Client(
    key="eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjQ5NzRhMWI5MWI1NDQ1YzQ5OGYzNzg0MWM4YjczZTE3IiwiaCI6Im11cm11cjY0In0="
)

# 4. Temizleme fonksiyonları
def temizle_il_adi(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r'\bmerkez\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediyesi\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediye\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.title().strip()

def temizle_il_ilce_adi(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r'\bbelediyesi\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediye\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.title().strip()

# 5. İl ve ilçe bulma fonksiyonu (temizlenmiş)
def get_city_district(lat, lon):
    try:
        location = geolocator.reverse((lat, lon), language='tr')
        sleep(1)
        address = location.raw.get('address', {})

        raw_city = (
            address.get('city') or
            address.get('province') or
            address.get('state') or
            address.get('region')
        )
        raw_district = (
            address.get('town') or
            address.get('county') or
            address.get('district') or
            address.get('suburb')
        )

        city = temizle_il_adi(raw_city)
        district = temizle_il_ilce_adi(raw_district)

        # Eğer il ve ilçe benzerse, ilçe'yi ilin adı yap
        if district and (district == city or "Merkez" in district):
            district = city

        print(f"✔️ Temizlendi: İl = {city}, İlçe = {district}")
        return pd.Series([city, district])

    except Exception as e:
        print("❌ İl/ilçe hatası:", e)
        return pd.Series([None, None])

# 6. İl ve ilçe sütunlarını ekle
df[['Bulunan İl', 'Bulunan İlçe']] = df.apply(
    lambda row: get_city_district(row['VAKA Lat'], row['VAKA Long']), axis=1
)

# 7. Lineer (kuş uçuşu) mesafe hesaplama
def hesapla_lineer_mesafe(row):
    try:
        vaka_koord = (row['VAKA Lat'], row['VAKA Long'])
        bayi_koord = (row['Bayi Enlem'], row['Bayi Boylam'])
        mesafe = geodesic(vaka_koord, bayi_koord).km
        return round(mesafe, 3)
    except Exception as e:
        print("❌ Lineer mesafe hatası:", e)
        return None

df['Lineer Mesafe (km)'] = df.apply(hesapla_lineer_mesafe, axis=1)

# 8. Reel (arabayla) yol mesafesi hesaplama
def hesapla_reel_yol_mesafesi(row):
    try:
        start = (row['VAKA Long'], row['VAKA Lat'])
        end = (row['Bayi Boylam'], row['Bayi Enlem'])

        response = ors_client.directions(
            coordinates=[start, end],
            profile='driving-car',
            format='geojson',
            preference='fastest'  # Gerçeğe daha yakın sonuç
        )

        # Güvenli kontrol: Tüm anahtarlar mevcut mu
        if (
            'features' in response and
            len(response['features']) > 0 and
            'properties' in response['features'][0] and
            'segments' in response['features'][0]['properties'] and
            len(response['features'][0]['properties']['segments']) > 0
        ):
            mesafe_metre = response['features'][0]['properties']['segments'][0]['distance']
            mesafe_km = mesafe_metre / 1000
            print(f"🚗 Reel Yol Mesafesi: {mesafe_km:.2f} km")
            return round(mesafe_km, 2)
        else:
            print("❌ ORS yanıtı beklenen formatta değil")
            return None

    except Exception as e:
        print("❌ Reel mesafe hatası:", e)
        return None

df['Reel Yol Mesafesi (km)'] = df.apply(hesapla_reel_yol_mesafesi, axis=1)

# 9. Sonucu kaydet
df.to_excel("test_sonuc.xlsx", index=False)
print("✅ Tam işlem tamamlandı. Dosya: test_sonuc.xlsx")


