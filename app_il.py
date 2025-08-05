import streamlit as st
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import openrouteservice
import re
from io import BytesIO
from time import sleep

# --- Sayfa Yapılandırması ve Başlık ---
st.set_page_config(page_title="Gelişmiş Mesafe ve Lokasyon Analiz Aracı", layout="wide")
st.title("🗺️ Gelişmiş Mesafe ve Lokasyon Analiz Aracı")
st.info(
    "Bu uygulama, yüklediğiniz Excel dosyasındaki 'VAKA' ve 'Bayi' koordinatları arasında kuş uçuşu ve karayolu mesafesini hesaplar. "
    "Ayrıca VAKA koordinatlarına göre İl/İlçe tespiti yapar ve hataları en aza indirmek için yeniden deneme mekanizması kullanır."
)

# --- API Anahtarı Yönetimi ---
st.sidebar.header("Ayarlar")
try:
    default_key = st.secrets.get("ORS_KEY", "")
except (FileNotFoundError, AttributeError):
    default_key = ""

api_key = st.sidebar.text_input(
    "OpenRouteService API Anahtarı",
    type="password",
    value=default_key,
    help="API anahtarınızı https://openrouteservice.org/dev/#/home adresinden ücretsiz alabilirsiniz."
)

if not api_key:
    st.warning("Lütfen devam etmek için sol menüden OpenRouteService API anahtarınızı girin.")
    st.stop()


# --- Fonksiyonlar ---

@st.cache_resource
def get_clients(key):
    """API anahtarına göre geopy ve openrouteservice istemcilerini oluşturur."""
    geolocator = Nominatim(user_agent=f"streamlit_geolocator_app_{st.session_state.session_id}")
    ors_client = openrouteservice.Client(key=key)
    return geolocator, ors_client

# Her kullanıcı oturumu için benzersiz bir ID oluştur
if 'session_id' not in st.session_state:
    import uuid
    st.session_state.session_id = str(uuid.uuid4())

geolocator, ors_client = get_clients(api_key)


def temizle_lokasyon_adi(text):
    """İl veya ilçe adlarındaki istenmeyen ifadeleri temizler ve formatlar."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    # 'Merkez', 'Belediyesi' gibi kelimeleri ve parantez içlerini temizle
    text = re.sub(r'\bmerkez\b|\bbelediyesi\b|\bbelediye\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\(.*\)', '', text) # Parantez içlerini temizle
    text = re.sub(r'\s{2,}', ' ', text).strip() # Çift boşlukları teke indir
    return text.title()


@st.cache_data
def get_city_district(_geolocator, lat, lon, retries=3):
    """
    Verilen koordinatlar için il ve ilçe bilgisini alır.
    Hata durumunda veya boş sonuçta yeniden dener.
    İl ve ilçe aynıysa, ilçeyi 'Merkez' olarak değiştirir.
    """
    for attempt in range(retries):
        try:
            # Nominatim kullanım politikasına uymak için istekler arası bekleme
            sleep(1.1)
            location = _geolocator.reverse((lat, lon), language='tr', timeout=20)
            
            if location and location.raw.get('address'):
                address = location.raw['address']
                
                # Olası tüm il ve ilçe anahtarlarını daha geniş bir liste ile kontrol et
                raw_city = address.get('province') or address.get('state')
                raw_district = (
                    address.get('county') or
                    address.get('town') or
                    address.get('district') or
                    address.get('suburb') or
                    address.get('village')
                )
                
                city = temizle_lokasyon_adi(raw_city)
                district = temizle_lokasyon_adi(raw_district)

                # Eğer il veya ilçe bulunamadıysa None dönsün ki sonraki adımda kontrol edilsin
                if not city or not district:
                    return None, None

                # İl ve ilçe adı aynı ise, ilçeyi "Merkez" yap
                if city == district:
                    district = "Merkez"
                
                return city, district
            
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            st.warning(f"Adres bulma hatası (Lat: {lat}, Lon: {lon}) - Deneme {attempt + 1}/{retries}. Hata: {e}")
            sleep(2) # Hata sonrası daha uzun bekle
        except Exception as e:
            st.error(f"Beklenmedik adres bulma hatası (Lat: {lat}, Lon: {lon}): {e}")
            return "Hata", "Hata"
            
    st.error(f"Adres bulunamadı (Lat: {lat}, Lon: {lon}) - Tüm denemeler başarısız.")
    return "Bulunamadı", "Bulunamadı"


def hesapla_mesafeler(row):
    """Tek bir satır için lineer ve reel yol mesafesini hesaplar."""
    try:
        vaka_koord = (row['VAKA Lat'], row['VAKA Long'])
        bayi_koord = (row['Bayi Enlem'], row['Bayi Boylam'])

        # Koordinatların geçerli olup olmadığını kontrol et
        if not all(isinstance(c, (int, float)) for c in vaka_koord + bayi_koord):
            return None, None
            
        # Lineer Mesafe
        lineer_mesafe = round(geodesic(vaka_koord, bayi_koord).km, 2)
        
        # Reel Yol Mesafesi (Long, Lat formatında)
        start_coords = (row['VAKA Long'], row['VAKA Lat'])
        end_coords = (row['Bayi Boylam'], row['Bayi Enlem'])
        
        response = ors_client.directions(
            coordinates=[start_coords, end_coords],
            profile='driving-car',
            format='geojson',
            preference='fastest',
            radiuses=[1000, 1000] # Yakın bir yol bulamazsa arama yarıçapını genişletir
        )
        mesafe_metre = response['features'][0]['properties']['segments'][0]['distance']
        reel_mesafe = round(mesafe_metre / 1000, 2)
        
        return lineer_mesafe, reel_mesafe
    except openrouteservice.exceptions.ApiError as e:
        st.warning(f"Rota bulunamadı (Satır {row.name}): {e}. Muhtemelen karayolu bağlantısı yok.")
        return lineer_mesafe, None # Lineer mesafeyi yine de döndür
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
    try:
        df_original = pd.read_excel(uploaded_file)
        # Gerekli sütunların varlığını kontrol et
        required_cols = ['VAKA Lat', 'VAKA Long', 'Bayi Enlem', 'Bayi Boylam']
        if not all(col in df_original.columns for col in required_cols):
            st.error(f"Yüklenen dosyada gerekli sütunlar eksik. Lütfen şu sütunların olduğundan emin olun: {', '.join(required_cols)}")
            st.stop()

    except Exception as e:
        st.error(f"Excel dosyası okunurken bir hata oluştu: {e}")
        st.stop()

    st.subheader("Yüklenen Veri Önizlemesi")
    st.dataframe(df_original.head())

    if st.button("✅ Analizi Başlat", type="primary", use_container_width=True):
        total_rows = len(df_original)
        df_result = df_original.copy()
        
        # Yeni sütunları başlangıçta boş olarak ekle
        df_result['Bulunan İl'] = ""
        df_result['Bulunan İlçe'] = ""
        df_result['Lineer Mesafe (km)'] = pd.NA
        df_result['Reel Yol Mesafesi (km)'] = pd.NA

        progress_bar = st.progress(0, text="Başlatılıyor...")
        
        for index, row in df_result.iterrows():
            progress_text = f"Satır {index + 1}/{total_rows} işleniyor... (Vaka: {row.get('Case Number', index)})"
            progress_percent = (index + 1) / total_rows
            progress_bar.progress(progress_percent, text=progress_text)
            
            # İl/İlçe bul
            il, ilce = get_city_district(geolocator, row['VAKA Lat'], row['VAKA Long'])
            df_result.at[index, 'Bulunan İl'] = il
            df_result.at[index, 'Bulunan İlçe'] = ilce
            
            # Mesafeleri hesapla
            lineer_mesafe, reel_mesafe = hesapla_mesafeler(row)
            df_result.at[index, 'Lineer Mesafe (km)'] = lineer_mesafe
            df_result.at[index, 'Reel Yol Mesafesi (km)'] = reel_mesafe

        progress_bar.empty()
        st.success("✅ Analiz tamamlandı!")

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
            use_container_width=True
        )
