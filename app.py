import streamlit as st
import pandas as pd
from PIL import Image
from google import genai
from google.genai import types
import json
import sqlite3  # <--- Biblioteca nativa do Python para Banco de Dados

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SQLITE)
# ==========================================
# Cria (ou conecta) a um arquivo chamado 'prontuario.db' na sua pasta
conn = sqlite3.connect('prontuario.db', check_same_thread=False)
c = conn.cursor()


def criar_tabela():
    c.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente TEXT,
            data TEXT,
            medico TEXT,
            tipo_documento TEXT,
            resumo TEXT
        )
    ''')
    conn.commit()


def salvar_registro(paciente, data, medico, tipo_documento, resumo):
    c.execute('''
        INSERT INTO historico (paciente, data, medico, tipo_documento, resumo)
        VALUES (?, ?, ?, ?, ?)
    ''', (paciente, data, medico, tipo_documento, resumo))
    conn.commit()


def carregar_historico():
    # Retorna os dados direto do banco para um formato Pandas (tabela)
    return pd.read_sql('SELECT * FROM historico', conn)


# Inicializa o banco de dados
criar_tabela()

# ==========================================
# INTERFACE DO STREAMLIT
# ==========================================
st.set_page_config(page_title="Prontuário Inteligente", page_icon="🩺", layout="wide")

st.title("🩺 Prontuário Inteligente - Protótipo com Banco de Dados")
st.write("Envie uma foto da receita ou exame para organizar o histórico do paciente.")

st.sidebar.header("Configurações")
api_key = st.sidebar.text_input("Insira sua Gemini API Key:", type="password")

# Formulário de Envio
col1, col2 = st.columns([1, 1])

with col1:
    nome_paciente = st.text_input("Nome do Paciente:", value="Paciente Teste")
    foto_upload = st.file_uploader("Selecione a foto da receita/exame", type=["jpg", "jpeg", "png"])

    if foto_upload:
        imagem = Image.open(foto_upload)
        st.image(imagem, caption="Documento Carregado", use_container_width=True)

# Processamento da IA
if foto_upload and api_key and st.button("Analisar e Salvar no Banco"):
    with st.spinner("Analisando imagem com IA..."):
        try:
            client = genai.Client(api_key=api_key)

            prompt = """
            Analise este documento médico e extraia as informações estritamente em formato JSON:
            {
                "data": "AAAA-MM-DD (se não houver, use a data atual)",
                "medico": "Nome do Médico ou Não Identificado",
                "tipo_documento": "Receita, Exame, Atestado ou Consulta",
                "resumo": "Resumo dos medicamentos receitados ou resultados do exame"
            }
            Retorne APENAS o JSON, sem marcação de código ou texto adicional.
            """

            response = client.models.generate_content(
                model="gemini-3.6-flash",  # <--- MODELO ATUALIZADO
                contents=[imagem, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )

            # Extrai os dados do JSON da IA
            dados = json.loads(response.text)

            # SALVA NO BANCO DE DADOS
            salvar_registro(
                paciente=nome_paciente,
                data=dados.get("data", ""),
                medico=dados.get("medico", ""),
                tipo_documento=dados.get("tipo_documento", ""),
                resumo=dados.get("resumo", "")
            )

            st.success("Documento analisado e salvo permanentemente!")

        except Exception as e:
            st.error(f"Erro ao processar imagem: {e}")

# Exibição do Histórico / Calendário
with col2:
    st.subheader("📋 Histórico do Paciente")

    # Carrega os dados direto do SQLite
    df = carregar_historico()

    if not df.empty:
        # Filtro por paciente
        pacientes = list(df["paciente"].unique())
        paciente_sel = st.selectbox("Filtrar por Paciente:", pacientes)

        df_filtrado = df[df["paciente"] == paciente_sel]

        # Exibição organizada na tabela
        st.dataframe(
            df_filtrado[["data", "medico", "tipo_documento", "resumo"]],
            use_container_width=True,
            hide_index=True
        )

        st.subheader("📅 Linha do Tempo (Datas)")
        # Ordena pela data mais recente
        for idx, row in df_filtrado.sort_values(by="data", ascending=False).iterrows():
            with st.expander(f"{row['data']} - {row['tipo_documento']} ({row['medico']})"):
                st.write(f"**Médico:** {row['medico']}")
                st.write(f"**Resumo/Instruções:** {row['resumo']}")
    else:
        st.info("Nenhum registro adicionado ainda.")