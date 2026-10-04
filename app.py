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
            
            # Tratamento flexível da data (suporta ISO AAAA-MM-DD e DD-MM-AAAA)
            try:
                raw_data = dados.get("data", "")
                data_parsed = pd.to_datetime(raw_data, dayfirst=True, errors='coerce')
                data_padrao = data_parsed.date() if not pd.isna(data_parsed) else datetime.date.today()
            except Exception:
                data_padrao = datetime.date.today()

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
                        parametros_finais = json.loads(parametros_finais_txt)
                    except Exception:
                        parametros_finais = {}
                        
                    if salvar_registro(nome_paciente, str(data_final), medico_final, tipo_final, resumo_final, parametros_finais):
                        st.session_state.dados_ia = None
                        st.toast("Registo guardado com sucesso!", icon="🎉")
                        st.rerun()

# ==========================================
# SEPARADOR 2: HISTÓRICO
# ==========================================
# ==========================================
# SEPARADOR 2: HISTÓRICO (Com Edição e Exclusão)
# ==========================================
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
        
        # Normalização de datas para ordenação
        df['data_dt'] = pd.to_datetime(df['data'], errors='coerce', dayfirst=True)
        df_filtrado = df[df["paciente"] == paciente_sel].sort_values(by="data_dt", ascending=ordem_asc)
        
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
        
        csv = df_filtrado.drop(columns=['data_dt'], errors='ignore').to_csv(index=False).encode('utf-8')
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
                    file_name=f"backup_completo_{datetime.date.today().strftime('%d_%m_%Y')}.json",
                    mime="application/json",
                    use_container_width=True
                )
            
        st.markdown("---")

        if df_filtrado.empty:
            st.warning("Nenhum registo encontrado com essa palavra.")
        else:
            for idx, row in df_filtrado.iterrows():
                try:
                    data_dt_obj = pd.to_datetime(row['data'], dayfirst=True)
                    data_formatada = data_dt_obj.strftime('%d/%m/%Y')
                    data_padrao_edit = data_dt_obj.date()
                except Exception:
                    data_formatada = str(row['data'])
                    data_padrao_edit = datetime.date.today()

                with st.container(border=True):
                    col_texto, col_botoes = st.columns([4, 2])
                    
                    with col_texto:
                        st.subheader(f"🗓️ {data_formatada} - {row['tipo_documento']}")
                        st.markdown(f"**🏥 Clínica/Médico:** {row['medico']}")
                        st.markdown(f"**📝 Detalhes:** {row['resumo']}")
                        
                    with col_botoes:
                        st.write("") 
                        
                        # --- BOTÃO DE EDIÇÃO (POPOVER) ---
                        with st.popover("✏️ Editar", use_container_width=True):
                            st.markdown(f"#### Editar Registo #{row['id']}")
                            with st.form(key=f"form_edit_{row['id']}"):
                                nova_data = st.date_input("🗓️ Data", value=data_padrao_edit, format="DD/MM/YYYY", key=f"d_{row['id']}")
                                novo_tipo = st.text_input("📄 Tipo de Documento", value=str(row['tipo_documento']), key=f"t_{row['id']}")
                                novo_medico = st.text_input("👨‍⚕️ Médico / Clínica", value=str(row['medico']), key=f"m_{row['id']}")
                                novo_resumo = st.text_area("📝 Resumo / Detalhes", value=str(row['resumo']), height=100, key=f"r_{row['id']}")
                                
                                # Tratamento dos Parâmetros
                                params_atuais = row.get("parametros", {})
                                if isinstance(params_atuais, str):
                                    try:
                                        params_atuais = json.loads(params_atuais)
                                    except Exception:
                                        params_atuais = {}
                                
                                params_txt = json.dumps(params_atuais, ensure_ascii=False, indent=2)
                                novos_params_txt = st.text_area("📊 Parâmetros (JSON)", value=params_txt, key=f"p_{row['id']}")
                                
                                btn_salvar_edit = st.form_submit_button("💾 Guardar Alterações", use_container_width=True)
                                
                                if btn_salvar_edit:
                                    try:
                                        novos_params = json.loads(novos_params_txt)
                                    except Exception:
                                        novos_params = params_atuais
                                        
                                    # Chama a função de atualização (atualizar_registro)
                                    if atualizar_registro(row['id'], row['paciente'], str(nova_data), novo_medico, novo_tipo, novo_resumo, novos_params):
                                        st.toast("Registo atualizado com sucesso!", icon="✅")
                                        st.rerun()

                        # --- BOTÃO DE EXCLUSÃO ---
                        confirmar_del = st.checkbox("Confirmar", key=f"chk_{row['id']}")
                        if st.button("🗑️ Apagar", key=f"excluir_{row['id']}", help="Marque a caixa ao lado para apagar", use_container_width=True):
                            if confirmar_del:
                                if excluir_registro(row['id']):
                                    st.toast("Registo apagado!", icon="🗑️")
                                    st.rerun()
                            else:
                                st.warning("Marque 'Confirmar' para apagar.")
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
# SEPARADOR 4: DASHBOARD COM PARÂMETROS NORMAIS
# ==========================================
with tab4:
    st.markdown("### 📈 Painel Clínico Avançado")

    df_dash = carregar_historico()
    
    if not df_dash.empty and "parametros" in df_dash.columns:
        pacientes_dash = list(df_dash["paciente"].unique())
        paciente_dash_sel = st.selectbox("🐶 Selecione o Paciente:", pacientes_dash, key="dash_paciente")
        df_dash = df_dash[df_dash["paciente"] == paciente_dash_sel]
        
        df_dash['data'] = pd.to_datetime(df_dash['data'], errors='coerce', dayfirst=True)
        df_dash = df_dash.dropna(subset=['data'])
        
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
                    ref_sugerida = dicionario_referencias.get(param_selecionado, {})
                    unid = ref_sugerida.get("unid", "")
                    
                    original_min = float(ref_sugerida.get("min") or 0.0)
                    original_max = float(ref_sugerida.get("max") or 0.0)
                    
                    cache_key = f"ia_ref_{param_selecionado}"
                    
                    if original_min == 0.0 and original_max == 0.0:
                        if cache_key not in st.session_state:
                            with st.spinner(f"🤖 A IA está a procurar os valores de referência padrão para '{param_selecionado}'..."):
                                prompt_ref = f"Forneça a faixa de referência saudável padrão na medicina veterinária (cães/gatos) para o exame '{param_selecionado}'. Retorne APENAS um JSON no formato exato: {{\"min\": 10.5, \"max\": 25.0, \"unid\": \"mg/dL\"}}. Não inclua texto explicativo, formatação markdown ou crases, apenas o objeto JSON."
                                resposta_ia = executar_ia_com_fallback(prompt_ref, json_mode=True)
                                
                                try:
                                    if isinstance(resposta_ia, str):
                                        resposta_ia = resposta_ia.replace("```json", "").replace("```", "").strip()
                                        dados_ia = json.loads(resposta_ia)
                                    else:
                                        dados_ia = resposta_ia
                                        
                                    st.session_state[cache_key] = {
                                        "min": float(dados_ia.get("min", 0.0)),
                                        "max": float(dados_ia.get("max", 0.0)),
                                        "unid": dados_ia.get("unid", unid)
                                    }
                                except Exception:
                                    st.session_state[cache_key] = {"min": 0.0, "max": 0.0, "unid": unid}
                        
                        valores_atuais_ref = st.session_state[cache_key]
                        original_min = valores_atuais_ref["min"]
                        original_max = valores_atuais_ref["max"]
                        if not unid:
                            unid = valores_atuais_ref["unid"]
                    
                    with st.expander("⚙️ Ajustar Faixa de Referência"):
                        if cache_key in st.session_state and (original_min > 0 or original_max > 0):
                            st.caption("✨ Valores sugeridos automaticamente pela Inteligência Artificial.")
                            
                        col_ref1, col_ref2 = st.columns(2)
                        with col_ref1:
                            ref_min = st.number_input("Mínimo Saudável", value=original_min, step=0.1)
                        with col_ref2:
                            ref_max = st.number_input("Máximo Saudável", value=original_max, step=0.1)
                            
                    valor_atual = df_serie[param_selecionado].iloc[-1]
                    valor_anterior = df_serie[param_selecionado].iloc[-2] if len(df_serie) > 1 else None
                    data_atual_str = df_serie.index[-1].strftime('%d/%m/%Y')
                    
                    texto_unidade = f" ({unid})" if unid.strip() else ""
                    sufixo_gauge = f" {unid}" if unid.strip() else ""

                    st.markdown("<br>", unsafe_allow_html=True)
                    col_info, col_gauge = st.columns([1.2, 1], gap="medium")
                    
                    with col_info:
                        st.markdown("#### 🩺 Estado Atual")
                        if valor_anterior is not None:
                            st.metric(label=f"Exame de {data_atual_str}", value=f"{valor_atual}{sufixo_gauge}", delta=f"{valor_atual - valor_anterior:.2f} (vs anterior)")
                        else:
                            st.metric(label=f"Exame de {data_atual_str}", value=f"{valor_atual}{sufixo_gauge}")
                            
                        if ref_min == 0.0 and ref_max == 0.0:
                            st.info("ℹ️ Ajuste a 'Faixa de Referência' acima para ver a análise automática.")
                        elif valor_atual < ref_min:
                            st.error(f"**Atenção:** Valor abaixo do normal (Mín: {ref_min}) ⬇️")
                        elif valor_atual > ref_max:
                            st.error(f"**Atenção:** Valor acima do normal (Máx: {ref_max}) ⬆️")
                        else:
                            st.success("**Excelente:** Valor dentro dos parâmetros saudáveis! ✅")
                                
                    with col_gauge:
                        limite_max_grafico = max(ref_max * 1.3, valor_atual * 1.2) if ref_max > 0 else valor_atual * 1.5
                        fig_gauge = go.Figure(go.Indicator(
                            mode = "gauge+number",
                            value = valor_atual,
                            number = {'suffix': sufixo_gauge, 'font': {'size': 26}},
                            gauge = {
                                'axis': {'range': [0, limite_max_grafico], 'tickwidth': 1},
                                'bar': {'color': "rgba(0,0,0,0)"}, 
                                'steps': [
                                    {'range': [0, ref_min], 'color': "#ffb7a1"}, 
                                    {'range': [ref_min, ref_max], 'color': "#a1ffb7"}, 
                                    {'range': [ref_max, limite_max_grafico], 'color': "#ff9494"} 
                                ],
                                'threshold': {
                                    'line': {'color': "#1f77b4", 'width': 6},
                                    'thickness': 0.8,
                                    'value': valor_atual
                                }
                            }
                        ))
                        fig_gauge.update_layout(margin=dict(l=15, r=15, t=15, b=15), height=200)
                        st.plotly_chart(fig_gauge, use_container_width=True)

                    st.markdown("---")
                    st.markdown(f"#### 📈 Evolução de **{param_selecionado.upper()}**")
                    
                    fig_line = px.line(df_serie, x=df_serie.index, y=param_selecionado, markers=True)
                    fig_line.update_traces(
                        line=dict(color="#2E86C1", width=4, shape="spline"), 
                        marker=dict(size=10, color="#1B4F72", line=dict(width=2, color="white")),
                        fill='tozeroy', 
                        fillcolor="rgba(46, 134, 193, 0.15)"
                    )
                    
                    if ref_min < ref_max:
                        fig_line.add_hrect(
                            y0=ref_min, y1=ref_max, line_width=0, fillcolor="green", opacity=0.08,
                            annotation_text="Faixa Saudável", annotation_position="top left",
                            annotation_font_color="green"
                        )
                        
                    fig_line.update_layout(
                        xaxis_title="", 
                        yaxis_title=f"Valor{texto_unidade}", 
                        margin=dict(l=10, r=10, t=30, b=10),
                        hovermode="x unified",
                        xaxis=dict(showgrid=False),
                        yaxis=dict(showgrid=True, gridcolor="rgba(200, 200, 200, 0.2)"),
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)"
                    )
                    
                    st.plotly_chart(fig_line, use_container_width=True)

                    st.markdown("---")
                    tab_kpis, tab_dados, tab_ia = st.tabs(["📊 Máx & Mín", "📅 Tabela Bruta", "🧠 Ajuda da IA"])
                    
                    with tab_kpis:
                        col_max, col_min, col_media = st.columns(3)
                        col_max.metric(label="Máximo Histórico", value=f"{df_serie[param_selecionado].max()}{sufixo_gauge}")
                        col_min.metric(label="Mínimo Histórico", value=f"{df_serie[param_selecionado].min()}{sufixo_gauge}")
                        col_media.metric(label="Média Geral", value=f"{df_serie[param_selecionado].mean():.2f}{sufixo_gauge}")

                    with tab_dados:
                        df_exibicao = df_serie.copy()
                        df_exibicao.index = df_exibicao.index.strftime('%d/%m/%Y')
                        df_exibicao.columns = [f"Valor Registado{texto_unidade}"]
                        st.dataframe(df_exibicao, use_container_width=True)
                        
                    with tab_ia:
                        st.markdown(f"**O que significa o parâmetro '{param_selecionado}'?**")
                        if st.button("Gerar Explicação Médica", use_container_width=True):
                            with st.spinner("A consultar literatura veterinária..."):
                                prompt_explica = f"O utilizador está a ver o parâmetro sanguíneo/exame '{param_selecionado}' num dashboard veterinário. Explique de forma muito resumida (máximo 3 parágrafos curtos) o que é este parâmetro, qual a sua função no organismo do animal, e o que pode significar se estiver demasiado alto ou demasiado baixo."
                                explicacao = executar_ia_com_fallback(prompt_explica, json_mode=False)
                                st.info(explicacao)
                else:
                    st.warning("Não há dados numéricos suficientes para desenhar o gráfico deste parâmetro.")
        else:
            st.info("Nenhum parâmetro numérico foi extraído nos registos deste paciente ainda.")

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
        df_consultas = df_todas[
            (df_todas["paciente"] == nome_perfil) & 
            (df_todas["tipo_documento"].str.contains("Consulta", case=False, na=False))
        ].sort_values(by="data", ascending=False)
        
        if not df_consultas.empty:
            st.subheader(f"📋 Registo das Consultas ({len(df_consultas)})")
            for idx, row in df_consultas.iterrows():
                try:
                    data_formatada = pd.to_datetime(row['data'], dayfirst=True).strftime('%d/%m/%Y')
                except Exception:
                    data_formatada = str(row['data'])
                    
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
                                try:
                                    dt_fmt = pd.to_datetime(r['data'], dayfirst=True).strftime('%d/%m/%Y')
                                except Exception:
                                    dt_fmt = str(r['data'])
                                    
                                texto_historico += f"- Data: {dt_fmt} | Tipo: {r['tipo_documento']} | Vet/Clínica: {r['medico']}\n"
                                texto_historico += f"  Detalhes/Doses: {r['resumo']}\n"
                                
                                params = r.get("parametros")
                                if isinstance(params, str):
                                    try:
                                        params = json.loads(params)
                                    except Exception:
                                        params = {}
                                
                                if isinstance(params, dict) and len(params) > 0:
                                    texto_historico += f"  Parâmetros do Exame: {json.dumps(params, ensure_ascii=False)}\n"
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
        df_meds_filtrado = df_meds[
            (df_meds["paciente"] == nome_perfil) & 
            (df_meds["tipo_documento"].str.contains("Medicamento", case=False, na=False))
        ].sort_values(by="data", ascending=False)

        if not df_meds_filtrado.empty:
            for idx, row in df_meds_filtrado.iterrows():
                try:
                    data_fmt = pd.to_datetime(row['data'], dayfirst=True).strftime('%d/%m/%Y')
                except Exception:
                    data_fmt = str(row['data'])

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
# SEPARADOR 7: ASSISTENTE IA & DÚVIDAS VETERINÁRIAS
# ==========================================
with tab7:
    st.markdown("### 🥩 Histórico Alimentar e Nutrição")
    st.write("Registe a dieta do paciente fotografando os **Níveis de Garantia** da embalagem da ração.")
    
    # 1. Seleção do Paciente
    df_pacientes = carregar_historico()
    lista_pacientes = list(df_pacientes["paciente"].unique()) if not df_pacientes.empty else []
    
    paciente_nutri = st.selectbox("🐶 Selecione o Paciente:", lista_pacientes + ["Outro..."], key="nutri_paciente")
    if paciente_nutri == "Outro...":
        paciente_nutri = st.text_input("Digite o nome do paciente:")

    st.markdown("---")
    
    # 2. Captura da Imagem (Câmera ou Upload)
    col_cam, col_up = st.columns(2)
    with col_cam:
        foto_camera = st.camera_input("📸 Tirar foto do rótulo")
    with col_up:
        foto_upload = st.file_uploader("📂 Ou envie uma foto", type=["jpg", "jpeg", "png"])
        
    imagem_rotulo = foto_camera or foto_upload

    # 3. Processamento da IA (Visão)
    if imagem_rotulo is not None:
        st.image(imagem_rotulo, caption="Rótulo Capturado", use_container_width=True)
        
        if st.button("🧠 Analisar Composição Nutricional", type="primary", use_container_width=True):
            with st.spinner("A ler o rótulo e a procurar informações da marca..."):
                
                # Preparamos o prompt multimodal
                prompt_visao = """
                Aja como um nutrólogo veterinário. Leia esta imagem do rótulo de uma ração para pets.
                Extraia as seguintes informações e retorne EXCLUSIVAMENTE um JSON com esta estrutura:
                {
                    "marca": "Nome da marca ou linha (ex: Royal Canin Renal, Premier Ambientes Internos)",
                    "tipo": "Seca ou Húmida",
                    "proteina_bruta_percentual": 0.0,
                    "extrato_etereo_percentual": 0.0,
                    "fosforo_percentual": 0.0,
                    "sodio_percentual": 0.0,
                    "calcio_percentual": 0.0,
                    "ingredientes_principais": "Lista curta dos 3 primeiros ingredientes",
                    "indicacao": "Se houver (ex: Filhotes, Renal, Obesidade, etc)"
                }
                Se a imagem não for de um rótulo ou algum valor não estiver presente, use 0.0 para números e "Não identificado" para textos.
                """
                
                try:
                    import google.generativeai as genai
                    from PIL import Image
                    import json
                    
                    # Convertendo a imagem do Streamlit para o formato PIL que o Gemini aceita
                    img_pil = Image.open(imagem_rotulo)
                    
                    # Chamada direta ao modelo Gemini 1.5 Flash (o mais rápido para visão)
                    modelo_visao = genai.GenerativeModel('gemini-1.5-flash')
                    resposta_visao = modelo_visao.generate_content([prompt_visao, img_pil])
                    
                    # Limpeza do JSON (remove as crases que o markdown da IA às vezes adiciona)
                    texto_json = resposta_visao.text.replace("```json", "").replace("```", "").strip()
                    dados_dieta = json.loads(texto_json)
                    
                    # Guarda na memória temporária para a etapa de confirmação
                    st.session_state['dados_dieta_temp'] = dados_dieta
                    
                except Exception as e:
                    st.error(f"Erro ao analisar a imagem: {e}")
                    
    # 4. Confirmação e Registo
    if 'dados_dieta_temp' in st.session_state:
        st.success("✅ Rótulo lido com sucesso! Verifique os dados abaixo:")
        dados = st.session_state['dados_dieta_temp']
        
        # Exibição bonita num cartão
        with st.container(border=True):
            st.markdown(f"#### 🍲 {dados.get('marca', 'Marca Indefinida')}")
            col1, col2, col3 = st.columns(3)
            col1.metric("Proteína Bruta", f"{dados.get('proteina_bruta_percentual', 0)}%")
            col2.metric("Gordura (Extrato Etéreo)", f"{dados.get('extrato_etereo_percentual', 0)}%")
            col3.metric("Fósforo", f"{dados.get('fosforo_percentual', 0)}%")
            
            st.write(f"**Indicação:** {dados.get('indicacao', 'Geral')}")
            st.write(f"**Ingredientes base:** {dados.get('ingredientes_principais', 'N/A')}")
            
        if st.button("💾 Salvar Histórico Alimentar", use_container_width=True):
            # Salva na memória do paciente para a Análise Cruzada conseguir ler
            st.session_state[f"dieta_{paciente_nutri}"] = dados 
            
            st.balloons()
            st.success(f"Dieta de {paciente_nutri} atualizada no sistema!")
            del st.session_state['dados_dieta_temp'] # Limpa a tela após salvar
            st.rerun() # Dá refresh para voltar ao estado inicial
