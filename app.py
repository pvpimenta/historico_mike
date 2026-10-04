import streamlit as st
import plotly.express as px
import pandas as pd
from PIL import Image
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

# Bibliotecas de extração local
import pdfplumber
import pytesseract

# Tenta importar fpdf2 para gerar PDF (se instalado)
try:
    from fpdf import FPDF
    FPDF_DISPONIVEL = True
except ImportError:
    FPDF_DISPONIVEL = False

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
supabase_url = st.secrets["SUPABASE_URL"]
supabase_key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(supabase_url, supabase_key)

# ==========================================
# PROVEDORES DE IA E SISTEMA DE FALLBACK
# ==========================================
# ==========================================
# PROVEDORES DE IA E SISTEMA DE FALLBACK
# ==========================================

def _chamar_gemini(prompt, json_mode=False):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Chave GEMINI_API_KEY não encontrada nos Secrets.")
    
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    
    response = client.models.generate_content(
        model="gemini-3.8-flash",  # Modelo estável e gratuito do Google
        contents=prompt,
        config=config
    )
    return response.text


def _chamar_groq(prompt, json_mode=False):
    api_key = st.secrets.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Chave GROQ_API_KEY não encontrada nos Secrets.")

    # Modelo atual recomendado pela Groq.
    # Também pode ser definido nos Secrets:
    # GROQ_MODEL = "openai/gpt-oss-120b"
    groq_model = st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b")

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key
    )

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        if "json" not in prompt.lower():
            prompt += "\\nResponda estritamente no formato JSON."

    response = client.chat.completions.create(
        model=groq_model,
        messages=[{"role": "user", "content": prompt}],
        timeout=30,
        **kwargs
    )

    return response.choices[0].message.content


def executar_ia_com_fallback(prompt, json_mode=False):
    """
    Ordem de execução:
    1. Gemini (Google - gemini-3.8-flash)
    2. Groq (OpenAI GPT-OSS 120B - Fallback)
    """
    provedores = [
        ("Gemini (Google gemini-3.8-flash)", _chamar_gemini),
        ("Groq (openai/gpt-oss-120b)", _chamar_groq)
    ]

    erros = []

    for nome_provedor, funcao_provedor in provedores:
        try:
            st.toast(f"Analisando com {nome_provedor}...", icon="🔄")
            texto_resposta = funcao_provedor(prompt, json_mode=json_mode)
            
            if json_mode:
                resultado = json.loads(texto_resposta)
            else:
                resultado = texto_resposta
                
            st.toast(f"✅ Sucesso via {nome_provedor}!", icon="🎉")
            return resultado
        except Exception as e:
            msg_erro = f"{nome_provedor}: {str(e)}"
            erros.append(msg_erro)
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
                if texto_pagina:
                    texto_completo += texto_pagina + "\n"
    except Exception as e:
        st.error(f"Erro ao ler PDF: {e}")
    return texto_completo

def extrair_texto_imagem(imagem):
    try:
        texto = pytesseract.image_to_string(imagem, lang='por')
        return texto
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
        return True
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")
        return False

def carregar_historico():
    try:
        resposta = supabase.table("historico").select("*").execute()
        if resposta.data:
            return pd.DataFrame(resposta.data)
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Erro ao carregar dados do Supabase: {e}")
        return pd.DataFrame()

def excluir_registro(id_registro):
    try:
        supabase.table("historico").delete().eq("id", id_registro).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao excluir do Supabase: {e}")
        return False

def gerar_backup_json():
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
    linhas.append(f"Período: {data_inicio.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')}")
    linhas.append(f"Data de Emissão: {datetime.date.today().strftime('%d/%m/%Y')}")
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
                linhas.append(f"🗓️ Data: {row['data']} | Tipo: {row['tipo_documento']}")
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
    
    for linha in texto_limpo.split('\n'):
        linha_str = linha.strip()
        if not linha_str:
            pdf.ln(3)
            continue
        if "RELATORIO CLINICO" in linha_str or "RELATÓRIO CLÍNICO" in linha_str:
            pdf.set_font("Helvetica", style="B", size=12)
            try:
                pdf.cell(pdf.epw, 7, linha_str, align="C", new_x="LMARGIN", new_y="NEXT")
            except Exception:
                pdf.cell(0, 7, linha_str, ln=True, align="C")
            pdf.set_font("Helvetica", size=10)
        elif "RESUMO CLINICO" in linha_str or "HISTORICO DE REGISTROS" in linha_str:
            pdf.ln(2)
            pdf.set_font("Helvetica", style="B", size=10)
            try:
                pdf.multi_cell(pdf.epw, 5, linha_str)
            except Exception:
                pdf.multi_cell(0, 5, linha_str)
            pdf.set_font("Helvetica", size=10)
        elif linha_str.startswith("===") or linha_str.startswith("---"):
            pdf.ln(1)
        else:
            try:
                pdf.multi_cell(pdf.epw, 5, linha_str)
            except Exception:
                pdf.multi_cell(0, 5, linha_str)
            
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
    """Calcula a idade em anos e meses com base na data de nascimento."""
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
        return True
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar perfil: {e}")
        return False

def carregar_perfil(nome_pet):
    try:
        res = supabase.table("perfil").select("*").eq("nome_pet", nome_pet).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return {}

# ==========================================
# INICIALIZAÇÃO DE ESTADO E PÁGINA
# ==========================================
if "dados_ia" not in st.session_state:
    st.session_state.dados_ia = None

if "resumo_consultas" not in st.session_state:
    st.session_state.resumo_consultas = None

st.set_page_config(page_title="Relatório do Pet", page_icon="🐕", layout="centered")

# ==========================================
# BARRA LATERAL (PERFIL)
# ==========================================
with st.sidebar:
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

    # Campo de Data de Nascimento e Contador de Idade
    data_nasc_input = st.date_input("🎂 Data de Nascimento", value=data_nasc_val,format="DD/MM/YYYY", key="data_nasc_input")
    
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

# ==========================================
# CONTEÚDO PRINCIPAL
# ==========================================
col_titulo, col_logo = st.columns([4, 1])
with col_titulo:
    st.title(f"🐶 Relatório do {nome_perfil}")
    st.markdown("*O diário inteligente de saúde do seu pet.*")

# Verifica disponibilidade das chaves
tem_gemini = "GEMINI_API_KEY" in st.secrets
tem_groq = "GROQ_API_KEY" in st.secrets

if not (tem_gemini or tem_groq):
    st.error("⚠️ Nenhuma chave de IA (GEMINI_API_KEY ou GROQ_API_KEY) foi encontrada nos Secrets.")

st.markdown("---")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📝 Adicionar Registo", 
    "🗂️ Histórico", 
    "⏰ Lembretes", 
    "📊 Dashboard", 
    "🩺 Consultas",
    "💊 Medicamentos"
])

# ------------------------------------------
# SEPARADOR 1: NOVO REGISTO
# ------------------------------------------
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
                    # 1. Extração Local
                    if arquivo_upload.type == "application/pdf":
                        texto_extraido = extrair_texto_pdf(arquivo_upload)
                    else:
                        texto_extraido = extrair_texto_imagem(imagem_carregada)

                    if not texto_extraido.strip():
                        st.warning("Não foi possível extrair nenhum texto legível desse arquivo.")
                    else:
                        # 2. Envia APENAS o texto com Fallback Automático
                        prompt = f"""
                        Você é um assistente veterinário. Leia o seguinte texto extraído (via OCR) de um documento médico:
                        
                        TEXTO EXTRAÍDO:
                        {texto_extraido}
                        
                        Extraia as informações estruturadas estritamente no seguinte formato JSON:
                        {{
                            "data": "AAAA-MM-DD",
                            "medico": "Nome do Médico ou Clínica",
                            "tipo_documento": "Receita, Exame, Atestado, Fatura ou Consulta",
                            "resumo": "Resumo detalhado dos medicamentos, resultados ou recomendações",
                            "parametros": {{"nome_do_parametro": valor_numerico}}
                        }}
                        Retorne APENAS o JSON válido, sem formatações adicionais.
                        """
                        
                        st.session_state.dados_ia = executar_ia_com_fallback(prompt, json_mode=True)
                        st.toast("✅ Leitura estruturada concluída!", icon="🤖")
                        
                except Exception as e:
                    st.error(f"Erro no processamento da IA: {e}")

    if st.session_state.dados_ia:
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.subheader("⚙️ Rever e Confirmar Dados")
            
            dados = st.session_state.dados_ia
            
            try:
                data_padrao = datetime.datetime.strptime(dados.get("data", ""), "%d-%m-%Y").date()
            except Exception:
                data_padrao = datetime.date.today()

            with st.form("form_confirmacao"):
                col_data, col_tipo = st.columns(2)
                with col_data:
                    data_final = st.date_input("🗓️ Data do Registo", value=data_padrao)
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
                        parametros_finais = json.loads(parametros_finais_txt)
                    except Exception:
                        parametros_finais = {}
                        
                    if salvar_registro(nome_paciente, str(data_final), medico_final, tipo_final, resumo_final, parametros_finais):
                        st.session_state.dados_ia = None
                        st.toast("Registo guardado com sucesso!", icon="🎉")
                        st.rerun()

# ------------------------------------------
# SEPARADOR 2: HISTÓRICO
# ------------------------------------------
with tab2:
    df = carregar_historico()
    
    if not df.empty:
        pacientes = list(df["paciente"].unique())
        
        col_filtro1, col_filtro2, col_filtro3 = st.columns([2, 2, 1.5])
        with col_filtro1:
            paciente_sel = st.selectbox("🐶 Selecione o Pet/Paciente:", pacientes)
        with col_filtro2:
            busca = st.text_input("🔍 Procurar:")
        with col_filtro3:
            ordem_ordem = st.selectbox("⏳ Ordem:", ["Cronológica (Mais antigo)", "Recentes Primeiro"])
            
        ordem_asc = (ordem_ordem == "Cronológica (Mais antigo)")
        df_filtrado = df[df["paciente"] == paciente_sel].sort_values(by="data", ascending=ordem_asc)
        
        if busca:
            df_filtrado = df_filtrado[
                df_filtrado['medico'].str.contains(busca, case=False, na=False) |
                df_filtrado['resumo'].str.contains(busca, case=False, na=False) |
                df_filtrado['tipo_documento'].str.contains(busca, case=False, na=False)
            ]

        st.markdown("<br>", unsafe_allow_html=True)
        col_met1, col_met2, col_met3 = st.columns(3)
        col_met1.metric("Registos", len(df_filtrado))
        col_met2.metric("Locais/Clínicas", df_filtrado['medico'].nunique())
        
        csv = df_filtrado.to_csv(index=False).encode('utf-8')
        json_backup = gerar_backup_json()
        
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
                    file_name=f"backup_completo_{datetime.date.today()}.json",
                    mime="application/json",
                    use_container_width=True
                )
            
        st.markdown("---")

        if df_filtrado.empty:
            st.warning("Nenhum registo encontrado com essa palavra.")
        else:
            for idx, row in df_filtrado.iterrows():
                # Converte a data para o formato DD/MM/AAAA
                try:
                    data_formatada = pd.to_datetime(row['data']).strftime('%d/%m/%Y')
                except:
                    data_formatada = row['data']

                with st.container(border=True):
                    col_texto, col_botao = st.columns([5, 1.5])
                    with col_texto:
                        # Usa a nova data_formatada aqui no subheader
                        st.subheader(f"🗓️ {data_formatada} - {row['tipo_documento']}")
                        st.markdown(f"**🏥 Clínica/Médico:** {row['medico']}")
                        st.markdown(f"**📝 Detalhes:** {row['resumo']}")
                    with col_botao:
                        st.write("") 
                        confirmar_del = st.checkbox("Confirmar", key=f"chk_{row['id']}")
                        if st.button("🗑️ Apagar", key=f"excluir_{row['id']}", help="Marque a caixa ao lado para apagar"):
                            if confirmar_del:
                                if excluir_registro(row['id']):
                                    st.toast("Registo apagado!", icon="🗑️")
                                    st.rerun()
                            else:
                                st.warning("Marque 'Confirmar' para apagar.")
    else:
        st.info("O histórico está vazio. Adicione um novo registo!")

# ------------------------------------------
# SEPARADOR 3: LEMBRETES
# ------------------------------------------
with tab3:
    st.subheader(f"🐾 Lembretes para o {nome_perfil}")
    st.write("Agende a troca da coleira, vacinas ou medicamentos.")

    with st.container(border=True):
        with st.form("form_lembrete"):
            item = st.text_input("O que o pet precisa? (Ex: Coleira Seresto, Vacina V10)")
            
            col_d, col_h = st.columns(2)
            with col_d:
                data_lembrete = st.date_input("🗓️ Data",format="DD/MM/YYYY")
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



# ------------------------------------------
# SEPARADOR 4: DASHBOARD COM PARÂMETROS NORMAIS
# ------------------------------------------
with tab4:
    st.markdown("### 📈 Painel Clínico Avançado")
    st.write("Acompanhe a evolução dos parâmetros com análise visual, limites de referência e ajuda da Inteligência Artificial.")
    
    df_dash = carregar_historico()
    
    if not df_dash.empty and "parametros" in df_dash.columns:
        pacientes_dash = list(df_dash["paciente"].unique())
        paciente_dash_sel = st.selectbox("🐶 Selecione o Paciente:", pacientes_dash, key="dash_paciente")
        df_dash = df_dash[df_dash["paciente"] == paciente_dash_sel]
        
        df_dash['data'] = pd.to_datetime(df_dash['data'])
        df_dash = df_dash.sort_values(by="data")
        
        lista_parametros_valores = []
        datas_validas = []
        dicionario_referencias = {} 
        
        for idx, row in df_dash.iterrows():
            params = row.get("parametros")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
                    
            if isinstance(params, dict) and len(params) > 0:
                valores_simples = {}
                for key, data_param in params.items():
                    if isinstance(data_param, dict) and "valor" in data_param:
                        valores_simples[key] = data_param["valor"]
                        dicionario_referencias[key] = {
                            "min": data_param.get("ref_min", 0.0),
                            "max": data_param.get("ref_max", 0.0),
                            "unid": data_param.get("unidade", "")
                        }
                    elif isinstance(data_param, (int, float)):
                        valores_simples[key] = data_param
                        if key not in dicionario_referencias:
                            dicionario_referencias[key] = {"min": 0.0, "max": 0.0, "unid": ""}
                
                if valores_simples:
                    lista_parametros_valores.append(valores_simples)
                    datas_validas.append(row["data"])
                
        if lista_parametros_valores:
            df_plot = pd.DataFrame(lista_parametros_valores)
            df_plot.index = pd.to_datetime(datas_validas)
            df_plot = df_plot.sort_index()
            
            colunas_disponiveis = df_plot.columns.tolist()
            param_selecionado = st.selectbox("🔬 Qual exame/parâmetro deseja analisar?", colunas_disponiveis)
            
            if param_selecionado:
                df_serie = df_plot[[param_selecionado]].dropna()
                
                if not df_serie.empty:
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    ref_sugerida = dicionario_referencias.get(param_selecionado, {})
                    unid = ref_sugerida.get("unid", "")
                    
                    with st.expander("⚙️ Ajustar Faixa de Referência (Opcional)"):
                        col_ref1, col_ref2 = st.columns(2)
                        with col_ref1:
                            ref_min = st.number_input("Valor Mínimo Saudável", value=float(ref_sugerida.get("min") or 0.0), step=0.1)
                        with col_ref2:
                            ref_max = st.number_input("Valor Máximo Saudável", value=float(ref_sugerida.get("max") or 0.0), step=0.1)
                    
                    valor_atual = df_serie[param_selecionado].iloc[-1]
                    valor_anterior = df_serie[param_selecionado].iloc[-2] if len(df_serie) > 1 else None
                    data_atual_str = df_serie.index[-1].strftime('%d/%m/%Y')
                    
                    # Alertas
                    estado_cor = "normal"
                    alerta_fora = ""
                    if ref_min < ref_max:
                        if valor_atual < ref_min:
                            estado_cor = "abaixo"
                            alerta_fora = " ⬇️ (Abaixo do Normal)"
                        elif valor_atual > ref_max:
                            estado_cor = "acima"
                            alerta_fora = " ⬆️ (Acima do Normal)"
                    
                    # KPIs principais
                    col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
                    with col_kpi1:
                        if valor_anterior is not None:
                            st.metric(label=f"Último: {data_atual_str}", value=f"{valor_atual} {unid}", delta=f"{valor_atual - valor_anterior:.2f} (vs anterior)")
                        else:
                            st.metric(label=f"Último: {data_atual_str}", value=f"{valor_atual} {unid}")
                    with col_kpi2:
                        st.metric(label="Máximo Histórico", value=f"{df_serie[param_selecionado].max()} {unid}")
                    with col_kpi3:
                        st.metric(label="Mínimo Histórico", value=f"{df_serie[param_selecionado].min()} {unid}")
                    
                    st.markdown("---")
                    
                    # Gráficos Lado a Lado: Linha (Histórico) e Velocímetro (Atual)
                    col_graf1, col_graf2 = st.columns([2, 1])
                    
                    with col_graf1:
                        fig_line = px.line(
                            df_serie, x=df_serie.index, y=param_selecionado, markers=True,
                            title=f"Evolução ao longo do tempo: <b>{param_selecionado.upper()}</b>"
                        )
                        fig_line.update_traces(line=dict(color="#2E86C1", width=3), marker=dict(size=8))
                        
                        if ref_min < ref_max:
                            fig_line.add_hrect(
                                y0=ref_min, y1=ref_max, line_width=0, fillcolor="green", opacity=0.15,
                                annotation_text="Saudável", annotation_position="top right"
                            )
                        fig_line.update_layout(xaxis_title="", yaxis_title=f"Valor {unid}", margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig_line, use_container_width=True)
                        
                    with col_graf2:
                        # Gráfico de Velocímetro (Gauge)
                        limite_max_grafico = max(ref_max * 1.3, valor_atual * 1.2) if ref_max > 0 else valor_atual * 1.5
                        fig_gauge = go.Figure(go.Indicator(
                            mode = "gauge+number",
                            value = valor_atual,
                            title = {'text': "Estado Atual", 'font': {'size': 16}},
                            number = {'suffix': f" {unid}", 'font': {'size': 24}},
                            gauge = {
                                'axis': {'range': [0, limite_max_grafico]},
                                'bar': {'color': "rgba(0,0,0,0)"}, # Esconde a barra padrão
                                'steps': [
                                    {'range': [0, ref_min], 'color': "#FFA07A"}, # Laranja (Abaixo)
                                    {'range': [ref_min, ref_max], 'color': "#90EE90"}, # Verde (Saudável)
                                    {'range': [ref_max, limite_max_grafico], 'color': "#FF6347"} # Vermelho (Acima)
                                ],
                                'threshold': {
                                    'line': {'color': "black", 'width': 4},
                                    'thickness': 0.75,
                                    'value': valor_atual
                                }
                            }
                        ))
                        fig_gauge.update_layout(margin=dict(l=20, r=20, t=40, b=20), height=300)
                        st.plotly_chart(fig_gauge, use_container_width=True)
                        if alerta_fora:
                            st.warning(f"**Atenção:** O valor atual encontra-se{alerta_fora.split('(')[1][:-1]}.")
                        else:
                            st.success("**Excelente:** O valor atual está dentro dos parâmetros normais.")

                    st.markdown("---")
                    
                    # Rodapé com Tabela de Dados e Ajuda da IA
                    col_tab, col_ia = st.columns([1, 1])
                    
                    with col_tab:
                        st.markdown("##### 📅 Tabela de Dados Brutos")
                        # Prepara tabela para exibir bonita
                        df_exibicao = df_serie.copy()
                        df_exibicao.index = df_exibicao.index.strftime('%d/%m/%Y')
                        df_exibicao.columns = [f"Valor Registado ({unid})"]
                        st.dataframe(df_exibicao, use_container_width=True)
                        
                    with col_ia:
                        st.markdown("##### 🧠 O que significa este parâmetro?")
                        if st.button(f"Explicar '{param_selecionado}' com IA", use_container_width=True):
                            with st.spinner("A consultar a literatura veterinária..."):
                                prompt_explica = f"O utilizador está a ver o parâmetro sanguíneo/exame '{param_selecionado}' num dashboard veterinário. Explique de forma muito resumida (máximo 3 parágrafos curtos) o que é este parâmetro, qual a sua função no organismo do animal, e o que pode significar se estiver demasiado alto ou demasiado baixo."
                                explicacao = executar_ia_com_fallback(prompt_explica, json_mode=False)
                                st.info(explicacao)
                else:
                    st.warning("Não há dados numéricos suficientes para desenhar o gráfico deste parâmetro.")
        else:
            st.info("Nenhum parâmetro numérico foi extraído nos registos deste paciente ainda.")

        
# ------------------------------------------
# SEPARADOR 5: CONSULTAS E RELATÓRIO DA IA
# ------------------------------------------
with tab5:
    st.markdown("### 🩺 Histórico de Consultas Veterinárias")
    st.write("Registe o que foi falado nas consultas e gere um resumo inteligente com a IA (Consultas, Exames e Medicações).")
    
    with st.container(border=True):
        st.subheader("➕ Registar Nova Consulta")
        with st.form("form_consulta"):
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                data_consulta = st.date_input("🗓️ Data da Consulta", value=datetime.date.today())
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
        df_consultas = df_todas[
            (df_todas["paciente"] == nome_perfil) & 
            (df_todas["tipo_documento"].str.contains("Consulta", case=False, na=False))
        ].sort_values(by="data", ascending=False)
        
        if not df_consultas.empty:
            st.subheader(f"📋 Registo das Consultas ({len(df_consultas)})")
            for idx, row in df_consultas.iterrows():
                # Converte a data para o formato DD/MM/AAAA
                try:
                    data_formatada = pd.to_datetime(row['data']).strftime('%d/%m/%Y')
                except:
                    data_formatada = row['data']
                    
                with st.expander(f"🗓️ {data_formatada} — {row['medico']}"):
                    st.markdown(f"**Relato:** {row['resumo']}")
            
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("### 🤖 Gerar Relatório Completo (IA)")
            st.write("A IA vai ler **Consultas**, **Exames Numéricos** e a aba de **Medicamentos** para criar o panorama geral.")
            
            if st.button("✨ Gerar/Atualizar Resumo do Histórico", type="primary", use_container_width=True):
                if tem_gemini or tem_groq:
                    with st.spinner("A analisar o perfil, exames, medicações e histórico com a IA..."):
                        try:
                            df_pet_completo = df_todas[df_todas["paciente"] == nome_perfil].sort_values(by="data", ascending=True)
                            
                            texto_historico = ""
                            for _, r in df_pet_completo.iterrows():
                                texto_historico += f"- Data: {r['data']} | Tipo: {r['tipo_documento']} | Vet/Clínica: {r['medico']}\n"
                                texto_historico += f"  Detalhes/Doses: {r['resumo']}\n"
                                
                                params = r.get("parametros")
                                if isinstance(params, str):
                                    try:
                                        params = json.loads(params)
                                    except:
                                        params = {}
                                
                                # ... (código anterior do for loop do texto_historico) ...
                                if isinstance(params, dict) and len(params) > 0:
                                    texto_historico += f"  Parâmetros do Exame: {json.dumps(params, ensure_ascii=False)}\n"
                                texto_historico += "\n"
                            
                            # 1. Definir os dados do pet usando as variáveis da Sidebar
                            idade_pet = calcular_idade(data_nasc_input) if data_nasc_input else "idade desconhecida"
                            raca_pet = raca_input if raca_input else "espécie desconhecida"

                            # 2. Criar o prompt_resumo focado no Relatório Clínico
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
                            
                            # 3. Executar a IA (Note que passamos prompt_resumo e json_mode=False)
                            st.session_state.resumo_consultas = executar_ia_com_fallback(prompt_resumo, json_mode=False)
                            st.toast("Relatório completo gerado!", icon="🩺")
                        except Exception as e:
                            st.error(f"Erro ao gerar resumo: {e}")
                else:
                    st.error("Nenhuma chave de IA configurada nos Secrets.")

            if st.session_state.resumo_consultas:
                st.markdown("<br>", unsafe_allow_html=True)
                st.info(st.session_state.resumo_consultas)

# ------------------------------------------
# SEPARADOR 6: MEDICAMENTOS (NOVA ABA)
# ------------------------------------------
with tab6:
    st.markdown("### 💊 Gestão de Medicamentos")
    st.write("Adicione remédios contínuos, desparasitantes ou tratamentos temporários.")
    
    with st.container(border=True):
        st.subheader("➕ Adicionar Novo Medicamento")
        with st.form("form_medicamento"):
            nome_med = st.text_input("Nome do Medicamento (Ex: Apoquel, Bravecto, Insulina)")
            
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                dose_med = st.text_input("Dose (Ex: 1 comprimido, 5ml, 2 UI)")
            with col_m2:
                freq_med = st.text_input("Frequência (Ex: A cada 12h, 1x ao mês)")
                
            data_inicio = st.date_input("Data de Início do Tratamento", value=datetime.date.today())
            
            if st.form_submit_button("Guardar Medicamento", use_container_width=True):
                if nome_med and dose_med:
                    resumo_med = f"Medicamento: {nome_med} | Dose: {dose_med} | Frequência: {freq_med}"
                    # Salvamos no banco de dados como tipo "Medicamento" para a IA conseguir filtrar e ler depois
                    salvar_registro(nome_perfil, str(data_inicio), "Prescrição / Casa", "Medicamento", resumo_med, {})
                    st.toast("Medicamento adicionado ao histórico!", icon="✅")
                    st.rerun()
                else:
                    st.warning("Por favor, preencha pelo menos o Nome e a Dose do medicamento.")
                    
    st.markdown("---")
    
    # Mostrar lista de medicamentos já guardados
    try:
        df_historico_med = carregar_historico()
        if not df_historico_med.empty:
            df_meds = df_historico_med[
                (df_historico_med["paciente"] == nome_perfil) & 
                (df_historico_med["tipo_documento"] == "Medicamento")
            ].sort_values(by="data", ascending=False)
            
            if not df_meds.empty:
                st.subheader(f"📋 Lista de Medicações Registadas ({len(df_meds)})")
                for _, row in df_meds.iterrows():
                    with st.container(border=True):
                        st.markdown(f"**🗓️ Início:** {row['data']}")
                        st.markdown(f"**💊 Detalhes:** {row['resumo']}")
            else:
                st.info("Ainda não há medicamentos registados para este pet.")
    except Exception as e:
        pass
        
      
            
        st.markdown("---")

        st.markdown("### 📄 Exportar Relatório para o Veterinário")
        st.write("Selecione o período e o conteúdo para gerar um documento pronto para enviar ao médico.")

        with st.container(border=True):
            col_dt1, col_dt2 = st.columns(2)
            with col_dt1:
                dt_inicio = st.date_input("🗓️ Data Inicial", value=datetime.date.today() - datetime.timedelta(days=90))
            with col_dt2:
                dt_fim = st.date_input("🗓️ Data Final", value=datetime.date.today())

            todo_historico = st.checkbox("📅 Selecionar Todo o Histórico (Ignorar Intervalo de Datas)")

            conteudo_opcao = st.radio(
                "O que deseja incluir no relatório?",
                ["Resumo da IA + Histórico Detalhado", "Apenas Resumo da IA", "Apenas Histórico Detalhado"],
                horizontal=True
            )

            df_pet = df_todas[df_todas["paciente"] == nome_perfil].copy()
            df_pet['data_dt'] = pd.to_datetime(df_pet['data'], errors='coerce').dt.date

            if not todo_historico:
                df_periodo = df_pet[(df_pet['data_dt'] >= dt_inicio) & (df_pet['data_dt'] <= dt_fim)].sort_values(by="data", ascending=True)
            else:
                df_periodo = df_pet.sort_values(by="data", ascending=True)

            incluir_resumo = "Resumo" in conteudo_opcao
            incluir_detalhes = "Histórico" in conteudo_opcao

            texto_exportacao = construir_texto_relatorio(
                nome_perfil,
                dt_inicio if not todo_historico else datetime.date(2000, 1, 1),
                dt_fim if not todo_historico else datetime.date.today(),
                incluir_resumo,
                incluir_detalhes,
                st.session_state.resumo_consultas,
                df_periodo
            )

            col_btn1, col_btn2 = st.columns(2)
            
            with col_btn1:
                st.download_button(
                    label="📝 Baixar em Texto (.txt)",
                    data=texto_exportacao.encode('utf-8'),
                    file_name=f"relatorio_vet_{nome_perfil}_{datetime.date.today()}.txt",
                    mime="text/plain",
                    use_container_width=True
                )

            with col_btn2:
                pdf_bytes = gerar_pdf_bytes(texto_exportacao)
                if pdf_bytes:
                    st.download_button(
                        label="📄 Baixar em PDF",
                        data=pdf_bytes,
                        file_name=f"relatorio_vet_{nome_perfil}_{datetime.date.today()}.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
                else:
                    st.info("Para ativar o download em PDF, adicione `fpdf2` ao arquivo `requirements.txt`.")
    else:
        st.info("Nenum dado encontrado no banco de dados.")
