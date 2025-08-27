import streamlit as st
import pandas as pd
import requests
import base64
import io
import time
from typing import Tuple, Optional, Dict, Any

# ==========================
# Uygulama Ayarları
# ==========================
st.set_page_config(layout="wide", page_title="Trendyol Ürün Raporlama – Geliştirilmiş")
st.title("Trendyol Ürün Listesi Karşılaştırma Aracı – Geliştirilmiş Sürüm")
st.caption("v2 • Hata toleransı yüksek, şablon indirme, kolon eşleştirme ve ek raporlarla zenginleştirildi")

# ==========================
# Yardımcı Fonksiyonlar
# ==========================

def _get_secrets() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Secrets güvenli okuma"""
    try:
        seller_id = st.secrets["SELLER_ID"]
        api_key = st.secrets["API_KEY"]
        api_secret = st.secrets["API_SECRET"]
        return seller_id, api_key, api_secret
    except KeyError as e:
        st.error(f"'{e.args[0]}' adlı gizli bilgi (Secret) bulunamadı. Lütfen Streamlit Cloud 'Secrets' bölümünü kontrol edin.")
        return None, None, None


def _auth_headers(api_key: str, api_secret: str) -> Dict[str, str]:
    credentials = f"{api_key}:{api_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }


@st.cache_data(show_spinner=False, ttl=10 * 60)
def fetch_products_from_trendyol(seller_id: str, headers: Dict[str, str], page_size: int = 200) -> Tuple[pd.DataFrame, Optional[str]]:
    """Trendyol'dan tüm ürünleri çeker. Rate limit için basit backoff uygular."""
    all_products = []
    page = 0
    max_retries = 5

    while True:
        url = f"https://api.trendyol.com/integration/product/sellers/{seller_id}/products?page={page}&size={page_size}"
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 429:
                    # rate limit: exponential backoff
                    sleep_s = 2 ** attempt
                    time.sleep(sleep_s)
                    continue
                resp.raise_for_status()
                data = resp.json()
                products_on_page = data.get("products", [])
                all_products.extend(products_on_page)
                break  # başarılı deneme
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return pd.DataFrame(), f"API'den ürünler çekilirken hata oluştu: {e}"
            except Exception as e:  # JSON parse vs.
                return pd.DataFrame(), f"Veri işleme hatası: {e}"

        # Sayfa bitti mi?
        if len(products_on_page) < page_size:
            break
        page += 1

    df = pd.DataFrame(all_products)
    return df, None


def normalize_products_df(df: pd.DataFrame) -> pd.DataFrame:
    """Kullanışlı kolonları öne çıkaran, eksik kolonları güvenle ekleyen normalize fonksiyonu."""
    desired_cols = [
        "productContentId",  # ana anahtar
        "barcode",
        "title",
        "brand",
        "stockCode",
        "listPrice",
        "salePrice",
        "currencyType",
        "vatRate",
        "stockUnitType",
        "quantity",
        "approved",
        "lastUpdateDate",
    ]
    out = df.copy()
    for c in desired_cols:
        if c not in out.columns:
            out[c] = None

    # Tarihi düzgün sırala/formatla (string ise)
    if pd.api.types.is_object_dtype(out["lastUpdateDate"]):
        with pd.option_context('mode.chained_assignment', None):
            out["lastUpdateDate"] = pd.to_datetime(out["lastUpdateDate"], errors="coerce")

    return out[desired_cols]


def coerce_product_id(df: pd.DataFrame) -> pd.DataFrame:
    """productContentId metin de olabilir; sağlamlaştır."""
    if "productContentId" in df.columns:
        with pd.option_context('mode.chained_assignment', None):
            df["productContentId"] = df["productContentId"].astype(str)
    return df


def try_read_excel(uploaded_file) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        return pd.read_excel(uploaded_file), None
    except Exception as e:
        return pd.DataFrame(), f"Excel okunamadı: {e}"


def smart_merge_prev_current(df_prev: pd.DataFrame, df_current: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[str]]:
    """İki DataFrame'i productContentId üzerinden birleştirir. Yoksa kullanıcıya eşleştirme önerir."""
    if "productContentId" not in df_prev.columns:
        # Yaygın kolon isimlerini dene
        alt_keys = ["productContentId", "contentId", "productId", "id"]
        found = None
        for k in alt_keys:
            if k in df_prev.columns:
                found = k
                break
        if found is None:
            return pd.DataFrame(), "Yüklediğiniz Excel'de 'productContentId' ya da muadili bir anahtar sütun bulunamadı. Lütfen şablonu kullanın veya sütunu ekleyin."
        else:
            df_prev = df_prev.rename(columns={found: "productContentId"})

    df_prev = coerce_product_id(df_prev)
    df_current = coerce_product_id(df_current)

    merged = pd.merge(
        df_prev,
        df_current,
        on="productContentId",
        how="left",
        suffixes=("_prev", "_current"),
        indicator=True,
    )
    return merged, None


def build_diff_report(merged: pd.DataFrame, comparable_cols=None) -> pd.DataFrame:
    """Fiyat/KDV/görünür alanlarda farkları raporlar."""
    if comparable_cols is None:
        comparable_cols = [
            ("barcode",),
            ("title",),
            ("salePrice",),
            ("listPrice",),
            ("currencyType",),
            ("vatRate",),
            ("approved",),
            ("quantity",),
        ]

    diffs = []
    for _, row in merged.iterrows():
        row_diffs = {"productContentId": row.get("productContentId")}
        changed = False
        for col_tuple in comparable_cols:
            col = col_tuple[0]
            prev_v = row.get(f"{col}_prev")
            cur_v = row.get(f"{col}_current")
            if pd.isna(prev_v) and pd.isna(cur_v):
                continue
            if (pd.isna(prev_v) and not pd.isna(cur_v)) or (not pd.isna(prev_v) and pd.isna(cur_v)) or (prev_v != cur_v):
                row_diffs[f"{col}_prev"] = prev_v
                row_diffs[f"{col}_current"] = cur_v
                changed = True
        if changed:
            diffs.append(row_diffs)

    return pd.DataFrame(diffs)


# ==========================
# Oturum Durumu
# ==========================
if "df_today" not in st.session_state:
    st.session_state.df_today = None
if "df_today_norm" not in st.session_state:
    st.session_state.df_today_norm = None

# ==========================
# Sol Panel – Ayarlar
# ==========================
with st.sidebar:
    st.header("Ayarlar")
    st.write("Trendyol API istek boyutu ve rapor seçenekleri")
    page_size = st.number_input("API sayfa boyutu (size)", min_value=50, max_value=500, value=200, step=50)
    show_raw = st.checkbox("Ham API verisini de göster", value=False)
    st.divider()
    st.subheader("Şablon")
    st.write("Dünkü Excel için örnek kolon şablonunu indirebilirsiniz.")
    template_df = pd.DataFrame({
        "productContentId": ["111111", "222222"],
        "barcode": ["ABC-001", "ABC-002"],
        "title": ["Örnek Ürün 1", "Örnek Ürün 2"],
        "vatRate": [20, 20],
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        template_df.to_excel(writer, index=False, sheet_name="yesterday")
    st.download_button("Excel Şablonunu İndir", data=buf.getvalue(), file_name="ornek_sablon.xlsx")

# ==========================
# 1. Adım – Güncel Ürün Listesi
# ==========================
st.subheader("1. Adım: Güncel Ürün Listenizi Çekin")
seller_id, api_key, api_secret = _get_secrets()

btn_fetch = st.button("Trendyol'dan Güncel Ürünleri Çek")
if btn_fetch:
    if not (seller_id and api_key and api_secret):
        st.stop()
    with st.spinner("Ürün listesi çekiliyor..."):
        df_today, err = fetch_products_from_trendyol(seller_id, _auth_headers(api_key, api_secret), page_size)
        if err:
            st.error(err)
        elif df_today.empty:
            st.warning("API'den ürün verisi çekilemedi. Lütfen API bağlantınızı kontrol edin.")
        else:
            st.success(f"Güncel ürün listesi başarıyla çekildi. Toplam {len(df_today)} ürün")
            st.session_state.df_today = df_today
            st.session_state.df_today_norm = normalize_products_df(df_today)
            if show_raw:
                st.dataframe(df_today.head(50))
            st.dataframe(st.session_state.df_today_norm.head(50))

# ==========================
# 2. Adım – Dünkü Excel Yükleme ve Karşılaştırma
# ==========================
if st.session_state.df_today_norm is not None:
    st.divider()
    st.subheader("2. Adım: Dünkü Excel Dosyasını Yükleyin")
    uploaded = st.file_uploader("Dünkü ürün listesini içeren Excel dosyasını seçin", type=["xlsx", "xls"]) 

    if uploaded is not None:
        df_prev, err = try_read_excel(uploaded)
        if err:
            st.error(err)
        elif df_prev.empty:
            st.warning("Yüklenen Excel boş görünüyor.")
        else:
            st.info(f"Yüklenen satır sayısı: {len(df_prev)}")

            with st.spinner("Dosyalar karşılaştırılıyor..."):
                merged, merge_err = smart_merge_prev_current(df_prev, st.session_state.df_today_norm)
                if merge_err:
                    st.error(merge_err)
                else:
                    # Eksikler
                    missing_df = merged[merged["_merge"] == "left_only"].copy()
                    # Farklar
                    diff_df = build_diff_report(merged)

            st.divider()
            st.subheader("Karşılaştırma Raporu")
            c1, c2 = st.columns(2)

            with c1:
                st.markdown("### Eksik Ürünler (Dünde var, Bugün yok)")
                if missing_df.empty:
                    st.success("Harika! Dünkü listeye göre eksik ürün bulunamadı.")
                else:
                    st.error(f"Eksik **{len(missing_df)}** ürün bulundu!")
                    show_cols = [
                        "productContentId",
                        "barcode_prev",
                        "title_prev",
                        "vatRate_prev",
                    ]
                    existing = [c for c in show_cols if c in missing_df.columns]
                    st.dataframe(missing_df[existing].head(200))
                    st.download_button(
                        "Eksik Ürünler Raporunu İndir (CSV)",
                        data=missing_df.to_csv(index=False),
                        file_name="eksik_urunler.csv",
                        mime="text/csv",
                    )

            with c2:
                st.markdown("### Değişen Alanlar (Fiyat/KDV/Stok vb.)")
                if diff_df.empty:
                    st.info("Fark tespit edilmedi.")
                else:
                    st.warning(f"Değişen alanı olan {len(diff_df)} kayıt var.")
                    st.dataframe(diff_df.head(200))
                    st.download_button(
                        "Fark Raporunu İndir (CSV)",
                        data=diff_df.to_csv(index=False),
                        file_name="degisen_alanlar.csv",
                        mime="text/csv",
                    )

            # Tümleşik birleştirme çıktısı
            with st.expander("Detaylı Birleştirme Çıktısı (Tüm Kolonlar)"):
                st.dataframe(merged.head(300))
                st.download_button(
                    "Tüm Birleştirme Çıktısı (CSV)",
                    data=merged.to_csv(index=False),
                    file_name="birlesik_cikti.csv",
                    mime="text/csv",
                )

# ==========================
# İpuçları
# ==========================
with st.expander("İpuçları & SSS"):
    st.markdown(
        """
        - **productContentId** anahtar alanıdır. Excel'inizde bu sütun yoksa *contentId*, *productId* veya *id* gibi muadiller otomatik algılanmaya çalışılır.
        - **Rate limit (429)** durumunda otomatik geri çekilme (exponential backoff) uygulanır.
        - Soldaki panelden **Excel şablonu** indirip dünün verisini bu formatta kaydedebilirsiniz.
        - Raporlar CSV olarak indirilebilir; Excel'e doğrudan aktarılabilir.
        - "Ham API verisini göster" seçeneğiyle Trendyol'un döndürdüğü ham JSON kolonlarını da görebilirsiniz.
        """
    )
