import streamlit as st
import pandas as pd
import urllib.parse
from PIL import Image
from google import genai
from google.genai import types
import json
import datetime
from supabase import create_client, Client

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
supabase_url = st.secrets["SUPABASE_URL"]
supabase_key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(supabase_url, supabase_key)

def salvar_registro(paciente, data, medico, tipo_documento, resumo):
    try:
        dados = {
            "paciente": paciente,
            "data": data,
            "medico": medico,
            "tipo_documento": tipo_documento,
            "resumo": resumo
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
        
# Inicializa variável de memória para os dados temporários da IA
if "dados_ia" not in st.session_state:
    st.session_state.dados_ia = None

# ==========================================
# INTERFACE DO STREAMLIT
# ==========================================
st.set_page_config(page_title="Relatório do Mike", page_icon="🏥", layout="centered")

# Cabeçalho Moderno
col_titulo, col_logo = st.columns([4, 1])
with col_titulo:
    st.title("🏥 Relatório do Mike")
    st.markdown("*Assistente inteligente para o KinDim.*")

# Verificação segura da Chave do Gemini
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
else:
    api_key = ""
    st.error("⚠️ Falta a chave GEMINI_API_KEY nos Secrets.")

st.markdown("---")
tab1, tab2, tab3 = st.tabs(["📝 Novo Registo", "🗂️ Histórico", "⏰ Lembretes (Remédios)"])
#tab1, tab2 = st.tabs(["📝 Adicionar Novo Registo", "🗂️ Histórico"])

# ------------------------------------------
# SEPARADOR 1: NOVO REGISTO
# ------------------------------------------
with tab1:
    st.markdown("### 📸 Digitalizar Documento")
    nome_paciente = st.text_input("👤 Nome do Paciente:", value="Mike")
    
    # Área de upload com design mais limpo
    foto_upload = st.file_uploader("Arraste ou selecione a foto da receita/exame", type=["jpg", "jpeg", "png"])
    
    if foto_upload:
        # Mostra a imagem com cantos arredondados (usando as colunas para não ficar gigante)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            imagem = Image.open(foto_upload)
            st.image(imagem, caption="Documento Carregado", use_container_width=True)

    if foto_upload and api_key:
        if st.button("✨ Ler com Inteligência Artificial", use_container_width=True, type="primary"):
            with st.spinner("A analisar o documento médico (isto pode levar alguns segundos)..."):
                try:
                    client = genai.Client(api_key=api_key)
                    prompt = """
                    Analise este documento médico e extraia as informações estritamente em formato JSON:
                    {
                        "data": "AAAA-MM-DD",
                        "medico": "Nome do Médico",
                        "tipo_documento": "Receita, Exame, Atestado ou Consulta",
                        "resumo": "Resumo dos medicamentos receitados ou resultados"
                    }
                    Retorne APENAS o JSON.
                    """
                    response = client.models.generate_content(
                        model="gemini-3.6-flash",
                        contents=[imagem, prompt],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    st.session_state.dados_ia = json.loads(response.text)
                    st.toast("✅ Leitura concluída com sucesso!", icon="🤖")
                except Exception as e:
                    st.error(f"Erro na leitura: {e}")

    # Formulário Interativo em "Cartão"
    if st.session_state.dados_ia:
        st.markdown("<br>", unsafe_allow_html=True) # Espaçamento
        with st.container(border=True): # Cria uma borda em volta do formulário
            st.subheader("⚙️ Rever e Confirmar Dados")
            st.caption("A IA preencheu estes dados. Pode editá-los livremente antes de guardar.")
            
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
                
                medico_final = st.text_input("👨‍⚕️ Médico", value=dados.get("medico", ""))
                resumo_final = st.text_area("📝 Resumo / Medicamentos", value=dados.get("resumo", ""), height=100)
                
                confirmar = st.form_submit_button("☁️ Guardar na Nuvem", use_container_width=True)
                
                if confirmar:
                    salvar_registro(nome_paciente, str(data_final), medico_final, tipo_final, resumo_final)
                    st.session_state.dados_ia = None
                    st.toast("Registo guardado com sucesso!", icon="🎉")
                    st.rerun()


# ------------------------------------------
# SEPARADOR 3: LEMBRETES E TRATAMENTOS
# ------------------------------------------
with tab3:
    st.subheader("🐾 Lembretes para o Mike")
    st.write("Agende a troca da coleira, vacinas ou medicamentos.")

    with st.container(border=True):
        with st.form("form_lembrete"):
            item = st.text_input("O que o Mike precisa? (Ex: Coleira Seresto, Antibiótico, Vacina)")
            
            col_d, col_h = st.columns(2)
            with col_d:
                data_lembrete = st.date_input("🗓️ Data")
            with col_h:
                # Horário padrão ao meio-dia
                hora_lembrete = st.time_input("⏰ Horário", value=datetime.time(12, 0))
                
            notas = st.text_area("📝 Observações (Ex: Dar junto com a ração)")

            salvar_lembrete = st.form_submit_button("Criar Lembrete 🔔", use_container_width=True)

        if salvar_lembrete:
            if item:
                # 1. Guarda no banco de dados para ficar no histórico do Mike
                # Passamos "Veterinário/Casa" como médico e "Lembrete" como tipo
                salvar_registro(
                    nome_paciente, 
                    str(data_lembrete), 
                    "Veterinário / Casa", 
                    f"Lembrete: {item}", 
                    notas
                )
                
                # 2. Criação do Link Mágico para o Google Calendar
                # Transforma os textos para formato de link
                titulo = urllib.parse.quote(f"🐶 Cuidar do Mike: {item}")
                detalhes = urllib.parse.quote(notas)
                
                # Formata a data e hora para o padrão do Google Calendar (AAAAMMDDTHHMMSS)
                data_str = data_lembrete.strftime("%Y%m%d")
                hora_str = hora_lembrete.strftime("%H%M%S")
                inicio = f"{data_str}T{hora_str}"
                
                # Link oficial de agendamento do Google
                link_gcal = f"https://www.google.com/calendar/render?action=TEMPLATE&text={titulo}&dates={inicio}/{inicio}&details={detalhes}"
                
                st.toast("Lembrete salvo no histórico!", icon="✅")
                
                # Exibe um botão bonito para o utilizador clicar
                st.info("Registo guardado! Clique no botão abaixo para ativar o alarme no seu telemóvel:")
                st.markdown(f"""
                <a href="{link_gcal}" target="_blank" style="background-color:#4285F4; color:white; padding:10px 20px; text-decoration:none; border-radius:8px; display:block; text-align:center; font-weight:bold; font-size:16px;">
                📅 Adicionar Notificação ao Calendário
                </a>
                """, unsafe_allow_html=True)
            else:
                st.warning("Por favor, preencha o nome do medicamento ou coleira.")
# ------------------------------------------
# SEPARADOR 2: HISTÓRICO (O CADERNO)
# ------------------------------------------

with tab2:
    df = carregar_historico()
    
    if not df.empty:
        # --- PAINEL DE CONTROLO (DASHBOARD) ---
        pacientes = list(df["paciente"].unique())
        
        # Alinha a seleção de paciente e a busca na mesma linha
        col_filtro1, col_filtro2 = st.columns(2)
        with col_filtro1:
            paciente_sel = st.selectbox("👤 Selecione o Paciente:", pacientes)
        with col_filtro2:
            busca = st.text_input("🔍 Procurar (médico, remédio...):")
            
        # Filtra por paciente
        df_filtrado = df[df["paciente"] == paciente_sel].sort_values(by="data", ascending=False)
        
        # Aplica o filtro de busca se o utilizador escreveu algo
        if busca:
            df_filtrado = df_filtrado[
                df_filtrado['medico'].str.contains(busca, case=False, na=False) |
                df_filtrado['resumo'].str.contains(busca, case=False, na=False) |
                df_filtrado['tipo_documento'].str.contains(busca, case=False, na=False)
            ]

        # Métricas Rápidas
        st.markdown("<br>", unsafe_allow_html=True)
        col_met1, col_met2, col_met3 = st.columns(3)
        col_met1.metric("Total de Registos", len(df_filtrado))
        col_met2.metric("Médicos Consultados", df_filtrado['medico'].nunique())
        
        # Botão de Exportação no estilo moderno
        csv = df_filtrado.to_csv(index=False).encode('utf-8')
        with col_met3:
            st.download_button(
                label="📥 Exportar Excel (CSV)",
                data=csv,
                file_name=f"historico_{paciente_sel}.csv",
                mime="text/csv",
                use_container_width=True
            )
            
        st.markdown("---")

        # --- EXIBIÇÃO EM FORMATO DE CARTÕES ---
        if df_filtrado.empty:
            st.warning("Nenhum registo encontrado com essa palavra.")
        else:
            for idx, row in df_filtrado.iterrows():
                # O parâmetro border=True cria um cartão fechado elegante
                with st.container(border=True):
                    col_texto, col_botao = st.columns([5, 1])
                    
                    with col_texto:
                        st.subheader(f"🗓️ {row['data']} - {row['tipo_documento']}")
                        st.markdown(f"**👨‍⚕️ Médico:** {row['medico']}")
                        st.markdown(f"**📝 Detalhes:** {row['resumo']}")
                    
                    with col_botao:
                        st.write("") 
                        st.write("")
                        if st.button("🗑️ Excluir", key=f"excluir_{row['id']}", help="Apagar este registo permanentemente"):
                            excluir_registro(row['id'])
                            st.toast("Registo apagado!", icon="🗑️")
                            st.rerun() 
    else:
        st.info("O seu histórico ainda está vazio. Vá ao separador 'Novo Registo' e adicione o seu primeiro documento!")
