# --- Değişiklik Özeti ---
# 1) Endpoint düzeltildi: sellers -> suppliers
#    Eski: https://api.trendyol.com/integration/product/sellers/{seller_id}/products
#    Yeni: https://api.trendyol.com/integration/product/suppliers/{seller_id}/products
# 2) Hata ayıklama zenginleştirildi: HTTP status ve response text/log gösterimi
# 3) Ek header: Accept: application/json
# 4) Secrets anahtarları kontrolü ve örnek st.secrets fy.
# 5) Bağlantı testi butonu ve log penceresi eklendi

import streamlit as st
import pandas as pd
import requests
import base64
import io
import time
from typing import Tuple, Optional, Dict, Any

st.set_page_config(layout="wide", page_title="Trendyol Ürün Raporlama – Bağlantı Testli")
st.title("Trendyol Ürün Listesi Karşılaştırma Aracı – Bağlantı Testli Sürüm")

# ==========================
# Yardımcılar
# ==========================

def _example_secrets_block() -> str:
    return (
        """
[general]\n
# Örnek: .streamlit/secrets.toml içine\n
SELLER_ID = "760933"\n
API_KEY = "KNYVmzAznZolA8sFNmuE"\n
API_SECRET = "yiXYD2aaCHZbY0ECXhOM"\n
        """.strip()
    )


def _get_secrets() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    missing = []
    try:
        seller_id = str(st.secrets["SELLER_ID"]) if "SELLER_ID" in st.secrets else None
        if not seller_id:
            missing.append("SELLER_ID")
        api_key = st.secrets.get("API_KEY")
        if not api_key:
            missing.append("API_KEY")
        api_secret = st.secrets.get("API_SECRET")
        if not api_secret:
            missing.append("API_SECRET")
        if missing:
            st.error("Eksik secret: " + ", ".join(missing))
            with st.expander("Örnek secrets.toml içeriği"):
                st.code(_example_secrets_block(), language="toml")
        return seller_id, api_key, api_secret
    except Exception as e:
        st.error(f"Secrets okunamadı: {e}")
        with st.expander("Örnek secrets.toml içeriği"):
            st.code(_example_secrets_block(), language="toml")
        return None, None, None


def _auth_headers(api_key: str, api_secret: str) -> Dict[str, str]:
    credentials = f"{api_key}:{api_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Trendyol bazı entegrasyonlarda User-Agent'ı kontrol eder; boş bırakmayın
        "User-Agent": "TY-Integration/1.0 (+Streamlit)"
    }


@st.cache_data(show_spinner=False, ttl=10 * 60)
def fetch_products_from_trendyol(seller_id: str, headers: Dict[str, str], page_size: int = 200, log=False) -> Tuple[pd.DataFrame, Optional[str]]:
    all_products = []
    page = 0
    max_retries = 5

    # DÜZELTME: sellers -> suppliers
    base_url = f"https://api.trendyol.com/integration/product/suppliers/{seller_id}/products"

    while True:
        url = f"{base_url}?page={page}&size={page_size}"
        last_text = None
        last_status = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                last_status = resp.status_code
                last_text = resp.text[:2000]
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code >= 400:
                    # Hata detayını döndür
                    return pd.DataFrame(), f"HTTP {resp.status_code}: {last_text}"
                data = resp.json()
                products_on_page = data.get("products", [])
                all_products.extend(products_on_page)
                break
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return pd.DataFrame(), f"İstek hatası: {e}"
            except Exception as e:
                return pd.DataFrame(), f"JSON/İşleme hatası: {e}. Son cevap: {last_text}"

        if len(all_products) == 0 and page == 0 and log:
            st.warning(f"İlk sayfada kayıt gelmedi. Son durum: HTTP {last_status}\n\n{last_text}")

        if len(products_on_page) < page_size:
            break
        page += 1

    df = pd.DataFrame(all_products)
    return df, None


def normalize_products_df(df: pd.DataFrame) -> pd.DataFrame:
    desired_cols = [
        "productContentId","barcode","title","brand","stockCode","listPrice","salePrice","currencyType","vatRate","stockUnitType","quantity","approved","lastUpdateDate",
    ]
    out = df.copy()
    for c in desired_cols:
        if c not in out.columns:
            out[c] = None
    if "lastUpdateDate" in out.columns and pd.api.types.is_object_dtype(out["lastUpdateDate"]):
        with pd.option_context('mode.chained_assignment', None):
            out["lastUpdateDate"] = pd.to_datetime(out["lastUpdateDate"], errors="coerce")
    return out[desired_cols]

# ==========================
# UI – Ayarlar
# ==========================
with st.sidebar:
    st.header("Bağlantı & Ayarlar")
    page_size = st.number_input("API sayfa boyutu (size)", 50, 500, 200, 50)
    debug = st.checkbox("Hata ayıklama çıktısını göster", value=True)
    st.caption("Not: Endpoint suppliers olarak ayarlandı.")

seller_id, api_key, api_secret = _get_secrets()

col1, col2 = st.columns(2)
with col1:
    if st.button("Bağlantı Testi (Ping)"):
        if not (seller_id and api_key and api_secret):
            st.stop()
        headers = _auth_headers(api_key, api_secret)
        with st.spinner("Test ediliyor..."):
            df, err = fetch_products_from_trendyol(seller_id, headers, 1, log=True)
            if err:
                st.error(err)
            else:
                st.success(f"Bağlantı OK. İlk sayfadan {len(df)} kayıt döndü.")

with col2:
    if st.button("Güncel Ürünleri Çek"):
        if not (seller_id and api_key and api_secret):
            st.stop()
        headers = _auth_headers(api_key, api_secret)
        with st.spinner("Ürün listesi çekiliyor..."):
            df_today, err = fetch_products_from_trendyol(seller_id, headers, page_size, log=debug)
            if err:
                st.error(err)
            elif df_today.empty:
                st.warning("API'den ürün verisi çekilemedi veya boş döndü.")
            else:
                st.success(f"Toplam {len(df_today)} ürün alındı.")
                st.dataframe(normalize_products_df(df_today).head(50))

st.divider()
st.subheader("Sık karşılaşılan nedenler")
st.markdown(
    """
- **Yanlış endpoint**: `sellers` yerine `suppliers` kullanılmalı. Bu yama bunu düzeltiyor.
- **Eksik secrets**: `SELLER_ID`, `API_KEY`, `API_SECRET` adlarıyla eklenmeli. (Büyük/küçük harf duyarlı)
- **Yetki/mağaza boş**: Yeni entegrasyonlarda ürünleriniz yoksa boş döner; test için `size=1` ile ping atın ve HTTP gövdesini kontrol edin.
- **Geçersiz kimlik**: API Key/Secret yanlış ise 401/403 döner. Hata panelinde status + body görünür.
    """
)
