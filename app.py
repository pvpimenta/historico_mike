import streamlit as st
import pandas as pd
from PIL import Image
from google import genai
from google.genai import types
import json
import datetime
import urllib.parse
import base64
from io import BytesIO
from supabase import create_client, Client

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
supabase_url = st.secrets["SUPABASE_URL"]
supabase_key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(supabase_url, supabase_key)

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
# FUNÇÕES DO BANCO DE DADOS (PERFIL/FOTO)
# ==========================================
def image_to_base64(image):
    buffered = BytesIO()
    image = image.convert("RGB")
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode()

def salvar_foto_perfil(nome_pet, imagem):
    img_base64 = image_to_base64(imagem)
    dados = {"nome_pet": nome_pet, "foto_base64": img_base64}
    try:
        res = supabase.table("perfil").select("*").eq("nome_pet", nome_pet).execute()
        if res.data:
            supabase.table("perfil").update({"foto_base64": img_base64}).eq("nome_pet", nome_pet).execute()
        else:
            supabase.table("perfil").insert(dados).execute()
        return True
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar foto: {e}")
        return False

def carregar_foto_perfil(nome_pet):
    try:
        res = supabase.table("perfil").select("foto_base64").eq("nome_pet", nome_pet).execute()
        if res.data:
            return res.data[0]["foto_base64"]
    except Exception:
        pass
    return None

# ==========================================
# INICIALIZAÇÃO DE ESTADO E PÁGINA
# ==========================================
if "dados_ia" not in st.session_state:
    st.session_state.dados_ia = None

if "resumo_consultas" not in st.session_state:
    st.session_state.resumo_consultas = None

st.set_page_config(page_title="Relatório do Mike", page_icon="🐕", layout="centered")

# ==========================================
# BARRA LATERAL (PERFIL)
# ==========================================
with st.sidebar:
    st.title("🐾 Perfil do Pet")
    
    nome_perfil = st.text_input("Nome do Paciente", value="Mike", key="nome_perfil")
    
    st.markdown("---")
    
    foto_b64 = carregar_foto_perfil(nome_perfil)
    
    if foto_b64:
        st.markdown(
            f'<div style="display: flex; justify-content: center;">'
            f'<img src="data:image/jpeg;base64,{foto_b64}" style="width:180px; height:180px; border-radius:50%; object-fit:cover; border: 3px solid #f0f2f6;">'
            f'</div><br>', 
            unsafe_allow_html=True
        )
    else:
        st.info("Nenhuma foto de perfil cadastrada. Envie uma abaixo!")
        
    nova_foto = st.file_uploader("Alterar foto de perfil", type=["jpg", "jpeg", "png"])
    if nova_foto:
        if st.button("💾 Salvar Nova Foto", use_container_width=True):
            img = Image.open(nova_foto)
            img.thumbnail((400, 400))
            if salvar_foto_perfil(nome_perfil, img):
                st.success("Foto atualizada!")
                st.rerun()

# ==========================================
# CONTEÚDO PRINCIPAL
# ==========================================
col_titulo, col_logo = st.columns([4, 1])
with col_titulo:
    st.title(f"🐶 Relatório do {nome_perfil}")
    st.markdown("*O diário inteligente de saúde do seu pet.*")

if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
else:
    api_key = ""
    st.error("⚠️ Falta a chave GEMINI_API_KEY nos Secrets.")

st.markdown("---")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📝 Adicionar Registo", 
    "🗂️ Histórico", 
    "⏰ Lembretes", 
    "📊 Dashboard", 
    "🩺 Consultas"
])

# ------------------------------------------
# SEPARADOR 1: NOVO REGISTO
# ------------------------------------------
with tab1:
    st.markdown("### 📸 Digitalizar Documento")
    nome_paciente = st.text_input("👤 Nome do Paciente / Pet:", value=nome_perfil)
    
    arquivo_upload = st.file_uploader("Arraste a foto ou ficheiro PDF do exame", type=["jpg", "jpeg", "png", "pdf"])
    
    documento_ia = None
    
    if arquivo_upload:
        if arquivo_upload.type == "application/pdf":
            st.info(f"📄 Ficheiro PDF carregado: {arquivo_upload.name}")
            documento_ia = types.Part.from_bytes(
                data=arquivo_upload.getvalue(),
                mime_type="application/pdf"
            )
        else:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                imagem = Image.open(arquivo_upload)
                st.image(imagem, caption="Documento Carregado", use_container_width=True)
                documento_ia = imagem

    if arquivo_upload and api_key:
        if st.button("✨ Ler com Inteligência Artificial", use_container_width=True, type="primary"):
            with st.spinner("A analisar o documento (pode demorar uns segundos)..."):
                try:
                    client = genai.Client(api_key=api_key)
                    prompt = """
                    Analise este documento médico e extraia as informações estritamente em formato JSON:
                    {
                        "data": "AAAA-MM-DD",
                        "medico": "Nome do Médico / Clínica",
                        "tipo_documento": "Receita, Exame, Atestado, Fatura ou Consulta",
                        "resumo": "Resumo detalhado dos medicamentos, resultados de exames ou recomendações",
                        "parametros": {"nome_do_parametro": valor_numerico}
                    }
                    Retorne APENAS o JSON válido.
                    """
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=[documento_ia, prompt],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    st.session_state.dados_ia = json.loads(response.text)
                    st.toast("✅ Leitura concluída com sucesso!", icon="🤖")
                except Exception as e:
                    st.error(f"Erro na leitura: {e}")

    if st.session_state.dados_ia:
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.subheader("⚙️ Rever e Confirmar Dados")
            
            dados = st.session_state.dados_ia
            
            try:
                data_padrao = datetime.datetime.strptime(dados.get("data", ""), "%Y-%m-%d").date()
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
        
        col_filtro1, col_filtro2 = st.columns(2)
        with col_filtro1:
            paciente_sel = st.selectbox("🐶 Selecione o Pet/Paciente:", pacientes)
        with col_filtro2:
            busca = st.text_input("🔍 Procurar:")
            
        df_filtrado = df[df["paciente"] == paciente_sel].sort_values(by="data", ascending=False)
        
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
                with st.container(border=True):
                    col_texto, col_botao = st.columns([5, 1.5])
                    with col_texto:
                        st.subheader(f"🗓️ {row['data']} - {row['tipo_documento']}")
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
                data_lembrete = st.date_input("🗓️ Data")
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
# SEPARADOR 4: DASHBOARD
# ------------------------------------------
with tab4:
    st.markdown("### 📈 Evolução dos Parâmetros Clínicos")
    st.write("Acompanhe os resultados dos exames ao longo do tempo.")
    
    df_dash = carregar_historico()
    
    if not df_dash.empty and "parametros" in df_dash.columns:
        pacientes_dash = list(df_dash["paciente"].unique())
        paciente_dash_sel = st.selectbox("🐶 Selecione o Paciente:", pacientes_dash, key="dash_paciente")
        df_dash = df_dash[df_dash["paciente"] == paciente_dash_sel]
        
        df_dash['data'] = pd.to_datetime(df_dash['data'])
        df_dash = df_dash.sort_values(by="data")
        
        lista_parametros = []
        datas_validas = []
        
        for idx, row in df_dash.iterrows():
            params = row.get("parametros")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
                    
            if isinstance(params, dict) and len(params) > 0:
                lista_parametros.append(params)
                datas_validas.append(row["data"])
                
        if lista_parametros:
            df_plot = pd.DataFrame(lista_parametros)
            df_plot.index = datas_validas
            
            colunas_disponiveis = df_plot.columns.tolist()
            param_selecionado = st.selectbox("🔬 Qual exame/parâmetro deseja visualizar?", colunas_disponiveis)
            
            if param_selecionado:
                df_serie = df_plot[param_selecionado].dropna()
                
                if not df_serie.empty:
                    st.line_chart(df_serie)
                else:
                    st.warning("Não há dados numéricos suficientes para desenhar o gráfico deste parâmetro.")
        else:
            st.info("Nenhum parâmetro numérico foi extraído nos registos deste paciente ainda.")
    else:
        st.info("O histórico está vazio ou a coluna 'parametros' não existe no banco de dados.")

# ------------------------------------------
# SEPARADOR 5: HISTÓRICO DE CONSULTAS (NOVO)
# ------------------------------------------
with tab5:
    st.markdown("### 🩺 Histórico de Consultas Veterinárias")
    st.write("Registe o que foi falado nas consultas e gere um resumo inteligente de toda a evolução médica.")
    
    # 1. Formulário para adicionar nova consulta
    with st.container(border=True):
        st.subheader("➕ Registar Nova Consulta")
        with st.form("form_consulta"):
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                data_consulta = st.date_input("🗓️ Data da Consulta", value=datetime.date.today())
            with col_c2:
                medico_consulta = st.text_input("👨‍⚕️ Veterinário / Clínica", value="Dr. Veterinário")
            
            detalhes_consulta = st.text_area(
                "📝 O que foi dito na consulta?", 
                placeholder="Ex: O pet apresentou episódios de vómito. O veterinário receitou Plasil por 3 dias e pediu exame de sangue.",
                height=120
            )
            
            btn_salvar_consulta = st.form_submit_button("💾 Guardar Consulta", use_container_width=True)
            
            if btn_salvar_consulta:
                if detalhes_consulta.strip():
                    if salvar_registro(nome_perfil, str(data_consulta), medico_consulta, "Consulta", detalhes_consulta, {}):
                        st.toast("Consulta registrada com sucesso!", icon="✅")
                        st.rerun()
                else:
                    st.warning("Por favor, descreva o que foi dito na consulta.")

    st.markdown("---")

    # 2. Carregar e exibir consultas anteriores
    df_todas = carregar_historico()
    
    if not df_todas.empty:
        # Filtra consultas do paciente atual
        df_consultas = df_todas[
            (df_todas["paciente"] == nome_perfil) & 
            (df_todas["tipo_documento"].str.contains("Consulta", case=False, na=False))
        ].sort_values(by="data", ascending=True) # Ordena em ordem cronológica
        
        if not df_consultas.empty:
            st.subheader(f"📋 Registo das Consultas ({len(df_consultas)})")
            
            # Mostra as consultas registradas
            for idx, row in df_consultas.sort_values(by="data", ascending=False).iterrows():
                with st.expander(f"🗓️ {row['data']} — {row['medico']}"):
                    st.markdown(f"**Relato:** {row['resumo']}")
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 3. Gerador de Resumo Clínico com IA (Gemini)
            st.markdown("### 🤖 Resumo do Histórico Clínico")
            st.write("Gere uma síntese inteligente de todas as consultas acumuladas até ao momento.")
            
            if st.button("✨ Gerar/Atualizar Resumo do Histórico", type="primary", use_container_width=True):
                if api_key:
                    with st.spinner("A analisar todo o histórico de consultas com a IA..."):
                        try:
                            # Prepara o texto com todo o histórico cronológico
                            texto_historico = ""
                            for _, r in df_consultas.iterrows():
                                texto_historico += f"- Data: {r['data']} | Vet/Clínica: {r['medico']}\n  Relato: {r['resumo']}\n\n"
                            
                            client = genai.Client(api_key=api_key)
                            prompt_resumo = f"""
                            Você é um assistente veterinário experiente. Analise o histórico cronológico de todas as consultas médicas do pet {nome_perfil} abaixo:

                            {texto_historico}

                            Elabore um resumo clínico claro, estruturado e fácil de ler para o tutor. Organize a resposta nos seguintes tópicos em Markdown:
                            1. 📌 **Visão Geral do Caso:** (Breve resumo da saúde geral do pet)
                            2. ⏱️ **Evolução Cronológica dos Sintomas/Sinais:** (O que mudou ao longo do tempo)
                            3. 💊 **Tratamentos e Medicamentos Citados:** (O que já foi prescrito/usado)
                            4. 💡 **Recomendações e Pontos de Atenção:** (Cuidados contínuos recomendados pelos médicos)
                            """
                            
                            response = client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=prompt_resumo
                            )
                            
                            st.session_state.resumo_consultas = response.text
                            st.toast("Resumo clínico atualizado!", icon="🩺")
                        except Exception as e:
                            st.error(f"Erro ao gerar resumo: {e}")
                else:
                    st.error("Chave GEMINI_API_KEY não configurada.")

            # Exibe o resumo se ele existir no estado da sessão
            if st.session_state.resumo_consultas:
                st.markdown("<br>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(st.session_state.resumo_consultas)
        else:
            st.info("Ainda não há nenhuma consulta cadastrada para este pet.")
    else:
        st.info("Nenhum dado encontrado no banco de dados.")
