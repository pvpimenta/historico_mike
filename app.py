import streamlit as st
import plotly.express as px
import pandas as pd
from PIL import Image, ImageOps
from google import genai
from google.genai import types
from openai import OpenAI
import plotly.graph_objects as go
import time
import json
import datetime
import urllib.parse
import base64
from io import BytesIO
from supabase import create_client, Client
import re
import math
import copy
import hashlib
import unicodedata
from plotly.subplots import make_subplots

# Bibliotecas de extração local
import pdfplumber
import pytesseract

# Tenta importar fpdf2 para gerar PDF (se instalado)
try:
    from fpdf import FPDF
    FPDF_DISPONIVEL = True
except ImportError:
    FPDF_DISPONIVEL = False

st.set_page_config(page_title="Relatório do Pet", page_icon="🐕", layout="centered")

# ==========================================
# DATAS: PADRÃO BRASILEIRO (DD/MM/AAAA)
# ==========================================
# No banco as datas são guardadas SEMPRE em ISO (AAAA-MM-DD).
# Na tela, nos relatórios, tabelas e gráficos aparecem SEMPRE como DD/MM/AAAA.
FORMATO_DATA_BR = "%d/%m/%Y"
_FORMATOS_ENTRADA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y")

def parse_data(valor):
    """Converte ISO, DD/MM/AAAA, date, datetime ou Timestamp em datetime.date (ou None)."""
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, datetime.datetime):
        return valor.date()
    if isinstance(valor, datetime.date):
        return valor
    texto = re.split(r"[T\s]", str(valor).strip())[0]
    for fmt in _FORMATOS_ENTRADA:
        try:
            return datetime.datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None

def formatar_data_br(valor, vazio="—"):
    """Devolve a data como DD/MM/AAAA; se não der para ler, devolve o texto original."""
    d = parse_data(valor)
    if d:
        return d.strftime(FORMATO_DATA_BR)
    texto = "" if valor is None else str(valor).strip()
    return texto if texto and texto.lower() not in ("nan", "nat", "none") else vazio

def converter_coluna_data(serie):
    """Converte uma coluna com formatos misturados (ISO e DD/MM/AAAA) para datetime."""
    return pd.to_datetime(serie.map(parse_data), errors="coerce")

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
supabase_url = st.secrets["SUPABASE_URL"]
supabase_key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(supabase_url, supabase_key)

# ==========================================
# PROVEDORES DE IA E SISTEMA DE FALLBACK
# ==========================================

def _modelo_gemini():
    return st.secrets.get("GEMINI_MODEL", "gemini-3.8-flash")


def _chamar_gemini(prompt, json_mode=False):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Chave GEMINI_API_KEY não encontrada nos Secrets.")
    
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    
    response = client.models.generate_content(
        model=_modelo_gemini(),
        contents=prompt,
        config=config
    )
    return response.text


def _chamar_groq(prompt, json_mode=False):
    api_key = st.secrets.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Chave GROQ_API_KEY não encontrada nos Secrets.")

    groq_model = st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b")

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key
    )

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        if "json" not in prompt.lower():
            prompt += "\nResponda estritamente no formato JSON."

    response = client.chat.completions.create(
        model=groq_model,
        messages=[{"role": "user", "content": prompt}],
        timeout=30,
        **kwargs
    )

    return response.choices[0].message.content


@st.cache_resource
def _cache_ia():
    """Cache de respostas da IA (vale para todas as sessões enquanto o app estiver no ar)."""
    return {}

def _chave_cache(prompt, json_mode, extra=""):
    return hashlib.sha256(f"{json_mode}|{extra}|{prompt}".encode("utf-8")).hexdigest()

def _guardar_cache(chave, valor, limite=200):
    cache = _cache_ia()
    cache[chave] = copy.deepcopy(valor)
    while len(cache) > limite:
        cache.pop(next(iter(cache)))

def _carregar_json(texto):
    """Lê JSON mesmo que a IA devolva cercas ```json ou texto extra ao redor."""
    if isinstance(texto, (dict, list)):
        return texto
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(texto).strip(), flags=re.I)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise

def executar_ia_com_fallback(prompt, json_mode=False, usar_cache=True):
    chave = _chave_cache(prompt, json_mode)
    cache = _cache_ia()
    if usar_cache and chave in cache:
        st.toast("♻️ Resposta reaproveitada — nenhuma chamada nova à IA.", icon="💾")
        return copy.deepcopy(cache[chave])

    provedores = [
        (f"Gemini ({_modelo_gemini()})", _chamar_gemini, "GEMINI_API_KEY"),
        (f"Groq ({st.secrets.get('GROQ_MODEL', 'openai/gpt-oss-120b')})", _chamar_groq, "GROQ_API_KEY"),
    ]
    erros = []
    for nome_provedor, funcao_provedor, secret in provedores:
        if not st.secrets.get(secret):          # sem chave: nem tenta
            erros.append(f"{nome_provedor}: chave {secret} ausente")
            continue
        try:
            st.toast(f"Analisando com {nome_provedor}...", icon="🔄")
            texto_resposta = funcao_provedor(prompt, json_mode=json_mode)
            resultado = _carregar_json(texto_resposta) if json_mode else texto_resposta
            _guardar_cache(chave, resultado)
            st.toast(f"✅ Sucesso via {nome_provedor}!", icon="🎉")
            return resultado
        except Exception as e:
            erros.append(f"{nome_provedor}: {e}")
            st.toast(f"⚠️ {nome_provedor} indisponível, tentando próximo...", icon="⏳")

    raise RuntimeError("Todos os provedores de IA falharam:\n" + "\n".join(erros))

# ==========================================
# FUNÇÕES DE EXTRAÇÃO LOCAL DE TEXTO (SEM IA)
# ==========================================
def extrair_texto_pdf(arquivo_pdf):
    texto_completo = ""
    try:
        arquivo_pdf.seek(0)
        with pdfplumber.open(arquivo_pdf) as pdf:
            for pagina in pdf.pages:
                texto_pagina = pagina.extract_text()
                if texto_pagina and len(texto_pagina.strip()) > 20:
                    texto_completo += texto_pagina + "\n"
                else:
                    # página escaneada (sem texto digital): OCR local
                    try:
                        texto_completo += extrair_texto_imagem(pagina.to_image(resolution=200).original) + "\n"
                    except Exception:
                        pass
    except Exception as e:
        st.error(f"Erro ao ler PDF: {e}")
    return texto_completo

def _preparar_imagem_ocr(imagem):
    """Cinza + ampliação + contraste: melhora bastante o OCR de fotos de exames."""
    img = ImageOps.exif_transpose(imagem).convert("L")
    maior = max(img.size)
    if maior < 2000:
        fator = 2000 / maior
        img = img.resize((int(img.width * fator), int(img.height * fator)), Image.LANCZOS)
    return ImageOps.autocontrast(img)

def extrair_texto_imagem(imagem):
    try:
        return pytesseract.image_to_string(_preparar_imagem_ocr(imagem), lang='por', config='--psm 6')
    except Exception as e:
        st.error(f"Erro no OCR da imagem: {e}")
        return ""

# ==========================================
# FUNÇÕES DO BANCO DE DADOS (HISTÓRICO)
# ==========================================
def salvar_registro(paciente, data, medico, tipo_documento, resumo, parametros):
    try:
        dados = {
            "paciente": paciente,
            "data": data,
            "medico": medico,
            "tipo_documento": tipo_documento,
            "resumo": resumo,
            "parametros": parametros
        }
        supabase.table("historico").insert(dados).execute()
        _buscar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")
        return False

@st.cache_data(ttl=300, show_spinner=False)
def _buscar_historico():
    resposta = supabase.table("historico").select("*").execute()
    return pd.DataFrame(resposta.data) if resposta.data else pd.DataFrame()

def carregar_historico():
    try:
        return _buscar_historico()
    except Exception as e:
        st.error(f"Erro ao carregar dados do Supabase: {e}")
        return pd.DataFrame()

def excluir_registro(id_registro):
    try:
        supabase.table("historico").delete().eq("id", id_registro).execute()
        _buscar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao excluir do Supabase: {e}")
        return False

def atualizar_registro(id_registro, paciente, data, medico, tipo_documento, resumo, parametros):
    try:
        dados = {
            "paciente": paciente,
            "data": data,
            "medico": medico,
            "tipo_documento": tipo_documento,
            "resumo": resumo,
            "parametros": parametros
        }
        supabase.table("historico").update(dados).eq("id", id_registro).execute()
        _buscar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao atualizar no Supabase: {e}")
        return False

def gerar_backup_json(df=None):
    if df is None:
        df = carregar_historico()
    if not df.empty:
        return df.to_json(orient="records", indent=2, force_ascii=False)
    return None

# ==========================================
# FUNÇÕES DE GERAR RELATÓRIO (PDF / TEXTO)
# ==========================================
def construir_texto_relatorio(paciente, data_inicio, data_fim, incluir_resumo, incluir_detalhes, resumo_ia, df_periodo):
    linhas = []
    linhas.append("==================================================")
    linhas.append(f"       RELATÓRIO CLÍNICO VETERINÁRIO - {paciente.upper()}")
    linhas.append("==================================================")
    linhas.append(f"Período: {formatar_data_br(data_inicio)} até {formatar_data_br(data_fim)}")
    linhas.append(f"Data de Emissão: {formatar_data_br(datetime.date.today())}")
    linhas.append("--------------------------------------------------\n")

    if incluir_resumo and resumo_ia:
        linhas.append("📌 RESUMO CLÍNICO (SÍNTESE DA IA):")
        linhas.append(resumo_ia)
        linhas.append("\n--------------------------------------------------\n")

    if incluir_detalhes:
        linhas.append(f"📋 HISTÓRICO DE REGISTROS E CONSULTAS ({len(df_periodo)} registro(s)):")
        linhas.append("")
        if not df_periodo.empty:
            for idx, row in df_periodo.iterrows():
                linhas.append(f"🗓️ Data: {formatar_data_br(row['data'])} | Tipo: {row['tipo_documento']}")
                linhas.append(f"🏥 Local/Médico: {row['medico']}")
                linhas.append(f"📝 Detalhes: {row['resumo']}")
                
                params = row.get("parametros")
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        params = {}
                if isinstance(params, dict) and len(params) > 0:
                    linhas.append("📊 Parâmetros:")
                    for k, v in params.items():
                        if isinstance(v, dict) and "valor" in v:
                            ref = f" (Ref: {v.get('ref_min')} a {v.get('ref_max')})" if v.get('ref_min') else ""
                            linhas.append(f"   - {k}: {v['valor']} {v.get('unidade', '')}{ref}")
                        else:
                            linhas.append(f"   - {k}: {v}")
                linhas.append("-" * 40)
        else:
            linhas.append("Nenhum registro encontrado no período selecionado.")

    return "\n".join(linhas)

def limpar_texto_pdf(texto):
    substituicoes = {
        "📌": "-> ", "📋": "-> ", "🗓️": "Data: ", "🏥": "Local: ",
        "📝": "Obs: ", "📊": "Params: ", "🐶": "", "🩺": "", "☁️": "", "✨": ""
    }
    for emoji, text_sub in substituicoes.items():
        texto = texto.replace(emoji, text_sub)
    return re.sub(r'[^\x00-\xFF]', '', texto)

def gerar_pdf_bytes(texto_relatorio):
    if not FPDF_DISPONIVEL:
        return None
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    texto_limpo = limpar_texto_pdf(texto_relatorio)






    # Garantir que a cor da fonte é preta (R=0, G=0, B=0), sem azul!
    pdf.set_text_color(0, 0, 0)
    
    for linha in texto_limpo.split('\n'):
        linha_str = linha.strip()
        if not linha_str:
            pdf.ln(3)
            continue
            
        # Títulos principais maiores para destacar bem no telemóvel (Tamanho 16)
        if "RELATORIO CLINICO" in linha_str or "RELATÓRIO CLÍNICO" in linha_str:
            pdf.set_font("Helvetica", style="B", size=14)
            try:
                # Altura da linha aumentada para 8
                pdf.cell(pdf.epw, 8, linha_str, align="C", new_x="LMARGIN", new_y="NEXT")
            except Exception:
                pdf.cell(0, 8, linha_str, ln=True, align="C")
            # Retorna para o novo tamanho base otimizado para ecrãs
            pdf.set_font("Helvetica", size=12)
            
        # Subtítulos (Tamanho 14 para manter a hierarquia clara)
        elif any(t in linha_str for t in ("RESUMO CLINICO", "RESUMO CLÍNICO", "HISTORICO DE REGISTROS", "HISTÓRICO DE REGISTROS")):
            pdf.ln(2)
            pdf.set_font("Helvetica", style="B", size=12)
            try:
                # Altura da linha aumentada para 7
                pdf.multi_cell(pdf.epw, 7, linha_str, new_x="LMARGIN", new_y="NEXT")
            except Exception:
                pdf.multi_cell(0, 7, linha_str)
            # Retorna para o novo tamanho base
            pdf.set_font("Helvetica", size=12)
            
        elif linha_str.startswith("===") or linha_str.startswith("---"):
            pdf.ln(1)
            
        # Texto normal (Tamanho 12 e entrelinhas maior para não cansar a vista)
        else:
            try:
                # Altura da linha alterada de 5 para 6 para leitura confortável
                pdf.multi_cell(pdf.epw, 6, linha_str, new_x="LMARGIN", new_y="NEXT")
            except Exception:
                pdf.multi_cell(0, 6, linha_str)
                
    return bytes(pdf.output())
    
    
    

    
# ==========================================
# FUNÇÕES DO PERFIL (FOTO E DATA NASCIMENTO)
# ==========================================
def image_to_base64(image):
    buffered = BytesIO()
    image = image.convert("RGB")
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode()

def calcular_idade(data_nascimento):
    if not data_nascimento:
        return ""
    hoje = datetime.date.today()
    anos = hoje.year - data_nascimento.year
    meses = hoje.month - data_nascimento.month
    dias = hoje.day - data_nascimento.day
    
    if dias < 0:
        meses -= 1
    if meses < 0:
        anos -= 1
        meses += 12
        
    partes = []
    if anos > 0:
        partes.append(f"{anos} {'ano' if anos == 1 else 'anos'}")
    if meses > 0:
        partes.append(f"{meses} {'mês' if meses == 1 else 'meses'}")
    if not partes:
        partes.append("Menos de 1 mês")
        
    return " e ".join(partes)

def salvar_perfil(nome_pet, foto_base64=None, data_nascimento=None, raca=None):
    dados = {"nome_pet": nome_pet}
    if foto_base64 is not None:
        dados["foto_base64"] = foto_base64
    if data_nascimento is not None:
        dados["data_nascimento"] = str(data_nascimento)
    if raca is not None:
        dados["raca"] = raca
        
    try:
        res = supabase.table("perfil").select("*").eq("nome_pet", nome_pet).execute()
        if res.data:
            supabase.table("perfil").update(dados).eq("nome_pet", nome_pet).execute()
        else:
            supabase.table("perfil").insert(dados).execute()
        _buscar_perfil.clear()
        return True
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar perfil: {e}")
        return False

@st.cache_data(ttl=300, show_spinner=False)
def _buscar_perfil(nome_pet):
    res = supabase.table("perfil").select("*").eq("nome_pet", nome_pet).execute()
    return res.data[0] if res.data else {}

def carregar_perfil(nome_pet):
    try:
        return _buscar_perfil(nome_pet)
    except Exception:
        return {}

# ==========================================
# ANÁLISE LOCAL DOS EXAMES (SEM IA)
# ==========================================
def _chave(txt):
    txt = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", txt.lower()).strip(" .:;-_")

def para_numero(valor):
    """Converte '12,5', '7.500', '< 0,5', 12 ou '14.2 g/dL' em float (ou None)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return None if (isinstance(valor, float) and math.isnan(valor)) else float(valor)
    m = re.search(r"-?\d+(?:[.,]\d+)*", str(valor))
    if not m:
        return None
    t = m.group(0)
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", t) and not t.lstrip("-").startswith("0."):
        t = t.replace(".", "")          # 7.500 -> 7500 (milhar no padrão brasileiro)
    try:
        return float(t)
    except ValueError:
        return None

def _como_dict(params):
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            return {}
    return params if isinstance(params, dict) else {}

# Nome padronizado -> apelidos que aparecem nos laudos
_APELIDOS = {
    "Hemácias": ["hemacias", "eritrocitos", "rbc", "hemacias totais", "contagem de hemacias"],
    "Hemoglobina": ["hemoglobina", "hgb", "hb"],
    "Hematócrito": ["hematocrito", "ht", "hct", "volume globular", "vg", "pcv"],
    "VCM": ["vcm", "mcv", "volume corpuscular medio"],
    "HCM": ["hcm", "mch", "hemoglobina corpuscular media"],
    "CHCM": ["chcm", "mchc", "concentracao de hemoglobina corpuscular media"],
    "Leucócitos": ["leucocitos", "leucocitos totais", "wbc", "global de leucocitos"],
    "Plaquetas": ["plaquetas", "plt", "contagem de plaquetas"],
    "Ureia": ["ureia"],
    "Creatinina": ["creatinina"],
    "ALT (TGP)": ["alt", "tgp", "alanina aminotransferase", "alt tgp", "tgp alt"],
    "Fosfatase Alcalina": ["fosfatase alcalina", "fa", "alp", "fal"],
    "Glicose": ["glicose", "glicemia", "glucose"],
    "Proteínas Totais": ["proteinas totais", "proteina total"],
    "Albumina": ["albumina"],
    "Fósforo": ["fosforo", "fosforo inorganico"],
    "Cálcio": ["calcio", "calcio total"],
    "Colesterol": ["colesterol", "colesterol total"],
    "Sódio": ["sodio"],
    "Potássio": ["potassio"],
    "SDMA": ["sdma"],
    "Bilirrubina Total": ["bilirrubina total"],
}
_ALIAS_MAP = {}
for _nome, _lista in _APELIDOS.items():
    _ALIAS_MAP[_chave(_nome)] = _nome
    for _ap in _lista:
        _ALIAS_MAP[_chave(_ap)] = _nome

def normalizar_nome_parametro(nome):
    """'HGB', 'Hemoglobina (HGB)' e 'hemoglobina' viram todos 'Hemoglobina'."""
    original = str(nome).strip()
    candidatos = [_chave(original), _chave(re.sub(r"\(.*?\)", "", original))]
    candidatos += [_chave(m) for m in re.findall(r"\((.*?)\)", original)]
    for c in candidatos:
        if c in _ALIAS_MAP:
            return _ALIAS_MAP[c]
    return original

# Faixas GENÉRICAS (variam por laboratório; a faixa impressa no laudo sempre prevalece)
# milhar=True: o valor pode vir em /µL ou em x10³/µL; a faixa é ajustada automaticamente.
REFERENCIAS_PADRAO = {
    "Hemácias": {"cao": (5.5, 8.5), "gato": (5.0, 10.0), "unidade": "milhões/µL"},
    "Hemoglobina": {"cao": (12.0, 18.0), "gato": (8.0, 15.0), "unidade": "g/dL"},
    "Hematócrito": {"cao": (37.0, 55.0), "gato": (24.0, 45.0), "unidade": "%"},
    "VCM": {"cao": (60.0, 77.0), "gato": (39.0, 55.0), "unidade": "fL"},
    "HCM": {"cao": (19.5, 24.5), "gato": (13.0, 17.0), "unidade": "pg"},
    "CHCM": {"cao": (32.0, 36.0), "gato": (30.0, 36.0), "unidade": "g/dL"},
    "Leucócitos": {"cao": (6000, 17000), "gato": (5500, 19500), "unidade": "/µL", "milhar": True},
    "Plaquetas": {"cao": (200000, 500000), "gato": (300000, 800000), "unidade": "/µL", "milhar": True},
    "Ureia": {"cao": (21.0, 60.0), "gato": (42.0, 64.0), "unidade": "mg/dL"},
    "Creatinina": {"cao": (0.5, 1.5), "gato": (0.8, 1.8), "unidade": "mg/dL"},
    "ALT (TGP)": {"cao": (10.0, 100.0), "gato": (10.0, 100.0), "unidade": "U/L"},
    "Fosfatase Alcalina": {"cao": (20.0, 150.0), "gato": (10.0, 100.0), "unidade": "U/L"},
    "Glicose": {"cao": (70.0, 120.0), "gato": (70.0, 150.0), "unidade": "mg/dL"},
    "Proteínas Totais": {"cao": (5.5, 7.5), "gato": (6.0, 8.0), "unidade": "g/dL"},
    "Albumina": {"cao": (2.3, 3.8), "gato": (2.5, 3.9), "unidade": "g/dL"},
    "Fósforo": {"cao": (2.5, 6.8), "gato": (3.0, 7.0), "unidade": "mg/dL"},
    "Cálcio": {"cao": (9.0, 11.8), "gato": (8.5, 11.5), "unidade": "mg/dL"},
    "Colesterol": {"cao": (110.0, 320.0), "gato": (65.0, 225.0), "unidade": "mg/dL"},
    "Sódio": {"cao": (141.0, 152.0), "gato": (147.0, 156.0), "unidade": "mEq/L"},
    "Potássio": {"cao": (3.6, 5.6), "gato": (3.6, 5.5), "unidade": "mEq/L"},
    "SDMA": {"cao": (0.0, 14.0), "gato": (0.0, 14.0), "unidade": "µg/dL"},
    "Bilirrubina Total": {"cao": (0.0, 0.6), "gato": (0.0, 0.4), "unidade": "mg/dL"},
}

def detectar_especie(texto):
    return "gato" if re.search(r"\b(gat|felin|cat)", _chave(texto or "")) else "cao"

def ref_valida(rmin, rmax):
    try:
        return (rmin is not None and rmax is not None and not pd.isna(rmin)
                and not pd.isna(rmax) and float(rmax) > float(rmin))
    except (TypeError, ValueError):
        return False

def _normalizar_faixa(rmin, rmax):
    rmin = None if rmin is None or pd.isna(rmin) else float(rmin)
    rmax = None if rmax is None or pd.isna(rmax) else float(rmax)
    if rmax is None or rmax <= 0:
        return None, None
    if rmin is None:
        rmin = 0.0                      # laudos do tipo "até 14"
    return (rmin, rmax) if rmax > rmin else (None, None)

ROTULO_STATUS = {"normal": "🟢 Normal", "abaixo": "🔻 Abaixo", "acima": "🔺 Acima", "sem_ref": "⚪ Sem referência"}
_ORDEM_STATUS = {"acima": 0, "abaixo": 1, "normal": 2, "sem_ref": 3}

def classificar_valor(valor, rmin, rmax):
    if not ref_valida(rmin, rmax):
        return "sem_ref"
    if valor < rmin:
        return "abaixo"
    if valor > rmax:
        return "acima"
    return "normal"

def extrair_valor_param(bruto):
    """Aceita {'valor':..,'unidade':..,'ref_min':..,'ref_max':..} ou um número simples."""
    if isinstance(bruto, dict):
        return (para_numero(bruto.get("valor")), str(bruto.get("unidade") or "").strip(),
                para_numero(bruto.get("ref_min")), para_numero(bruto.get("ref_max")))
    return para_numero(bruto), "", None, None

def normalizar_parametros(params):
    """Padroniza nomes e números do que a IA (ou o utilizador) devolveu."""
    saida = {}
    for nome, bruto in _como_dict(params).items():
        valor, unid, rmin, rmax = extrair_valor_param(bruto)
        if valor is None:
            continue
        saida[normalizar_nome_parametro(nome)] = {"valor": valor, "unidade": unid, "ref_min": rmin, "ref_max": rmax}
    return saida

def resumir_parametros(params):
    partes = []
    for nome, bruto in _como_dict(params).items():
        valor, unid, rmin, rmax = extrair_valor_param(bruto)
        if valor is None:
            continue
        txt = f"{normalizar_nome_parametro(nome)}: {valor:g} {unid}".strip()
        a, b = _normalizar_faixa(rmin, rmax)
        if a is not None:
            txt += f" (ref {a:g}–{b:g})"
        partes.append(txt)
    return "; ".join(partes)

_COLUNAS_EXAMES = ["data", "parametro", "valor", "unidade", "ref_min", "ref_max", "origem_ref"]

def montar_df_exames(df_hist, especie="cao"):
    """Tabela longa (uma linha por data x parâmetro) com referência já resolvida."""
    vazio = pd.DataFrame(columns=_COLUNAS_EXAMES)
    if df_hist is None or df_hist.empty:
        return vazio
    linhas = []
    for _, r in df_hist.iterrows():
        if re.search(r"nutri|lembrete|medicament", str(r.get("tipo_documento", "")), re.I):
            continue
        data = parse_data(r.get("data"))
        params = _como_dict(r.get("parametros"))
        if not data or not params:
            continue
        for nome, bruto in params.items():
            valor, unid, rmin, rmax = extrair_valor_param(bruto)
            if valor is None:
                continue
            linhas.append({"data": pd.Timestamp(data), "parametro": normalizar_nome_parametro(nome),
                           "valor": valor, "unidade": unid, "ref_min": rmin, "ref_max": rmax})
    if not linhas:
        return vazio

    df = pd.DataFrame(linhas)
    for c in ("ref_min", "ref_max"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("data", kind="stable").drop_duplicates(subset=["data", "parametro"], keep="last").reset_index(drop=True)

    refs = {}
    for p, g in df.groupby("parametro"):
        unid = next((u for u in reversed(g["unidade"].tolist()) if u), "")
        rmin, rmax, origem = float("nan"), float("nan"), "—"
        for _, lin in g.iloc[::-1].iterrows():          # usa a faixa do laudo mais recente
            a, b = _normalizar_faixa(lin["ref_min"], lin["ref_max"])
            if a is not None:
                rmin, rmax, origem = a, b, "laudo"
                break
        if origem == "—":
            padrao = REFERENCIAS_PADRAO.get(p)
            if padrao:
                a, b = padrao[especie]
                if padrao.get("milhar") and g["valor"].median() < 1000:
                    a, b = a / 1000, b / 1000
                    unid = unid or "x10³/µL"
                rmin, rmax, origem = a, b, "padrão"
                unid = unid or padrao["unidade"]
        refs[p] = (unid, rmin, rmax, origem)

    df["unidade"] = df["parametro"].map(lambda p: refs[p][0])
    df["ref_min"] = df["parametro"].map(lambda p: refs[p][1])
    df["ref_max"] = df["parametro"].map(lambda p: refs[p][2])
    df["origem_ref"] = df["parametro"].map(lambda p: refs[p][3])
    return df[_COLUNAS_EXAMES]

def resumo_ultimos_exames(df_ex):
    linhas = []
    for p, g in df_ex.groupby("parametro"):
        g = g.sort_values("data")
        ult = g.iloc[-1]
        ant = g.iloc[-2]["valor"] if len(g) > 1 else None
        status = classificar_valor(ult["valor"], ult["ref_min"], ult["ref_max"])
        unid = f" {ult['unidade']}" if ult["unidade"] else ""
        ref_txt = f"{ult['ref_min']:g} – {ult['ref_max']:g}" if ref_valida(ult["ref_min"], ult["ref_max"]) else "—"
        linhas.append({
            "Parâmetro": p, "Último valor": f"{ult['valor']:g}{unid}", "Referência": ref_txt,
            "Situação": ROTULO_STATUS[status],
            "Variação": f"{ult['valor'] - ant:+g}" if ant is not None else "—",
            "Data": ult["data"].strftime(FORMATO_DATA_BR), "Medições": len(g),
            "status": status, "_ordem": _ORDEM_STATUS[status],
        })
    df = pd.DataFrame(linhas).sort_values(["_ordem", "Parâmetro"]).drop(columns="_ordem")
    return df.reset_index(drop=True)

def config_eixo_data(datas, max_ticks=10):
    """Eixo X em DD/MM/AAAA (o padrão do Plotly mostra 'Nov 13, 2026')."""
    unicas = sorted({pd.Timestamp(d).to_pydatetime() for d in datas if not pd.isna(d)})
    cfg = dict(showgrid=False, hoverformat=FORMATO_DATA_BR)
    if 0 < len(unicas) <= max_ticks:
        cfg.update(tickmode="array", tickvals=unicas, ticktext=[d.strftime(FORMATO_DATA_BR) for d in unicas])
    else:
        cfg.update(tickformat=FORMATO_DATA_BR)
    return cfg

def construir_fig_evolucao(df_ex, parametros, modo_ref=True):
    """Todos os parâmetros num só gráfico, em escala comparável."""
    fig = go.Figure()
    ignorados = []
    for p in parametros:
        g = df_ex[df_ex["parametro"] == p].sort_values("data")
        if g.empty:
            continue
        if modo_ref:
            rmin, rmax = g["ref_min"].iloc[-1], g["ref_max"].iloc[-1]
            if not ref_valida(rmin, rmax):
                ignorados.append(p)
                continue
            y = (g["valor"] - rmin) / (rmax - rmin)
            esc = "Escala: %{y:.2f} (0 = mín, 1 = máx)"
        else:
            base = g["valor"].iloc[0]
            if not base:
                ignorados.append(p)
                continue
            y = (g["valor"] / base - 1) * 100
            esc = "Variação: %{y:+.1f}%"
        fig.add_trace(go.Scatter(
            x=g["data"], y=y, mode="lines+markers", name=p,
            customdata=[[v, u] for v, u in zip(g["valor"], g["unidade"])],
            hovertemplate="<b>%{fullData.name}</b><br>%{x|%d/%m/%Y}<br>Valor: %{customdata[0]} %{customdata[1]}<br>" + esc + "<extra></extra>",
        ))
    if modo_ref:
        fig.add_hrect(y0=0, y1=1, line_width=0, fillcolor="green", opacity=0.08,
                      annotation_text="Faixa saudável", annotation_position="top left", annotation_font_color="green")
    else:
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
    fig.update_layout(
        height=480, margin=dict(l=10, r=10, t=30, b=10), hovermode="closest",
        xaxis=config_eixo_data(df_ex["data"]),
        yaxis=dict(title="Escala da faixa de referência" if modo_ref else "Variação % desde a 1ª medição",
                   showgrid=True, gridcolor="rgba(200,200,200,0.2)"),
        legend=dict(orientation="h", y=-0.2), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig, ignorados

def construir_fig_mini(df_ex, colunas=3):
    """Um mini-gráfico por parâmetro, com a faixa saudável em verde."""
    params = sorted(df_ex["parametro"].unique())
    n_linhas = max(1, math.ceil(len(params) / colunas))
    titulos = []
    for p in params:
        u = df_ex.loc[df_ex["parametro"] == p, "unidade"].iloc[-1]
        titulos.append(f"{p} ({u})" if u else p)
    espac = 0.12 if n_linhas <= 1 else min(0.12, 0.8 / (n_linhas - 1))
    fig = make_subplots(rows=n_linhas, cols=colunas, subplot_titles=titulos,
                        vertical_spacing=espac, horizontal_spacing=0.08)
    for i, p in enumerate(params):
        r, c = divmod(i, colunas)
        g = df_ex[df_ex["parametro"] == p].sort_values("data")
        unid = g["unidade"].iloc[-1]
        fig.add_trace(go.Scatter(
            x=g["data"], y=g["valor"], mode="lines+markers", showlegend=False,
            line=dict(color="#2E86C1", width=3), marker=dict(size=8, color="#1B4F72"),
            hovertemplate="%{x|%d/%m/%Y}<br>%{y} " + str(unid) + "<extra></extra>",
        ), row=r + 1, col=c + 1)
        rmin, rmax = g["ref_min"].iloc[-1], g["ref_max"].iloc[-1]
        if ref_valida(rmin, rmax):
            fig.add_hrect(y0=rmin, y1=rmax, line_width=0, fillcolor="green", opacity=0.12, row=r + 1, col=c + 1)
    fig.update_xaxes(tickformat="%d/%m/%y", nticks=3, showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(200,200,200,0.2)")
    fig.update_annotations(font_size=12)
    fig.update_layout(height=max(320, 260 * n_linhas), margin=dict(l=10, r=10, t=40, b=10),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    return fig

# ==========================================
# PROMPTS (um único pedido à IA por documento / rótulo)
# ==========================================
PROMPT_EXTRACAO_EXAME = """Você é um assistente veterinário. Leia o texto extraído (via OCR/PDF) de um documento médico veterinário.

TEXTO EXTRAÍDO:
<<TEXTO>>

Regras:
- Responda SOMENTE com um JSON válido, sem comentários e sem markdown.
- "data": data do documento/coleta no formato AAAA-MM-DD (no texto, as datas costumam estar em DD/MM/AAAA).
- "parametros": inclua APENAS resultados numéricos de exames (hemograma, bioquímico, urinálise etc.), um item por parâmetro, com o nome padronizado em português (ex.: Hemácias, Hemoglobina, Hematócrito, VCM, HCM, CHCM, Leucócitos, Plaquetas, Ureia, Creatinina, ALT (TGP), Fosfatase Alcalina, Glicose, Proteínas Totais, Albumina, Fósforo, Cálcio, Colesterol, Sódio, Potássio, SDMA).
- Cada parâmetro: {"valor": número, "unidade": "texto", "ref_min": número ou null, "ref_max": número ou null}. Use ponto como separador decimal e copie o valor exatamente como está no laudo, sem converter unidades. ref_min e ref_max são os valores de referência impressos no próprio laudo; se vier só "até X", use ref_min 0.
- Se o documento não tiver exames numéricos (receita, consulta etc.), use "parametros": {}.
- "tipo_documento": Receita, Exame, Atestado, Fatura ou Consulta.
- "resumo": resumo objetivo com medicamentos (e doses), resultados fora da referência e recomendações.

Formato:
{"data": "AAAA-MM-DD", "medico": "Nome do Médico ou Clínica", "tipo_documento": "Exame", "resumo": "...", "parametros": {"Hemoglobina": {"valor": 14.2, "unidade": "g/dL", "ref_min": 12.0, "ref_max": 18.0}}}
"""

PROMPT_ROTULO = """Aja como um nutrólogo veterinário. Leia esta imagem do rótulo de uma ração para pets.
Retorne EXCLUSIVAMENTE um JSON com esta estrutura:
{
  "marca": "Nome da marca ou linha (ex: Royal Canin Renal)",
  "tipo": "Seca ou Húmida",
  "proteina_bruta_percentual": 0.0,
  "extrato_etereo_percentual": 0.0,
  "fosforo_percentual": 0.0,
  "sodio_percentual": 0.0,
  "calcio_percentual": 0.0,
  "ingredientes_principais": "Lista curta dos 3 primeiros ingredientes",
  "indicacao": "Se houver (ex: Filhotes, Renal, Obesidade)"
}
Use os valores MÍNIMOS/MÁXIMOS dos Níveis de Garantia como estão impressos. Se a imagem não for de um rótulo ou algum valor não estiver presente, use 0.0 para números e "Não identificado" para textos."""

DIETA_VAZIA = {"marca": "", "tipo": "Seca", "proteina_bruta_percentual": 0.0, "extrato_etereo_percentual": 0.0,
               "fosforo_percentual": 0.0, "sodio_percentual": 0.0, "calcio_percentual": 0.0,
               "ingredientes_principais": "", "indicacao": ""}

# ==========================================
# INICIALIZAÇÃO DE ESTADO E PÁGINA
# ==========================================
if "dados_ia" not in st.session_state:
    st.session_state.dados_ia = None

if "resumo_consultas" not in st.session_state:
    st.session_state.resumo_consultas = None

# ==========================================
# BARRA LATERAL (PERFIL)
# ==========================================
with st.sidebar:
    st.title("🐾 Perfil do Pet")
    nome_perfil = st.text_input("Nome do Paciente", value="Mike", key="nome_perfil")

    perfil_dados = carregar_perfil(nome_perfil)
    raca_atual = perfil_dados.get("raca", "")
    raca_input = st.text_input("🐕 Espécie / Raça", value=raca_atual, placeholder="Ex: Cão - Golden Retriever")
    st.markdown("---")
    foto_b64 = perfil_dados.get("foto_base64")
    data_nasc_str = perfil_dados.get("data_nascimento")
    
    if data_nasc_str:
        try:
            data_nasc_val = datetime.datetime.strptime(data_nasc_str, "%Y-%m-%d").date()
        except ValueError:
            data_nasc_val = datetime.date.today()
    else:
        data_nasc_val = datetime.date.today()

    if foto_b64:
        st.markdown(
            f'<div style="display: flex; justify-content: center;">'
            f'<img src="data:image/jpeg;base64,{foto_b64}" style="width:180px; height:180px; border-radius:50%; object-fit:cover; border: 3px solid #f0f2f6;">'
            f'</div><br>', 
            unsafe_allow_html=True
        )
    else:
        st.info("Nenhuma foto de perfil cadastrada. Envie uma abaixo!")

    data_nasc_input = st.date_input("🎂 Data de Nascimento", value=data_nasc_val, format="DD/MM/YYYY", key="data_nasc_input")
    
    if data_nasc_input and data_nasc_input <= datetime.date.today():
        idade_formatada = calcular_idade(data_nasc_input)
        st.info(f"🎈 **Idade Atual:** {idade_formatada}")

    nova_foto = st.file_uploader("Alterar foto de perfil", type=["jpg", "jpeg", "png"])
    
    if st.button("💾 Salvar Perfil", use_container_width=True):
        img_b64 = foto_b64
        if nova_foto:
            img = Image.open(nova_foto)
            img.thumbnail((400, 400))
            img_b64 = image_to_base64(img)
            
        if salvar_perfil(nome_perfil, img_b64, data_nasc_input, raca_input):
            st.success("Perfil atualizado com sucesso!")
            st.rerun()

with st.sidebar:
    st.markdown("---")
    if st.button("🔄 Atualizar dados", use_container_width=True,
                 help="Os dados do Supabase ficam em cache por alguns minutos para o app ficar mais rápido."):
        _buscar_historico.clear()
        _buscar_perfil.clear()
        st.rerun()

# ==========================================
# CONTEÚDO PRINCIPAL
# ==========================================
col_titulo, col_logo = st.columns([4, 1])
with col_titulo:
    st.title(f"🐶 Relatório do {nome_perfil}")
    st.markdown("*O diário inteligente de saúde do seu pet.*")

tem_gemini = "GEMINI_API_KEY" in st.secrets
tem_groq = "GROQ_API_KEY" in st.secrets

if not (tem_gemini or tem_groq):
    st.error("⚠️️ Nenhuma chave de IA (GEMINI_API_KEY ou GROQ_API_KEY) foi encontrada nos Secrets.")

st.markdown("---")

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📝 Adicionar Registo", 
    "🗂️ Histórico", 
    "⏰ Lembretes", 
    "📊 Dashboard", 
    "🩺 Consultas",
    "💊 Medicamentos",
    "🥩 Nutrição"
])

# ==========================================
# SEPARADOR 1: NOVO REGISTO
# ==========================================
with tab1:
    st.markdown("### 📸 Digitalizar Documento")
    nome_paciente = st.text_input("👤 Nome do Paciente / Pet:", value=nome_perfil)
    
    arquivo_upload = st.file_uploader("Arraste a foto ou ficheiro PDF do exame", type=["jpg", "jpeg", "png", "pdf"])
    
    texto_extraido = ""
    imagem_carregada = None
    
    if arquivo_upload:
        if arquivo_upload.type == "application/pdf":
            st.info(f"📄 Ficheiro PDF carregado: {arquivo_upload.name}")
        else:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                imagem_carregada = Image.open(arquivo_upload)
                st.image(imagem_carregada, caption="Documento Carregado", use_container_width=True)

    if arquivo_upload and (tem_gemini or tem_groq):
        if st.button("✨ Ler com Inteligência Artificial", use_container_width=True, type="primary"):
            with st.spinner("Extraindo texto e analisando com a IA..."):
                try:
                    if arquivo_upload.type == "application/pdf":
                        texto_extraido = extrair_texto_pdf(arquivo_upload)
                    else:
                        texto_extraido = extrair_texto_imagem(imagem_carregada)

                    if not texto_extraido.strip():
                        st.warning("Não foi possível extrair nenhum texto legível desse arquivo.")
                    else:
                        prompt = PROMPT_EXTRACAO_EXAME.replace("<<TEXTO>>", texto_extraido[:15000])
                        
                        resultado_ia = executar_ia_com_fallback(prompt, json_mode=True)
                        if isinstance(resultado_ia, dict):
                            resultado_ia["parametros"] = normalizar_parametros(resultado_ia.get("parametros", {}))
                            st.session_state.dados_ia = resultado_ia
                        else:
                            st.error("A IA devolveu um formato inesperado. Tente novamente.")
                        st.toast("✅ Leitura estruturada concluída!", icon="🤖")
                        
                except Exception as e:
                    st.error(f"Erro no processamento da IA: {e}")

    if st.session_state.dados_ia:
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.subheader("⚙️ Rever e Confirmar Dados")
            
            dados = st.session_state.dados_ia
            
            data_padrao = parse_data(dados.get("data", "")) or datetime.date.today()

            with st.form("form_confirmacao"):
                col_data, col_tipo = st.columns(2)
                with col_data:
                    data_final = st.date_input("🗓️ Data do Registo", value=data_padrao, format="DD/MM/YYYY")
                with col_tipo:
                    tipo_final = st.text_input("📄 Tipo de Documento", value=dados.get("tipo_documento", ""))
                
                medico_final = st.text_input("👨‍⚕️ Médico / Clínica", value=dados.get("medico", ""))
                resumo_final = st.text_area("📝 Resumo / Medicamentos", value=dados.get("resumo", ""), height=100)
                
                parametros_extraidos = dados.get("parametros", {})
                parametros_str = json.dumps(parametros_extraidos, ensure_ascii=False, indent=2)
                parametros_finais_txt = st.text_area("📊 Parâmetros Numéricos (JSON)", value=parametros_str, help="Corrija se a IA errou algum número.")
                
                confirmar = st.form_submit_button("☁️ Guardar na Nuvem", use_container_width=True)
                
                if confirmar:
                    try:
                        parametros_finais = normalizar_parametros(json.loads(parametros_finais_txt))
                    except Exception:
                        parametros_finais = {}
                        st.toast("JSON dos parâmetros inválido: registo guardado sem valores numéricos.", icon="⚠️")
                        
                    if salvar_registro(nome_paciente, str(data_final), medico_final, tipo_final, resumo_final, parametros_finais):
                        st.session_state.dados_ia = None
                        st.toast("Registo guardado com sucesso!", icon="🎉")
                        st.rerun()

# ==========================================
# SEPARADOR 2: HISTÓRICO
# ==========================================
@st.dialog("✏️ Editar Registo do Histórico", width="large")
def modal_editar_registro(row, idx):
    data_padrao_edit = parse_data(row['data']) or datetime.date.today()

    with st.form(key=f"form_edit_modal_{row['id']}_{idx}"):
        col_m1, col_m2 = st.columns(2)
        
        with col_m1:
            nova_data = st.date_input("🗓️ Data do Documento", value=data_padrao_edit, format="DD/MM/YYYY", key=f"md_{row['id']}_{idx}")
            novo_tipo = st.text_input("📄 Tipo de Documento", value=str(row['tipo_documento']), key=f"mt_{row['id']}_{idx}")

        with col_m2:
            novo_medico = st.text_input("👨‍⚕️ Médico / Clínica", value=str(row['medico']), key=f"mm_{row['id']}_{idx}")

        novo_resumo = st.text_area("📝 Resumo / Detalhes", value=str(row['resumo']), height=120, key=f"mr_{row['id']}_{idx}")
        
        params_atuais = row.get("parametros", {})
        if isinstance(params_atuais, str):
            try:
                params_atuais = json.loads(params_atuais)
            except Exception:
                params_atuais = {}

        with st.expander("⚙️ Opções Avançadas (Dados Técnicos / JSON)"):
            st.caption("Esta secção é destinada a configurações estruturadas avançadas.")
            params_txt = json.dumps(params_atuais, ensure_ascii=False, indent=2)
            novos_params_txt = st.text_area("Parâmetros estruturados", value=params_txt, height=100, key=f"mp_{row['id']}_{idx}")

        col_f1, col_f2 = st.columns([1, 1])
        with col_f1:
            btn_salvar_edit = st.form_submit_button("💾 Guardar Alterações", use_container_width=True, type="primary")
        
        if btn_salvar_edit:
            try:
                novos_params = json.loads(novos_params_txt)
            except Exception:
                novos_params = params_atuais
            
            data_formatada_salvar = nova_data.isoformat()
            
            sucesso = atualizar_registro(
                id_registro=row['id'],
                paciente=row['paciente'],
                data=data_formatada_salvar,
                medico=novo_medico,
                tipo_documento=novo_tipo,
                resumo=novo_resumo,
                parametros=novos_params
            )
            
            if sucesso:
                st.toast("Registo atualizado com sucesso!", icon="✅")
                st.rerun()


with tab2:
    df = carregar_historico()
    
    if not df.empty and "paciente" in df.columns:
        pacientes = sorted([str(p).strip() for p in df["paciente"].dropna().unique() if str(p).strip()])
        
        if not pacientes:
            st.warning("Nenhum paciente/pet válido foi encontrado no histórico.")
        else:
            col_filtro1, col_filtro2, col_filtro3 = st.columns([2, 2, 1.5])
            with col_filtro1:
                paciente_sel = st.selectbox("🐶 Selecione o Pet/Paciente:", pacientes)
            with col_filtro2:
                busca = st.text_input("🔍 Procurar:")
            with col_filtro3:
                ordem_ordem = st.selectbox("⏳ Ordem:", ["Cronológica (Mais antigo)", "Recentes Primeiro"])
                
            ordem_asc = (ordem_ordem == "Cronológica (Mais antigo)")
            
            df['data_dt'] = converter_coluna_data(df['data'])
            df_filtrado = df[df["paciente"].astype(str) == paciente_sel].sort_values(by="data_dt", ascending=ordem_asc)
            
            if busca:
                df_filtrado = df_filtrado[
                    df_filtrado['medico'].astype(str).str.contains(busca, case=False, na=False) |
                    df_filtrado['resumo'].astype(str).str.contains(busca, case=False, na=False) |
                    df_filtrado['tipo_documento'].astype(str).str.contains(busca, case=False, na=False)
                ]

            st.markdown("<br>", unsafe_allow_html=True)
            col_met1, col_met2, col_met3 = st.columns(3)
            col_met1.metric("Registos", len(df_filtrado))
            col_met2.metric("Locais/Clínicas", df_filtrado['medico'].nunique())
            
            df_csv = df_filtrado.drop(columns=['data_dt'], errors='ignore').copy()
            df_csv['data'] = df_csv['data'].map(formatar_data_br)
            csv = df_csv.to_csv(index=False).encode('utf-8')
            json_backup = gerar_backup_json(df.drop(columns=['data_dt'], errors='ignore'))
            
            with col_met3:
                st.download_button(
                    label="📥 Baixar CSV",
                    data=csv,
                    file_name=f"historico_{paciente_sel}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
                if json_backup:
                    st.download_button(
                        label="🛡️ Backup (JSON)",
                        data=json_backup,
                        file_name=f"backup_completo_{datetime.date.today().strftime('%d_%m_%Y')}.json",
                        mime="application/json",
                        use_container_width=True
                    )
                
            st.markdown("---")

            if df_filtrado.empty:
                st.warning("Nenhum registo encontrado com essa palavra.")
            else:
                for idx, row in df_filtrado.iterrows():
                    data_formatada = formatar_data_br(row['data'])

                    with st.container(border=True):
                        col_texto, col_botoes = st.columns([5, 2]) 
                        
                        with col_texto:
                            st.subheader(f"🗓️ {data_formatada} - {row['tipo_documento']}")
                            st.markdown(f"**🏥 Clínica/Médico:** {row['medico']}")
                            st.markdown(f"**📝 Detalhes:** {row['resumo']}")
                            
                        with col_botoes:
                            st.write("") 
                            if st.button("✏ Editar", key=f"btn_modal_edit_{row['id']}_{idx}"):
                                modal_editar_registro(row, idx)

                            st.write("") 

                            key_chk = f"chk_{row['id']}_{idx}"
                            key_btn = f"excluir_{row['id']}_{idx}"

                            if st.button("🗑️ Apagar", key=key_btn, help="Marque a caixa abaixo para apagar"):
                                if st.session_state.get(key_chk, False):
                                    if excluir_registro(row['id']):
                                        st.toast("Registo apagado!", icon="🗑️")
                                        st.rerun()
                                else:
                                    st.warning("Marque 'Confirmar' 👇")
                                    
                            st.checkbox("Confirmar", key=key_chk)

    else:
        st.info("O histórico está vazio. Adicione um novo registo!")

# ==========================================
# SEPARADOR 3: LEMBRETES
# ==========================================
with tab3:
    st.subheader(f"🐾 Lembretes para o {nome_perfil}")
    st.write("Agende a troca da coleira, vacinas ou medicamentos.")

    with st.container(border=True):
        with st.form("form_lembrete"):
            item = st.text_input("O que o pet precisa? (Ex: Coleira Seresto, Vacina V10)")
            
            col_d, col_h = st.columns(2)
            with col_d:
                data_lembrete = st.date_input("🗓️ Data", value=datetime.date.today(), format="DD/MM/YYYY")
            with col_h:
                hora_lembrete = st.time_input("⏰ Horário", value=datetime.time(12, 0))
                
            notas = st.text_area("📝 Observações (Ex: Dar com a ração)")

            salvar_lembrete = st.form_submit_button("Criar Lembrete 🔔", use_container_width=True)

        if salvar_lembrete:
            if item:
                salvar_registro(nome_perfil, str(data_lembrete), "Veterinário / Casa", f"Lembrete: {item}", notas, {})
                
                titulo = urllib.parse.quote(f"🐶 Cuidar do {nome_perfil}: {item}")
                detalhes = urllib.parse.quote(notas)
                
                data_str = data_lembrete.strftime("%Y%m%d")
                hora_str = hora_lembrete.strftime("%H%M%S")
                inicio = f"{data_str}T{hora_str}"
                
                link_gcal = f"https://www.google.com/calendar/render?action=TEMPLATE&text={titulo}&dates={inicio}/{inicio}&details={detalhes}"
                
                st.toast("Lembrete salvo no histórico!", icon="✅")
                st.info("Registo guardado! Clique no botão abaixo para ativar o alarme no seu telemóvel:")
                st.markdown(f"""
                <a href="{link_gcal}" target="_blank" style="background-color:#4285F4; color:white; padding:10px 20px; text-decoration:none; border-radius:8px; display:block; text-align:center; font-weight:bold; font-size:16px;">
                📅 Adicionar Notificação ao Calendário
                </a>
                """, unsafe_allow_html=True)
            else:
                st.warning("Por favor, preencha o nome do medicamento ou coleira.")

# ==========================================
# SEPARADOR 4: DASHBOARD DE EXAMES (análise local, sem IA)
# ==========================================
with tab4:
    st.markdown("### 📈 Painel Clínico Avançado")

    df_dash_raw = carregar_historico()
    pacientes_dash = []
    if not df_dash_raw.empty and {"parametros", "paciente"}.issubset(df_dash_raw.columns):
        pacientes_dash = sorted({str(p).strip() for p in df_dash_raw["paciente"].dropna() if str(p).strip()})

    if not pacientes_dash:
        st.info("Ainda não há exames com valores numéricos. Digitalize um exame na aba **Adicionar Registo**.")
    else:
        idx_dash = pacientes_dash.index(nome_perfil) if nome_perfil in pacientes_dash else 0
        paciente_dash_sel = st.selectbox("🐶 Selecione o Paciente:", pacientes_dash, index=idx_dash, key="dash_paciente")

        especie_dash = detectar_especie(raca_input)
        nome_especie = "gatos" if especie_dash == "gato" else "cães"
        df_ex = montar_df_exames(
            df_dash_raw[df_dash_raw["paciente"].astype(str).str.strip() == paciente_dash_sel], especie_dash
        )

        if df_ex.empty:
            st.info("Nenhum parâmetro numérico foi extraído nos registos deste paciente ainda.")
        else:
            st.caption(
                f"📄 Usa a faixa de referência impressa no laudo; quando não há, usa valores padrão para {nome_especie} "
                f"(definido pelo campo *Espécie / Raça* do perfil). Esta aba não chama a IA, exceto nos botões opcionais."
            )
            aba_geral, aba_mini, aba_detalhe, aba_tabela = st.tabs(
                ["🌐 Visão geral", "🧩 Gráfico de cada exame", "🔬 Detalhe de um exame", "📅 Tabela completa"]
            )

            # ---------- VISÃO GERAL ----------
            with aba_geral:
                resumo_df = resumo_ultimos_exames(df_ex)
                fora = int(resumo_df["status"].isin(["abaixo", "acima"]).sum())
                m1, m2, m3 = st.columns(3)
                m1.metric("Datas de exame", int(df_ex["data"].nunique()))
                m2.metric("Parâmetros", len(resumo_df))
                m3.metric("Fora da faixa (último exame)", fora)

                st.markdown("#### 🩺 Situação atual")
                st.dataframe(resumo_df.drop(columns=["status"]), hide_index=True, use_container_width=True)

                st.markdown("#### 📈 Evolução de todos os exames")
                modo_txt = st.radio(
                    "Escala do gráfico",
                    ["Faixa de referência (0 = mínimo, 1 = máximo)", "Variação % desde a 1ª medição"],
                    horizontal=True, key=f"dash_escala_{paciente_dash_sel}",
                )
                modo_ref = modo_txt.startswith("Faixa")
                todos_params = sorted(df_ex["parametro"].unique())
                padrao_sel = [p for p in todos_params if (df_ex["parametro"] == p).sum() >= 2] or todos_params
                selecionados = st.multiselect("Parâmetros no gráfico", todos_params, default=padrao_sel,
                                              key=f"dash_multi_{paciente_dash_sel}")
                if selecionados:
                    fig_geral, ignorados = construir_fig_evolucao(df_ex, selecionados, modo_ref)
                    if len(ignorados) == len(selecionados):
                        st.info("Nenhum dos parâmetros escolhidos tem faixa de referência. Use a escala de variação %.")
                    else:
                        st.plotly_chart(fig_geral, use_container_width=True)
                    if ignorados:
                        st.caption("Sem faixa de referência (não aparecem nesta escala): " + ", ".join(ignorados))
                else:
                    st.info("Selecione ao menos um parâmetro.")

            # ---------- MINI-GRÁFICOS ----------
            with aba_mini:
                st.caption("Um gráfico por parâmetro. A faixa verde é a referência saudável.")
                st.plotly_chart(construir_fig_mini(df_ex), use_container_width=True)

            # ---------- DETALHE ----------
            with aba_detalhe:
                lista_param = sorted(df_ex["parametro"].unique())
                param_sel = st.selectbox("🔬 Qual exame/parâmetro deseja analisar?", lista_param,
                                         key=f"dash_param_{paciente_dash_sel}")
                g = df_ex[df_ex["parametro"] == param_sel].sort_values("data")
                unid = str(g["unidade"].iloc[-1] or "")
                origem = g["origem_ref"].iloc[-1]
                ref_min0 = g["ref_min"].iloc[-1]
                ref_max0 = g["ref_max"].iloc[-1]
                ref_min0 = 0.0 if pd.isna(ref_min0) else float(ref_min0)
                ref_max0 = 0.0 if pd.isna(ref_max0) else float(ref_max0)

                ia_key = f"ia_ref_{paciente_dash_sel}_{param_sel}"
                if origem == "—" and ia_key in st.session_state:
                    ref_min0, ref_max0 = st.session_state[ia_key]["min"], st.session_state[ia_key]["max"]
                    unid = unid or st.session_state[ia_key].get("unid", "")
                    origem = "IA"

                with st.expander("⚙️ Ajustar Faixa de Referência"):
                    st.caption({
                        "laudo": "📄 Faixa lida do próprio laudo.",
                        "padrão": f"📚 Faixa padrão genérica para {nome_especie}; a do seu laboratório pode ser diferente.",
                        "IA": "✨ Sugerida pela IA — confirme com o veterinário.",
                        "—": "Sem faixa conhecida para este parâmetro. Preencha abaixo ou peça uma sugestão à IA.",
                    }[origem])
                    col_ref1, col_ref2 = st.columns(2)
                    with col_ref1:
                        ref_min = st.number_input("Mínimo Saudável", value=ref_min0, step=0.1,
                                                  key=f"rmin_{paciente_dash_sel}_{param_sel}_{origem}")
                    with col_ref2:
                        ref_max = st.number_input("Máximo Saudável", value=ref_max0, step=0.1,
                                                  key=f"rmax_{paciente_dash_sel}_{param_sel}_{origem}")
                    if origem == "—" and (tem_gemini or tem_groq):
                        if st.button("🤖 Sugerir faixa com IA (opcional)", key=f"btn_ref_ia_{paciente_dash_sel}_{param_sel}"):
                            prompt_ref = (
                                f"Forneça a faixa de referência saudável padrão na medicina veterinária ({nome_especie}) "
                                f"para o exame '{param_sel}'. Retorne APENAS um JSON no formato exato: "
                                '{"min": 10.5, "max": 25.0, "unid": "mg/dL"}'
                            )
                            try:
                                resp_ref = executar_ia_com_fallback(prompt_ref, json_mode=True)
                                st.session_state[ia_key] = {
                                    "min": float(para_numero(resp_ref.get("min")) or 0.0),
                                    "max": float(para_numero(resp_ref.get("max")) or 0.0),
                                    "unid": str(resp_ref.get("unid", "")),
                                }
                                st.rerun()
                            except Exception as e:
                                st.error(f"Não foi possível obter a faixa: {e}")

                tem_faixa = ref_valida(ref_min, ref_max)
                valor_atual = float(g["valor"].iloc[-1])
                valor_anterior = float(g["valor"].iloc[-2]) if len(g) > 1 else None
                data_atual_str = g["data"].iloc[-1].strftime(FORMATO_DATA_BR)
                texto_unidade = f" ({unid})" if unid.strip() else ""
                sufixo_gauge = f" {unid}" if unid.strip() else ""

                st.markdown("<br>", unsafe_allow_html=True)
                col_info, col_gauge = st.columns([1.2, 1], gap="medium")

                with col_info:
                    st.markdown("#### 🩺 Estado Atual")
                    if valor_anterior is not None:
                        st.metric(label=f"Exame de {data_atual_str}", value=f"{valor_atual:g}{sufixo_gauge}",
                                  delta=f"{valor_atual - valor_anterior:.2f} (vs anterior)")
                    else:
                        st.metric(label=f"Exame de {data_atual_str}", value=f"{valor_atual:g}{sufixo_gauge}")

                    situacao = classificar_valor(valor_atual, ref_min, ref_max)
                    if situacao == "sem_ref":
                        st.info("ℹ️ Ajuste a 'Faixa de Referência' acima para ver a análise automática.")
                    elif situacao == "abaixo":
                        st.error(f"**Atenção:** Valor abaixo do normal (Mín: {ref_min:g}) ⬇️")
                    elif situacao == "acima":
                        st.error(f"**Atenção:** Valor acima do normal (Máx: {ref_max:g}) ⬆️")
                    else:
                        st.success("**Excelente:** Valor dentro dos parâmetros saudáveis! ✅")

                with col_gauge:
                    if tem_faixa:
                        limite_max_grafico = max(ref_max * 1.3, valor_atual * 1.2)
                        passos = [
                            {'range': [0, ref_min], 'color': "#ffb7a1"},
                            {'range': [ref_min, ref_max], 'color': "#a1ffb7"},
                            {'range': [ref_max, limite_max_grafico], 'color': "#ff9494"},
                        ]
                    else:
                        limite_max_grafico = valor_atual * 1.5 if valor_atual > 0 else 1
                        passos = []
                    fig_gauge = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=valor_atual,
                        number={'suffix': sufixo_gauge, 'font': {'size': 26}},
                        gauge={
                            'axis': {'range': [0, limite_max_grafico], 'tickwidth': 1},
                            'bar': {'color': "rgba(0,0,0,0)"},
                            'steps': passos,
                            'threshold': {'line': {'color': "#1f77b4", 'width': 6}, 'thickness': 0.8, 'value': valor_atual},
                        }
                    ))
                    fig_gauge.update_layout(margin=dict(l=15, r=15, t=15, b=15), height=200)
                    st.plotly_chart(fig_gauge, use_container_width=True)

                st.markdown("---")
                st.markdown(f"#### 📈 Evolução de **{param_sel.upper()}**")
                fig_line = go.Figure(go.Scatter(
                    x=g["data"], y=g["valor"], mode="lines+markers",
                    line=dict(color="#2E86C1", width=4, shape="spline"),
                    marker=dict(size=10, color="#1B4F72", line=dict(width=2, color="white")),
                    fill="tozeroy", fillcolor="rgba(46, 134, 193, 0.15)",
                    hovertemplate="%{x|%d/%m/%Y}<br>%{y} " + unid + "<extra></extra>",
                ))
                if tem_faixa:
                    fig_line.add_hrect(
                        y0=ref_min, y1=ref_max, line_width=0, fillcolor="green", opacity=0.08,
                        annotation_text="Faixa Saudável", annotation_position="top left", annotation_font_color="green"
                    )
                fig_line.update_layout(
                    xaxis_title="", yaxis_title=f"Valor{texto_unidade}", margin=dict(l=10, r=10, t=30, b=10),
                    hovermode="x unified", xaxis=config_eixo_data(g["data"]),
                    yaxis=dict(showgrid=True, gridcolor="rgba(200, 200, 200, 0.2)"),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)"
                )
                st.plotly_chart(fig_line, use_container_width=True)

                st.markdown("---")
                tab_kpis, tab_dados, tab_ia = st.tabs(["📊 Máx & Mín", "📅 Tabela Bruta", "🧠 Ajuda da IA"])

                with tab_kpis:
                    col_max, col_min, col_media = st.columns(3)
                    col_max.metric("Máximo Histórico", f"{g['valor'].max():g}{sufixo_gauge}")
                    col_min.metric("Mínimo Histórico", f"{g['valor'].min():g}{sufixo_gauge}")
                    col_media.metric("Média Geral", f"{g['valor'].mean():.2f}{sufixo_gauge}")

                with tab_dados:
                    df_exibicao = g[["data", "valor"]].copy()
                    df_exibicao["data"] = df_exibicao["data"].dt.strftime(FORMATO_DATA_BR)
                    df_exibicao.columns = ["Data", f"Valor Registado{texto_unidade}"]
                    st.dataframe(df_exibicao, hide_index=True, use_container_width=True)

                with tab_ia:
                    st.markdown(f"**O que significa o parâmetro '{param_sel}'?**")
                    expl_key = f"expl_{param_sel}"
                    if expl_key not in st.session_state:
                        if st.button("Gerar Explicação Médica (usa IA)", use_container_width=True, key=f"btn_expl_{param_sel}"):
                            with st.spinner("A consultar literatura veterinária..."):
                                prompt_explica = (
                                    f"O utilizador está a ver o parâmetro sanguíneo/exame '{param_sel}' num dashboard veterinário. "
                                    "Explique de forma muito resumida (máximo 3 parágrafos curtos) o que é este parâmetro, "
                                    "qual a sua função no organismo do animal, e o que pode significar se estiver demasiado alto ou demasiado baixo."
                                )
                                try:
                                    st.session_state[expl_key] = executar_ia_com_fallback(prompt_explica, json_mode=False)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Erro ao gerar explicação: {e}")
                    else:
                        st.info(st.session_state[expl_key])

            # ---------- TABELA COMPLETA ----------
            with aba_tabela:
                tabela = df_ex.pivot_table(index="data", columns="parametro", values="valor", aggfunc="last").sort_index(ascending=False)
                tabela.index = [d.strftime(FORMATO_DATA_BR) for d in tabela.index]
                tabela.index.name = "Data"
                st.dataframe(tabela, use_container_width=True)
                st.download_button(
                    "📥 Baixar tabela (CSV para Excel)",
                    data=tabela.to_csv(sep=";", decimal=",").encode("utf-8-sig"),
                    file_name=f"exames_{paciente_dash_sel}.csv", mime="text/csv", use_container_width=True,
                )

# ==========================================
# SEPARADOR 5: CONSULTAS E RELATÓRIO DA IA
# ==========================================
with tab5:
    st.markdown("### 🩺 Histórico de Consultas Veterinárias")
    st.write("Registe o que foi falado nas consultas e gere um resumo inteligente com a IA (Consultas, Exames e Medicações).")
    
    with st.container(border=True):
        st.subheader("➕ Registar Nova Consulta")
        with st.form("form_consulta"):
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                data_consulta = st.date_input("🗓️ Data da Consulta", value=datetime.date.today(), format="DD/MM/YYYY")
            with col_c2:
                medico_consulta = st.text_input("👨‍⚕️ Veterinário / Clínica", value="Dr. Veterinário")
            
            detalhes_consulta = st.text_area("📝 O que foi dito na consulta?", height=120)
            btn_salvar_consulta = st.form_submit_button("💾 Guardar Consulta", use_container_width=True)
            
            if btn_salvar_consulta:
                if detalhes_consulta.strip():
                    if salvar_registro(nome_perfil, str(data_consulta), medico_consulta, "Consulta", detalhes_consulta, {}):
                        st.toast("Consulta registrada com sucesso!", icon="✅")
                        st.rerun()
                else:
                    st.warning("Descreva o que foi dito na consulta.")

    st.markdown("---")
    df_todas = carregar_historico()
    if not df_todas.empty:
        df_todas["_data_dt"] = converter_coluna_data(df_todas["data"])
    
    if not df_todas.empty:
        df_consultas = df_todas[
            (df_todas["paciente"] == nome_perfil) & 
            (df_todas["tipo_documento"].str.contains("Consulta", case=False, na=False))
        ].sort_values(by="_data_dt", ascending=False)
        
        if not df_consultas.empty:
            st.subheader(f"📋 Registo das Consultas ({len(df_consultas)})")
            for idx, row in df_consultas.iterrows():
                data_formatada = formatar_data_br(row['data'])
                    
                with st.expander(f"🗓️ {data_formatada} — {row['medico']}"):
                    st.markdown(f"**Relato:** {row['resumo']}")
            
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("### 🤖 Gerar Relatório Completo (IA)")
            st.write("A IA vai ler **Consultas**, **Exames Numéricos** e a aba de **Medicamentos** para criar o panorama geral.")
            
            if st.button("✨ Gerar/Atualizar Resumo do Histórico", type="primary", use_container_width=True):
                if tem_gemini or tem_groq:
                    with st.spinner("A analisar o perfil, exames, medicações e histórico com a IA..."):
                        try:
                            df_pet_completo = df_todas[df_todas["paciente"] == nome_perfil].sort_values(by="_data_dt", ascending=True)
                            
                            texto_historico = ""
                            for _, r in df_pet_completo.iterrows():
                                dt_fmt = formatar_data_br(r['data'])
                                    
                                texto_historico += f"- Data: {dt_fmt} | Tipo: {r['tipo_documento']} | Vet/Clínica: {r['medico']}\n"
                                texto_historico += f"  Detalhes/Doses: {r['resumo']}\n"
                                
                                params = r.get("parametros")
                                if isinstance(params, str):
                                    try:
                                        params = json.loads(params)
                                    except Exception:
                                        params = {}
                                
                                if isinstance(params, dict) and len(params) > 0:
                                    texto_historico += f"  Parâmetros do Exame: {resumir_parametros(params)}\n"
                                texto_historico += "\n"
                            
                            idade_pet = calcular_idade(data_nasc_input) if 'data_nasc_input' in locals() and data_nasc_input else "idade desconhecida"
                            raca_pet = raca_input if 'raca_input' in locals() and raca_input else "espécie desconhecida"

                            prompt_resumo = f"""
                            Você é um médico veterinário experiente. Analise o seguinte histórico médico do paciente e crie um relatório clínico geral, claro e estruturado.
                            
                            🐶 DADOS DO PACIENTE:
                            - Nome: {nome_perfil}
                            - Espécie/Raça: {raca_pet}
                            - Idade Atual: {idade_pet}
                            
                            🏥 HISTÓRICO MÉDICO REGISTADO:
                            {texto_historico}
                            
                            Com base nestes dados, crie um resumo do estado de saúde geral do paciente. 
                            Avalie a evolução dos exames tendo em conta os parâmetros ideais esperados para a raça ({raca_pet}) e a idade ({idade_pet}).
                            Destaque os principais pontos de atenção, alertas para parâmetros anormais e sugira recomendações gerais de acompanhamento.
                            Formate a resposta de forma bonita e profissional usando Markdown.
                            """
                            
                            st.session_state.resumo_consultas = executar_ia_com_fallback(prompt_resumo, json_mode=False)
                            st.toast("Relatório completo gerado!", icon="🩺")
                        except Exception as e:
                            st.error(f"Erro ao gerar resumo: {e}")
                else:
                    st.error("Nenhuma chave de IA configurada nos Secrets.")

            if st.session_state.get("resumo_consultas"):
                st.markdown("<br>", unsafe_allow_html=True)
                st.info(st.session_state.resumo_consultas)

# ==========================================
# SEPARADOR 6: MEDICAMENTOS
# ==========================================
with tab6:
    st.markdown("### 💊 Gestão de Medicamentos")
    st.write("Adicione remédios contínuos, desparasitantes ou tratamentos temporários para o pet.")
    
    with st.container(border=True):
        st.subheader("➕ Adicionar Medicamento / Tratamento")
        with st.form("form_medicamento"):
            col_med1, col_med2 = st.columns(2)
            with col_med1:
                nome_remedio = st.text_input("💊 Nome do Medicamento / Vacina / Desparasitante:", placeholder="Ex: Apoquel, Simparic, Amoxicilina")
                dosagem = st.text_input("📏 Dosagem e Frequência:", placeholder="Ex: 1 comprimido de 12h em 12h")
            with col_med2:
                tipo_uso = st.selectbox("🏷️ Tipo de Uso:", ["Tratamento Temporário", "Uso Contínuo", "Desparasitante / Antipulgas", "Vacina"])
                prescrito_por = st.text_input("👨‍⚕️ Prescrito Por / Clínica:", value="Veterinário Responsável")

            col_d1, col_d2 = st.columns(2)
            with col_d1:
                data_inicio = st.date_input("🗓️ Data de Início / Aplicação", value=datetime.date.today(), format="DD/MM/YYYY")
            with col_d2:
                duracao = st.text_input("⏳ Duração do Tratamento:", placeholder="Ex: 7 dias, Contínuo, Anual")

            obs_med = st.text_area("📝 Instruções / Observações:", placeholder="Ex: Dar com o alimento, conservar na frigorífico.")
            
            btn_salvar_med = st.form_submit_button("💊 Guardar Medicamento", use_container_width=True)

            if btn_salvar_med:
                if nome_remedio.strip():
                    resumo_formatado = f"Remédio: {nome_remedio} | Dose: {dosagem} | Uso: {tipo_uso} | Duração: {duracao} | Obs: {obs_med}"
                    if salvar_registro(nome_perfil, str(data_inicio), prescrito_por, "Medicamento", resumo_formatado, {}):
                        st.toast("Medicamento guardado no histórico!", icon="💊")
                        st.rerun()
                else:
                    st.warning("Por favor, introduza o nome do medicamento.")

    st.markdown("---")
    st.subheader(f"📋 Tratamentos Registados ({nome_perfil})")
    
    df_meds = carregar_historico()
    if not df_meds.empty:
        df_meds["_data_dt"] = converter_coluna_data(df_meds["data"])
    if not df_meds.empty:
        df_meds_filtrado = df_meds[
            (df_meds["paciente"] == nome_perfil) & 
            (df_meds["tipo_documento"].str.contains("Medicamento", case=False, na=False))
        ].sort_values(by="_data_dt", ascending=False)

        if not df_meds_filtrado.empty:
            for idx, row in df_meds_filtrado.iterrows():
                data_fmt = formatar_data_br(row['data'])

                with st.container(border=True):
                    col_m1, col_m2 = st.columns([4, 1.2])
                    with col_m1:
                        st.markdown(f"##### 💊 {data_fmt} — {row['medico']}")
                        st.markdown(f"{row['resumo']}")
                    with col_m2:
                        chk_del_med = st.checkbox("Confirmar", key=f"chk_med_{row['id']}")
                        if st.button("🗑️ Apagar", key=f"del_med_{row['id']}"):
                            if chk_del_med:
                                if excluir_registro(row['id']):
                                    st.toast("Medicamento apagado!", icon="🗑️")
                                    st.rerun()
                            else:
                                st.warning("Marque a caixa para confirmar.")
        else:
            st.info("Nenhum medicamento ou tratamento foi registado ainda.")
    else:
        st.info("O histórico está vazio.")

# ==========================================
# SEPARADOR 7: NUTRIÇÃO (guardada no Supabase)
# ==========================================
def analisar_rotulo_com_ia(imagem_bytes):
    """Uma única chamada de visão; o resultado fica em cache (mesma foto = zero chamadas novas)."""
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Chave GEMINI_API_KEY não encontrada nos Secrets.")
    chave = _chave_cache(PROMPT_ROTULO, True, hashlib.sha256(imagem_bytes).hexdigest())
    cache = _cache_ia()
    if chave in cache:
        return copy.deepcopy(cache[chave])
    img = Image.open(BytesIO(imagem_bytes))
    img.thumbnail((1600, 1600))
    resposta = genai.Client(api_key=api_key).models.generate_content(
        model=_modelo_gemini(),
        contents=[PROMPT_ROTULO, img],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    dados_rotulo = _carregar_json(resposta.text)
    _guardar_cache(chave, dados_rotulo)
    return dados_rotulo


def _num_dieta(valor):
    return float(para_numero(valor) or 0.0)


with tab7:
    st.markdown("### 🥩 Histórico Alimentar e Nutrição")
    st.write("Registe a dieta fotografando os **Níveis de Garantia** da embalagem da ração — ou preencha à mão, sem usar IA.")

    df_nutri_all = carregar_historico()
    lista_pacientes = []
    if not df_nutri_all.empty and "paciente" in df_nutri_all.columns:
        lista_pacientes = sorted({str(p).strip() for p in df_nutri_all["paciente"].dropna() if str(p).strip()})
    if nome_perfil not in lista_pacientes:
        lista_pacientes.insert(0, nome_perfil)

    paciente_nutri = st.selectbox("🐶 Selecione o Paciente:", lista_pacientes + ["Outro..."],
                                  index=lista_pacientes.index(nome_perfil), key="nutri_paciente")
    if paciente_nutri == "Outro...":
        paciente_nutri = st.text_input("Digite o nome do paciente:", key="nutri_paciente_outro").strip()
    if not paciente_nutri:
        st.info("Digite o nome do paciente para continuar.")
        st.stop()

    st.markdown("---")
    foto_upload = st.file_uploader("📂 Envie uma foto do rótulo (opcional)", type=["jpg", "jpeg", "png"], key="nutri_foto")

    col_ia, col_manual = st.columns(2)
    with col_ia:
        if foto_upload is not None:
            st.image(foto_upload, caption="Rótulo Carregado", use_container_width=True)
            if tem_gemini:
                if st.button("🧠 Ler rótulo com IA", type="primary", use_container_width=True):
                    with st.spinner("A ler o rótulo..."):
                        try:
                            st.session_state["dados_dieta_temp"] = analisar_rotulo_com_ia(foto_upload.getvalue())
                        except Exception as e:
                            st.error(f"Erro ao analisar a imagem: {e}")
            else:
                st.info("Sem GEMINI_API_KEY nos Secrets: preencha manualmente.")
    with col_manual:
        if st.button("✍️ Preencher manualmente (sem IA)", use_container_width=True):
            st.session_state["dados_dieta_temp"] = dict(DIETA_VAZIA)

    if "dados_dieta_temp" in st.session_state:
        dados_d = st.session_state["dados_dieta_temp"]
        marca_ia = str(dados_d.get("marca", "") or "")
        tipo_ia = str(dados_d.get("tipo", "")).lower()
        idx_tipo = 0 if "seca" in tipo_ia else (1 if ("mida" in tipo_ia or "umid" in tipo_ia) else 2)

        with st.container(border=True):
            st.subheader("⚙️ Rever e Confirmar a Dieta")
            with st.form("form_dieta"):
                c1, c2 = st.columns(2)
                marca = c1.text_input("🍲 Marca / Linha", value="" if "identificado" in marca_ia.lower() else marca_ia)
                tipo_racao = c2.selectbox("Tipo", ["Seca", "Húmida", "Outra"], index=idx_tipo)
                c3, c4, c5 = st.columns(3)
                prot = c3.number_input("Proteína bruta (%)", min_value=0.0, value=_num_dieta(dados_d.get("proteina_bruta_percentual")), step=0.1)
                gord = c4.number_input("Gordura / extrato etéreo (%)", min_value=0.0, value=_num_dieta(dados_d.get("extrato_etereo_percentual")), step=0.1)
                fosf = c5.number_input("Fósforo (%)", min_value=0.0, value=_num_dieta(dados_d.get("fosforo_percentual")), step=0.01)
                c6, c7 = st.columns(2)
                sodio = c6.number_input("Sódio (%)", min_value=0.0, value=_num_dieta(dados_d.get("sodio_percentual")), step=0.01)
                calcio = c7.number_input("Cálcio (%)", min_value=0.0, value=_num_dieta(dados_d.get("calcio_percentual")), step=0.01)
                indicacao = st.text_input("🎯 Indicação", value=str(dados_d.get("indicacao", "") or ""))
                ingredientes = st.text_input("🥣 Ingredientes principais", value=str(dados_d.get("ingredientes_principais", "") or ""))
                data_dieta = st.date_input("🗓️ Início desta dieta", value=datetime.date.today(), format="DD/MM/YYYY")
                salvar_dieta = st.form_submit_button("💾 Guardar no Histórico Alimentar", use_container_width=True, type="primary")

            if salvar_dieta:
                if not marca.strip():
                    st.warning("Informe a marca/linha da ração.")
                else:
                    parametros_dieta = {
                        "Proteína Bruta": {"valor": prot, "unidade": "%"},
                        "Gordura (Extrato Etéreo)": {"valor": gord, "unidade": "%"},
                        "Fósforo": {"valor": fosf, "unidade": "%"},
                        "Sódio": {"valor": sodio, "unidade": "%"},
                        "Cálcio": {"valor": calcio, "unidade": "%"},
                    }
                    resumo_dieta = (
                        f"Ração: {marca} ({tipo_racao}) | Proteína {prot:g}% | Gordura {gord:g}% | Fósforo {fosf:g}% | "
                        f"Sódio {sodio:g}% | Cálcio {calcio:g}% | Indicação: {indicacao or '—'} | Ingredientes: {ingredientes or '—'}"
                    )
                    if salvar_registro(paciente_nutri, data_dieta.isoformat(), "Rótulo da ração", "Nutrição", resumo_dieta, parametros_dieta):
                        st.session_state.pop("dados_dieta_temp", None)
                        st.toast(f"Dieta de {paciente_nutri} guardada no histórico!", icon="🥩")
                        st.rerun()
            if st.button("❌ Descartar", key="descartar_dieta"):
                st.session_state.pop("dados_dieta_temp", None)
                st.rerun()

    # ---------- HISTÓRICO ALIMENTAR ----------
    st.markdown("---")
    st.subheader(f"📋 Histórico alimentar de {paciente_nutri}")
    df_dietas = pd.DataFrame()
    if not df_nutri_all.empty and {"paciente", "tipo_documento"}.issubset(df_nutri_all.columns):
        df_dietas = df_nutri_all[
            (df_nutri_all["paciente"].astype(str).str.strip() == paciente_nutri)
            & (df_nutri_all["tipo_documento"].astype(str).str.contains("Nutri", case=False, na=False))
        ].copy()

    if df_dietas.empty:
        st.info("Nenhuma dieta registada ainda para este paciente.")
    else:
        df_dietas["_data_dt"] = converter_coluna_data(df_dietas["data"])
        df_dietas = df_dietas.sort_values("_data_dt", ascending=False)

        if len(df_dietas) >= 2:
            linhas_ev = []
            for _, rd in df_dietas.iterrows():
                for nome_p, bruto in _como_dict(rd.get("parametros")).items():
                    v, _, _, _ = extrair_valor_param(bruto)
                    if v is not None and rd["_data_dt"] is not pd.NaT:
                        linhas_ev.append({"data": rd["_data_dt"], "param": nome_p, "valor": v})
            df_ev = pd.DataFrame(linhas_ev)
            if not df_ev.empty:
                st.markdown("#### 📈 Evolução dos níveis de garantia")
                nomes_ev = ["Proteína Bruta", "Gordura (Extrato Etéreo)", "Fósforo"]
                fig_ev = make_subplots(rows=1, cols=3, subplot_titles=[f"{n} (%)" for n in nomes_ev], horizontal_spacing=0.08)
                for i, n in enumerate(nomes_ev):
                    gd = df_ev[df_ev["param"] == n].sort_values("data")
                    fig_ev.add_trace(go.Scatter(
                        x=gd["data"], y=gd["valor"], mode="lines+markers", showlegend=False,
                        hovertemplate="%{x|%d/%m/%Y}<br>%{y}%<extra></extra>",
                    ), row=1, col=i + 1)
                fig_ev.update_xaxes(tickformat="%d/%m/%y", nticks=3, showgrid=False)
                fig_ev.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10),
                                     plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_ev, use_container_width=True)

        for pos, (_, rd) in enumerate(df_dietas.iterrows()):
            with st.container(border=True):
                col_d1, col_d2 = st.columns([4, 1.2])
                with col_d1:
                    selo = " — **dieta atual**" if pos == 0 else ""
                    st.markdown(f"##### 🍲 {formatar_data_br(rd['data'])}{selo}")
                    st.markdown(str(rd["resumo"]))
                with col_d2:
                    chk_del = st.checkbox("Confirmar", key=f"chk_nutri_{rd['id']}")
                    if st.button("🗑️ Apagar", key=f"del_nutri_{rd['id']}"):
                        if chk_del:
                            if excluir_registro(rd["id"]):
                                st.toast("Dieta apagada!", icon="🗑️")
                                st.rerun()
                        else:
                            st.warning("Marque a caixa para confirmar.")
