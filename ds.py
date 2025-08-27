# Trendyol 401 (TrendyolAuthorizationException) – Hızlı Teşhis & Düzeltme
# Nedenleri (çoğunlukla):
# 1) API KEY veya SECRET hatalı / eksik kopyalandı (ön/arka boşluk, satır sonu, görünmez karakter).
# 2) SELLER_ID farklı mağazaya ait; KEY/SECRET bu mağazaya bağlı değil.
# 3) Secrets dosyasında tırnak/format hatası veya ortam yeniden başlatılmadı.
# 4) Authorization Basic header yanlış oluşturuluyor.
# 5) Yanlış endpoint (sende sellers doğruydu; suppliers kullanma).
# Aşağıdaki yardımcılar 401'i hızlıca teşhis etmeye ve düzeltmeye yarar.

import streamlit as st
import base64
import requests

st.set_page_config(layout="wide", page_title="Trendyol 401 Teşhis")
st.title("Trendyol 401 – Kimlik Doğrulama Teşhis Konsolu")

# --- 1) Secrets'i güvenli şekilde oku ve temizle

def _clean(s: str) -> str:
    # Baştaki/sondaki boşluk, \n, \t ve görünmeyen karakterleri at
    return (s or "").strip().replace("\r", "").replace("\n", "").replace("\t", "")

seller_id = _clean(st.text_input("SELLER_ID", value=st.secrets.get("SELLER_ID", "")))
api_key   = _clean(st.text_input("API_KEY", value=st.secrets.get("API_KEY", ""), type="password"))
api_secret= _clean(st.text_input("API_SECRET", value=st.secrets.get("API_SECRET", ""), type="password"))

colA, colB = st.columns(2)
with colA:
    st.markdown("### 1) Temel kontroller")
    st.write(f"SELLER_ID: `{seller_id}` (uzunluk={len(seller_id)})")
    st.write(f"API_KEY uzunluk: {len(api_key)}")
    st.write(f"API_SECRET uzunluk: {len(api_secret)}")
    if any(x == "" for x in [seller_id, api_key, api_secret]):
        st.error("Boş değer var. Tüm alanları doldurun.")

with colB:
    st.markdown("### 2) Authorization (Basic) önizleme")
    pair = f"{api_key}:{api_secret}"
    b64 = base64.b64encode(pair.encode()).decode()
    st.code(f"Authorization: Basic {b64[:6]}...{b64[-6:]}  (maskeli)")
    st.caption("Baş ve sondan 6 karakter gösterilir; ara kısım maskelidir. Bu satırda boşluk/\n olmamalı.")

st.divider()

# --- 3) Test isteği (sellers endpoint) – 401 mi, başka mı?

def _headers():
    return {
        "Authorization": f"Basic {base64.b64encode(f'{api_key}:{api_secret}'.encode()).decode()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "TY-Integration/1.0 (+Streamlit)",
    }

if st.button("Kimlik Doğrulama Testi (sellers)"):
    if not (seller_id and api_key and api_secret):
        st.stop()
    url = f"https://api.trendyol.com/integration/product/sellers/{seller_id}/products?page=0&size=1"
    with st.spinner("Çağrı yapılıyor..."):
        try:
            r = requests.get(url, headers=_headers(), timeout=25)
            st.write(f"Status: {r.status_code}")
            st.code(r.text[:2000])
            if r.status_code == 401:
                st.error("401 devam ediyorsa: (1) KEY/SECRET yanlış veya bu SELLER_ID'ye ait değil, (2) KEY/SECRET süresi dolmuş/rotasyon yapılmış olabilir.")
            elif r.status_code == 200:
                st.success("Kimlik doğrulama OK – ürün verisi döndü. Uygulamada da aynı header fonksiyonunu kullanın.")
        except requests.RequestException as e:
            st.error(f"İstek hatası: {e}")

st.divider()

st.markdown("""
### Düzeltme Adımları (Özet)
1. **Trendyol Panel > Entegrasyon** ekranından yeni bir **API Key/Secret oluştur** (rotasyon). Oluşturduğun key/secret'i hiç boşluk eklemeden kopyala.
2. `.streamlit/secrets.toml` dosyanı bu formatla güncelle ve **yeniden başlat**:
```toml
SELLER_ID = "760933"
API_KEY   = "BURAYA_YENI_KEY"
API_SECRET= "BURAYA_YENI_SECRET"
```
3. SELLER_ID'nin **aynı mağazaya** ait olduğundan emin ol. Farklı mağaza hesabının key/secret'i bu ID ile çalışmaz.
4. Uygulamada Authorization header'ını **Basic base64(key:secret)** olarak oluşturduğumuz yukarıdaki `_headers()` ile aynı tut.
5. Endpoint olarak mağazan için **sellers** kullan. (suppliers sende 556 veriyordu.)
""")
