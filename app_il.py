import streamlit as st
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import openrouteservice
import re
from io import BytesIO
from time import sleep
import uuid
import base64 # Resim için eklendi

def add_bg_from_local(image_file):
    """
    Lokal bir dosyadan Base64 formatında arka plan resmi ekler ve okunabilirlik için stil ayarları yapar.
    """
    with open(image_file, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    st.markdown(
    f"""
    <style>
    .stApp {{
        background-image: linear-gradient(rgba(0, 0, 0, 0.6), rgba(0, 0, 0, 0.6)), url(data:image/{"jpg"};base64,{encoded_string});
        background-size: cover;
        background-repeat: no-repeat;
        background-attachment: fixed;
        background-position: center;
    }}
    /* Sidebar'ı daha okunaklı yapmak için yarı şeffaf arka plan */
    [data-testid="stSidebar"] > div:first-child {{
        background-color: rgba(38, 39, 48, 0.8);
    }}
    /* Tabloların ve diğer ana elementlerin okunabilirliğini artırmak */
    .stDataFrame, .stTextInput, .stButton, .stFileUploader {{
        background-color: rgba(255, 255, 255, 0.05);
    }}
    </style>
    """,
    unsafe_allow_html=True
    )

# --- Sayfa Yapılandırması ve Başlık ---
st.set_page_config(page_title="Gelişmiş Mesafe ve Lokasyon Analiz Aracı", layout="wide")

# Arka plan resmini ve stilleri uygula
add_bg_from_local('arkaplan.jpg')

st.title("🗺️ Gelişmiş Mesafe ve Lokasyon Analiz Aracı")
st.info(
    "Bu uygulama, yüklediğiniz Excel dosyasındaki 'VAKA' ve 'Bayi' koordinatları arasında kuş uçuşu ve karayolu mesafesini hesaplar. "
    "Ayrıca VAKA koordinatlarına göre İl/İlçe tespiti yapar."
)

# --- API Anahtarı Yönetimi ---
st.sidebar.header("Ayarlar")
try:
    # Streamlit secrets'tan anahtar okuma (deploy edildiğinde kullanılır)
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
    # Nominatim'in user_agent'ının her oturumda benzersiz olması iyi bir pratiktir.
    geolocator = Nominatim(user_agent=f"streamlit_geolocator_app_{st.session_state.session_id}")
    ors_client = openrouteservice.Client(key=key)
    return geolocator, ors_client

# Her kullanıcı oturumu için benzersiz bir ID oluştur (user_agent için)
if 'session_id' not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

geolocator, ors_client = get_clients(api_key)


def temizle_lokasyon_adi(text):
    """İl veya ilçe adlarındaki istenmeyen ifadeleri temizler ve formatlar."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    text = re.sub(r'\bmerkez\b|\bbelediyesi\b|\bbelediye\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\(.*\)', '', text) # Parantez içlerini temizle
    text = re.sub(r'\s{2,}', ' ', text).strip() # Çift boşlukları teke indir
    return text.title()


# Adres ayrıştırma için Türkiye'nin 81 ilini içeren set.
TURKISH_CITIES = {
    'Adana', 'Adıyaman', 'Afyonkarahisar', 'Ağrı', 'Amasya', 'Ankara', 'Antalya', 'Artvin', 'Aydın',
    'Balıkesir', 'Bilecik', 'Bingöl', 'Bitlis', 'Bolu', 'Burdur', 'Bursa', 'Çanakkale', 'Çankırı',
    'Çorum', 'Denizli', 'Diyarbakır', 'Edirne', 'Elazığ', 'Erzincan', 'Erzurum', 'Eskişehir',
    'Gaziantep', 'Giresun', 'Gümüşhane', 'Hakkari', 'Hatay', 'Isparta', 'Mersin', 'İstanbul',
    'İzmir', 'Kars', 'Kastamonu', 'Kayseri', 'Kırklareli', 'Kırşehir', 'Kocaeli', 'Konya',
    'Kütahya', 'Malatya', 'Manisa', 'Kahramanmaraş', 'Mardin', 'Muğla', 'Muş', 'Nevşehir', 'Niğde',
    'Ordu', 'Rize', 'Sakarya', 'Samsun', 'Siirt', 'Sinop', 'Sivas', 'Tekirdağ', 'Tokat', 'Trabzon',
    'Tunceli', 'Şanlıurfa', 'Uşak', 'Van', 'Yozgat', 'Zonguldak', 'Aksaray', 'Bayburt', 'Karaman',
    'Kırıkkale', 'Batman', 'Şırnak', 'Bartın', 'Ardahan', 'Iğdır', 'Yalova', 'Karabük', 'Kilis',
    'Osmaniye', 'Düzce'
}

@st.cache_data
def get_city_district(_geolocator, lat, lon, retries=3):
    """
    Verilen koordinatlar için il ve ilçe bilgisini alır. (En Gelişmiş Versiyon)
    - Standart anahtarları arar.
    - Bulamazsa, 'display_name' alanını ayrıştırır.
    - Kısmi sonuçları ve coğrafi bölgeleri yedek olarak kullanır.
    """
    for attempt in range(retries):
        try:
            sleep(1.1) # API limitlerine uymak için bekle
            location = _geolocator.reverse((lat, lon), language='tr', timeout=20)
            
            if not location or not location.raw:
                continue

            address = location.raw.get('address', {})
            city, district = None, None

            # 1. Standart metodlarla il ve ilçe ara
            raw_city = address.get('province') or address.get('state')
            raw_district = (address.get('town') or address.get('county') or 
                            address.get('municipality') or address.get('city_district') or 
                            address.get('district') or address.get('suburb') or address.get('village'))
            
            city = temizle_lokasyon_adi(raw_city)
            district = temizle_lokasyon_adi(raw_district)

            # 2. Yedek Plan: 'display_name'i ayrıştır
            if not city or not district:
                display_name = location.raw.get('display_name', '')
                parts = [temizle_lokasyon_adi(p) for p in display_name.split(',')]
                
                found_city_from_parts = None
                for part in parts:
                    if part in TURKISH_CITIES:
                        found_city_from_parts = part
                        break
                
                if not city and found_city_from_parts:
                    city = found_city_from_parts

                if not district and city and city in parts:
                    city_index = parts.index(city)
                    if city_index > 0:
                        potential_district = parts[city_index - 1]
                        if potential_district != city and "Bölgesi" not in potential_district:
                            district = potential_district
            
            # 3. Sonuçları Değerlendir ve Döndür (En Esnek Adım)
            if city and district:
                return (city, "Merkez") if city == district else (city, district)
            
            if district:
                region = temizle_lokasyon_adi(address.get('region'))
                final_city = city or region or "İl Bilinmiyor"
                return final_city, district
            
            if city:
                return city, "İlçe Bilinmiyor"

        except (GeocoderTimedOut, GeocoderServiceError) as e:
            st.warning(f"Adres bulma hatası (Lat: {lat}, Lon: {lon}) - Deneme {attempt + 1}/{retries}. Hata: {e}")
            sleep(2) # Hata sonrası daha uzun bekle
        except Exception as e:
            st.error(f"Beklenmedik bir adres bulma hatası oluştu (Lat: {lat}, Lon: {lon}): {e}")
            return "Hata", "Hata"
            
    st.error(f"Adres bulunamadı (Lat: {lat}, Lon: {lon}) - Tüm denemeler başarısız.")
    return "Bulunamadı", "Bulunamadı"


def hesapla_mesafeler(row):
    """Tek bir satır için lineer ve reel yol mesafesini hesaplar."""
    try:
        vaka_koord = (row['VAKA Lat'], row['VAKA Long'])
        bayi_koord = (row['Bayi Enlem'], row['Bayi Boylam'])

        if not all(isinstance(c, (int, float)) for c in vaka_koord + bayi_koord):
            return pd.NA, pd.NA
            
        lineer_mesafe = round(geodesic(vaka_koord, bayi_koord).km, 2)
        
        start_coords = (row['VAKA Long'], row['VAKA Lat'])
        end_coords = (row['Bayi Boylam'], row['Bayi Enlem'])
        
        response = ors_client.directions(
            coordinates=[start_coords, end_coords],
            profile='driving-car',
            format='geojson',
            preference='fastest', # 'recommended' olarak da denenebilir
            radiuses=[1000, 1000]
        )
        mesafe_metre = response['features'][0]['properties']['segments'][0]['distance']
        reel_mesafe = round(mesafe_metre / 1000, 2)
        
        return lineer_mesafe, reel_mesafe
    except openrouteservice.exceptions.ApiError:
        try:
            lineer_mesafe = round(geodesic((row['VAKA Lat'], row['VAKA Long']), (row['Bayi Enlem'], row['Bayi Boylam'])).km, 2)
            st.warning(f"Rota bulunamadı (Satır {row.name}). Karayolu bağlantısı olmayabilir.")
            return lineer_mesafe, pd.NA
        except:
             return pd.NA, pd.NA
    except Exception as e:
        st.warning(f"Mesafe hesaplama hatası (Satır {row.name}): {e}")
        return pd.NA, pd.NA

# --- Dosya Yükleme ve Ana İşlem ---
uploaded_file = st.file_uploader(
    "İşlem Yapılacak Excel Dosyasını Yükleyin",
    type=["xlsx"],
    help="Dosyanızda 'VAKA Lat', 'VAKA Long', 'Bayi Enlem', 'Bayi Boylam' sütunları bulunmalıdır."
)

if uploaded_file is not None:
    try:
        df_original = pd.read_excel(uploaded_file, dtype={'Case Number': str})
        required_cols = ['VAKA Lat', 'VAKA Long', 'Bayi Enlem', 'Bayi Boylam']
        if not all(col in df_original.columns for col in required_cols):
            st.error(f"Yüklenen dosyada gerekli sütunlar eksik: {', '.join(required_cols)}")
            st.stop()
    except Exception as e:
        st.error(f"Excel dosyası okunurken bir hata oluştu: {e}")
        st.stop()

    st.subheader("Yüklenen Veri Önizlemesi")
    st.dataframe(df_original.head())

    if st.button("✅ Analizi Başlat", type="primary", use_container_width=True):
        total_rows = len(df_original)
        df_result = df_original.copy()
        
        df_result['Bulunan İl'] = ""
        df_result['Bulunan İlçe'] = ""
        df_result['Lineer Mesafe (km)'] = pd.NA
        df_result['Reel Yol Mesafesi (km)'] = pd.NA

        progress_bar = st.progress(0, text="Başlatılıyor...")
        
        for index, row in df_result.iterrows():
            case_number = row.get('Case Number', index)
            progress_text = f"Satır {index + 1}/{total_rows} işleniyor... (Vaka: {case_number})"
            progress_percent = (index + 1) / total_rows
            progress_bar.progress(progress_percent, text=progress_text)
            
            il, ilce = get_city_district(geolocator, row['VAKA Lat'], row['VAKA Long'])
            df_result.at[index, 'Bulunan İl'] = il
            df_result.at[index, 'Bulunan İlçe'] = ilce
            
            lineer_mesafe, reel_mesafe = hesapla_mesafeler(row)
            df_result.at[index, 'Lineer Mesafe (km)'] = lineer_mesafe
            df_result.at[index, 'Reel Yol Mesafesi (km)'] = reel_mesafe

        progress_bar.empty()
        st.success("✅ Analiz tamamlandı!")

        st.subheader("Sonuçlar")
        st.dataframe(df_result)

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

