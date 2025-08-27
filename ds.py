import streamlit as st
import pandas as pd
import requests
import base64
import os
import io

# --- Streamlit Arayüzü ve Ayarları ---
st.set_page_config(layout="wide", page_title="Trendyol Ürün Raporlama")
st.title("Trendyol Ürün Listesi Karşılaştırma Aracı")

# --- API Bilgilerini Güvenli Olarak Oku ---
try:
    SELLER_ID = st.secrets["SELLER_ID"]
    API_KEY = st.secrets["API_KEY"]
    API_SECRET = st.secrets["API_SECRET"]
except KeyError as e:
    st.error(f"'{e.args[0]}' adlı gizli bilgi (Secret) bulunamadı. Lütfen Streamlit Cloud 'Secrets' bölümünü kontrol edin.")
    st.stop()

# --- Trendyol API için kimlik bilgileri hazırlanıyor ---
credentials = f"{API_KEY}:{API_SECRET}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()
HEADERS = {
    "Authorization": f"Basic {encoded_credentials}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

# --- Trendyol API Fonksiyonları ---
def get_products_from_trendyol():
    """Tüm ürünleri Trendyol API'sinden çeker ve DataFrame olarak döndürür."""
    all_products = []
    page = 0
    size = 50
    while True:
        url = f"https://api.trendyol.com/sapigw/suppliers/{SELLER_ID}/products?page={page}&size={size}"
        try:
            response = requests.get(url, headers=HEADERS)
            response.raise_for_status()
            data = response.json()
            
            products_on_page = data.get("products", [])
            all_products.extend(products_on_page)
            
            # Sayfa sonuna gelindiyse döngüyü kır
            if len(products_on_page) < size:
                break
            page += 1
        except requests.exceptions.RequestException as e:
            return None, f"API'den ürünler çekilirken hata oluştu: {e}"
        except Exception as e:
            return None, f"Veri işleme hatası: {e}"
            
    df = pd.DataFrame(all_products)
    return df, None

# --- Karşılaştırma Fonksiyonu ---
def compare_product_dfs(df1, df2):
    """İki DataFrame'i karşılaştırır ve eksik ürünleri bulur."""
    merged_df = pd.merge(df1, df2, on='productContentId', how='left', suffixes=('_prev', '_current'), indicator=True)
    missing_products = merged_df[merged_df['_merge'] == 'left_only']
    return missing_products.drop(columns=['_merge'])

# --- Ana Uygulama Akışı ---

# Durum yönetimi için oturum durumu kullan
if 'df_today' not in st.session_state:
    st.session_state.df_today = None

st.subheader("1. Adım: Güncel Ürün Listenizi Çekin")
if st.button("Güncel Ürün Listesini Trendyol'dan Çek"):
    with st.spinner("Ürün listesi çekiliyor..."):
        df_today, error = get_products_from_trendyol()
        if error:
            st.error(error)
        elif df_today.empty:
            st.warning("API'den ürün verisi çekilemedi. Lütfen API bağlantınızı kontrol edin.")
            st.session_state.df_today = None
        else:
            st.session_state.df_today = df_today
            st.success("Güncel ürün listesi başarıyla çekildi.")
            st.dataframe(df_today.head())
            st.info(f"{len(df_today)} adet ürün çekildi.")

# Çekme işlemi tamamlandıysa 2. adımı göster
if st.session_state.df_today is not None:
    st.divider()
    st.subheader("2. Adım: Dünkü Excel Dosyasını Yükleyin")
    uploaded_file = st.file_uploader("Dünkü ürün listesini içeren Excel dosyasını seçin", type=['xlsx', 'xls'])

    if uploaded_file is not None:
        try:
            # Yüklenen dosyayı DataFrame'e oku
            df_yesterday = pd.read_excel(uploaded_file)
            
            # Karşılaştırma yap
            with st.spinner("Dosyalar karşılaştırılıyor..."):
                missing_products_df = compare_product_dfs(df_yesterday, st.session_state.df_today)
                
            st.divider()
            st.subheader("Karşılaştırma Raporu")
            
            if not missing_products_df.empty:
                st.error(f"Eksik **{len(missing_products_df)}** ürün bulundu!")
                st.dataframe(missing_products_df[['productContentId', 'barcode_prev', 'title_prev', 'vatRate_prev']])
                st.download_button(
                    label="Eksik Ürünler Raporunu İndir",
                    data=missing_products_df.to_csv(index=False),
                    file_name="eksik_urunler.csv",
                    mime="text/csv"
                )
            else:
                st.success("Harika! Dünkü listeye göre eksik ürün bulunamadı.")
                
        except Exception as e:
            st.error(f"Dosya okunurken bir hata oluştu: {e}")
