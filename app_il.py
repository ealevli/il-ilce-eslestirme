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


# --- Fonksiyonlar ---

# Geopy ve ORS istemcilerini bir kere oluşturup cache'le
@st.cache_resource
def get_clients(key):
    """API anahtarına göre geopy ve openrouteservice istemcilerini oluşturur."""
    geolocator = Nominatim(user_agent="streamlit_geolocator_app")
    ors_client = openrouteservice.Client(key=key)
    return geolocator, ors_client

geolocator, ors_client = get_clients(api_key)

# DÜZENLENDİ: İki fonksiyon yerine tek, birleştirilmiş ve doğru çalışan bir fonksiyon
def temizle_lokasyon_adi(text):
    """İl veya ilçe adlarındaki 'merkez', 'belediye' gibi istenmeyen ifadeleri temizler."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    text = re.sub(r'\bmerkez\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediyesi\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbelediye\b', '', text, flags=re.IGNORECASE)
    # Temizlik sonrası oluşan çift boşlukları tek boşluğa indirir
    text = re.sub(r'\s{2,}', ' ', text)
    # Baş harfleri büyütür ve kenar boşluklarını son bir kez daha alır
    return text.title().strip()


@st.cache_data
def get_city_district(_geolocator, lat, lon):
    """Verilen koordinatlar için il ve ilçe bilgisini alır ve temizler."""
    try:
        # Nominatim kullanım politikasına uymak için istekler arası bekleme
        sleep(1) 
        location = _geolocator.reverse((lat, lon), language='tr', timeout=10)
        address = location.raw.get('address', {})
        
        # Olası tüm anahtarları kontrol et
        raw_city = address.get('province') or address.get('state')
        raw_district = address.get('county') or address.get('town') or address.get('district') or address.get('suburb')
        
        # DÜZENLENDİ: Tek ve doğru temizleme fonksiyonunu kullan
        city = temizle_lokasyon_adi(raw_city)
        district = temizle_lokasyon_adi(raw_district)
        
        return city, district
    except Exception as e:
        st.error(f"Adres bulma hatası (Lat: {lat}, Lon: {lon}): {e}")
        return f"Hata", "Hata"


def hesapla_mesafeler(row):
    """Tek bir satır için lineer ve reel yol mesafesini hesaplar."""
    try:
        vaka_koord = (row['VAKA Lat'], row['VAKA Long'])
        bayi_koord = (row['Bayi Enlem'], row['Bayi Boylam'])
        
        # Lineer Mesafe
        lineer_mesafe = round(geodesic(vaka_koord, bayi_koord).km, 2)
        
        # Reel Yol Mesafesi
        start_coords = (row['VAKA Long'], row['VAKA Lat'])
        end_coords = (row['Bayi Boylam'], row['Bayi Enlem'])
        
        response = ors_client.directions(
            coordinates=[start_coords, end_coords],
            profile='driving-car',
            format='geojson',
            preference='fastest'
        )
        mesafe_metre = response['features'][0]['properties']['segments'][0]['distance']
        reel_mesafe = round(mesafe_metre / 1000, 2)
        
        return lineer_mesafe, reel_mesafe
    except Exception as e:
        st.warning(f"Mesafe hesaplama hatası (Satır {row.name}): {e}")
        return None, None

# --- Dosya Yükleme ve Ana İşlem ---
uploaded_file = st.file_uploader(
    "İşlem Yapılacak Excel Dosyasını Yükleyin",
    type=["xlsx"],
    help="Dosyanızda 'VAKA Lat', 'VAKA Long', 'Bayi Enlem', 'Bayi Boylam' sütunları bulunmalıdır."
)

if uploaded_file is not None:
    df_original = pd.read_excel(uploaded_file)
    st.subheader("Yüklenen Veri Önizlemesi")
    st.dataframe(df_original.head())

    if st.button("✅ Analizi Başlat", type="primary"):
        total_rows = len(df_original)
        df_result = df_original.copy()
        
        # Yeni sütunları başlangıçta boş olarak ekle
        df_result['Bulunan İl'] = ""
        df_result['Bulunan İlçe'] = ""
        df_result['Lineer Mesafe (km)'] = 0.0
        df_result['Reel Yol Mesafesi (km)'] = 0.0

        progress_bar = st.progress(0, text="Başlatılıyor...")
        
        for index, row in df_result.iterrows():
            # İl/İlçe bul
            il, ilce = get_city_district(geolocator, row['VAKA Lat'], row['VAKA Long'])
            df_result.at[index, 'Bulunan İl'] = il
            df_result.at[index, 'Bulunan İlçe'] = ilce
            
            # Mesafeleri hesapla
            lineer_mesafe, reel_mesafe = hesapla_mesafeler(row)
            df_result.at[index, 'Lineer Mesafe (km)'] = lineer_mesafe
            df_result.at[index, 'Reel Yol Mesafesi (km)'] = reel_mesafe
            
            # İlerleme durumunu güncelle
            progress_percent = (index + 1) / total_rows
            progress_bar.progress(progress_percent, text=f"Satır {index + 1}/{total_rows} işleniyor...")

        progress_bar.empty()
        st.success("✅ Hesaplamalar tamamlandı!")

        st.subheader("Sonuçlar")
        st.dataframe(df_result)

        # --- Sonuçları İndirme Butonu ---
        @st.cache_data
        def convert_df_to_excel(df):
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Sonuclar')
            return output.getvalue()

        excel_data = convert_df_to_excel(df_result)
        st.download_button(
            label="📥 Sonuçları Excel Olarak İndir",
            data=excel_data,
            file_name=f"sonuc_{uploaded_file.name}",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
