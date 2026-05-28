"""Entry point Streamlit-приложения.

Две страницы через st.navigation (sidebar nav слева):
    📚 База знаний — поиск/типы/лекции/похожие по корпусу метода Анны
    🎨 Контент    — генератор + черновики + источники + заметки

Запуск:
    docker compose up -d ui
    # → http://localhost:8501

Безопасность: STREAMLIT_PASSWORD в env (без — bypass для dev/localhost).
"""
from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

from psy_helper.ui.content import render as render_content
from psy_helper.ui.helpers import gate_password
from psy_helper.ui.knowledge import render as render_knowledge

load_dotenv()

st.set_page_config(
    page_title="psy-helper",
    layout="wide",
    page_icon="📚",
    initial_sidebar_state="expanded",
)

gate_password()

pg = st.navigation([
    st.Page(render_knowledge, title="База знаний", icon="📚", default=True),
    st.Page(render_content, title="Контент", icon="🎨"),
])
pg.run()
