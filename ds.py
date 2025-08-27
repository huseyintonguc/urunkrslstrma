import streamlit as st
import pandas as pd
import requests
import base64
import io
import time
from typing import Tuple, Optional, Dict

# ==========================
# Uygulama Başlığı
# ==========================
st.set_page_config(layout="wide", page_title="Trendyol Ürün Karşılaştırma – Aktif Ürünler")
st.title("Trendyol Ürün Listesi Karşılaştırma Aracı – Aktif Ürün Odaklı")
st.caption("Tek dosya • sellers endpoint • aktif ürün filtresi • şablon indirme (XLSX yoksa CSV) • hata ayıklama ve raporlar")

# ==========================
# Yardımcılar
# ==========================

def _get_secrets() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    missing = []
    try:
        seller_id = str(st.secrets.get("SELLER_ID", "")).strip()
        api_key = st.secrets.get("API_KEY")
        api_secret = st.secrets.get("API_SECRET")
        if not seller_id:
            missing.append("SELLER_ID")
        if not api_key:
            missing.append("API_KEY")
        if not api_secret:
            missing.append("API_SECRET")
        if missing:
            st.error("Eksik secret: " + ", ".join(missing))
        return seller_id or None, api_key, api_secret
    except Exception as e:
        st.error(f"Secrets okunamadı: {e}")
        return None, None, None


def _auth_headers(api_key: str, api_secret: str) -> Dict[str, str]:
    creds = f"{api_key}:{api_secret}"
    b64 = base64.b64encode(creds.encode()).decode()
    return {
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "TY-Integration/1.0 (+Streamlit)",
    }


@st.cache_data(show_spinner=False, ttl=10 * 60)
def fetch_products_from_trendyol(seller_id: str, headers: Dict[str, str], page_size: int = 200, only_active: bool = False, debug: bool = False) -> Tuple[pd.DataFrame, Optional[str]]:
    base_url = f"https://api.trendyol.com/integration/product/sellers/{seller_id}/products"
    all_products = []
    page = 0

    while True:
        url = f"{base_url}?page={page}&size={page_size}"
        page_items = []
        last_text = None
        last_status = None
        for attempt in range(5):
            try:
                r = requests.get(url, headers=headers, timeout=30)
                last_status = r.status_code
                last_text = r.text[:2000]
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code >= 400:
                    return pd.DataFrame(), f"HTTP {r.status_code}: {last_text}"
                data = r.json()
                page_items = data.get("products", [])
                if only_active:
                    page_items = [p for p in page_items if p.get("approved") and (p.get("quantity", 0) > 0)]
                all_products.extend(page_items)
                break
            except requests.RequestException as e:
                if attempt == 4:
                    return pd.DataFrame(), f"Bağlantı hatası: {e}"
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                return pd.DataFrame(), f"JSON/işleme hatası: {e}. Son yanıt: {last_text} (HTTP {last_status})"

        if page == 0 and debug and len(page_items) == 0:
            st.info(f"İlk sayfa boş döndü. HTTP {last_status}\n\n{last_text}")

        if len(page_items) < page_size:
            break
        page += 1

    return pd.DataFrame(all_products), None


def normalize_products_df(df: pd.DataFrame) -> pd.DataFrame:
    desired = [
        "productContentId", "barcode", "title", "brand", "stockCode",
        "listPrice", "salePrice", "currencyType", "vatRate", "stockUnitType",
        "quantity", "approved", "lastUpdateDate",
    ]
    out = df.copy()
    for c in desired:
        if c not in out.columns:
            out[c] = None
    if "lastUpdateDate" in out.columns and pd.api.types.is_object_dtype(out["lastUpdateDate"]):
        with pd.option_context('mode.chained_assignment', None):
            out["lastUpdateDate"] = pd.to_datetime(out["lastUpdateDate"], errors="coerce")
    if "productContentId" in out.columns:
        with pd.option_context('mode.chained_assignment', None):
            out["productContentId"] = out["productContentId"].astype(str)
    return out[desired]


def try_read_excel_or_csv(uploaded_file) -> Tuple[pd.DataFrame, Optional[str]]:
    name = (getattr(uploaded_file, "name", "") or "").lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(uploaded_file), None
        # Önce openpyxl'ı deneyelim; yoksa pandas motoru varsayılanı dener
        try:
            import openpyxl  # noqa
            return pd.read_excel(uploaded_file, engine="openpyxl"), None
        except Exception:
            return pd.read_excel(uploaded_file), None
    except Exception as e:
        return pd.DataFrame(), f"Dosya okunamadı: {e}"


def smart_merge_prev_current(df_prev: pd.DataFrame, df_current: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[str]]:
    if "productContentId" not in df_prev.columns:
        for k in ["contentId", "productId", "id"]:
            if k in df_prev.columns:
                df_prev = df_prev.rename(columns={k: "productContentId"})
                break
        if "productContentId" not in df_prev.columns:
            return pd.DataFrame(), "Excel/CSV'de 'productContentId' veya muadili bir sütun bulunamadı."
    with pd.option_context('mode.chained_assignment', None):
        df_prev["productContentId"] = df_prev["productContentId"].astype(str)
        df_current["productContentId"] = df_current["productContentId"].astype(str)

    merged = pd.merge(
        df_prev, df_current, on="productContentId", how="left", suffixes=("_prev", "_current"), indicator=True
    )
    return merged, None


def build_diff_report(merged: pd.DataFrame) -> pd.DataFrame:
    cols = ["barcode", "title", "salePrice", "listPrice", "currencyType", "vatRate", "approved", "quantity"]
    diffs = []
    for _, row in merged.iterrows():
        changed = False
        bucket = {"productContentId": row.get("productContentId")}
        for c in cols:
            pv, cv = row.get(f"{c}_prev"), row.get(f"{c}_current")
            if (pd.isna(pv) and pd.isna(cv)):
                continue
            if (pd.isna(pv) and not pd.isna(cv)) or (not pd.isna(pv) and pd.isna(cv)) or (pv != cv):
                bucket[f"{c}_prev"], bucket[f"{c}_current"] = pv, cv
                changed = True
        if changed:
            diffs.append(bucket)
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
    page_size = st.number_input("API sayfa boyutu (size)", min_value=50, max_value=500, value=200, step=50)
    only_active = st.checkbox("Sadece aktif ürünleri al (approved & quantity>0)", value=True)
    debug = st.checkbox("Hata ayıklama çıktısını göster", value=False)
    st.divider()
    st.subheader("Şablon İndir (XLSX yoksa CSV)")
    st.write("Dünkü veriyi bu şablonla kaydedebilirsiniz.")
    template_df = pd.DataFrame({
        "productContentId": ["111111", "222222"],
        "barcode": ["ABC-001", "ABC-002"],
        "title": ["Örnek Ürün 1", "Örnek Ürün 2"],
        "vatRate": [20, 20],
    })
    # XLSX yazıcı yoksa CSV'ye düş
    try:
        import openpyxl  # noqa
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            template_df.to_excel(writer, index=False, sheet_name="yesterday")
        st.download_button("Excel Şablonunu İndir (XLSX)", data=buf.getvalue(), file_name="ornek_sablon.xlsx")
    except Exception:
        st.download_button("Şablonu İndir (CSV)", data=template_df.to_csv(index=False).encode("utf-8"), file_name="ornek_sablon.csv")

# ==========================
# 1. Adım – Güncel Ürün Listesi
# ==========================
st.subheader("1. Adım: Güncel Ürün Listenizi Çekin")
seller_id, api_key, api_secret = _get_secrets()

if st.button("Trendyol'dan Güncel Ürünleri Çek"):
    if not (seller_id and api_key and api_secret):
        st.stop()
    with st.spinner("Ürün listesi çekiliyor..."):
        df_today, err = fetch_products_from_trendyol(
            seller_id, _auth_headers(api_key, api_secret), page_size, only_active=only_active, debug=debug
        )
        if err:
            st.error(err)
        elif df_today.empty:
            st.warning("API'den ürün verisi çekilemedi veya boş döndü.")
        else:
            st.success(f"Güncel ürün listesi alındı. (Toplam {len(df_today)} kayıt)")
            st.session_state.df_today = df_today
            st.session_state.df_today_norm = normalize_products_df(df_today)
            st.dataframe(st.session_state.df_today_norm.head(50))

# ==========================
# 2. Adım – Dünkü Excel/CSV Yükleme ve Karşılaştırma
# ==========================
if st.session_state.df_today_norm is not None:
    st.divider()
    st.subheader("2. Adım: Dünkü Excel/CSV Dosyasını Yükleyin")
    uploaded = st.file_uploader("Dünkü ürünü içeren Excel/CSV dosyasını seçin", type=["xlsx", "xls", "csv"]) 

    if uploaded is not None:
        df_prev, err = try_read_excel_or_csv(uploaded)
        if err:
            st.error(err)
        elif df_prev.empty:
            st.warning("Yüklenen dosya boş görünüyor.")
        else:
            st.info(f"Yüklenen satır sayısı: {len(df_prev)}")
            with st.spinner("Dosyalar karşılaştırılıyor..."):
                merged, merge_err = smart_merge_prev_current(df_prev, st.session_state.df_today_norm)
                if merge_err:
                    st.error(merge_err)
                else:
                    missing_df = merged[merged["_merge"] == "left_only"].copy()
                    diff_df = build_diff_report(merged)

            st.divider()
            st.subheader("Karşılaştırma Raporu")
            c1, c2 = st.columns(2)

            with c1:
                st.markdown("### Eksik Ürünler (Dünde var, Bugün yok)")
                if missing_df.empty:
                    st.success("Dünkü listeye göre eksik ürün bulunamadı.")
                else:
                    st.error(f"Eksik **{len(missing_df)}** ürün bulundu!")
                    show_cols = ["productContentId", "barcode_prev", "title_prev", "vatRate_prev"]
                    existing = [c for c in show_cols if c in missing_df.columns]
                    st.dataframe(missing_df[existing].head(200))
                    st.download_button(
                        "Eksik Ürünler Raporu (CSV)", data=missing_df.to_csv(index=False).encode("utf-8"), file_name="eksik_urunler.csv", mime="text/csv"
                    )

            with c2:
                st.markdown("### Değişen Alanlar (Fiyat/KDV/Stok vb.)")
                if diff_df.empty:
                    st.info("Fark tespit edilmedi.")
                else:
                    st.warning(f"Değişen alanı olan {len(diff_df)} kayıt var.")
                    st.dataframe(diff_df.head(200))
                    st.download_button(
                        "Fark Raporu (CSV)", data=diff_df.to_csv(index=False).encode("utf-8"), file_name="degisen_alanlar.csv", mime="text/csv"
                    )

            with st.expander("Detaylı Birleştirme Çıktısı (Tüm Kolonlar)"):
                st.dataframe(merged.head(300))
                st.download_button(
                    "Birleşik Çıktı (CSV)", data=merged.to_csv(index=False).encode("utf-8"), file_name="birlesik_cikti.csv", mime="text/csv"
                )

# ==========================
# İpuçları & SSS
# ==========================
with st.expander("İpuçları & SSS"):
    st.markdown(
        """
- **Endpoint:** `sellers/{SELLER_ID}/products` kullanılıyor (mağazan bu uçta aktif).
- **Aktif filtre:** Varsayılan **approved=True** ve **quantity>0**.
- **Şablon indirme:** Sunucuda `xlsxwriter` yoksa otomatik **CSV**'ye düşer; varsa **openpyxl** ile XLSX verir.
- **Rate limit (429):** Otomatik geri çekilme uygulanır.
        """
    )
