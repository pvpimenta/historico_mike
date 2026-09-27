import streamlit as st
import pandas as pd
from PIL import Image
from google import genai
from google.genai import types
import json
import datetime
import urllib.parse
from supabase import create_client, Client

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
supabase_url = st.secrets["SUPABASE_URL"]
supabase_key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(supabase_url, supabase_key)

def salvar_registro(paciente, data, medico, tipo_documento, resumo, parametros):
    try:
        dados = {
            "paciente": paciente,
            "data": data,
            "medico": medico,
            "tipo_documento": tipo_documento,
            "resumo": resumo,
            "parametros": parametros # Nova coluna JSON
        }
        supabase.table("historico").insert(dados).execute()
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")

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
    except Exception as e:
        st.error(f"Erro ao excluir do Supabase: {e}")
        
if "dados_ia" not in st.session_state:
    st.session_state.dados_ia = None

# ==========================================
# INTERFACE DO STREAMLIT
# ==========================================
st.set_page_config(page_title="Relatório do Mike", page_icon="🐕", layout="centered")
# ==========================================
# BARRA LATERAL (PERFIL)
# ==========================================
with st.sidebar:
    st.title("🐾 Perfil do Pet")
    
    # Campo para o usuário definir de qual pet é o perfil atual
    nome_perfil = st.text_input("Nome do Paciente", value="Mike", key="nome_perfil")
    
    st.markdown("---")
    
    # Carrega a foto do banco de dados
    foto_b64 = carregar_foto_perfil(nome_perfil)
    
    if foto_b64:
        # Mostra a imagem com bordas arredondadas usando HTML/CSS
        st.markdown(
            f'<div style="display: flex; justify-content: center;">'
            f'<img src="data:image/jpeg;base64,{foto_b64}" style="width:180px; height:180px; border-radius:50%; object-fit:cover; border: 3px solid #f0f2f6;">'
            f'</div><br>', 
            unsafe_allow_html=True
        )
    else:
        st.info("Nenhuma foto de perfil encontrada. Faça o upload abaixo!")
        
    # Upload de nova foto
    nova_foto = st.file_uploader("Alterar foto de perfil", type=["jpg", "jpeg", "png"])
    if nova_foto:
        if st.button("💾 Salvar Nova Foto", use_container_width=True):
            img = Image.open(nova_foto)
            # Redimensiona a imagem para não pesar no banco de dados
            img.thumbnail((400, 400)) 
            if salvar_foto_perfil(nome_perfil, img):
                st.success("Foto atualizada com sucesso!")
                st.rerun() # Atualiza a página para mostrar a nova foto
# Cabeçalho Moderno
col_titulo, col_logo = st.columns([4, 1])
with col_titulo:
    st.title("🐶 Relatório do Mike")
    st.markdown("*O diário inteligente do Kim Dim.*")

if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
else:
    api_key = ""
    st.error("⚠️ Falta a chave GEMINI_API_KEY nos Secrets.")

st.markdown("---")

# ADICIONADO A 4ª GUIA: Dashboard
tab1, tab2, tab3, tab4 = st.tabs(["📝 Adicionar Registo", "🗂️ Histórico", "⏰ Lembretes", "📊 Dashboard"])

# ------------------------------------------
# SEPARADOR 1: NOVO REGISTO (COM PARÂMETROS)
# ------------------------------------------
with tab1:
    st.markdown("### 📸 Digitalizar Documento")
    nome_paciente = st.text_input("👤 Nome do Paciente / Pet:", value="Mike")
    
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
                    # NOVO PROMPT: instrução explícita para extrair parâmetros em formato de dicionário
                    prompt = """
                    Analise este documento médico e extraia as informações estritamente em formato JSON:
                    {
                        "data": "AAAA-MM-DD",
                        "medico": "Nome do Médico / Clínica",
                        "tipo_documento": "Receita, Exame, Atestado, Fatura ou Consulta",
                        "resumo": "Resumo detalhado dos medicamentos, resultados de exames ou recomendações",
                        "parametros": {"nome_do_parametro": valor_numerico} // Extraia métricas numéricas do exame como chave/valor. Ex: {"peso": 10.5, "ureia": 45, "glicemia": 90}
                    }
                    Retorne APENAS o JSON válido.
                    """
                    response = client.models.generate_content(
                        model="gemini-2.5-flash", # Atualizado modelo se desejar, ou mantenha gemini-1.5-flash/3.6
                        contents=[documento_ia, prompt],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    st.session_state.dados_ia = json.loads(response.text)
                    st.toast("✅ Leitura concluída com sucesso!", icon="🤖")
                except Exception as e:
                    st.error(f"Erro na leitura: {e}")

    # Formulário Interativo
    if st.session_state.dados_ia:
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.subheader("⚙️ Rever e Confirmar Dados")
            
            dados = st.session_state.dados_ia
            
            try:
                data_padrao = datetime.datetime.strptime(dados.get("data", ""), "%Y-%m-%d").date()
            except:
                data_padrao = datetime.date.today()

            with st.form("form_confirmacao"):
                col_data, col_tipo = st.columns(2)
                with col_data:
                    data_final = st.date_input("🗓️ Data do Registo", value=data_padrao)
                with col_tipo:
                    tipo_final = st.text_input("📄 Tipo de Documento", value=dados.get("tipo_documento", ""))
                
                medico_final = st.text_input("👨‍⚕️ Médico / Clínica", value=dados.get("medico", ""))
                resumo_final = st.text_area("📝 Resumo / Medicamentos", value=dados.get("resumo", ""), height=100)
                
                # Exibir e permitir edição dos parâmetros extraídos
                parametros_extraidos = dados.get("parametros", {})
                parametros_str = json.dumps(parametros_extraidos, ensure_ascii=False, indent=2)
                parametros_finais_txt = st.text_area("📊 Parâmetros Numéricos (JSON)", value=parametros_str, help="Corrija se a IA errou algum número.")
                
                confirmar = st.form_submit_button("☁️ Guardar na Nuvem", use_container_width=True)
                
                if confirmar:
                    try:
                        parametros_finais = json.loads(parametros_finais_txt)
                    except:
                        parametros_finais = {}
                        
                    salvar_registro(nome_paciente, str(data_final), medico_final, tipo_final, resumo_final, parametros_finais)
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
        with col_met3:
            st.download_button(
                label="📥 Baixar Histórico",
                data=csv,
                file_name=f"historico_{paciente_sel}.csv",
                mime="text/csv",
                use_container_width=True
            )
            
        st.markdown("---")

        if df_filtrado.empty:
            st.warning("Nenhum registo encontrado com essa palavra.")
        else:
            for idx, row in df_filtrado.iterrows():
                with st.container(border=True):
                    col_texto, col_botao = st.columns([5, 1])
                    with col_texto:
                        st.subheader(f"🗓️ {row['data']} - {row['tipo_documento']}")
                        st.markdown(f"**🏥 Clínica/Médico:** {row['medico']}")
                        st.markdown(f"**📝 Detalhes:** {row['resumo']}")
                    with col_botao:
                        st.write("") 
                        st.write("")
                        if st.button("🗑️", key=f"excluir_{row['id']}", help="Apagar este registo"):
                            excluir_registro(row['id'])
                            st.toast("Registo apagado!", icon="🗑️")
                            st.rerun() 
    else:
        st.info("O histórico está vazio. Adicione um novo registo!")

# ------------------------------------------
# SEPARADOR 3: LEMBRETES 
# ------------------------------------------
with tab3:
    st.subheader("🐾 Lembretes para o Mike")
    st.write("Agende a troca da coleira, vacinas ou medicamentos.")

    with st.container(border=True):
        with st.form("form_lembrete"):
            item = st.text_input("O que o Mike precisa? (Ex: Coleira Seresto, Vacina V10)")
            
            col_d, col_h = st.columns(2)
            with col_d:
                data_lembrete = st.date_input("🗓️ Data")
            with col_h:
                hora_lembrete = st.time_input("⏰ Horário", value=datetime.time(12, 0))
                
            notas = st.text_area("📝 Observações (Ex: Dar com a ração)")

            salvar_lembrete = st.form_submit_button("Criar Lembrete 🔔", use_container_width=True)

        if salvar_lembrete:
            if item:
                # Passando um dicionário vazio para os parâmetros neste caso
                salvar_registro("Mike", str(data_lembrete), "Veterinário / Casa", f"Lembrete: {item}", notas, {})
                
                titulo = urllib.parse.quote(f"🐶 Cuidar do Mike: {item}")
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
# SEPARADOR 4: DASHBOARD (GRÁFICOS)
# ------------------------------------------
with tab4:
    st.markdown("### 📈 Evolução dos Parâmetros Clínicos")
    st.write("Acompanhe os resultados dos exames ao longo do tempo.")
    
    df_dash = carregar_historico()
    
    if not df_dash.empty and "parametros" in df_dash.columns:
        # Puxa o paciente e filtra
        pacientes_dash = list(df_dash["paciente"].unique())
        paciente_dash_sel = st.selectbox("🐶 Selecione o Paciente:", pacientes_dash, key="dash_paciente")
        df_dash = df_dash[df_dash["paciente"] == paciente_dash_sel]
        
        # Converte as datas e ordena
        df_dash['data'] = pd.to_datetime(df_dash['data'])
        df_dash = df_dash.sort_values(by="data")
        
        # Extrai os parâmetros para um DataFrame à parte
        lista_parametros = []
        datas_validas = []
        
        for idx, row in df_dash.iterrows():
            params = row.get("parametros")
            # Se for string, transforma em dict
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except:
                    params = {}
                    
            if isinstance(params, dict) and len(params) > 0:
                lista_parametros.append(params)
                datas_validas.append(row["data"])
                
        if lista_parametros:
            # Cria DataFrame apenas com os parâmetros
            df_plot = pd.DataFrame(lista_parametros)
            df_plot.index = datas_validas
            
            # Pega as colunas (nomes dos exames/parâmetros)
            colunas_disponiveis = df_plot.columns.tolist()
            
            param_selecionado = st.selectbox("🔬 Qual exame/parâmetro deseja visualizar?", colunas_disponiveis)
            
            if param_selecionado:
                # Remove dados em branco para fazer o plot bonitinho
                df_serie = df_plot[param_selecionado].dropna()
                
                if not df_serie.empty:
                    st.line_chart(df_serie)
                else:
                    st.warning("Não há dados numéricos suficientes para desenhar o gráfico deste parâmetro.")
        else:
            st.info("Nenhum parâmetro numérico foi extraído nos registos deste paciente ainda.")
    else:
        st.info("O histórico está vazio ou a coluna 'parametros' não existe no banco de dados.")
