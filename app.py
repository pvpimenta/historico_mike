import streamlit as st
import pandas as pd
from PIL import Image
from google import genai
from google.genai import types
import json
import datetime
from supabase import create_client, Client # <--- Nova biblioteca

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
# Ligar ao Supabase usando as chaves secretas
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
        return pd.DataFrame() # Retorna tabela vazia para não quebrar a página



def excluir_registro(id_registro):
    try:
        # Pede ao Supabase para apagar a linha onde o 'id' seja igual ao do botão clicado
        supabase.table("historico").delete().eq("id", id_registro).execute()
    except Exception as e:
        st.error(f"Erro ao excluir do Supabase: {e}")
        
# Inicializa variável de memória para os dados temporários da IA
if "dados_ia" not in st.session_state:
    st.session_state.dados_ia = None

# ==========================================
# INTERFACE DO STREAMLIT
# ==========================================
st.set_page_config(page_title="Caderno do Paciente", page_icon="📖", layout="centered")

st.title("📖 Caderno do Paciente (Nuvem)")

# Verificação segura da Chave do Gemini
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
else:
    api_key = ""
    st.error("⚠️ Falta a chave GEMINI_API_KEY nos Secrets.")

tab1, tab2 = st.tabs(["📝 Novo Registo", "🗂️ Histórico (O Caderno)"])

with tab1:
    st.write("Insira os dados e a foto para a IA preencher a ficha.")
    nome_paciente = st.text_input("Nome do Paciente:", value="Paciente Teste")
    foto_upload = st.file_uploader("Selecione a foto da receita/exame", type=["jpg", "jpeg", "png"])
    
    if foto_upload:
        imagem = Image.open(foto_upload)
        st.image(imagem, caption="Documento", width=300)

    if foto_upload and api_key and st.button("Ler com Inteligência Artificial"):
        with st.spinner("A ler documento..."):
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
                st.success("Leitura concluída! Por favor, reveja os dados abaixo.")
            except Exception as e:
                st.error(f"Erro: {e}")

    if st.session_state.dados_ia:
        st.markdown("---")
        st.subheader("⚙️ Rever e Confirmar")
        
        dados = st.session_state.dados_ia
        
        try:
            data_padrao = datetime.datetime.strptime(dados.get("data", ""), "%Y-%m-%d").date()
        except:
            data_padrao = datetime.date.today()

        with st.form("form_confirmacao"):
            st.info("Pode editar qualquer campo caso a IA não tenha lido corretamente.")
            
            data_final = st.date_input("Data do Registo", value=data_padrao)
            medico_final = st.text_input("Médico", value=dados.get("medico", ""))
            tipo_final = st.text_input("Tipo de Documento", value=dados.get("tipo_documento", ""))
            resumo_final = st.text_area("Resumo / Informações", value=dados.get("resumo", ""))
            
            confirmar = st.form_submit_button("✅ Guardar na Nuvem Permanente")
            
            if confirmar:
                salvar_registro(nome_paciente, str(data_final), medico_final, tipo_final, resumo_final)
                st.session_state.dados_ia = None
                st.success("Registo guardado com sucesso! Vá ao separador 'Histórico' para ver.")
                st.rerun()

# ------------------------------------------
# SEPARADOR 2: HISTÓRICO (O CADERNO)
# ------------------------------------------
with tab2:
    st.subheader("Consultar Registos")
    df = carregar_historico()
    
    if not df.empty:
        pacientes = list(df["paciente"].unique())
        paciente_sel = st.selectbox("Escolha o paciente:", pacientes)
        
        df_filtrado = df[df["paciente"] == paciente_sel].sort_values(by="data", ascending=False)
        
        for idx, row in df_filtrado.iterrows():
            with st.container():
                # Dividimos em duas colunas (uma maior para o texto, outra menor para o botão)
                col_texto, col_botao = st.columns([4, 1])
                
                with col_texto:
                    st.markdown(f"### 🗓️ {row['data']} - {row['tipo_documento']}")
                    st.markdown(f"**👨‍⚕️ Médico:** {row['medico']}")
                    st.markdown(f"**📝 Detalhes:** {row['resumo']}")
                
                with col_botao:
                    # st.write("") usado para empurrar o botão um pouco para baixo e alinhar
                    st.write("") 
                    st.write("")
                    # O "key" usa o ID do banco de dados para nunca apagar o item errado
                    if st.button("🗑️ Excluir", key=f"excluir_{row['id']}"):
                        excluir_registro(row['id'])
                        st.rerun() # Atualiza a página instantaneamente para fazer o item sumir
                        
                st.markdown("---")
    else:
        st.info("O caderno está vazio. Adicione um novo registo.")
