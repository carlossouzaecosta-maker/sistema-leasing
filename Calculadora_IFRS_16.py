import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import os
import hashlib
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import io

# --------------------------------------------------------------------------
# CONFIGURAÇÃO DE TELA E DIRETÓRIOS
# --------------------------------------------------------------------------
st.set_page_config(page_title="Sistema de Controle de Leasing - IFRS 16", layout="wide")
UPLOAD_DIR = "fotos_ativos"
os.makedirs(UPLOAD_DIR, exist_ok=True)
DB_NAME = "leasing_control.db"

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

# --------------------------------------------------------------------------
# DICIONÁRIO DE CONTAS PADRÃO DO SISTEMA
# --------------------------------------------------------------------------
STANDARD_ACCOUNTS = {
    "JUROS_DRE": ("4.1.2.01.005", "Despesa Financeira IFRS 16", "DRE - Despesa Financeira"),
    "DEPREC_DRE": ("3.1.3.01.010", "Despesa de Depreciação ROU", "DRE - Despesa Operacional"),
    "PASSIVO_CP": ("2.1.4.01.001", "Passivo de Arrendamento CP", "Passivo Circulante"),
    "PASSIVO_LP": ("2.2.2.01.001", "Passivo de Arrendamento LP", "Passivo Não Circulante"),
    "ATIVO_ROU": ("1.2.3.01.001", "Ativo Direito de Uso (ROU)", "Ativo Não Circulante"),
    "DEPREC_ACUM_ROU": ("1.2.3.02.001", "(-) Deprec. Acumulada ROU", "Ativo Não Circulante (Retificadora)"),
    "BANCO_CAIXA": ("1.1.1.02.001", "Banco Conta Movimento", "Ativo Circulante"),
    "GANHO_DRE": ("3.2.1.01.015", "Ganho na Rescisão/Remensuração IFRS 16 (DRE)", "DRE - Outras Receitas"),
    "PERDA_DRE": ("3.2.2.01.020", "Perda na Rescisão de Arrendamento (DRE)", "DRE - Outras Despesas"),
    "MULTA_DRE": ("3.2.2.01.025", "Despesa de Multas Contratuais (DRE)", "DRE - Despesas Operacionais")
}

# --------------------------------------------------------------------------
# BANCO DE DADOS & MIGRAÇÕES
# --------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Usuários
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        user_type TEXT NOT NULL,
        must_reset_password INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Permissões de empresas para usuários Externos
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        company_id INTEGER NOT NULL,
        UNIQUE(user_id, company_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )""")

    # Criação do Administrador Padrão caso não exista
    admin_email = "carloscosta@m1consultoria.com.br"
    admin_pw_hash = hash_password("@@M1consultoria123@@")
    cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO users (email, password_hash, user_type, must_reset_password)
            VALUES (?, ?, 'ADMIN', 0)
        """, (admin_email, admin_pw_hash))

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        economic_group TEXT NOT NULL,
        cnpj TEXT UNIQUE NOT NULL,
        company_name TEXT NOT NULL
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS company_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        standard_role TEXT NOT NULL,
        client_account_code TEXT,
        client_account_name TEXT,
        UNIQUE(company_id, standard_role),
        FOREIGN KEY(company_id) REFERENCES companies(id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS discount_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        effective_date DATE NOT NULL,
        annual_rate REAL NOT NULL,
        monthly_rate REAL NOT NULL,
        description TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        contract_code TEXT NOT NULL,
        contract_object TEXT NOT NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        grace_period_months INTEGER DEFAULT 0,
        initial_installment REAL NOT NULL,
        status TEXT DEFAULT 'ATIVO',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(company_id) REFERENCES companies(id)
    )""")

    cursor.execute("PRAGMA table_info(contracts)")
    col_dict = {row[1]: row[3] for row in cursor.fetchall()}
    if col_dict.get("asset_type") == 1:
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("""
            CREATE TABLE contracts_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                contract_code TEXT NOT NULL,
                contract_object TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                grace_period_months INTEGER DEFAULT 0,
                initial_installment REAL NOT NULL,
                status TEXT DEFAULT 'ATIVO',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id) REFERENCES companies(id)
            )
        """)
        cursor.execute("""
            INSERT INTO contracts_new (id, company_id, contract_code, contract_object, start_date, end_date, grace_period_months, initial_installment, created_at)
            SELECT id, company_id, contract_code, contract_object, start_date, end_date, grace_period_months, initial_installment, created_at
            FROM contracts
        """)
        cursor.execute("DROP TABLE contracts")
        cursor.execute("ALTER TABLE contracts_new RENAME TO contracts")
        cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contract_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL,
        asset_type TEXT NOT NULL,
        asset_serial_number TEXT,
        third_party_owner TEXT,
        asset_photo_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(contract_id) REFERENCES contracts(id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contract_modifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL,
        effective_month_index INTEGER NOT NULL,
        new_installment REAL NOT NULL,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP,
        FOREIGN KEY(contract_id) REFERENCES contracts(id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contract_terminations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL UNIQUE,
        termination_month_index INTEGER NOT NULL,
        termination_date DATE NOT NULL,
        penalty_amount REAL DEFAULT 0.0,
        reason TEXT,
        liability_written_off REAL NOT NULL,
        rou_written_off REAL NOT NULL,
        gain_loss_amount REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(contract_id) REFERENCES contracts(id)
    )""")

    conn.commit()

init_db()

# --------------------------------------------------------------------------
# CONTROLE DE SESSÃO E LOGIN
# --------------------------------------------------------------------------
if "logged_user" not in st.session_state:
    st.session_state.logged_user = None

if "selected_contract_id" not in st.session_state:
    st.session_state.selected_contract_id = None

def get_allowed_companies_query_filter(user_dict):
    if user_dict["user_type"] in ["ADMIN", "Interno"]:
        return "", []
    else:
        conn = get_db()
        comps = pd.read_sql_query("SELECT company_id FROM user_companies WHERE user_id = ?", conn, params=(user_dict["id"],))
        cids = comps["company_id"].tolist()
        if not cids:
            return " AND 1=0 ", []
        placeholders = ",".join(["?"] * len(cids))
        return f" AND comp.id IN ({placeholders}) ", cids

# --------------------------------------------------------------------------
# TELA DE LOGIN / PRIMEIRO ACESSO
# --------------------------------------------------------------------------
def render_login():
    col_l1, col_l2, col_l3 = st.columns([1, 1.3, 1])
    with col_l2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.title("🔒 Acesso ao Sistema de Leasing")
        st.caption("Controle e Mensuração CPC 06 (R2) / IFRS 16")
        
        login_tab1, login_tab2 = st.tabs(["🔑 Entrar no Sistema", "🆕 Primeiro Acesso / Criar Senha"])
        
        conn = get_db()
        with login_tab1:
            with st.form("form_login"):
                email = st.text_input("E-mail Cadastrado")
                senha = st.text_input("Senha", type="password")
                btn_entrar = st.form_submit_button("Entrar")
                
                if btn_entrar:
                    email_c = email.strip().lower()
                    u_row = conn.execute("SELECT id, email, password_hash, user_type, must_reset_password FROM users WHERE LOWER(email) = ?", (email_c,)).fetchone()
                    if not u_row:
                        st.error("E-mail não cadastrado no sistema. Contate o Administrador.")
                    else:
                        u_id, u_mail, u_hash, u_type, must_reset = u_row
                        if must_reset == 1 or not u_hash:
                            st.warning("Seu usuário necessita cadastrar uma nova senha. Por favor, acerte na aba 'Primeiro Acesso / Criar Senha'.")
                        else:
                            if u_hash == hash_password(senha):
                                st.session_state.logged_user = {
                                    "id": u_id,
                                    "email": u_mail,
                                    "user_type": u_type
                                }
                                st.success("Acesso autorizado com sucesso!")
                                st.rerun()
                            else:
                                st.error("Senha incorreta. Verifique suas credenciais.")

        with login_tab2:
            st.info("Caso seu usuário tenha acabado de ser cadastrado ou sua senha tenha sido resetada pelo administrador, defina sua senha abaixo:")
            with st.form("form_first_access"):
                email_f = st.text_input("Confirme seu E-mail")
                nova_senha = st.text_input("Nova Senha", type="password")
                conf_senha = st.text_input("Confirme a Nova Senha", type="password")
                btn_gravar_senha = st.form_submit_button("Cadastrar Nova Senha")
                
                if btn_gravar_senha:
                    email_fc = email_f.strip().lower()
                    u_row = conn.execute("SELECT id, user_type, must_reset_password FROM users WHERE LOWER(email) = ?", (email_fc,)).fetchone()
                    if not u_row:
                        st.error("E-mail não encontrado.")
                    elif u_row[2] == 0:
                        st.info("Sua senha já está ativa. Caso tenha esquecido, solicite o reset ao administrador.")
                    elif len(nova_senha) < 6:
                        st.error("A senha deve possuir pelo menos 6 caracteres.")
                    elif nova_senha != conf_senha:
                        st.error("As duas senhas digitadas não conferem. Verifique e tente novamente.")
                    else:
                        new_h = hash_password(nova_senha)
                        conn.execute("UPDATE users SET password_hash = ?, must_reset_password = 0 WHERE id = ?", (new_h, u_row[0]))
                        conn.commit()
                        st.success("Senha cadastrada com sucesso! Retorne à aba 'Entrar no Sistema' para efetuar o login.")

if not st.session_state.logged_user:
    render_login()
    st.stop()

# --------------------------------------------------------------------------
# BARRA LATERAL: INFORMAÇÕES DE SESSÃO & LOGOUT
# --------------------------------------------------------------------------
current_user = st.session_state.logged_user
st.sidebar.markdown(f"**👤 Usuário:** `{current_user['email']}`")
st.sidebar.caption(f"Perfil: **{current_user['user_type']}**")
if st.sidebar.button("🚪 Sair do Sistema"):
    st.session_state.logged_user = None
    st.session_state.selected_contract_id = None
    st.rerun()

st.sidebar.markdown("---")

# --------------------------------------------------------------------------
# RESOLUÇÃO DE CONTAS CONTÁBEIS (PADRÃO OU CLIENTE)
# --------------------------------------------------------------------------
def get_company_account_label(company_id: int, role: str):
    conn = get_db()
    res = conn.execute(
        "SELECT client_account_code, client_account_name FROM company_accounts WHERE company_id = ? AND standard_role = ?",
        (company_id, role)
    ).fetchone()
    
    std_code, std_name, _ = STANDARD_ACCOUNTS[role]
    if res and res[0] and res[0].strip() != "":
        code = res[0].strip()
        name = res[1].strip() if res[1] and res[1].strip() != "" else std_name
        return f"{code} - {name}"
    return f"{std_code} - {std_name}"

def get_rate_for_date_and_company(company_id: int, target_date: date):
    conn = get_db()
    df = pd.read_sql_query(
        """SELECT * FROM discount_rates 
           WHERE company_id = ? AND effective_date <= ? 
           ORDER BY effective_date DESC LIMIT 1""",
        conn, params=(company_id, target_date.strftime("%Y-%m-%d"))
    )
    if df.empty:
        df_gen = pd.read_sql_query(
            """SELECT * FROM discount_rates 
               WHERE (company_id IS NULL OR company_id = 0) AND effective_date <= ? 
               ORDER BY effective_date DESC LIMIT 1""",
            conn, params=(target_date.strftime("%Y-%m-%d"),)
        )
        if not df_gen.empty:
            row = df_gen.iloc[0]
            return row["annual_rate"], row["monthly_rate"]
        return 0.10, ((1 + 0.10) ** (1/12) - 1)
        
    row = df.iloc[0]
    return row["annual_rate"], row["monthly_rate"]

# --------------------------------------------------------------------------
# MOTOR FINANCEIRO E CONTÁBIL IFRS 16 / CPC 06 (R2) COM SEGREGAÇÃO CP/LP
# --------------------------------------------------------------------------
def calculate_contract_schedule(contract_id: int):
    conn = get_db()
    c = pd.read_sql_query("SELECT * FROM contracts WHERE id = ?", conn, params=(contract_id,)).iloc[0]
    comp_id = int(c["company_id"])
    mods = pd.read_sql_query(
        "SELECT * FROM contract_modifications WHERE contract_id = ? ORDER BY effective_month_index ASC", 
        conn, params=(contract_id,)
    )
    term_df = pd.read_sql_query("SELECT * FROM contract_terminations WHERE contract_id = ?", conn, params=(contract_id,))
    has_termination = not term_df.empty
    term_month = int(term_df.iloc[0]["termination_month_index"]) if has_termination else 999999

    start_dt = datetime.strptime(c["start_date"], "%Y-%m-%d").date()
    end_dt = datetime.strptime(c["end_date"], "%Y-%m-%d").date()
    
    r_delta = relativedelta(end_dt + relativedelta(days=1), start_dt)
    total_months = r_delta.years * 12 + r_delta.months
    if total_months <= 0:
        total_months = 1

    _, initial_monthly_rate = get_rate_for_date_and_company(comp_id, start_dt)
    grace = min(int(c["grace_period_months"]), total_months - 1)
    active_pmt = float(c["initial_installment"])
    
    # 1. Mensuração Inicial (VP inicial)
    pv_initial = 0.0
    for m in range(1, total_months + 1):
        pmt = 0.0 if m <= grace else active_pmt
        pv_initial += pmt / ((1 + initial_monthly_rate) ** m)
        
    liability_balance = pv_initial
    rou_balance = pv_initial
    current_deprec = rou_balance / total_months

    raw_schedule = []
    current_rate = initial_monthly_rate
    current_pmt = active_pmt

    for m in range(1, total_months + 1):
        ref_date = (start_dt + relativedelta(months=m)) - relativedelta(days=1)
        
        if m > term_month:
            raw_schedule.append({
                "Mês": m,
                "Data de Competência": ref_date.strftime("%d/%m/%Y"),
                "Contraprestação Devida (R$)": 0.0,
                "Despesa Financeira / Juros (R$)": 0.0,
                "Amortização do Principal (R$)": 0.0,
                "Passivo Inicial (R$)": 0.0,
                "Passivo Final (R$)": 0.0,
                "Depreciação ROU (R$)": 0.0,
                "Ativo Direito de Uso Líquido (R$)": 0.0,
                "Variação Remensuração Delta (R$)": 0.0,
                "Ganho Remensuração DRE (R$)": 0.0,
                "Taxa Efetiva Mensal (%)": current_rate * 100
            })
            continue

        _, new_market_rate = get_rate_for_date_and_company(comp_id, ref_date)
        taxa_mudou = abs(new_market_rate - current_rate) > 1e-6
        
        mod_rows = mods[mods["effective_month_index"] == m]
        pmt_mudou = not mod_rows.empty
        
        delta_remensuracao = 0.0
        ganho_dre_remensuracao = 0.0

        # EVENTO DE REMENSURAÇÃO
        if (pmt_mudou or taxa_mudou) and m <= total_months and m <= term_month:
            if pmt_mudou:
                current_pmt = float(mod_rows.iloc[-1]["new_installment"])
            if taxa_mudou:
                current_rate = new_market_rate
                
            rem_months = total_months - m + 1
            new_pv = sum(current_pmt / ((1 + current_rate) ** t) for t in range(1, rem_months + 1))
            delta_remensuracao = new_pv - liability_balance
            
            if delta_remensuracao < 0 and abs(delta_remensuracao) > rou_balance:
                ganho_dre_remensuracao = abs(delta_remensuracao) - rou_balance
                rou_balance = 0.0
                current_deprec = 0.0
            else:
                rou_balance = max(0.0, rou_balance + delta_remensuracao)
                current_deprec = rou_balance / rem_months if rem_months > 0 else 0.0

            liability_balance = new_pv

        pmt_devida = 0.0 if m <= grace else current_pmt
        interest = liability_balance * current_rate
        amortization = pmt_devida - interest
        ending_liability = max(0.0, liability_balance - amortization)
        
        depreciation = current_deprec
        ending_rou = max(0.0, rou_balance - depreciation)

        if m == term_month:
            final_liability_recorded = 0.0
            final_rou_recorded = 0.0
        else:
            final_liability_recorded = ending_liability
            final_rou_recorded = ending_rou

        raw_schedule.append({
            "Mês": m,
            "Data de Competência": ref_date.strftime("%d/%m/%Y"),
            "Contraprestação Devida (R$)": pmt_devida,
            "Despesa Financeira / Juros (R$)": interest,
            "Amortização do Principal (R$)": amortization,
            "Passivo Inicial (R$)": liability_balance,
            "Passivo Final (R$)": final_liability_recorded,
            "Depreciação ROU (R$)": depreciation,
            "Ativo Direito de Uso Líquido (R$)": final_rou_recorded,
            "Variação Remensuração Delta (R$)": delta_remensuracao,
            "Ganho Remensuração DRE (R$)": ganho_dre_remensuracao,
            "Taxa Efetiva Mensal (%)": current_rate * 100
        })

        liability_balance = ending_liability
        rou_balance = ending_rou

    # 2. Segregação Contábil Automática de Curto Prazo (CP) e Longo Prazo (LP)
    df_sched = pd.DataFrame(raw_schedule)
    cp_list = []
    lp_list = []
    total_linhas = len(df_sched)

    for i in range(total_linhas):
        passivo_final_mes = df_sched.iloc[i]["Passivo Final (R$)"]
        if passivo_final_mes <= 0.0:
            cp_list.append(0.0)
            lp_list.append(0.0)
        else:
            # Soma das amortizações dos próximos 12 meses
            prox_12_amort = df_sched.iloc[i+1 : min(i+13, total_linhas)]["Amortização do Principal (R$)"].sum()
            cp_val = min(passivo_final_mes, max(0.0, prox_12_amort))
            lp_val = max(0.0, passivo_final_mes - cp_val)
            cp_list.append(cp_val)
            lp_list.append(lp_val)

    df_sched["Passivo Circulante - CP (R$)"] = cp_list
    df_sched["Passivo Não Circulante - LP (R$)"] = lp_list

    return df_sched

# --------------------------------------------------------------------------
# MENU DO SISTEMA (CONFORME PERFIL DE ACESSO)
# --------------------------------------------------------------------------
menu_options = [
    "Painel Geral & Relatórios", 
    "Lançamentos Contábeis (Período / ERP)",
    "Notas Explicativas (Auditoria IFRS 16)",
    "Demonstrativo LALUR / LACS (Lucro Real)",
    "Rescisão & Baixa de Contrato",
    "Reajustes & Aditivos Contratuais",
    "Cadastro de Contratos", 
    "Ativos do Contrato (Patrimonial)", 
    "Cadastro de Empresas", 
    "Parâmetros de Taxas (IBR)"
]

if current_user["user_type"] == "ADMIN":
    menu_options.append("👥 Gestão de Usuários")

menu = st.sidebar.radio("Navegação do Sistema", menu_options)

# ==========================================================================
# TELA: GESTÃO DE USUÁRIOS (EXCLUSIVO ADMINISTRADOR)
# ==========================================================================
if menu == "👥 Gestão de Usuários":
    st.title("👥 Gestão de Usuários e Permissões")
    st.info("Cadastre novos usuários com perfil **Interno** (acesso global a todas as empresas) ou **Externo** (acesso restrito às empresas selecionadas). Quando o usuário esquecer a senha, clique em 'Resetar Senha'.")

    conn = get_db()
    tab_novousuario, tab_listar = st.tabs(["➕ Cadastrar Novo Usuário", "📋 Usuários Cadastrados & Reset"])

    with tab_novousuario:
        col_u1, col_u2 = st.columns([1.2, 1.5])
        with col_u1:
            with st.form("form_create_user"):
                new_email = st.text_input("E-mail de Login do Usuário")
                new_type = st.selectbox("Tipo de Acesso", ["Interno", "Externo"])
                
                comps_all = pd.read_sql_query("SELECT id, company_name || ' (' || cnpj || ')' as label FROM companies ORDER BY company_name", conn)
                selected_comps = []
                if new_type == "Externo":
                    st.caption("Selecione as empresas que este usuário poderá visualizar e gerenciar:")
                    selected_comps = st.multiselect("Empresas Vinculadas", comps_all["label"].tolist())

                btn_add_user = st.form_submit_button("Criar Usuário")
                if btn_add_user:
                    email_c = new_email.strip().lower()
                    if not email_c or "@" not in email_c:
                        st.error("Informe um e-mail válido.")
                    elif new_type == "Externo" and not selected_comps:
                        st.error("Para usuários externos, selecione ao menos uma empresa vinculada.")
                    else:
                        try:
                            # Cria o usuário sem senha com must_reset_password=1
                            conn.execute("""
                                INSERT INTO users (email, password_hash, user_type, must_reset_password)
                                VALUES (?, NULL, ?, 1)
                            """, (email_c, new_type))
                            new_uid = conn.execute("SELECT id FROM users WHERE LOWER(email) = ?", (email_c,)).fetchone()[0]

                            if new_type == "Externo":
                                for comp_lbl in selected_comps:
                                    cid = int(comps_all[comps_all["label"] == comp_lbl]["id"].values[0])
                                    conn.execute("INSERT INTO user_companies (user_id, company_id) VALUES (?, ?)", (new_uid, cid))

                            conn.commit()
                            st.success(f"Usuário {email_c} criado com sucesso! No primeiro acesso, o sistema solicitará o cadastro de senha.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Este e-mail já está cadastrado no sistema.")

    with tab_listar:
        st.subheader("Usuários Ativos no Sistema")
        users_df = pd.read_sql_query("SELECT id, email, user_type, must_reset_password, created_at FROM users ORDER BY id ASC", conn)
        
        for idx, u in users_df.iterrows():
            c_u1, c_u2, c_u3, c_u4 = st.columns([2, 1.2, 1.5, 1.2])
            with c_u1:
                st.write(f"**{u['email']}**")
                st.caption(f"Criado em: {u['created_at']}")
            with c_u2:
                tag_tipo = "👑 Administrador" if u['user_type'] == "ADMIN" else ("🏢 Interno" if u['user_type'] == "Interno" else "🌐 Externo")
                st.write(tag_tipo)
            with c_u3:
                status_pw = "⚠️ Aguardando 1º Acesso / Reset" if u['must_reset_password'] == 1 else "✅ Senha Ativa"
                st.write(status_pw)
            with c_u4:
                if u["user_type"] != "ADMIN":
                    if st.button("🔄 Resetar Senha", key=f"btn_reset_{u['id']}"):
                        conn.execute("UPDATE users SET password_hash = NULL, must_reset_password = 1 WHERE id = ?", (u["id"],))
                        conn.commit()
                        st.warning(f"Senha de {u['email']} resetada! O usuário definirá nova senha ao tentar logar.")
                        st.rerun()
            st.divider()

# ==========================================================================
# TELA: NOTAS EXPLICATIVAS (MOVIMENTAÇÃO DO EXERCÍCIO PARA AUDITORIA)
# ==========================================================================
elif menu == "Notas Explicativas (Auditoria IFRS 16)":
    st.title("📑 Notas Explicativas da Movimentação do Exercício (IFRS 16 / CPC 06)")
    st.info("Demonstrativo formal exigido pelas normas contábeis internacionais (CPC 06 R2 item 53 / IFRS 16.53) com a conciliação do Passivo e do Ativo ROU no exercício, além da análise de maturidade dos fluxos contratuais.")

    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    comps = pd.read_sql_query(f"SELECT id, company_name || ' (' || cnpj || ')' as label, company_name, cnpj FROM companies comp WHERE 1=1 {filtro_sql} ORDER BY company_name", conn, params=filtro_params)

    if comps.empty:
        st.warning("Nenhuma empresa disponível para seu perfil de acesso.")
    else:
        col_ne1, col_ne2 = st.columns([2, 1])
        with col_ne1:
            emp_sel_ne = st.selectbox("Selecione a Empresa", comps["label"].tolist(), key="sb_ne_emp")
            emp_row = comps[comps["label"] == emp_sel_ne].iloc[0]
            emp_id = int(emp_row["id"])
            emp_nome = emp_row["company_name"]
            emp_cnpj = emp_row["cnpj"]
        with col_ne2:
            ano_exercicio = st.number_input("Ano de Referência do Exercício Social", min_value=2019, max_value=2050, value=date.today().year)

        dt_inicio_exercicio = date(ano_exercicio, 1, 1)
        dt_fim_exercicio = date(ano_exercicio, 12, 31)

        contracts_emp = pd.read_sql_query("SELECT id, contract_code, start_date, end_date FROM contracts WHERE company_id = ?", conn, params=(emp_id,))

        if contracts_emp.empty:
            st.info("Nenhum contrato cadastrado para esta empresa.")
        else:
            saldo_inicial_passivo = 0.0
            saldo_inicial_rou = 0.0
            adicoes_novos_contratos = 0.0
            total_remensuracoes = 0.0
            total_juros_exercicio = 0.0
            total_pagamentos_exercicio = 0.0
            total_depreciacao_exercicio = 0.0
            baixas_rescisao_passivo = 0.0
            baixas_rescisao_rou = 0.0
            saldo_final_passivo = 0.0
            saldo_final_rou = 0.0

            # Maturidade
            mat_ano1_cp = 0.0
            mat_ano2 = 0.0
            mat_ano3 = 0.0
            mat_ano4 = 0.0
            mat_ano5 = 0.0
            mat_apos_5anos = 0.0

            for _, c_row in contracts_emp.iterrows():
                cid = c_row["id"]
                sched = calculate_contract_schedule(cid)
                dt_c_start = datetime.strptime(c_row["start_date"], "%Y-%m-%d").date()

                # Saldo em 31/12 do ano anterior
                sched_ant = sched[sched["Data de Competência"].apply(lambda d: datetime.strptime(d, "%d/%m/%Y").date() <= date(ano_exercicio - 1, 12, 31))]
                if dt_c_start <= date(ano_exercicio - 1, 12, 31):
                    if sched_ant.empty:
                        vp_ini = sched.iloc[0]["Passivo Inicial (R$)"]
                        saldo_inicial_passivo += vp_ini
                        saldo_inicial_rou += vp_ini
                    else:
                        saldo_inicial_passivo += sched_ant.iloc[-1]["Passivo Final (R$)"]
                        saldo_inicial_rou += sched_ant.iloc[-1]["Ativo Direito de Uso Líquido (R$)"]

                # Novos contratos iniciados neste exercício
                if dt_inicio_exercicio <= dt_c_start <= dt_fim_exercicio:
                    adicoes_novos_contratos += sched.iloc[0]["Passivo Inicial (R$)"]

                # Movimentações do exercício
                sched_exerc = sched[sched["Data de Competência"].apply(lambda d: dt_inicio_exercicio <= datetime.strptime(d, "%d/%m/%Y").date() <= dt_fim_exercicio)]
                if not sched_exerc.empty:
                    total_juros_exercicio += sched_exerc["Despesa Financeira / Juros (R$)"].sum()
                    total_pagamentos_exercicio += sched_exerc["Contraprestação Devida (R$)"].sum()
                    total_depreciacao_exercicio += sched_exerc["Depreciação ROU (R$)"].sum()
                    total_remensuracoes += sched_exerc["Variação Remensuração Delta (R$)"].sum()

                # Rescisões
                term_c = pd.read_sql_query("""
                    SELECT * FROM contract_terminations 
                    WHERE contract_id = ? AND termination_date >= ? AND termination_date <= ?
                """, conn, params=(cid, dt_inicio_exercicio.strftime("%Y-%m-%d"), dt_fim_exercicio.strftime("%Y-%m-%d")))
                if not term_c.empty:
                    baixas_rescisao_passivo += term_c["liability_written_off"].sum()
                    baixas_rescisao_rou += term_c["rou_written_off"].sum()

                # Saldo Final em 31/12
                sched_fim = sched[sched["Data de Competência"].apply(lambda d: datetime.strptime(d, "%d/%m/%Y").date() <= dt_fim_exercicio)]
                if not sched_fim.empty:
                    ultimo_m = sched_fim.iloc[-1]
                    saldo_final_passivo += ultimo_m["Passivo Final (R$)"]
                    saldo_final_rou += ultimo_m["Ativo Direito de Uso Líquido (R$)"]
                    
                    # Maturidade dos fluxos vincendos
                    idx_fim = sched_fim.index[-1]
                    vincendas = sched.iloc[idx_fim + 1:]
                    mat_ano1_cp += vincendas.iloc[0:12]["Amortização do Principal (R$)"].sum()
                    mat_ano2 += vincendas.iloc[12:24]["Amortização do Principal (R$)"].sum()
                    mat_ano3 += vincendas.iloc[24:36]["Amortização do Principal (R$)"].sum()
                    mat_ano4 += vincendas.iloc[36:48]["Amortização do Principal (R$)"].sum()
                    mat_ano5 += vincendas.iloc[48:60]["Amortização do Principal (R$)"].sum()
                    mat_apos_5anos += vincendas.iloc[60:]["Amortização do Principal (R$)"].sum()

            st.markdown("---")
            st.subheader(f"1. Quadro de Movimentação Contábil do Exercício Social findo em 31/12/{ano_exercicio}")
            
            df_mov = pd.DataFrame([
                {"Rubrica": f"Saldo Inicial em 01/01/{ano_exercicio}", "Passivo de Arrendamento (R$)": saldo_inicial_passivo, "Direito de Uso ROU (R$)": saldo_inicial_rou},
                {"Rubrica": "(+) Adições / Novos Contratos Contratados", "Passivo de Arrendamento (R$)": adicoes_novos_contratos, "Direito de Uso ROU (R$)": adicoes_novos_contratos},
                {"Rubrica": "(+/-) Remensurações e Aditivos de Taxa/Índice", "Passivo de Arrendamento (R$)": total_remensuracoes, "Direito de Uso ROU (R$)": total_remensuracoes},
                {"Rubrica": "(-) Baixas por Rescisão / Distrato", "Passivo de Arrendamento (R$)": -baixas_rescisao_passivo, "Direito de Uso ROU (R$)": -baixas_rescisao_rou},
                {"Rubrica": "(+) Despesa de Juros Financeiros Apropriados", "Passivo de Arrendamento (R$)": total_juros_exercicio, "Direito de Uso ROU (R$)": 0.0},
                {"Rubrica": "(-) Pagamentos Efetivos de Contraprestações", "Passivo de Arrendamento (R$)": -total_pagamentos_exercicio, "Direito de Uso ROU (R$)": 0.0},
                {"Rubrica": "(-) Despesa de Depreciação do Exercício", "Passivo de Arrendamento (R$)": 0.0, "Direito de Uso ROU (R$)": -total_depreciacao_exercicio},
                {"Rubrica": f"Saldo Final em 31/12/{ano_exercicio}", "Passivo de Arrendamento (R$)": saldo_final_passivo, "Direito de Uso ROU (R$)": saldo_final_rou}
            ])

            st.dataframe(df_mov.style.format({"Passivo de Arrendamento (R$)": "R$ {:,.2f}", "Direito de Uso ROU (R$)": "R$ {:,.2f}"}), use_container_width=True)

            st.markdown("---")
            st.subheader("2. Análise de Vencimentos do Passivo (Maturity Analysis - IFRS 16.58)")
            st.caption("Abertura dos fluxos de amortização do passivo vincendo por faixas temporais:")

            mat_cp_total = mat_ano1_cp
            mat_lp_total = mat_ano2 + mat_ano3 + mat_ano4 + mat_ano5 + mat_apos_5anos

            df_mat = pd.DataFrame([
                {"Faixa Temporal de Vencimento": "Até 1 ano (Passivo Circulante - CP)", "Valor (R$)": mat_cp_total, "% do Total": (mat_cp_total / saldo_final_passivo * 100) if saldo_final_passivo > 0 else 0},
                {"Faixa Temporal de Vencimento": "Entre 1 e 2 anos (Passivo Não Circulante - LP)", "Valor (R$)": mat_ano2, "% do Total": (mat_ano2 / saldo_final_passivo * 100) if saldo_final_passivo > 0 else 0},
                {"Faixa Temporal de Vencimento": "Entre 2 e 3 anos (Passivo Não Circulante - LP)", "Valor (R$)": mat_ano3, "% do Total": (mat_ano3 / saldo_final_passivo * 100) if saldo_final_passivo > 0 else 0},
                {"Faixa Temporal de Vencimento": "Entre 3 e 4 anos (Passivo Não Circulante - LP)", "Valor (R$)": mat_ano4, "% do Total": (mat_ano4 / saldo_final_passivo * 100) if saldo_final_passivo > 0 else 0},
                {"Faixa Temporal de Vencimento": "Entre 4 e 5 anos (Passivo Não Circulante - LP)", "Valor (R$)": mat_ano5, "% do Total": (mat_ano5 / saldo_final_passivo * 100) if saldo_final_passivo > 0 else 0},
                {"Faixa Temporal de Vencimento": "Mais de 5 anos (Passivo Não Circulante - LP)", "Valor (R$)": mat_apos_5anos, "% do Total": (mat_apos_5anos / saldo_final_passivo * 100) if saldo_final_passivo > 0 else 0},
                {"Faixa Temporal de Vencimento": "Total do Passivo de Arrendamento", "Valor (R$)": saldo_final_passivo, "% do Total": 100.0}
            ])

            st.dataframe(df_mat.style.format({"Valor (R$)": "R$ {:,.2f}", "% do Total": "{:.2f}%"}), use_container_width=True)

            out_ne = io.BytesIO()
            with pd.ExcelWriter(out_ne, engine='openpyxl') as writer:
                df_mov.to_excel(writer, sheet_name="Movimentacao_Exercicio", index=False)
                df_mat.to_excel(writer, sheet_name="Vencimentos_Maturity", index=False)
            st.download_button(
                label="📥 Baixar Notas Explicativas em Excel (.xlsx)",
                data=out_ne.getvalue(),
                file_name=f"Notas_Explicativas_IFRS16_{emp_cnpj}_{ano_exercicio}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# ==========================================================================
# TELA: LANÇAMENTOS CONTÁBEIS POR PERÍODO (COM MOMENTO ZERO E REMENSURAÇÃO)
# ==========================================================================
elif menu == "Lançamentos Contábeis (Período / ERP)":
    st.title("📑 Lançamentos Contábeis por Período (Partidas Dobradas para ERP)")
    st.info("Gera os lançamentos contábeis completos: **Reconhecimento Inicial no Momento Zero ($t=0$)**, **Remensurações ($\Delta$)**, Juros, Depreciação, Pagamentos e Rescisões.")

    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    companies = pd.read_sql_query(f"SELECT id, company_name || ' (' || cnpj || ')' as label, cnpj, company_name FROM companies comp WHERE 1=1 {filtro_sql} ORDER BY company_name", conn, params=filtro_params)

    if companies.empty:
        st.warning("Nenhuma empresa disponível para seu perfil de acesso.")
    else:
        col_f1, col_f2, col_f3, col_f4 = st.columns([1.5, 1.2, 1, 1])
        with col_f1:
            emp_sel = st.selectbox("Selecione a Empresa (CNPJ)", companies["label"].tolist(), key="sb_emp_lanc")
            emp_row = companies[companies["label"] == emp_sel].iloc[0]
            emp_id = int(emp_row["id"])
            emp_cnpj = emp_row["cnpj"]
            emp_nome = emp_row["company_name"]

        with col_f2:
            contracts_opts = pd.read_sql_query("SELECT id, contract_code FROM contracts WHERE company_id = ? ORDER BY contract_code", conn, params=(emp_id,))
            list_c = ["Todos os Contratos da Empresa"] + contracts_opts["contract_code"].tolist()
            c_filter_sel = st.selectbox("Escopo de Contratos", list_c)

        with col_f3:
            dt_inicial = st.date_input("Data Inicial do Período", value=date(date.today().year, 1, 1), format="DD/MM/YYYY")
        with col_f4:
            dt_final = st.date_input("Data Final do Período", value=date(date.today().year, 12, 31), format="DD/MM/YYYY")

        if dt_final < dt_inicial:
            st.error("A Data Final não pode ser anterior à Data Inicial.")
        else:
            query_c = "SELECT id, contract_code, contract_object, start_date FROM contracts WHERE company_id = ?"
            params_c = [emp_id]
            if c_filter_sel != "Todos os Contratos da Empresa":
                query_c += " AND contract_code = ?"
                params_c.append(c_filter_sel)

            contracts_target = pd.read_sql_query(query_c, conn, params=params_c)

            if contracts_target.empty:
                st.info("Nenhum contrato localizado.")
            else:
                acc_juros_dre = get_company_account_label(emp_id, "JUROS_DRE")
                acc_deprec_dre = get_company_account_label(emp_id, "DEPREC_DRE")
                acc_passivo_cp = get_company_account_label(emp_id, "PASSIVO_CP")
                acc_ativo_rou = get_company_account_label(emp_id, "ATIVO_ROU")
                acc_deprec_acum = get_company_account_label(emp_id, "DEPREC_ACUM_ROU")
                acc_banco = get_company_account_label(emp_id, "BANCO_CAIXA")
                acc_ganho_dre = get_company_account_label(emp_id, "GANHO_DRE")
                acc_perda_dre = get_company_account_label(emp_id, "PERDA_DRE")
                acc_multa_dre = get_company_account_label(emp_id, "MULTA_DRE")

                lancamentos = []

                for _, c_row in contracts_target.iterrows():
                    cid = c_row["id"]
                    ccode = c_row["contract_code"]
                    start_dt_contract = datetime.strptime(c_row["start_date"], "%Y-%m-%d").date()
                    sched = calculate_contract_schedule(cid)

                    # 1. LANÇAMENTO INICIAL NO MOMENTO ZERO (t = 0)
                    if dt_inicial <= start_dt_contract <= dt_final:
                        vp_inicial = float(sched.iloc[0]["Passivo Inicial (R$)"])
                        dt_zero_str = start_dt_contract.strftime("%d/%m/%Y")
                        lancamentos.append({
                            "Data": dt_zero_str,
                            "Empresa": emp_nome,
                            "CNPJ": emp_cnpj,
                            "Contrato": ccode,
                            "Operação": "Reconhecimento Inicial (Momento Zero)",
                            "Conta Débito": acc_ativo_rou,
                            "Conta Crédito": acc_passivo_cp,
                            "Valor (R$)": vp_inicial,
                            "Histórico": f"Vlr. reconhecimento inicial IFRS 16 em {dt_zero_str} - Contrato {ccode}"
                        })

                    # 2. MOVIMENTAÇÕES MENSAIS
                    for _, s_row in sched.iterrows():
                        dt_line = datetime.strptime(s_row["Data de Competência"], "%d/%m/%Y").date()
                        if dt_inicial <= dt_line <= dt_final:
                            dt_str = s_row["Data de Competência"]
                            v_juros = float(s_row["Despesa Financeira / Juros (R$)"])
                            v_deprec = float(s_row["Depreciação ROU (R$)"])
                            v_pmt = float(s_row["Contraprestação Devida (R$)"])
                            v_delta = float(s_row.get("Variação Remensuração Delta (R$)", 0.0))
                            v_ganho_rem = float(s_row.get("Ganho Remensuração DRE (R$)", 0.0))

                            # 2.1. Lançamento da Remensuração (Delta)
                            if abs(v_delta) > 0.001:
                                if v_delta > 0:
                                    # Aumento do Passivo e do Ativo ROU
                                    lancamentos.append({
                                        "Data": dt_str,
                                        "Empresa": emp_nome,
                                        "CNPJ": emp_cnpj,
                                        "Contrato": ccode,
                                        "Operação": "Remensuração (+ Aumento do Passivo)",
                                        "Conta Débito": acc_ativo_rou,
                                        "Conta Crédito": acc_passivo_cp,
                                        "Valor (R$)": v_delta,
                                        "Histórico": f"Vlr. remensuração contratual acréscimo em {dt_str} - Contrato {ccode}"
                                    })
                                else:
                                    # Redução do Passivo
                                    val_red = abs(v_delta)
                                    val_rou_red = val_red - v_ganho_rem
                                    if val_rou_red > 0:
                                        lancamentos.append({
                                            "Data": dt_str,
                                            "Empresa": emp_nome,
                                            "CNPJ": emp_cnpj,
                                            "Contrato": ccode,
                                            "Operação": "Remensuração (- Redução do Passivo)",
                                            "Conta Débito": acc_passivo_cp,
                                            "Conta Crédito": acc_ativo_rou,
                                            "Valor (R$)": val_rou_red,
                                            "Histórico": f"Vlr. remensuração contratual decréscimo em {dt_str} - Contrato {ccode}"
                                        })
                                    if v_ganho_rem > 0:
                                        lancamentos.append({
                                            "Data": dt_str,
                                            "Empresa": emp_nome,
                                            "CNPJ": emp_cnpj,
                                            "Contrato": ccode,
                                            "Operação": "Ganho Remensuração (Excedente ROU)",
                                            "Conta Débito": acc_passivo_cp,
                                            "Conta Crédito": acc_ganho_dre,
                                            "Valor (R$)": v_ganho_rem,
                                            "Histórico": f"Vlr. ganho remensuração excedente ROU em {dt_str} - Contrato {ccode}"
                                        })

                            # 2.2. Juros Financeiros
                            if v_juros > 0:
                                lancamentos.append({
                                    "Data": dt_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Juros Financeiros",
                                    "Conta Débito": acc_juros_dre,
                                    "Conta Crédito": acc_passivo_cp,
                                    "Valor (R$)": v_juros,
                                    "Histórico": f"Vlr. juros s/ arrendamento em {dt_str} - Contrato {ccode}"
                                })

                            # 2.3. Depreciação do ROU
                            if v_deprec > 0:
                                lancamentos.append({
                                    "Data": dt_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Depreciação ROU",
                                    "Conta Débito": acc_deprec_dre,
                                    "Conta Crédito": acc_deprec_acum,
                                    "Valor (R$)": v_deprec,
                                    "Histórico": f"Vlr. depreciação ROU em {dt_str} - Contrato {ccode}"
                                })

                            # 2.4. Pagamento da Contraprestação
                            if v_pmt > 0:
                                lancamentos.append({
                                    "Data": dt_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Pagamento Contraprestação",
                                    "Conta Débito": acc_passivo_cp,
                                    "Conta Crédito": acc_banco,
                                    "Valor (R$)": v_pmt,
                                    "Histórico": f"Vlr. pgto contraprestação em {dt_str} - Contrato {ccode}"
                                })

                    # 3. RESCISÕES
                    term_c = pd.read_sql_query("""
                        SELECT * FROM contract_terminations 
                        WHERE contract_id = ? AND termination_date >= ? AND termination_date <= ?
                    """, conn, params=(cid, dt_inicial.strftime("%Y-%m-%d"), dt_final.strftime("%Y-%m-%d")))

                    if not term_c.empty:
                        for _, row_t in term_c.iterrows():
                            dt_term_str = datetime.strptime(row_t["termination_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
                            passivo_b = float(row_t["liability_written_off"])
                            rou_b = float(row_t["rou_written_off"])
                            dif_resultado = float(row_t["gain_loss_amount"])
                            multa_paga = float(row_t["penalty_amount"])

                            if dif_resultado >= 0:
                                lancamentos.append({
                                    "Data": dt_term_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Baixa por Rescisão (Passivo vs ROU)",
                                    "Conta Débito": acc_passivo_cp,
                                    "Conta Crédito": acc_ativo_rou,
                                    "Valor (R$)": rou_b,
                                    "Histórico": f"Vlr. baixa ROU p/ rescisão em {dt_term_str} - Contrato {ccode}"
                                })
                                if dif_resultado > 0:
                                    lancamentos.append({
                                        "Data": dt_term_str,
                                        "Empresa": emp_nome,
                                        "CNPJ": emp_cnpj,
                                        "Contrato": ccode,
                                        "Operação": "Ganho na Rescisão Contratual",
                                        "Conta Débito": acc_passivo_cp,
                                        "Conta Crédito": acc_ganho_dre,
                                        "Valor (R$)": dif_resultado,
                                        "Histórico": f"Vlr. ganho na baixa do leasing em {dt_term_str} - Contrato {ccode}"
                                    })
                            else:
                                lancamentos.append({
                                    "Data": dt_term_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Baixa por Rescisão (Passivo vs ROU)",
                                    "Conta Débito": acc_passivo_cp,
                                    "Conta Crédito": acc_ativo_rou,
                                    "Valor (R$)": passivo_b,
                                    "Histórico": f"Vlr. baixa passivo p/ rescisão em {dt_term_str} - Contrato {ccode}"
                                })
                                lancamentos.append({
                                    "Data": dt_term_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Perda na Rescisão Contratual",
                                    "Conta Débito": acc_perda_dre,
                                    "Conta Crédito": acc_ativo_rou,
                                    "Valor (R$)": abs(dif_resultado),
                                    "Histórico": f"Vlr. perda na baixa do leasing em {dt_term_str} - Contrato {ccode}"
                                })

                            if multa_paga > 0:
                                lancamentos.append({
                                    "Data": dt_term_str,
                                    "Empresa": emp_nome,
                                    "CNPJ": emp_cnpj,
                                    "Contrato": ccode,
                                    "Operação": "Multa Rescisória Indenizatória",
                                    "Conta Débito": acc_multa_dre,
                                    "Conta Crédito": acc_banco,
                                    "Valor (R$)": multa_paga,
                                    "Histórico": f"Vlr. pgto multa rescisória em {dt_term_str} - Contrato {ccode}"
                                })

                df_lote = pd.DataFrame(lancamentos)

                if df_lote.empty:
                    st.warning("Nenhum lançamento no período selecionado.")
                else:
                    total_mov = df_lote["Valor (R$)"].sum()

                    st.markdown("---")
                    col_k1, col_k2, col_k3, col_k4 = st.columns(4)
                    col_k1.metric("Período de Apuração", f"{(dt_final - dt_inicial).days + 1} dias")
                    col_k2.metric("Total de Débitos", f"R$ {total_mov:,.2f}")
                    col_k3.metric("Total de Créditos", f"R$ {total_mov:,.2f}")
                    col_k4.metric("Status do Lote", "✅ Balanceado (D = C)")

                    e1, e2 = st.columns([1.5, 3])
                    with e1:
                        out_excel = io.BytesIO()
                        with pd.ExcelWriter(out_excel, engine='openpyxl') as writer:
                            df_lote.to_excel(writer, sheet_name="Lancamentos_IFRS16", index=False)
                        st.download_button(
                            label="📥 Baixar Lançamentos em Excel (.xlsx)",
                            data=out_excel.getvalue(),
                            file_name=f"Lancamentos_Contabeis_{emp_cnpj}_{dt_inicial.strftime('%d%m%Y')}_a_{dt_final.strftime('%d%m%Y')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                    with e2:
                        csv_lote = df_lote.to_csv(index=False).encode("utf-8-sig")
                        st.download_button(
                            label="📥 Baixar Lançamentos em CSV",
                            data=csv_lote,
                            file_name=f"Lancamentos_Contabeis_{emp_cnpj}_{dt_inicial.strftime('%d%m%Y')}_a_{dt_final.strftime('%d%m%Y')}.csv",
                            mime="text/csv"
                        )

                    st.markdown(f"**Extrato Analítico com Data Explícita ({dt_inicial.strftime('%d/%m/%Y')} a {dt_final.strftime('%d/%m/%Y')}):**")
                    st.dataframe(
                        df_lote.style.format({"Valor (R$)": "R$ {:,.2f}"}),
                        use_container_width=True,
                        height=420
                    )

# ==========================================================================
# DEMAIS TELAS: LALUR, RESCISÃO, REAJUSTES, CONTRATOS, ATIVOS, EMPRESAS, TAXAS E DASHBOARD
# ==========================================================================
elif menu == "Demonstrativo LALUR / LACS (Lucro Real)":
    st.title("⚖️ Controle Fiscal do LALUR / LACS (Lucro Real - Lei 12.973/14)")
    st.info("Demonstrativo das Adições (Juros e Depreciação ROU) e Exclusões (Contraprestações pagas dedutíveis) para a ECF (Bloco M).")

    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    companies = pd.read_sql_query(f"SELECT id, company_name || ' (' || cnpj || ')' as label, cnpj, company_name FROM companies comp WHERE 1=1 {filtro_sql} ORDER BY company_name", conn, params=filtro_params)

    if companies.empty:
        st.warning("Nenhuma empresa disponível.")
    else:
        cl1, cl2, cl3, cl4 = st.columns([1.5, 1.2, 1, 1])
        with cl1:
            emp_sel_lalur = st.selectbox("Empresa (CNPJ)", companies["label"].tolist(), key="sb_emp_lalur")
            emp_row = companies[companies["label"] == emp_sel_lalur].iloc[0]
            emp_id = int(emp_row["id"])
            emp_cnpj = emp_row["cnpj"]
            emp_nome = emp_row["company_name"]

        with cl2:
            contracts_opts = pd.read_sql_query("SELECT id, contract_code FROM contracts WHERE company_id = ? ORDER BY contract_code", conn, params=(emp_id,))
            list_c = ["Todos os Contratos da Empresa"] + contracts_opts["contract_code"].tolist()
            c_sel_lalur = st.selectbox("Escopo do LALUR", list_c, key="sb_scope_lalur")

        with cl3:
            dt_ini_lalur = st.date_input("Início Período", value=date(date.today().year, 1, 1), format="DD/MM/YYYY", key="dt_ini_lalur")
        with cl4:
            dt_fim_lalur = st.date_input("Fim Período", value=date(date.today().year, 12, 31), format="DD/MM/YYYY", key="dt_fim_lalur")

        query_c = "SELECT id, contract_code FROM contracts WHERE company_id = ?"
        params_c = [emp_id]
        if c_sel_lalur != "Todos os Contratos da Empresa":
            query_c += " AND contract_code = ?"
            params_c.append(c_sel_lalur)

        contracts_target = pd.read_sql_query(query_c, conn, params=params_c)

        if contracts_target.empty:
            st.info("Nenhum contrato ativo para apuração fiscal.")
        else:
            lalur_lines = []
            for _, c_row in contracts_target.iterrows():
                cid = c_row["id"]
                ccode = c_row["contract_code"]
                sched = calculate_contract_schedule(cid)

                for _, s_row in sched.iterrows():
                    dt_line = datetime.strptime(s_row["Data de Competência"], "%d/%m/%Y").date()
                    if dt_ini_lalur <= dt_line <= dt_fim_lalur:
                        dt_comp = s_row["Data de Competência"]
                        v_juros = float(s_row["Despesa Financeira / Juros (R$)"])
                        v_deprec = float(s_row["Depreciação ROU (R$)"])
                        v_pmt = float(s_row["Contraprestação Devida (R$)"])

                        total_adicoes = v_deprec + v_juros
                        total_exclusoes = v_pmt
                        ajuste_liquido = total_adicoes - total_exclusoes

                        lalur_lines.append({
                            "Competência": dt_comp,
                            "Contrato": ccode,
                            "Empresa": emp_nome,
                            "CNPJ": emp_cnpj,
                            "(+) Adição: Depreciação ROU": v_deprec,
                            "(+) Adição: Juros Financeiros": v_juros,
                            "(=) Total Adições Parte A": total_adicoes,
                            "(-) Exclusão: Contraprestação Dedutível": total_exclusoes,
                            "(=) Efeito Líquido no Lucro Real": ajuste_liquido
                        })

            df_lalur = pd.DataFrame(lalur_lines)
            if df_lalur.empty:
                st.warning("Nenhum evento tributável no período.")
            else:
                tot_adicoes_geral = df_lalur["(=) Total Adições Parte A"].sum()
                tot_exclusoes_geral = df_lalur["(-) Exclusão: Contraprestação Dedutível"].sum()
                efeito_liquido_geral = df_lalur["(=) Efeito Líquido no Lucro Real"].sum()

                st.markdown("---")
                lk1, lk2, lk3, lk4 = st.columns(4)
                lk1.metric("(+) Total Adições no LALUR", f"R$ {tot_adicoes_geral:,.2f}")
                lk2.metric("(-) Total Exclusões no LALUR", f"R$ {tot_exclusoes_geral:,.2f}")
                lk3.metric("(=) Ajuste Líquido Lucro Real", f"{'+' if efeito_liquido_geral>=0 else ''} R$ {efeito_liquido_geral:,.2f}")
                impacto_tributario_34 = efeito_liquido_geral * 0.34
                lk4.metric("Impacto Fiscal Estimado (34%)", f"R$ {abs(impacto_tributario_34):,.2f}")

                lx1, lx2 = st.columns([1.5, 3])
                with lx1:
                    out_lalur = io.BytesIO()
                    with pd.ExcelWriter(out_lalur, engine='openpyxl') as writer:
                        df_lalur.to_excel(writer, sheet_name="Demonstrativo_LALUR", index=False)
                    st.download_button(
                        label="📥 Baixar Demonstrativo LALUR em Excel (.xlsx)",
                        data=out_lalur.getvalue(),
                        file_name=f"Demonstrativo_LALUR_{emp_cnpj}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                st.dataframe(df_lalur.style.format({
                    "(+) Adição: Depreciação ROU": "R$ {:,.2f}",
                    "(+) Adição: Juros Financeiros": "R$ {:,.2f}",
                    "(=) Total Adições Parte A": "R$ {:,.2f}",
                    "(-) Exclusão: Contraprestação Dedutível": "R$ {:,.2f}",
                    "(=) Efeito Líquido no Lucro Real": "R$ {:,.2f}"
                }), use_container_width=True)

elif menu == "Rescisão & Baixa de Contrato":
    st.title("⚖️ Rescisão Antecipada & Baixa Contratual (IFRS 16 / CPC 06 R2)")
    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    contratos = pd.read_sql_query(f"""
        SELECT c.id, c.contract_code || ' - ' || comp.company_name as label, c.status
        FROM contracts c
        JOIN companies comp ON comp.id = c.company_id
        WHERE 1=1 {filtro_sql}
        ORDER BY c.id DESC
    """, conn, params=filtro_params)

    if contratos.empty:
        st.warning("Nenhum contrato disponível.")
    else:
        tab_rescisao, tab_hist_rescisoes = st.tabs(["📝 Registrar Nova Rescisão", "📋 Histórico de Distratos"])
        with tab_rescisao:
            sel_c_label = st.selectbox("Selecione o Contrato para Distrato", contratos["label"].tolist())
            c_id = int(contratos[contratos["label"] == sel_c_label]["id"].values[0])
            ja_rescindido = conn.execute("SELECT COUNT(*) FROM contract_terminations WHERE contract_id = ?", (c_id,)).fetchone()[0]
            if ja_rescindido > 0:
                st.warning("Este contrato já foi rescindido anteriormente.")
            else:
                sched = calculate_contract_schedule(c_id)
                with st.form("form_termination"):
                    rc1, rc2, rc3 = st.columns(3)
                    with rc1:
                        mes_distrato = st.number_input("Mês da Rescisão (Competência final)", min_value=1, max_value=len(sched), value=min(12, len(sched)))
                    with rc2:
                        dt_distrato = st.date_input("Data Efetiva da Rescisão", value=date.today(), format="DD/MM/YYYY")
                    with rc3:
                        multa = st.number_input("Multa Rescisória Indenizatória Paga (R$)", min_value=0.0, value=0.0, step=500.0)

                    motivo_distrato = st.text_area("Motivo do Distrato", placeholder="Ex: Devolução do imóvel locado...")

                    linha_mes_rescisao = sched[sched["Mês"] == mes_distrato].iloc[0]
                    passivo_baixado = float(linha_mes_rescisao["Passivo Final (R$)"])
                    rou_baixado = float(linha_mes_rescisao["Ativo Direito de Uso Líquido (R$)"])
                    resultado_baixa = passivo_baixado - rou_baixado

                    st.markdown("---")
                    sb1, sb2, sb3 = st.columns(3)
                    sb1.metric("Passivo Baixado", f"R$ {passivo_baixado:,.2f}")
                    sb2.metric("Ativo ROU Baixado", f"R$ {rou_baixado:,.2f}")
                    sb3.metric("Resultado da Baixa", f"R$ {abs(resultado_baixa):,.2f}", delta="Ganho" if resultado_baixa>=0 else "-Perda")

                    if st.form_submit_button("🚨 Confirmar e Gravar Rescisão"):
                        conn.execute("""
                            INSERT INTO contract_terminations (contract_id, termination_month_index, termination_date, penalty_amount, reason, liability_written_off, rou_written_off, gain_loss_amount)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (c_id, mes_distrato, dt_distrato.strftime("%Y-%m-%d"), multa, motivo_distrato.strip(), passivo_baixado, rou_baixado, resultado_baixa))
                        conn.execute("UPDATE contracts SET status = 'RESCINDIDO' WHERE id = ?", (c_id,))
                        conn.commit()
                        st.success("Contrato rescindido com sucesso!")
                        st.rerun()

        with tab_hist_rescisoes:
            df_terms = pd.read_sql_query(f"""
                SELECT t.id, c.contract_code as 'Contrato', comp.company_name as 'Empresa', t.termination_month_index as 'Mês Baixa',
                       t.termination_date, t.liability_written_off as 'Passivo Baixado (R$)', t.rou_written_off as 'ROU Baixado (R$)', 
                       t.gain_loss_amount as 'Ganho/(Perda) DRE (R$)', t.penalty_amount as 'Multa Paga (R$)'
                FROM contract_terminations t
                JOIN contracts c ON c.id = t.contract_id
                JOIN companies comp ON comp.id = c.company_id
                WHERE 1=1 {filtro_sql}
                ORDER BY t.id DESC
            """, conn, params=filtro_params)
            st.dataframe(df_terms, use_container_width=True)

elif menu == "Reajustes & Aditivos Contratuais":
    st.title("🔄 Alteração de Contraprestação (Reajustes IPCA / IGP-M / Aditivos)")
    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    contratos = pd.read_sql_query(f"SELECT c.id, c.contract_code || ' - ' || comp.company_name as label FROM contracts c JOIN companies comp ON comp.id = c.company_id WHERE c.status != 'RESCINDIDO' {filtro_sql} ORDER BY c.id DESC", conn, params=filtro_params)

    if contratos.empty:
        st.warning("Nenhum contrato ativo disponível.")
    else:
        sel_c = st.selectbox("Selecione o Contrato para Reajustar", contratos["label"].tolist())
        c_id = int(contratos[contratos["label"] == sel_c]["id"].values[0])

        with st.form("form_reajuste"):
            mes_efetivo = st.number_input("Mês de Efetivação do Reajuste (Ex: 13, 25, 37, 60...)", min_value=2, max_value=360, value=60)
            nova_parcela = st.number_input("Novo Valor da Contraprestação Mensal (R$)", min_value=1.0, value=31842.0, step=100.0)
            motivo = st.text_input("Motivo", placeholder="Ex: Reajuste Anual IGPM")
            if st.form_submit_button("Gravar Alteração no Contrato"):
                conn.execute("INSERT INTO contract_modifications (contract_id, effective_month_index, new_installment, reason) VALUES (?, ?, ?, ?)",
                             (c_id, mes_efetivo, nova_parcela, motivo))
                conn.commit()
                st.success("Reajuste gravado com sucesso!")
                st.rerun()

        st.subheader("Histórico de Alterações Gravadas")
        df_m = pd.read_sql_query("SELECT id, effective_month_index as 'Mês Efetivo', new_installment as 'Nova Parcela (R$)', reason as 'Motivo', created_at as 'Registrado em' FROM contract_modifications WHERE contract_id = ? ORDER BY effective_month_index", conn, params=(c_id,))
        st.dataframe(df_m.style.format({"Nova Parcela (R$)": "R$ {:,.2f}"}), use_container_width=True)

elif menu == "Cadastro de Contratos":
    st.title("📄 Cadastro de Contrato de Leasing / Locação")
    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    companies = pd.read_sql_query(f"SELECT id, economic_group || ' - ' || company_name || ' (' || cnpj || ')' as label FROM companies comp WHERE 1=1 {filtro_sql} ORDER BY company_name", conn, params=filtro_params)

    if companies.empty:
        st.warning("Cadastre ao menos uma empresa antes.")
    else:
        with st.form("form_contract"):
            c1, c2, c3 = st.columns(3)
            with c1:
                comp_sel = st.selectbox("Empresa Arrendatária", companies["label"].tolist())
                comp_id = int(companies[companies["label"] == comp_sel]["id"].values[0])
                code = st.text_input("Código do Contrato", placeholder="Ex: CTR 05")
            with c2:
                dt_ini = st.date_input("Data de Início", value=date.today(), format="DD/MM/YYYY")
                dt_fim = st.date_input("Data de Término", value=date.today() + relativedelta(months=60) - relativedelta(days=1), format="DD/MM/YYYY")
            with c3:
                val_pmt = st.number_input("Contraprestação Inicial (R$)", min_value=1.0, value=30703.0, step=100.0)
                carencia = st.number_input("Carência (Meses)", min_value=0, max_value=60, value=0)

            obj_contrato = st.text_area("Objeto do Contrato", placeholder="Descrição do bem locado...")
            if st.form_submit_button("Salvar Contrato"):
                if code.strip() and obj_contrato and dt_fim > dt_ini:
                    conn.execute("""
                        INSERT INTO contracts (company_id, contract_code, contract_object, start_date, end_date, grace_period_months, initial_installment, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'ATIVO')
                    """, (comp_id, code.strip(), obj_contrato.strip(), dt_ini.strftime("%Y-%m-%d"), dt_fim.strftime("%Y-%m-%d"), carencia, val_pmt))
                    conn.commit()
                    st.success("Contrato cadastrado com sucesso!")
                else:
                    st.error("Verifique os campos obrigatórios.")

elif menu == "Ativos do Contrato (Patrimonial)":
    st.title("🏭 Gestão Patrimonial: Ativos Vinculados ao Contrato")
    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    contratos = pd.read_sql_query(f"SELECT c.id, c.contract_code || ' - ' || comp.company_name as label FROM contracts c JOIN companies comp ON comp.id = c.company_id WHERE 1=1 {filtro_sql} ORDER BY c.id DESC", conn, params=filtro_params)

    if contratos.empty:
        st.warning("Cadastre um contrato primeiro.")
    else:
        sel_c_label = st.selectbox("Selecione o Contrato", contratos["label"].tolist())
        c_id = int(contratos[contratos["label"] == sel_c_label]["id"].values[0])

        with st.form("form_add_asset"):
            c_a1, c_a2, c_a3 = st.columns(3)
            with c_a1:
                tipo_ativo = st.selectbox("Tipificação do Bem", ["Imóvel Comercial", "Máquinas e Equipamentos", "Veículos e Frotas", "Equipamentos de TI", "Instalações"])
            with c_a2:
                serial = st.text_input("Identificador / Matrícula / Chassi")
            with c_a3:
                terceiro = st.text_input("Proprietário / Locador")
            foto = st.file_uploader("Foto do Bem", type=["jpg", "png", "jpeg"])

            if st.form_submit_button("Adicionar Bem ao Contrato"):
                foto_path = None
                if foto:
                    ext = foto.name.split(".")[-1]
                    foto_path = os.path.join(UPLOAD_DIR, f"ativo_{c_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}")
                    with open(foto_path, "wb") as f:
                        f.write(foto.getbuffer())
                conn.execute("INSERT INTO contract_assets (contract_id, asset_type, asset_serial_number, third_party_owner, asset_photo_path) VALUES (?, ?, ?, ?, ?)",
                             (c_id, tipo_ativo, serial.strip(), terceiro.strip(), foto_path))
                conn.commit()
                st.success("Ativo vinculado!")

        assets = pd.read_sql_query("SELECT id, asset_type as 'Tipo', asset_serial_number as 'Identificador', third_party_owner as 'Proprietário' FROM contract_assets WHERE contract_id = ?", conn, params=(c_id,))
        st.dataframe(assets, use_container_width=True)

elif menu == "Cadastro de Empresas":
    st.title("🏢 Gestão de Empresas & Plano de Contas ERP")
    conn = get_db()
    tab_e1, tab_e2 = st.tabs(["🏛️ Empresas", "📑 Plano de Contas De-Para ERP"])
    with tab_e1:
        with st.form("form_comp"):
            grp = st.text_input("Grupo Econômico")
            cnpj = st.text_input("CNPJ")
            nm = st.text_input("Razão Social")
            if st.form_submit_button("Salvar Empresa"):
                if grp and cnpj and nm:
                    try:
                        conn.execute("INSERT INTO companies (economic_group, cnpj, company_name) VALUES (?, ?, ?)", (grp.strip(), cnpj.strip(), nm.strip()))
                        conn.commit()
                        st.success("Empresa cadastrada!")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("CNPJ já cadastrado.")
        st.dataframe(pd.read_sql_query("SELECT id, economic_group as 'Grupo', cnpj as 'CNPJ', company_name as 'Razão Social' FROM companies", conn), use_container_width=True)

    with tab_e2:
        comps_list = pd.read_sql_query("SELECT id, company_name || ' (' || cnpj || ')' as label FROM companies ORDER BY company_name", conn)
        if not comps_list.empty:
            sel_e = st.selectbox("Selecione a Empresa", comps_list["label"].tolist())
            eid = int(comps_list[comps_list["label"] == sel_e]["id"].values[0])
            acc_saved = pd.read_sql_query("SELECT standard_role, client_account_code, client_account_name FROM company_accounts WHERE company_id = ?", conn, params=(eid,))
            saved_map = {row["standard_role"]: (row["client_account_code"], row["client_account_name"]) for _, row in acc_saved.iterrows()}
            with st.form("form_accs"):
                inputs_m = {}
                for role, (std_code, std_name, std_grp) in STANDARD_ACCOUNTS.items():
                    c_i, c_c, c_n = st.columns([1.5, 1.2, 2])
                    with c_i:
                        st.write(f"**{std_name}** ({std_code})")
                    with c_c:
                        vc = st.text_input("Conta Cliente", value=saved_map.get(role, ("", ""))[0] or "", key=f"c_{role}")
                    with c_n:
                        vn = st.text_input("Descrição Cliente", value=saved_map.get(role, ("", ""))[1] or "", key=f"n_{role}")
                    inputs_m[role] = (vc.strip(), vn.strip())
                if st.form_submit_button("Salvar Contas ERP"):
                    for role, (cc, cn) in inputs_m.items():
                        conn.execute("""
                            INSERT INTO company_accounts (company_id, standard_role, client_account_code, client_account_name)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(company_id, standard_role) DO UPDATE SET client_account_code = excluded.client_account_code, client_account_name = excluded.client_account_name
                        """, (eid, role, cc, cn))
                    conn.commit()
                    st.success("Plano de contas atualizado!")

elif menu == "Parâmetros de Taxas (IBR)":
    st.title("📈 Parâmetros de Mercado: Taxas de Desconto (IBR por Empresa)")
    conn = get_db()
    comps = pd.read_sql_query("SELECT id, company_name || ' (' || cnpj || ')' as label FROM companies ORDER BY company_name", conn)
    if not comps.empty:
        with st.form("form_taxa"):
            emp_sel_t = st.selectbox("Empresa Arrendatária", comps["label"].tolist())
            eid = int(comps[comps["label"] == emp_sel_t]["id"].values[0])
            dt_vig = st.date_input("Data de Vigência", value=date.today(), format="DD/MM/YYYY")
            tx_a = st.number_input("Taxa Anual Nominal (%)", min_value=0.01, max_value=100.0, value=10.42, step=0.01, format="%.4f") / 100
            desc = st.text_input("Descrição")
            if st.form_submit_button("Salvar Taxa"):
                tx_m = ((1 + tx_a) ** (1/12)) - 1
                conn.execute("INSERT INTO discount_rates (company_id, effective_date, annual_rate, monthly_rate, description) VALUES (?, ?, ?, ?, ?)",
                             (eid, dt_vig.strftime("%Y-%m-%d"), tx_a, tx_m, desc))
                conn.commit()
                st.success("Taxa registrada!")

        st.dataframe(pd.read_sql_query("""
            SELECT r.id, c.company_name as 'Empresa', r.effective_date as 'Vigência', 
                   ROUND(r.annual_rate * 100, 4) as 'Taxa Anual (%)', r.description as 'Descrição'
            FROM discount_rates r LEFT JOIN companies c ON c.id = r.company_id ORDER BY r.effective_date DESC
        """, conn), use_container_width=True)

elif menu == "Painel Geral & Relatórios":
    st.title("📊 Painel Executivo IFRS 16 & Relatório Histórico")
    conn = get_db()
    filtro_sql, filtro_params = get_allowed_companies_query_filter(current_user)
    df_comp_all = pd.read_sql_query(f"SELECT DISTINCT economic_group, company_name FROM companies comp WHERE 1=1 {filtro_sql}", conn, params=filtro_params)
    grupos = sorted(df_comp_all["economic_group"].unique().tolist()) if not df_comp_all.empty else []

    col_filtro1, col_filtro2, col_filtro3 = st.columns([1.2, 1.5, 1.2])
    with col_filtro1:
        grupo_sel = st.selectbox("Grupo Econômico", ["Todos"] + grupos)
    with col_filtro2:
        empresas_disp = df_comp_all[df_comp_all["economic_group"] == grupo_sel]["company_name"].unique().tolist() if grupo_sel != "Todos" else df_comp_all["company_name"].unique().tolist()
        empresa_sel = st.selectbox("Empresa", ["Todas"] + sorted(empresas_disp))
    with col_filtro3:
        data_posicao = st.date_input("Data de Posição / Corte", value=date.today(), format="DD/MM/YYYY")

    query = f"""
    SELECT c.id, c.contract_code, comp.economic_group, comp.company_name, c.contract_object,
           c.start_date, c.end_date, c.initial_installment, c.status
    FROM contracts c
    JOIN companies comp ON comp.id = c.company_id
    WHERE 1=1 {filtro_sql}
    """
    params = list(filtro_params)
    if grupo_sel != "Todos":
        query += " AND comp.economic_group = ?"
        params.append(grupo_sel)
    if empresa_sel != "Todas":
        query += " AND comp.company_name = ?"
        params.append(empresa_sel)

    df_contracts = pd.read_sql_query(query, conn, params=params)

    if df_contracts.empty:
        st.info("Nenhum contrato localizado.")
    else:
        detalhes = []
        for _, row in df_contracts.iterrows():
            c_id = row["id"]
            sched = calculate_contract_schedule(c_id)
            vp_ini = sched.iloc[0]["Passivo Inicial (R$)"] if not sched.empty else 0.0

            sched_pass = sched[sched["Data de Competência"].apply(lambda d: datetime.strptime(d, "%d/%m/%Y").date() <= data_posicao)]
            if sched_pass.empty:
                passivo_pos = vp_ini
                passivo_cp = sched.iloc[0]["Passivo Circulante - CP (R$)"]
                passivo_lp = sched.iloc[0]["Passivo Não Circulante - LP (R$)"]
                ativo_pos = vp_ini
                juros_acum = deprec_acum = pago_acum = 0.0
            else:
                ultima = sched_pass.iloc[-1]
                passivo_pos = ultima["Passivo Final (R$)"]
                passivo_cp = ultima["Passivo Circulante - CP (R$)"]
                passivo_lp = ultima["Passivo Não Circulante - LP (R$)"]
                ativo_pos = ultima["Ativo Direito de Uso Líquido (R$)"]
                juros_acum = sched_pass["Despesa Financeira / Juros (R$)"].sum()
                deprec_acum = sched_pass["Depreciação ROU (R$)"].sum()
                pago_acum = sched_pass["Contraprestação Devida (R$)"].sum()

            detalhes.append({
                "ID": c_id, "Status": row["status"], "Contrato": row["contract_code"],
                "Empresa": row["company_name"], "Grupo": row["economic_group"],
                "Passivo Total (R$)": passivo_pos,
                "Passivo CP (R$)": passivo_cp,
                "Passivo LP (R$)": passivo_lp,
                "ROU Líquido (R$)": ativo_pos,
                "Juros Acum. (R$)": juros_acum, "Deprec. Acum. (R$)": deprec_acum
            })

        df_pos = pd.DataFrame(detalhes)
        st.subheader(f"Posição Contábil Consolidada em {data_posicao.strftime('%d/%m/%Y')}")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Passivo Total", f"R$ {df_pos['Passivo Total (R$)'].sum():,.2f}")
        k2.metric("• Passivo Circulante (CP)", f"R$ {df_pos['Passivo CP (R$)'].sum():,.2f}")
        k3.metric("• Passivo Não Circulante (LP)", f"R$ {df_pos['Passivo LP (R$)'].sum():,.2f}")
        k4.metric("Ativo Direito de Uso (ROU)", f"R$ {df_pos['ROU Líquido (R$)'].sum():,.2f}")

        st.markdown("---")
        for idx, row_c in df_pos.iterrows():
            c_c1, c_c2, c_c3, c_c4 = st.columns([2.5, 2, 2, 1.2])
            with c_c1:
                st.write(f"**{row_c['Contrato']}** ({row_c['Empresa']})")
                st.caption(f"Status: {row_c['Status']}")
            with c_c2:
                st.write(f"**Passivo:** R$ {row_c['Passivo Total (R$)']:,.2f}")
                st.caption(f"CP: R$ {row_c['Passivo CP (R$)']:,.2f} | LP: R$ {row_c['Passivo LP (R$)']:,.2f}")
            with c_c3:
                st.write(f"**ROU Líquido:** R$ {row_c['ROU Líquido (R$)']:,.2f}")
            with c_c4:
                if st.button("🔍 Abrir Grade", key=f"btn_open_{row_c['ID']}"):
                    st.session_state.selected_contract_id = row_c["ID"]

        if st.session_state.selected_contract_id is not None:
            c_sel_id = st.session_state.selected_contract_id
            st.markdown("---")
            st.subheader(f"📑 Grade de Amortização com Segregação CP/LP e Remensuração (ID: {c_sel_id})")
            grade = calculate_contract_schedule(c_sel_id)

            d1, d2 = st.columns([1.5, 3])
            with d1:
                out_grade = io.BytesIO()
                with pd.ExcelWriter(out_grade, engine='openpyxl') as writer:
                    grade.to_excel(writer, sheet_name="Memoria_IFRS16", index=False)
                st.download_button("📥 Baixar Grade em Excel (.xlsx)", out_grade.getvalue(), f"Calculo_IFRS16_{c_sel_id}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            st.dataframe(
                grade.style.format({
                    "Contraprestação Devida (R$)": "R$ {:,.2f}",
                    "Despesa Financeira / Juros (R$)": "R$ {:,.2f}",
                    "Amortização do Principal (R$)": "R$ {:,.2f}",
                    "Passivo Inicial (R$)": "R$ {:,.2f}",
                    "Passivo Final (R$)": "R$ {:,.2f}",
                    "Passivo Circulante - CP (R$)": "R$ {:,.2f}",
                    "Passivo Não Circulante - LP (R$)": "R$ {:,.2f}",
                    "Depreciação ROU (R$)": "R$ {:,.2f}",
                    "Ativo Direito de Uso Líquido (R$)": "R$ {:,.2f}",
                    "Variação Remensuração Delta (R$)": "R$ {:,.2f}",
                    "Ganho Remensuração DRE (R$)": "R$ {:,.2f}",
                    "Taxa Efetiva Mensal (%)": "{:.4f}%"
                }),
                use_container_width=True,
                height=450
            )