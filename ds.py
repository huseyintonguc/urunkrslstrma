# Bu dosya iki parça içerir:
# 1) Minimal "Ping" testçisi: IP/kimlik/endpoint problemlerini ayrıştırır.
# 2) Mevcut uygulamaya entegre edilebilir sağlamlaştırılmış fetch fonksiyonu (556 ve WAF durumları için).
# Not: 556 çoğunlukla anlık bakım/WAF/edge hatası ya da kaynak IP kısıtından gelir. Aynı kimlik
#      bilgileri ile yerel makinenizden çalışıyorsa; ama Streamlit Cloud'da 556 alıyorsanız, büyük olasılıkla IP tarafıdır.

import streamlit as st
import requests, base64, time, pandas as pd
from typing import Dict, Tuple, Optional

st.set_page_config(layout="wide", page_title="Trendyol 556 Teşhis")
st.title("Trendyol API – 556 (Service Unavailable) Teşhis Konsolu")

# -----------------
# Yardımcılar
# -----------------

def _auth_headers(api_key: str, api_secret: str) -> Dict[str, str]:
    creds = f"{api_key}:{api_secret}"
    b64 = base64.b64encode(creds.encode()).decode()
    return {
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        # UA önemli: boş bırakmayın
        "User-Agent": "TY-Integration/1.0 (+Streamlit)"
    }

ENDPOINTS = [
    # Doğru olan
    "https://api.trendyol.com/integration/product/suppliers/{supplier_id}/products",
    # Eski/düzensiz kullananlar için (beklenen: 404/401/403 döner)
    "https://api.trendyol.com/integration/product/sellers/{supplier_id}/products",
]


def ping_once(url: str, headers: Dict[str, str], timeout=25) -> Tuple[int, str, Dict[str, str]]:
    r = requests.get(url, headers=headers, timeout=timeout)
    return r.status_code, r.text[:3000], dict(r.headers)

# -----------------
# UI
# -----------------
with st.sidebar:
    st.header("Kimlik Bilgileri")
    supplier_id = st.text_input("Supplier (Satıcı) ID", value="760933")
    api_key = st.text_input("API Key", value="", type="password")
    api_secret = st.text_input("API Secret", value="", type="password")
    page_size = st.number_input("size", 1, 200, 1)
    want_retry = st.checkbox("556/5xx için kısa süreli tekrar dene", value=True)

col1, col2 = st.columns(2)

with col1:
    if st.button("Tüm endpointleri PING et"):
        if not (supplier_id and api_key and api_secret):
            st.error("Kimlik bilgilerini doldurun.")
        else:
            headers = _auth_headers(api_key, api_secret)
            for ep in ENDPOINTS:
                url = ep.format(supplier_id=supplier_id) + f"?page=0&size={page_size}"
                with st.spinner(f"{url} çağrılıyor..."):
                    try:
                        code, body, resp_headers = ping_once(url, headers)
                        st.write(f"**{ep}** → Status: {code}")
                        st.code(body)
                        if code == 556:
                            st.warning("556: Service Unavailable. Bu genelde edge/WAF ya da anlık bakım kaynaklıdır. Aynı istekleri yerelde deneyin.")
                        if code in (401, 403):
                            st.info("401/403: Key/Secret yanlış ya da yetki yok.")
                        if code == 404:
                            st.info("404: Genellikle yanlış endpoint (sellers) ya da yol.")
                        # Bazı durumlarda Akamai/AWAF başlıkları ipucu verir
                        if any(k.lower().startswith("x-akamai") for k in resp_headers.keys()):
                            st.caption("Akamai başlıkları tespit edildi – WAF/edge cevabı olabilir.")
                    except requests.RequestException as e:
                        st.error(f"İstek hatası: {e}")

with col2:
    st.markdown("### Uygulama için sağlamlaştırılmış fetch fonksiyonu")
    st.code(
        '''
@st.cache_data(show_spinner=False, ttl=10*60)
def fetch_products_from_trendyol(supplier_id: str, headers: Dict[str, str], page_size: int = 200) -> Tuple[pd.DataFrame, Optional[str]]:
    import requests, time, pandas as pd
    base_url = f"https://api.trendyol.com/integration/product/suppliers/{supplier_id}/products"
    all_products = []
    page = 0

    while True:
        url = f"{base_url}?page={page}&size={page_size}"
        for attempt in range(5):
            try:
                r = requests.get(url, headers=headers, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code == 556:
                    # Çok kısa bekleyip birkaç kez dene (edge sorunlarında işe yarayabilir)
                    time.sleep(1.0 + attempt)
                    continue if attempt < 4 else None
                if r.status_code >= 400:
                    return pd.DataFrame(), f"HTTP {r.status_code}: {r.text[:1500]}"
                data = r.json()
                page_items = data.get("products", [])
                all_products.extend(page_items)
                break
            except requests.RequestException as e:
                if attempt == 4:
                    return pd.DataFrame(), f"Bağlantı hatası: {e}"
                time.sleep(1.5*(attempt+1))
        if len(page_items) < page_size:
            break
        page += 1
    return pd.DataFrame(all_products), None
        ''', language="python"
    )

st.divider()
st.subheader("Ne kontrol etmeliyim?")
st.markdown(
    """
1. **Aynı kimliklerle yerel bilgisayardan deneyin.** Yerelde 200 dönerken bulutta 556 alıyorsanız, problem %90 **kaynak IP** tarafıdır (WAF/edge). Çözüm: Yerelde çalıştırmak ya da sabit/izinli bir IP üzerinden istek atmak.
2. **Doğru endpoint:** `suppliers/{supplier_id}/products`. `sellers` 404/556 verebilir.
3. **Header'lar:** `Authorization: Basic base64(key:secret)`, `Accept: application/json`, `Content-Type: application/json`, dolu bir `User-Agent`.
4. **Geçici durumlar:** 556 kısa süreli olabilir; fonksiyona hafif retry ekledik. Sorun kalıcıysa IP/erişim kısıtıdır.
    """
)
