import streamlit as st
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import openrouteservice
import re
from io import BytesIO
from time import sleep

# --- Sayfa Yapılandırması ve Başlık ---
st.set_page_config(page_title="Mesafe ve Lokasyon Analiz Aracı", layout="wide")
st.title("🗺️ Mesafe ve Lokasyon Analiz Aracı")
st.info(
    "Bu uygulama, yüklediğiniz Excel dosyasındaki 'VAKA' ve 'Bayi' koordinatları arasında kuş uçuşu ve karayolu mesafesini hesaplar. "
    "Ayrıca VAKA koordinatlarına göre İl/İlçe tespiti yapar."
)

# --- API Anahtarı Yönetimi ---
st.sidebar.header("Ayarlar")
# Streamlit Secrets'tan anahtarı almayı dene
try:
    default_key = st.secrets["ORS_KEY"]
except (FileNotFoundError, KeyError):
    default_key = ""

api_key = st.sidebar.text_input(
    "OpenRouteService API Anahtarı",
    type="password",
    value=default_key,
    help="API anahtarınızı https://openrouteservice.org/dev/#/home adresinden alabilirsiniz."
)

if not api_key:
    st.warning("Lütfen devam etmek için sol menüden OpenRouteService API anahtarınızı girin.")
    st.stop()


# --- Orijinal Fonksiyonlarınız (Streamlit için küçük düzenlemelerle) ---

# Geopy ve ORS istemcilerini bir kere oluştur
@st.cache_resource
def get_clients(key):
    geolocator = Nominatim(user_agent="streamlit_geolocator_app")
    ors_client = openrouteservice.Client(key=key)
    return geolocator, ors_client

geolocator, ors_client = get_clients(api_key)


def temizle_il_adi(text):
    if not text: return None
    text = text.strip()
    text = re.sub(r'\bmerkez\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediyesi\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediye\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.title().strip()


def temizle_il_ilce_adi(text):
    if not text: return None
    text = text.strip()
    text = re.sub(r'\bbelediyesi\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediye\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.title().strip()


def get_city_district(lat, lon):
    try:
        location = geolocator.reverse((lat, lon), language='tr', timeout=10)
        sleep(1) # Nominatim kullanım politikasına uymak için
        address = location.raw.get('address', {})
        raw_city = address.get('province') or address.get('state')
        raw_district = address.get('county') or address.get('town') or address.get('district') or address.get('suburb')
        city = temizle_il_adi(raw_city)
        district = temizle_il_ilce_adi(raw_district)
        return pd.Series([city, district])
    except Exception as e:
        return pd.Series([f"Hata: {e}", None])


def hesapla_lineer_mesafe(row):
    try:
        vaka_koord = (row['VAKA Lat'], row['VAKA Long'])
        bayi_koord = (row['Bayi Enlem'], row['Bayi Boylam'])
        return round(geodesic(vaka_koord, bayi_koord).km, 2)
    except Exception:
        return None


def hesapla_reel_yol_mesafesi(row):
    try:
        start = (row['VAKA Long'], row['VAKA Lat'])
        end = (row['Bayi Boylam'], row['Bayi Enlem'])
        response = ors_client.directions(
            coordinates=[start, end],
            profile='driving-car',
            format='geojson',
            preference='fastest'
        )
        mesafe_metre = response['features'][0]['properties']['segments'][0]['distance']
        return round(mesafe_metre / 1000, 2)
    except Exception:
        return None

# --- Dosya Yükleme Alanı ---
uploaded_file = st.file_uploader(
    "İşlem Yapılacak Excel Dosyasını Yükleyin",
    type=["xlsx"],
    help="Dosyanızda 'VAKA Lat', 'VAKA Long', 'Bayi Enlem', 'Bayi Boylam' sütunları bulunmalıdır."
)

if uploaded_file is not None:
    df = pd.read_excel(uploaded_file)
    st.subheader("Yüklenen Veri Önizlemesi")
    st.dataframe(df.head())

    # --- Hesaplamayı Başlatma Butonu ---
    if st.button("✅ Analizi Başlat", type="primary"):
        total_rows = len(df)
        results = []

        # Status ve progress bar ile kullanıcıyı bilgilendir
        with st.status("Hesaplamalar yapılıyor, lütfen bekleyin...", expanded=True) as status:
            progress_bar = st.progress(0, text="Başlatılıyor...")
            
            for index, row in df.iterrows():
                # İl/İlçe bul
                il_ilce = get_city_district(row['VAKA Lat'], row['VAKA Long'])
                
                # Mesafeleri hesapla
                lineer_mesafe = hesapla_lineer_mesafe(row)
                reel_mesafe = hesapla_reel_yol_mesafesi(row)

                # Sonuçları birleştir
                processed_row = {
                    **row.to_dict(),
                    'Bulunan İl': il_ilce[0],
                    'Bulunan İlçe': il_ilce[1],
                    'Lineer Mesafe (km)': lineer_mesafe,
                    'Reel Yol Mesafesi (km)': reel_mesafe
                }
                results.append(processed_row)

                # İlerleme durumunu güncelle
                progress_percent = (index + 1) / total_rows
                progress_bar.progress(progress_percent, text=f"Satır {index + 1}/{total_rows} işleniyor...")

            status.update(label="✅ Hesaplamalar tamamlandı!", state="complete", expanded=False)

        # Sonuçları DataFrame'e dönüştür
        df_result = pd.DataFrame(results)

        st.subheader("Sonuçlar")
        st.dataframe(df_result)

        # --- Sonuçları İndirme Butonu ---
        @st.cache_data
        def convert_df_to_excel(df):
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Sonuclar')
            processed_data = output.getvalue()
            return processed_data

        excel_data = convert_df_to_excel(df_result)
        st.download_button(
            label="📥 Sonuçları Excel Olarak İndir",
            data=excel_data,
            file_name=f"sonuc_{uploaded_file.name}",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )