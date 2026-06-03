import io
import re
import unicodedata
from pathlib import Path
from textwrap import dedent
from typing import Dict, List, Optional, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

plt.switch_backend("Agg")


st.set_page_config(
    page_title="Tanita Dashboard",
    page_icon="⚕️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def scan_patient_folders(base_dir: Path) -> List[Path]:
    _skip = {".venv", "__pycache__", ".git", ".claude"}
    result = []
    try:
        for item in sorted(base_dir.iterdir()):
            if (
                item.is_dir()
                and item.name not in _skip
                and not item.name.startswith(".")
                and not item.name.startswith("_")
                and ((item / "DATA.CSV").exists() or (item / "DATAX.CSV").exists())
            ):
                result.append(item)
    except PermissionError:
        pass
    return result


def parse_patient_data(txt_path: Path) -> Dict[str, str]:
    import unicodedata as _ud

    def _norm(s: str) -> str:
        s = _ud.normalize("NFKD", s).encode("ascii", "ignore").decode()
        import re as _re

        return _re.sub(r"\s+", " ", _re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()

    out: Dict[str, str] = {}
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = txt_path.read_text(encoding=enc)
            break
        except Exception:
            text = ""
    for line in text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            out[_norm(key)] = val.strip()
    return out


# ── Sidebar: patient folder selector ────────────────────────────────────────
_BASE_DIR = Path(__file__).parent
_patient_folders = scan_patient_folders(_BASE_DIR)

_source_mode = "manual"
_selected_folder: Optional[Path] = None
_csv_path: Optional[Path] = None
_patient_info: Dict[str, str] = {}

_CUSTOM_PATH_LABEL = "📁 Ruta personalizada..."

st.sidebar.markdown("### 📁 Paciente")
_folder_options = ["── Subir archivo ──"] + [f.name for f in _patient_folders] + [_CUSTOM_PATH_LABEL]
_selected_name = st.sidebar.selectbox("Seleccionar paciente", options=_folder_options, index=0)

if _selected_name == _CUSTOM_PATH_LABEL:
    if st.sidebar.button("Seleccionar carpeta...", use_container_width=True):
        import subprocess, sys

        _proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import tkinter as tk; from tkinter import filedialog; "
                "r=tk.Tk(); r.withdraw(); r.wm_attributes('-topmost',1); "
                "p=filedialog.askdirectory(title='Seleccionar carpeta de paciente'); "
                "r.destroy(); print(p)",
            ],
            capture_output=True,
            text=True,
        )
        _picked = _proc.stdout.strip()
        if _picked:
            st.session_state["custom_folder_path"] = _picked
            st.rerun()

    _custom_path_str = st.session_state.get("custom_folder_path", "")
    if _custom_path_str:
        st.sidebar.caption(f"📂 `{Path(_custom_path_str).name}`")
        _custom_folder = Path(_custom_path_str)
        _has_csv = (_custom_folder / "DATA.CSV").exists() or (_custom_folder / "DATAX.CSV").exists()
        if _custom_folder.is_dir() and _has_csv:
            _source_mode = "folder"
            _selected_folder = _custom_folder
            _data_txt = _custom_folder / "data.txt"
            if _data_txt.exists():
                _patient_info = parse_patient_data(_data_txt)
        else:
            st.sidebar.warning("Carpeta no válida o sin DATA.CSV / DATAX.CSV.")
    else:
        st.sidebar.caption("Ninguna carpeta seleccionada.")
elif _selected_name != "── Subir archivo ──":
    _source_mode = "folder"
    _selected_folder = _BASE_DIR / _selected_name
    _data_txt = _selected_folder / "data.txt"
    if _data_txt.exists():
        _patient_info = parse_patient_data(_data_txt)

st.sidebar.markdown("---")

# Pre-fill profile from patient data or use defaults
_default_name = _patient_info.get("nombre", "Usuario Tanita")
_default_gender_idx = (
    1
    if _patient_info.get("genero", "").lower().startswith("masc") or _patient_info.get("genero", "") in ("m", "M")
    else 0
)
_default_age = 35
try:
    _default_age = int(float(_patient_info.get("edad", "35").replace(",", ".")))
except (ValueError, TypeError):
    pass

_wkey = _selected_folder.name if _selected_folder else "manual"
st.sidebar.markdown("### 👤 Perfil del Usuario")
user_name = st.sidebar.text_input("Nombre del Paciente", value=_default_name, key=f"uname_{_wkey}")
user_gender = st.sidebar.selectbox(
    "Género", options=["Femenino", "Masculino"], index=_default_gender_idx, key=f"ugender_{_wkey}"
)
user_age = st.sidebar.number_input("Edad (años)", min_value=1, max_value=120, value=_default_age, key=f"uage_{_wkey}")

if _patient_info.get("altura"):
    st.sidebar.caption(f"📏 Altura: {_patient_info['altura']} m")
if _patient_info.get("nivel de actividad fisica"):
    st.sidebar.caption(f"🏃 Actividad: {_patient_info['nivel de actividad fisica']}")

st.sidebar.markdown("---")


METRIC_CONFIG = {
    "weight": {
        "label": "Peso",
        "suffix": "kg",
        "decimals": 1,
        "color": "#6C8EFA",
        "higher_is_better": False,
        "aliases": ["weight", "body weight", "peso", "peso corporal"],
    },
    "body_fat_pct": {
        "label": "% Grasa",
        "suffix": "%",
        "decimals": 1,
        "color": "#7BC8B6",
        "higher_is_better": False,
        "aliases": [
            "body fat %",
            "body fat",
            "fat %",
            "% grasa",
            "grasa corporal",
            "grasa corporal %",
            "body fat percentage",
        ],
    },
    "muscle_mass": {
        "label": "Masa muscular",
        "suffix": "kg",
        "decimals": 1,
        "color": "#B39DDB",
        "higher_is_better": True,
        "aliases": ["muscle mass", "masa muscular", "masa muscular total"],
    },
    "metabolic_age": {
        "label": "Edad metabólica",
        "suffix": "años",
        "decimals": 0,
        "color": "#94A3B8",
        "higher_is_better": False,
        "aliases": ["metabolic age", "edad metabolica", "edad metabólica", "body age", "met age"],
    },
    "bmi": {
        "label": "IMC",
        "suffix": "",
        "decimals": 1,
        "color": "#F59E8B",
        "higher_is_better": False,
        "aliases": ["bmi", "imc", "body mass index", "indice de masa corporal", "índice de masa corporal"],
    },
    "total_body_water_pct": {
        "label": "% Agua corporal",
        "suffix": "%",
        "decimals": 1,
        "color": "#5BC0EB",
        "higher_is_better": True,
        "aliases": [
            "total body water %",
            "body water %",
            "agua corporal total",
            "% agua corporal",
            "agua corporal",
        ],
    },
    "visceral_fat": {
        "label": "Grasa visceral",
        "suffix": "nivel",
        "decimals": 0,
        "color": "#F4B183",
        "higher_is_better": False,
        "aliases": ["visceral fat", "grasa visceral", "visceral fat rating", "visceral fat level"],
    },
    "bone_mass": {
        "label": "Masa ósea",
        "suffix": "kg",
        "decimals": 1,
        "color": "#C6B9CD",
        "higher_is_better": True,
        "aliases": ["bone mass", "masa osea", "masa ósea"],
    },
    "bmr": {
        "label": "BMR",
        "suffix": "kcal",
        "decimals": 0,
        "color": "#F7C59F",
        "higher_is_better": None,
        "aliases": ["bmr", "basal metabolic rate", "tasa metabolica basal", "tasa metabólica basal", "tmb"],
    },
    "daily_calorie_intake": {
        "label": "Ingesta calórica",
        "suffix": "kcal",
        "decimals": 0,
        "color": "#A0CED9",
        "higher_is_better": None,
        "aliases": [
            "daily calorie intake",
            "daily caloric intake",
            "ingesta calorica diaria",
            "ingesta calórica diaria",
            "recommended calorie intake",
            "ingesta calorica",
        ],
    },
    "physique_rating": {
        "label": "Clasificación física",
        "suffix": "",
        "decimals": 0,
        "color": "#9DB4C0",
        "higher_is_better": None,
        "aliases": ["physique rating", "classificacion fisica", "clasificación física", "body type", "physique"],
    },
    "abdominal_circumference": {
        "label": "Perímetro abdominal",
        "suffix": "cm",
        "decimals": 1,
        "color": "#E8A87C",
        "higher_is_better": False,
        "aliases": [
            "abdominal circumference",
            "perimetro abdominal",
            "perímetro abdominal",
            "waist circumference",
            "circunferencia abdominal",
            "cintura",
        ],
    },
}

DISPLAY_NAMES = {metric: config["label"] for metric, config in METRIC_CONFIG.items()}

ALIAS_MAP = {
    "datetime": ["datetime", "timestamp", "date time", "fecha hora", "measurement datetime"],
    "date": ["date", "fecha", "measurement date", "record date"],
    "time": ["time", "hora", "measurement time", "record time"],
    **{metric: config["aliases"] for metric, config in METRIC_CONFIG.items()},
}

PRIMARY_KPI_KEYS = ["weight", "body_fat_pct", "muscle_mass", "metabolic_age"]
SECONDARY_KPI_KEYS = [
    "bmi",
    "total_body_water_pct",
    "visceral_fat",
    "bone_mass",
    "bmr",
    "daily_calorie_intake",
    "physique_rating",
    "abdominal_circumference",
]
COMPOSITION_TREND_KEYS = ["body_fat_pct", "muscle_mass", "total_body_water_pct", "bone_mass"]
METABOLIC_TREND_KEYS = ["metabolic_age", "visceral_fat", "bmi", "bmr", "daily_calorie_intake"]

HEADER_HINTS = [
    "date",
    "fecha",
    "time",
    "hora",
    "weight",
    "peso",
    "fat",
    "grasa",
    "muscle",
    "masa",
    "metabolic",
    "body",
    "arm",
    "brazo",
    "leg",
    "pierna",
    "trunk",
    "tronco",
    "visceral",
    "agua",
    "bmi",
    "imc",
    "bone",
    "hueso",
    "bmr",
    "kcal",
]

NUMERIC_HINTS = [
    "weight",
    "peso",
    "fat",
    "grasa",
    "muscle",
    "masa",
    "water",
    "agua",
    "bone",
    "hueso",
    "bmi",
    "imc",
    "metabolic",
    "metabolica",
    "visceral",
    "bmr",
    "kcal",
    "edad",
    "arm",
    "brazo",
    "leg",
    "pierna",
    "trunk",
    "tronco",
    "kg",
    "%",
]

SOFT_COLORS = {metric: config["color"] for metric, config in METRIC_CONFIG.items()}


st.markdown(
    """
    <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(112, 156, 214, 0.16), transparent 28%),
                linear-gradient(180deg, #edf4fb 0%, #f7fafd 48%, #eef2f7 100%);
            color: #16324f;
        }
        .block-container {
            max-width: 1340px;
            padding-top: 1.35rem;
            padding-bottom: 4rem;
        }
        h1, h2, h3 {
            color: #16324f;
            letter-spacing: -0.02em;
        }
        div[data-testid="stFileUploader"] {
            background: rgba(255, 255, 255, 0.88);
            border: 1px dashed #88a9c9;
            border-radius: 18px;
            padding: 0.75rem 1rem;
            box-shadow: 0 12px 28px rgba(22, 50, 79, 0.07);
        }
        [data-testid="stMetric"] {
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(242, 248, 253, 0.95) 100%);
            border: 1px solid #c9d9ea;
            border-radius: 18px;
            padding: 0.9rem 1rem;
            box-shadow: 0 12px 28px rgba(22, 50, 79, 0.08);
        }
        [data-testid="stMetricLabel"] {
            color: #33597c;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] {
            color: #0f2942;
        }
        div[data-testid="stDataFrame"] {
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #d3dfeb;
            border-radius: 18px;
            padding: 0.35rem;
            box-shadow: 0 12px 28px rgba(22, 50, 79, 0.06);
        }
        .stPlotlyChart {
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #d3dfeb;
            border-radius: 18px;
            padding: 0.35rem 0.35rem 0.15rem 0.35rem;
            box-shadow: 0 12px 28px rgba(22, 50, 79, 0.06);
        }
        .stDownloadButton > button,
        .stButton > button {
            border-radius: 999px;
            border: 1px solid #aec3d9;
            background: linear-gradient(180deg, #ffffff 0%, #eef5fb 100%);
            color: #16324f;
            font-weight: 600;
        }
        .stAlert {
            border-radius: 16px;
        }
        .report-banner {
            display: flex;
            justify-content: space-between;
            gap: 1.25rem;
            align-items: stretch;
            padding: 1.35rem 1.5rem;
            border-radius: 22px;
            background: linear-gradient(135deg, #2e77b7 0%, #4da0dc 52%, #d9eef8 100%);
            color: #ffffff;
            box-shadow: 0 18px 32px rgba(23, 70, 113, 0.18);
            margin-bottom: 1rem;
        }
        .report-banner h1 {
            margin: 0.2rem 0 0.35rem 0;
            color: #ffffff;
            font-size: 2rem;
        }
        .report-banner p {
            margin: 0;
            max-width: 720px;
            color: rgba(255, 255, 255, 0.92);
            line-height: 1.45;
        }
        .report-eyebrow {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            opacity: 0.92;
            font-weight: 700;
        }
        .report-badge-card {
            min-width: 245px;
            background: rgba(255, 255, 255, 0.14);
            border: 1px solid rgba(255, 255, 255, 0.28);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            backdrop-filter: blur(8px);
            align-self: center;
        }
        .report-badge-label {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            opacity: 0.88;
        }
        .report-badge-value {
            margin-top: 0.35rem;
            font-size: 1.25rem;
            font-weight: 700;
        }
        .report-meta-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.8rem;
            margin-bottom: 1.1rem;
        }
        .report-meta-card {
            background: rgba(255, 255, 255, 0.92);
            border: 1px solid #cfdeec;
            border-radius: 18px;
            padding: 0.85rem 1rem;
            box-shadow: 0 10px 22px rgba(22, 50, 79, 0.05);
        }
        .report-meta-card strong {
            display: block;
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #5e7e9b;
            margin-bottom: 0.25rem;
        }
        .report-meta-card span {
            color: #16324f;
            font-size: 1.05rem;
            font-weight: 700;
        }
        .section-header {
            margin: 0.4rem 0 0.9rem 0;
        }
        .section-header h3 {
            margin: 0;
            font-size: 1.28rem;
        }
        .section-header p {
            margin: 0.22rem 0 0 0;
            color: #5f748c;
            font-size: 0.95rem;
        }
        .summary-panel {
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #d0ddea;
            border-radius: 20px;
            padding: 1rem 1.1rem;
            box-shadow: 0 12px 28px rgba(22, 50, 79, 0.06);
            min-height: 100%;
        }
        .summary-panel h4 {
            margin: 0 0 0.8rem 0;
            color: #16324f;
            font-size: 1.06rem;
        }
        .clinical-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.94rem;
        }
        .clinical-table th {
            text-align: left;
            background: #edf5fc;
            color: #355879;
            padding: 0.68rem 0.7rem;
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            border-bottom: 1px solid #d4e0eb;
        }
        .clinical-table td {
            padding: 0.72rem 0.7rem;
            border-bottom: 1px solid #e6edf4;
            color: #183652;
            vertical-align: middle;
        }
        .clinical-table tr:last-child td {
            border-bottom: none;
        }
        .report-pill {
            display: inline-block;
            border-radius: 999px;
            padding: 0.24rem 0.6rem;
            font-size: 0.78rem;
            font-weight: 700;
            border: 1px solid transparent;
        }
        .report-pill.up {
            background: rgba(74, 170, 131, 0.14);
            color: #1f6f48;
            border-color: rgba(74, 170, 131, 0.24);
        }
        .report-pill.down {
            background: rgba(70, 127, 192, 0.14);
            color: #235e94;
            border-color: rgba(70, 127, 192, 0.22);
        }
        .report-pill.warn {
            background: rgba(224, 107, 60, 0.13);
            color: #9b3a10;
            border-color: rgba(224, 107, 60, 0.25);
        }
        .report-pill.neutral {
            background: rgba(148, 163, 184, 0.14);
            color: #52667c;
            border-color: rgba(148, 163, 184, 0.2);
        }
        .insight-stack {
            display: grid;
            gap: 0.75rem;
        }
        .insight-card {
            background: linear-gradient(180deg, #f8fbfe 0%, #eff5fb 100%);
            border: 1px solid #d6e2ee;
            border-radius: 16px;
            padding: 0.82rem 0.92rem;
        }
        .insight-card strong {
            display: block;
            color: #1b4469;
            margin-bottom: 0.18rem;
        }
        .insight-card span {
            color: #4f6782;
            line-height: 1.42;
            font-size: 0.94rem;
        }
        .report-footnote {
            margin-top: 0.7rem;
            font-size: 0.82rem;
            color: #69819b;
        }
        @media (max-width: 980px) {
            .report-banner {
                flex-direction: column;
            }
            .report-meta-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media print {
            header, footer, #MainMenu,
            [data-testid="stToolbar"],
            [data-testid="stDecoration"],
            [data-testid="collapsedControl"],
            section[data-testid="stSidebar"],
            .stDownloadButton,
            .stButton,
            .stFileUploader,
            [data-testid="stStatusWidget"] {
                display: none !important;
                visibility: hidden !important;
            }
            html, body, .stApp, .main, .block-container,
            [data-testid="stAppViewContainer"],
            [data-testid="stHeader"] {
                background: #ffffff !important;
                color: #000000 !important;
                box-shadow: none !important;
            }
            .block-container {
                max-width: 100% !important;
                padding: 0 !important;
                margin: 0 !important;
            }
            .report-banner,
            .report-meta-card,
            .summary-panel,
            [data-testid="stMetric"],
            div[data-testid="stDataFrame"],
            .stPlotlyChart,
            .insight-card {
                background: #ffffff !important;
                color: #000000 !important;
                border: 1px solid #222222 !important;
                box-shadow: none !important;
            }
            .report-banner h1,
            .report-banner p,
            .report-eyebrow,
            .report-meta-card strong,
            .report-meta-card span,
            .summary-panel h4,
            .clinical-table th,
            .clinical-table td,
            .insight-card strong,
            .insight-card span,
            .report-footnote,
            .section-header p {
                color: #000000 !important;
            }
            .report-pill {
                background: #ffffff !important;
                color: #000000 !important;
                border: 1px solid #222222 !important;
            }
            .stPlotlyChart {
                filter: grayscale(100%);
                page-break-inside: avoid;
            }
        }
        
        /* Estilos para las tablas de referencia estilo Tanita */
        .tanita-tracker-table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
            font-size: 0.9rem;
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #c9d9ea;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 12px 28px rgba(22, 50, 79, 0.06);
        }
        .tanita-tracker-table th, .tanita-tracker-table td {
            padding: 0.6rem 0.5rem;
            border: 1px solid #d3dfeb;
            text-align: center;
        }
        .tanita-tracker-table th {
            background: #edf5fc;
            color: #16324f;
            font-size: 0.8rem;
            font-weight: 700;
        }
        .tanita-tracker-table .metric-label-cell {
            text-align: left;
            background: #f4f8fc;
            color: #16324f;
            font-weight: 700;
            min-width: 180px;
            font-size: 0.85rem;
            padding-left: 0.8rem;
        }
        .tanita-tracker-table .metric-label-cell div {
            line-height: 1.2;
        }
        .tanita-tracker-table .lang-en {
            color: #16324f;
            font-size: 0.8rem;
            font-weight: 600;
        }
        .tanita-tracker-table .lang-es {
            color: #5e7e9b;
            font-size: 0.72rem;
            font-weight: 500;
        }
        .tanita-tracker-table .lang-fr {
            color: #7b94ad;
            font-size: 0.72rem;
            font-weight: 500;
        }
        .tanita-ref-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 0.5rem;
            margin-bottom: 1rem;
            font-size: 0.78rem;
            background: rgba(255, 255, 255, 0.95);
            border: 1px solid #d3dfeb;
            border-radius: 8px;
            overflow: hidden;
        }
        .tanita-ref-table th, .tanita-ref-table td {
            padding: 0.4rem 0.5rem;
            border: 1px solid #e6edf4;
            text-align: center;
        }
        .tanita-ref-table th {
            background: #f0f6fc;
            color: #355879;
            font-weight: 700;
        }
        .tanita-ref-table.compact-table td {
            padding: 0.25rem 0.4rem;
        }
        .ref-highlight-row {
            background-color: #f0f7ff !important;
            font-weight: 600;
        }
        .ref-highlight-row td {
            border-top: 1px solid #85bbf0 !important;
            border-bottom: 1px solid #85bbf0 !important;
            color: #0d2c4c !important;
        }
        .ref-highlight-cell {
            background: linear-gradient(135deg, #2e77b7 0%, #4da0dc 100%) !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            box-shadow: inset 0 0 4px rgba(0,0,0,0.15);
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_html_block(html: str) -> None:
    st.markdown(dedent(html).strip(), unsafe_allow_html=True)


def render_table_html(html: str) -> None:
    st.html(_TANITA_TABLE_CSS + html)


def normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().strip()
    normalized = normalized.replace("%", " pct ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def deduplicate_columns(columns: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    resolved: List[str] = []
    for raw in columns:
        base = str(raw).strip() or "columna"
        count = seen.get(base, 0)
        resolved.append(base if count == 0 else f"{base}_{count + 1}")
        seen[base] = count + 1
    return resolved


def decode_csv(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("No se pudo decodificar el archivo CSV.")


def score_header_candidate(line: str) -> int:
    parts = [normalize_label(part) for part in re.split(r"[;,\t]", line) if part.strip()]
    if not parts:
        return -1
    joined = " ".join(parts)
    score = sum(1 for hint in HEADER_HINTS if hint in joined)
    if len(parts) >= 4:
        score += 1
    return score


def trim_to_header(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("El archivo CSV está vacío.")
    best_index = 0
    best_score = -1
    for index, line in enumerate(lines[:12]):
        score = score_header_candidate(line)
        if score > best_score:
            best_index = index
            best_score = score
    return "\n".join(lines[best_index:])


def load_raw_dataframe(text: str) -> pd.DataFrame:
    errors: List[str] = []
    for separator in (None, ";", ",", "\t"):
        try:
            dataframe = pd.read_csv(
                io.StringIO(text),
                sep=separator,
                engine="python",
                dtype=str,
                skip_blank_lines=True,
            )
            if dataframe.shape[1] >= 2:
                return dataframe
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("No se pudo interpretar el CSV. Verifica que el archivo DATAX.CSV sea válido.")


def parse_numeric_value(value: object) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "n/a", "na", "-"}:
        return None
    text = text.replace("−", "-")
    text = re.sub(r"[^0-9,\.\-]", "", text)
    if not text or text in {"-", ".", ",", "-.", "-,"}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    elif text.count(",") > 1 and "." not in text:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_datetime_series(series: pd.Series) -> pd.Series:
    cleaned = series.fillna("").astype(str).str.strip()
    parsed = pd.to_datetime(cleaned, errors="coerce", dayfirst=True)
    if parsed.notna().sum() == 0:
        parsed = pd.to_datetime(cleaned, errors="coerce", dayfirst=False)
    return parsed


def is_segmental(normalized_name: str) -> bool:
    return any(token in normalized_name for token in ["arm", "brazo", "leg", "pierna", "trunk", "tronco"])


def should_skip_metric(metric_key: str, normalized_name: str) -> bool:
    if metric_key in {"body_fat_pct", "muscle_mass"} and is_segmental(normalized_name):
        return True
    if metric_key == "body_fat_pct" and "visceral" in normalized_name:
        return True
    if metric_key == "weight" and any(token in normalized_name for token in ["target", "goal", "ideal"]):
        return True
    return False


def find_best_column(columns: List[str], aliases: List[str], metric_key: Optional[str] = None) -> Optional[str]:
    best_column: Optional[str] = None
    best_score = 0
    for column in columns:
        normalized_column = normalize_label(column)
        if metric_key and should_skip_metric(metric_key, normalized_column):
            continue
        for alias in aliases:
            normalized_alias = normalize_label(alias)
            if normalized_column == normalized_alias:
                score = 100
            elif normalized_column.startswith(normalized_alias):
                score = 85
            elif normalized_alias in normalized_column:
                score = 70
            elif set(normalized_alias.split()).issubset(set(normalized_column.split())):
                score = 60
            else:
                score = 0
            if score > best_score:
                best_score = score
                best_column = column
    return best_column


def should_convert_to_numeric(series: pd.Series, column_name: str) -> bool:
    normalized_name = normalize_label(column_name)
    if any(token in normalized_name for token in ["date", "fecha", "time", "hora"]):
        return False
    non_empty = series.fillna("").astype(str).str.strip().ne("")
    if not non_empty.any():
        return False
    numeric_candidate = series.map(parse_numeric_value)
    ratio = numeric_candidate.notna().sum() / non_empty.sum()
    if ratio >= 0.75:
        return True
    return any(hint in normalized_name for hint in NUMERIC_HINTS) and numeric_candidate.notna().sum() > 0


def identify_segmental_columns(columns: List[str]) -> Dict[str, Dict[Tuple[str, str], str]]:
    grouped: Dict[str, Dict[Tuple[str, str], str]] = {"muscle": {}, "fat": {}}
    for column in columns:
        normalized = normalize_label(column)
        part = None
        side = None
        if any(token in normalized for token in ["arm", "brazo"]):
            part = "arm"
        elif any(token in normalized for token in ["leg", "pierna"]):
            part = "leg"
        elif any(token in normalized for token in ["trunk", "tronco"]):
            part = "trunk"
        if any(token in normalized for token in ["left", "izq", "izquierda"]):
            side = "left"
        elif any(token in normalized for token in ["right", "der", "derecha"]):
            side = "right"
        elif part == "trunk":
            side = "center"
        if part is None or side is None:
            continue
        if any(token in normalized for token in ["muscle", "masa muscular"]):
            grouped["muscle"][(part, side)] = column
        elif any(token in normalized for token in ["fat", "grasa"]):
            grouped["fat"][(part, side)] = column
    return grouped


def choose_segmental_family(
    grouped_segments: Dict[str, Dict[Tuple[str, str], str]],
) -> Tuple[Optional[str], Dict[Tuple[str, str], str]]:
    ranked = sorted(
        grouped_segments.items(),
        key=lambda item: (
            len({part for part, _ in item[1].keys() if part in {"arm", "leg"}}),
            len(item[1]),
            1 if item[0] == "muscle" else 0,
        ),
        reverse=True,
    )
    for family, values in ranked:
        if any(part in {"arm", "leg"} for part, _ in values.keys()):
            return family, values
    return None, {}


@st.cache_data(show_spinner=False)
def process_csv(file_bytes: bytes) -> Tuple[pd.DataFrame, Dict[str, object]]:
    text = decode_csv(file_bytes)
    trimmed_text = trim_to_header(text)
    raw_df = load_raw_dataframe(trimmed_text)
    raw_df = raw_df.dropna(how="all").copy()
    raw_df.columns = deduplicate_columns([str(column).strip() for column in raw_df.columns])
    raw_df = raw_df.loc[:, [column for column in raw_df.columns if not str(column).startswith("Unnamed")]]
    if raw_df.empty:
        raise ValueError("No se encontraron filas utilizables en el CSV.")

    processed_df = raw_df.copy()
    column_names = list(processed_df.columns)
    detected_metrics = {
        metric: find_best_column(column_names, aliases, metric)
        for metric, aliases in ALIAS_MAP.items()
        if metric not in {"date", "time", "datetime"}
    }
    datetime_column = find_best_column(column_names, ALIAS_MAP["datetime"], "datetime")
    date_column = find_best_column(column_names, ALIAS_MAP["date"], "date")
    time_column = find_best_column(column_names, ALIAS_MAP["time"], "time")

    if datetime_column:
        measurement_datetime = parse_datetime_series(processed_df[datetime_column])
    elif date_column and time_column:
        measurement_datetime = parse_datetime_series(
            processed_df[date_column].fillna("").astype(str).str.strip()
            + " "
            + processed_df[time_column].fillna("").astype(str).str.strip()
        )
    elif date_column:
        measurement_datetime = parse_datetime_series(processed_df[date_column])
    else:
        raise ValueError("No se encontró una columna de fecha reconocible en el archivo DATAX.CSV.")

    processed_df.insert(0, "Fecha medición", measurement_datetime)

    for column in processed_df.columns:
        series = processed_df[column]
        if should_convert_to_numeric(series, column):
            processed_df[column] = series.map(parse_numeric_value)

    processed_df = processed_df.dropna(how="all")
    processed_df = (
        processed_df[processed_df["Fecha medición"].notna()].sort_values("Fecha medición").reset_index(drop=True)
    )
    if processed_df.empty:
        raise ValueError("No se pudieron interpretar fechas válidas en el archivo.")

    segments = identify_segmental_columns(list(processed_df.columns))
    segment_family, segment_columns = choose_segmental_family(segments)

    metadata: Dict[str, object] = {
        "metric_columns": detected_metrics,
        "segment_family": segment_family,
        "segment_columns": segment_columns,
    }
    return processed_df, metadata


def format_metric_value(value: object, suffix: str = "", decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/D"
    if decimals == 0:
        formatted_value = f"{value:.0f}"
    else:
        formatted_value = f"{value:.{decimals}f}"
    if suffix:
        return f"{formatted_value} {suffix}"
    return formatted_value


def format_delta_value(delta: float, suffix: str = "", decimals: int = 1) -> str:
    if decimals == 0:
        formatted = f"{delta:+.0f}"
    else:
        formatted = f"{delta:+.{decimals}f}"
    if suffix:
        return f"{formatted} {suffix}"
    return formatted


def get_metric_series(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_key: str,
) -> Optional[pd.Series]:
    column = metric_columns.get(metric_key)
    if not column or column not in dataframe.columns:
        return None
    series = dataframe[column].dropna()
    if series.empty:
        return None
    return series


def build_delta_badge(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_key: str,
) -> str:
    series = get_metric_series(dataframe, metric_columns, metric_key)
    if series is None or len(series) < 2:
        return '<span class="report-pill neutral">Sin histórico</span>'
    delta = float(series.iloc[-1] - series.iloc[0])
    decimals = int(METRIC_CONFIG[metric_key]["decimals"])
    suffix = str(METRIC_CONFIG[metric_key]["suffix"])
    threshold = 1.0 if decimals == 0 else 0.1
    if abs(delta) < threshold:
        return '<span class="report-pill neutral">Estable</span>'
    higher_is_better = METRIC_CONFIG[metric_key].get("higher_is_better")
    if higher_is_better is None:
        css_class = "down" if delta > 0 else "neutral"
    elif (higher_is_better and delta > 0) or (not higher_is_better and delta < 0):
        css_class = "up"
    else:
        css_class = "warn"
    delta_label = format_delta_value(delta, suffix, decimals)
    return f'<span class="report-pill {css_class}">{delta_label} vs inicio</span>'


def build_trend_sentence(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_key: str,
) -> Optional[Tuple[str, str]]:
    series = get_metric_series(dataframe, metric_columns, metric_key)
    if series is None or len(series) < 2:
        return None
    delta = float(series.iloc[-1] - series.iloc[0])
    decimals = int(METRIC_CONFIG[metric_key]["decimals"])
    suffix = str(METRIC_CONFIG[metric_key]["suffix"])
    threshold = 1.0 if decimals == 0 else 0.1
    if abs(delta) < threshold:
        detail = "Sin variaciones relevantes frente al primer registro."
    else:
        movement = "incremento" if delta > 0 else "descenso"
        delta_text = format_delta_value(abs(delta), suffix, decimals).lstrip("+")
        higher_is_better = METRIC_CONFIG[metric_key].get("higher_is_better")
        if higher_is_better is None:
            context = ""
        elif (higher_is_better and delta > 0) or (not higher_is_better and delta < 0):
            context = " · tendencia favorable."
        else:
            context = " · tendencia a vigilar."
        detail = f"{movement.capitalize()} de {delta_text} desde el inicio{context}"
    return METRIC_CONFIG[metric_key]["label"], detail


def collect_trend_insights(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    limit: int = 4,
) -> List[Tuple[str, str]]:
    insights = [
        build_trend_sentence(dataframe, metric_columns, key)
        for key in [
            "weight",
            "body_fat_pct",
            "muscle_mass",
            "total_body_water_pct",
            "visceral_fat",
            "metabolic_age",
        ]
    ]
    filtered_insights = [insight for insight in insights if insight is not None]
    if not filtered_insights:
        return [("Seguimiento", "No hay suficiente histórico para resumir la evolución.")]
    return filtered_insights[:limit]


def collect_metric_snapshot(
    latest_row: pd.Series,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
) -> List[Tuple[str, str]]:
    snapshot = []
    for key in metric_keys:
        if not metric_columns.get(key):
            continue
        snapshot.append(
            (
                METRIC_CONFIG[key]["label"],
                format_metric_value(
                    latest_row.get(metric_columns[key]),
                    str(METRIC_CONFIG[key]["suffix"]),
                    int(METRIC_CONFIG[key]["decimals"]),
                ),
            )
        )
    return snapshot


def create_metric_line_figure(dataframe: pd.DataFrame, column: str, label: str, color: str) -> Optional[go.Figure]:
    chart_df = dataframe[["Fecha medición", column]].dropna()
    if chart_df.empty:
        return None
    figure = px.line(
        chart_df,
        x="Fecha medición",
        y=column,
        markers=True,
        template="plotly_white",
        color_discrete_sequence=[color],
    )
    figure.update_layout(
        title=label,
        margin=dict(l=20, r=20, t=55, b=20),
        hovermode="x unified",
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(249,252,255,0.98)",
        font=dict(color="#16324f"),
        xaxis_title="Fecha",
        yaxis_title=label,
        title_x=0.03,
    )
    figure.update_traces(
        line=dict(width=3),
        marker=dict(size=8, line=dict(width=1, color="#ffffff")),
    )
    figure.update_xaxes(showgrid=True, gridcolor="#d9e5f0")
    figure.update_yaxes(showgrid=True, gridcolor="#d9e5f0")
    return figure


def create_metric_group_figure(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
    title: str,
) -> Optional[go.Figure]:
    selected_keys = [key for key in metric_keys if metric_columns.get(key)]
    if not selected_keys:
        return None
    long_frames = []
    for key in selected_keys:
        column = metric_columns[key]
        metric_df = dataframe[["Fecha medición", column]].dropna().copy()
        if metric_df.empty:
            continue
        metric_df.columns = ["Fecha medición", "Valor"]
        metric_df["Métrica"] = DISPLAY_NAMES[key]
        long_frames.append(metric_df)
    if not long_frames:
        return None
    composition_df = pd.concat(long_frames, ignore_index=True)
    figure = px.line(
        composition_df,
        x="Fecha medición",
        y="Valor",
        color="Métrica",
        facet_row="Métrica",
        markers=True,
        template="plotly_white",
        color_discrete_map={DISPLAY_NAMES[key]: SOFT_COLORS[key] for key in selected_keys},
    )
    figure.for_each_annotation(lambda annotation: annotation.update(text=annotation.text.split("=")[-1]))
    figure.update_layout(
        title=title,
        height=240 * len(long_frames),
        hovermode="x unified",
        margin=dict(l=20, r=20, t=55, b=20),
        showlegend=False,
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(249,252,255,0.98)",
        font=dict(color="#16324f"),
        title_x=0.03,
    )
    figure.update_xaxes(title_text="Fecha")
    figure.update_yaxes(title_text="Valor", showgrid=True, gridcolor="#d9e5f0")
    return figure


def build_segmental_chart_data(
    dataframe: pd.DataFrame,
    family: Optional[str],
    segment_columns: Dict[Tuple[str, str], str],
) -> Tuple[Optional[pd.DataFrame], List[Tuple[str, float]], str]:
    if not family or not segment_columns:
        return None, [], "Análisis segmental"
    latest_row = dataframe.iloc[-1]
    chart_rows = []
    part_labels = {"arm": "Brazo", "leg": "Pierna", "trunk": "Tronco"}
    side_labels = {"left": "Izquierda", "right": "Derecha", "center": "Central"}
    for (part, side), column in segment_columns.items():
        value = latest_row.get(column)
        if value is None or pd.isna(value):
            continue
        chart_rows.append(
            {
                "Segmento": part_labels.get(part, part.title()),
                "Lado": side_labels.get(side, side.title()),
                "Valor": float(value),
                "Parte": part,
                "Lateralidad": side,
            }
        )
    chart_df = pd.DataFrame(chart_rows)
    if chart_df.empty or not chart_df["Segmento"].isin(["Brazo", "Pierna"]).any():
        return None, [], "Análisis segmental"
    title = "Análisis segmental"
    if family == "muscle":
        title += " de masa muscular"
    elif family == "fat":
        title += " de grasa"
    radar_order = [
        ("arm", "left", "Brazo izq"),
        ("leg", "left", "Pierna izq"),
        ("trunk", "center", "Tronco"),
        ("leg", "right", "Pierna der"),
        ("arm", "right", "Brazo der"),
    ]
    radar_rows = []
    for part, side, label in radar_order:
        match = chart_df[(chart_df["Parte"] == part) & (chart_df["Lateralidad"] == side)]
        if not match.empty:
            radar_rows.append((label, float(match.iloc[0]["Valor"])))
    return chart_df, radar_rows, title


def create_segmental_bar_figure(chart_df: pd.DataFrame, title: str) -> go.Figure:
    figure = px.bar(
        chart_df,
        x="Segmento",
        y="Valor",
        color="Lado",
        barmode="group",
        template="plotly_white",
        color_discrete_sequence=["#78A9FF", "#70C9B0", "#C4D2E3"],
        title=title,
    )
    figure.update_layout(
        margin=dict(l=20, r=20, t=55, b=20),
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(249,252,255,0.98)",
        xaxis_title="Segmento",
        yaxis_title="Valor",
        font=dict(color="#16324f"),
        title_x=0.03,
    )
    figure.update_yaxes(showgrid=True, gridcolor="#d9e5f0")
    return figure


def create_segmental_radar_figure(radar_rows: List[Tuple[str, float]], title: str) -> Optional[go.Figure]:
    if not radar_rows:
        return None
    theta = [label for label, _ in radar_rows]
    values = [value for _, value in radar_rows]
    theta.append(theta[0])
    values.append(values[0])
    radar_figure = go.Figure()
    radar_figure.add_trace(
        go.Scatterpolar(
            r=values,
            theta=theta,
            fill="toself",
            line=dict(color="#2e77b7", width=3),
            fillcolor="rgba(46, 119, 183, 0.18)",
            name="Perfil segmental",
        )
    )
    radar_figure.update_layout(
        title=f"{title} · perfil radial",
        margin=dict(l=20, r=20, t=55, b=20),
        paper_bgcolor="rgba(255,255,255,0)",
        font=dict(color="#16324f"),
        title_x=0.03,
        polar=dict(
            bgcolor="rgba(249,252,255,0.98)",
            radialaxis=dict(showgrid=True, gridcolor="#d9e5f0", linecolor="#d9e5f0"),
            angularaxis=dict(gridcolor="#e0e9f1", linecolor="#d9e5f0"),
        ),
        showlegend=False,
    )
    return radar_figure


def render_section_header(title: str, subtitle: str) -> None:
    render_html_block(
        f"""
        <div class="section-header">
            <h3>{title}</h3>
            <p>{subtitle}</p>
        </div>
        """
    )


def render_report_header(file_name: str, dataframe: pd.DataFrame, detected_labels: List[str]) -> None:
    latest_measurement = dataframe["Fecha medición"].max()
    first_measurement = dataframe["Fecha medición"].min()
    metric_count = len(detected_labels)
    render_html_block(
        f"""
        <div class="report-banner">
            <div>
                <div class="report-eyebrow">Body composition analyser report</div>
                <h1>Informe clínico de composición corporal</h1>
                <p>
                    Visualización longitudinal con estética de informe técnico, enfocada en lectura rápida,
                    trazabilidad temporal y presentación profesional en consulta o impresión.
                </p>
            </div>
            <div class="report-badge-card">
                <div class="report-badge-label">Última medición registrada</div>
                <div class="report-badge-value">{latest_measurement:%d/%m/%Y %H:%M}</div>
            </div>
        </div>
        <div class="report-meta-grid">
            <div class="report-meta-card">
                <strong>Archivo fuente</strong>
                <span>{Path(file_name).name}</span>
            </div>
            <div class="report-meta-card">
                <strong>Periodo analizado</strong>
                <span>{first_measurement:%d/%m/%Y} - {latest_measurement:%d/%m/%Y}</span>
            </div>
            <div class="report-meta-card">
                <strong>Registros válidos</strong>
                <span>{len(dataframe)}</span>
            </div>
            <div class="report-meta-card">
                <strong>Métricas detectadas</strong>
                <span>{metric_count}</span>
            </div>
        </div>
        """
    )


def render_summary_panels(
    dataframe: pd.DataFrame,
    latest_row: pd.Series,
    metric_columns: Dict[str, Optional[str]],
) -> None:
    summary_keys = [key for key in PRIMARY_KPI_KEYS + SECONDARY_KPI_KEYS if metric_columns.get(key)]
    if not summary_keys:
        return
    summary_rows = []
    for key in summary_keys:
        current_value = format_metric_value(
            latest_row.get(metric_columns[key]),
            str(METRIC_CONFIG[key]["suffix"]),
            int(METRIC_CONFIG[key]["decimals"]),
        )
        summary_rows.append(
            (
                f"<tr>"
                f"<td>{METRIC_CONFIG[key]['label']}</td>"
                f"<td>{current_value}</td>"
                f"<td>{build_delta_badge(dataframe, metric_columns, key)}</td>"
                f"</tr>"
            )
        )

    insights = collect_trend_insights(dataframe, metric_columns, limit=4)

    left_col, right_col = st.columns([1.25, 1])
    with left_col:
        render_html_block(
            f"""
            <div class="summary-panel">
                <h4>Resumen de medición actual</h4>
                <table class="clinical-table">
                    <thead>
                        <tr>
                            <th>Métrica</th>
                            <th>Valor actual</th>
                            <th>Evolución</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(summary_rows)}
                    </tbody>
                </table>
                <div class="report-footnote">
                    Comparativa automática respecto al primer registro válido del archivo cargado.
                </div>
            </div>
            """
        )
    with right_col:
        insight_cards = "".join(
            [
                (f'<div class="insight-card">' f"<strong>{title}</strong>" f"<span>{detail}</span>" f"</div>")
                for title, detail in insights
            ]
        )
        render_html_block(
            f"""
            <div class="summary-panel">
                <h4>Lectura longitudinal</h4>
                <div class="insight-stack">
                    {insight_cards}
                </div>
                <div class="report-footnote">
                    Presentación diseñada para consulta clínica, seguimiento personal y revisión en impresión.
                </div>
            </div>
            """
        )


def render_metric_cards(
    latest_row: pd.Series,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
    columns_per_row: int = 4,
    prev_row: Optional[pd.Series] = None,
) -> bool:
    available_keys = [key for key in metric_keys if metric_columns.get(key)]
    if not available_keys:
        return False
    for start in range(0, len(available_keys), columns_per_row):
        chunk = available_keys[start : start + columns_per_row]
        card_columns = st.columns(len(chunk))
        for card_column, key in zip(card_columns, chunk):
            with card_column:
                col = metric_columns[key]
                decimals = int(METRIC_CONFIG[key]["decimals"])
                suffix = str(METRIC_CONFIG[key]["suffix"])
                delta_display = None
                delta_color = "off"
                if prev_row is not None and col in prev_row:
                    cur_val = latest_row.get(col)
                    prv_val = prev_row.get(col)
                    if cur_val is not None and prv_val is not None and not pd.isna(cur_val) and not pd.isna(prv_val):
                        delta_val = float(cur_val) - float(prv_val)
                        delta_display = format_delta_value(delta_val, suffix, decimals)
                        hib = METRIC_CONFIG[key].get("higher_is_better")
                        if hib is True:
                            delta_color = "normal"
                        elif hib is False:
                            delta_color = "inverse"
                        else:
                            delta_color = "off"
                st.metric(
                    METRIC_CONFIG[key]["label"],
                    format_metric_value(latest_row.get(col), suffix, decimals),
                    delta=delta_display,
                    delta_color=delta_color,
                )
    return True


def build_excel_bytes(dataframe: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    export_df = dataframe.copy()
    for column in export_df.select_dtypes(include=["datetime64[ns]"]).columns:
        export_df[column] = export_df[column].dt.strftime("%Y-%m-%d %H:%M")
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Tanita")
    buffer.seek(0)
    return buffer.getvalue()


def save_matplotlib_figure(fig: plt.Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


def style_pdf_axis(ax, title: str, y_label: str) -> None:
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold", color="#16324f", pad=10)
    ax.set_ylabel(y_label, fontsize=9, color="#355879")
    ax.grid(True, axis="y", color="#d9e5f0", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.set_facecolor("#f9fcff")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d9e5f0")
    ax.spines["bottom"].set_color("#d9e5f0")
    ax.tick_params(axis="x", colors="#355879", labelsize=8)
    ax.tick_params(axis="y", colors="#355879", labelsize=8)


def build_pdf_line_chart_image(dataframe: pd.DataFrame, column: str, label: str, color: str) -> Optional[bytes]:
    chart_df = dataframe[["Fecha medición", column]].dropna()
    if chart_df.empty:
        return None
    fig, ax = plt.subplots(figsize=(8.8, 3.25))
    fig.patch.set_facecolor("#f9fcff")
    x_values = chart_df["Fecha medición"]
    y_values = chart_df[column].astype(float)
    ax.plot(
        x_values,
        y_values,
        color=color,
        linewidth=2.8,
        marker="o",
        markersize=4.6,
    )
    ax.fill_between(x_values, y_values, y_values.min(), color=color, alpha=0.12)
    style_pdf_axis(ax, label, label)
    ax.set_xlabel("Fecha", fontsize=9, color="#355879")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%y"))
    last_x = x_values.iloc[-1]
    last_y = float(y_values.iloc[-1])
    ax.scatter([last_x], [last_y], s=42, color="#16324f", zorder=5)
    ax.annotate(
        format_metric_value(last_y, "", 1),
        xy=(last_x, last_y),
        xytext=(8, -10),
        textcoords="offset points",
        fontsize=8,
        color="#16324f",
        fontweight="bold",
    )
    fig.autofmt_xdate(rotation=18)
    fig.tight_layout()
    return save_matplotlib_figure(fig)


def build_pdf_indexed_trend_chart_image(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
    title: str,
) -> Optional[bytes]:
    selected_keys = [key for key in metric_keys if metric_columns.get(key)]
    if not selected_keys:
        return None
    fig, ax = plt.subplots(figsize=(6.7, 2.85))
    fig.patch.set_facecolor("#f9fcff")
    plotted = False
    for key in selected_keys:
        column = metric_columns[key]
        metric_df = dataframe[["Fecha medición", column]].dropna().copy()
        if metric_df.empty:
            continue
        base_value = float(metric_df.iloc[0, 1])
        if abs(base_value) < 1e-9:
            continue
        indexed_values = (metric_df.iloc[:, 1].astype(float) / base_value) * 100
        ax.plot(
            metric_df["Fecha medición"],
            indexed_values,
            label=DISPLAY_NAMES[key],
            color=SOFT_COLORS[key],
            linewidth=2.2,
        )
        ax.scatter(
            metric_df["Fecha medición"].iloc[-1],
            indexed_values.iloc[-1],
            color=SOFT_COLORS[key],
            s=26,
            zorder=5,
        )
        plotted = True
    if not plotted:
        plt.close(fig)
        return None
    ax.axhline(100, color="#94a3b8", linewidth=1, linestyle="--")
    style_pdf_axis(ax, title, "Índice")
    ax.set_xlabel("Fecha", fontsize=8.5, color="#355879")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%y"))
    ax.legend(loc="upper left", frameon=False, fontsize=7.5, ncol=2)
    fig.autofmt_xdate(rotation=16)
    fig.tight_layout()
    return save_matplotlib_figure(fig)


def build_pdf_delta_chart_image(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
    title: str,
) -> Optional[bytes]:
    rows = []
    for key in metric_keys:
        column = metric_columns.get(key)
        if not column:
            continue
        series = dataframe[column].dropna()
        if len(series) < 2:
            continue
        rows.append(
            {
                "metric": METRIC_CONFIG[key]["label"],
                "delta": float(series.iloc[-1] - series.iloc[0]),
                "color": SOFT_COLORS[key],
                "suffix": str(METRIC_CONFIG[key]["suffix"]),
                "decimals": int(METRIC_CONFIG[key]["decimals"]),
            }
        )
    if not rows:
        return None
    delta_df = pd.DataFrame(rows).sort_values("delta")
    fig, ax = plt.subplots(figsize=(6.7, 2.85))
    fig.patch.set_facecolor("#f9fcff")
    ax.barh(delta_df["metric"], delta_df["delta"], color=delta_df["color"], alpha=0.88)
    ax.axvline(0, color="#94a3b8", linewidth=1)
    style_pdf_axis(ax, title, "Cambio")
    ax.set_xlabel("Variación vs inicio", fontsize=8.5, color="#355879")
    for index, row in delta_df.reset_index(drop=True).iterrows():
        delta_text = format_delta_value(row["delta"], row["suffix"], row["decimals"])
        horizontal_alignment = "left" if row["delta"] >= 0 else "right"
        offset = 0.02 * max(abs(delta_df["delta"]).max(), 1)
        text_x = row["delta"] + offset if row["delta"] >= 0 else row["delta"] - offset
        ax.text(text_x, index, delta_text, va="center", ha=horizontal_alignment, fontsize=7.5, color="#16324f")
    fig.tight_layout()
    return save_matplotlib_figure(fig)


def build_pdf_group_chart_image(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
    title: str,
) -> Optional[bytes]:
    selected_keys = [key for key in metric_keys if metric_columns.get(key)]
    if not selected_keys:
        return None
    metric_frames: List[Tuple[str, pd.DataFrame]] = []
    for key in selected_keys:
        column = metric_columns[key]
        metric_df = dataframe[["Fecha medición", column]].dropna().copy()
        if metric_df.empty:
            continue
        metric_frames.append((key, metric_df))
    if not metric_frames:
        return None

    fig, axes = plt.subplots(len(metric_frames), 1, figsize=(9.4, max(3.0 * len(metric_frames), 3.6)))
    fig.patch.set_facecolor("#f9fcff")
    if len(metric_frames) == 1:
        axes = [axes]
    for ax, (key, metric_df) in zip(axes, metric_frames):
        ax.plot(
            metric_df["Fecha medición"],
            metric_df.iloc[:, 1],
            color=SOFT_COLORS[key],
            linewidth=2.3,
            marker="o",
            markersize=4,
        )
        style_pdf_axis(ax, DISPLAY_NAMES[key], DISPLAY_NAMES[key])
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%y"))
    axes[-1].set_xlabel("Fecha", fontsize=9, color="#355879")
    fig.suptitle(title, x=0.03, y=0.995, ha="left", fontsize=13, fontweight="bold", color="#16324f")
    fig.tight_layout()
    return save_matplotlib_figure(fig)


def build_pdf_segment_chart_image(chart_df: Optional[pd.DataFrame], title: str) -> Optional[bytes]:
    if chart_df is None or chart_df.empty:
        return None
    pivot_df = chart_df.pivot_table(index="Segmento", columns="Lado", values="Valor", aggfunc="mean").fillna(0)
    if pivot_df.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    fig.patch.set_facecolor("#f9fcff")
    x_positions = np.arange(len(pivot_df.index))
    sides = list(pivot_df.columns)
    bar_width = 0.75 / max(len(sides), 1)
    color_map = {
        "Izquierda": "#78A9FF",
        "Derecha": "#70C9B0",
        "Central": "#C4D2E3",
    }
    offsets = np.linspace(-(len(sides) - 1) / 2, (len(sides) - 1) / 2, len(sides)) * bar_width
    for index, side in enumerate(sides):
        ax.bar(
            x_positions + offsets[index],
            pivot_df[side].values,
            width=bar_width,
            label=side,
            color=color_map.get(side, "#9DB4C0"),
        )
    ax.set_xticks(x_positions)
    ax.set_xticklabels(pivot_df.index.tolist())
    style_pdf_axis(ax, title, "Valor")
    ax.set_xlabel("Segmento", fontsize=9, color="#355879")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    return save_matplotlib_figure(fig)


def build_pdf_summary_panels(
    dataframe: pd.DataFrame,
    latest_row: pd.Series,
    metric_columns: Dict[str, Optional[str]],
    styles,
):
    title_s = ParagraphStyle("PDFPanelTitle", fontName="Helvetica-Bold", fontSize=8.5, leading=10,
                              textColor=colors.HexColor("#16324f"))
    hdr_s = ParagraphStyle("PDFTblHdr", fontName="Helvetica-Bold", fontSize=6.5, leading=8,
                            textColor=colors.HexColor("#355879"))
    cell_s = ParagraphStyle("PDFTblCell", fontName="Helvetica", fontSize=6.5, leading=8,
                             textColor=colors.HexColor("#183652"))
    insight_s = ParagraphStyle("PDFInsight", fontName="Helvetica", fontSize=7, leading=9,
                                textColor=colors.HexColor("#4f6782"))

    # ── Left: summary table ──────────────────────────────────────────────────
    summary_keys = [k for k in PRIMARY_KPI_KEYS + SECONDARY_KPI_KEYS if metric_columns.get(k)]
    tbl_data = [[Paragraph("<b>Métrica</b>", hdr_s),
                 Paragraph("<b>Valor actual</b>", hdr_s),
                 Paragraph("<b>Evolución</b>", hdr_s)]]
    tbl_style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d3dfeb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf5fc")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for _k in summary_keys:
        _col = metric_columns[_k]
        _cur = format_metric_value(latest_row.get(_col), str(METRIC_CONFIG[_k]["suffix"]),
                                   int(METRIC_CONFIG[_k]["decimals"]))
        _series = dataframe[_col].dropna() if _col in dataframe.columns else pd.Series(dtype=float)
        if len(_series) >= 2:
            _delta = float(_series.iloc[-1] - _series.iloc[0])
            _thr = 1.0 if int(METRIC_CONFIG[_k]["decimals"]) == 0 else 0.1
            if abs(_delta) < _thr:
                _dt, _dc = "Estable", "#52667c"
            else:
                _hib = METRIC_CONFIG[_k].get("higher_is_better")
                _dt = format_delta_value(_delta, str(METRIC_CONFIG[_k]["suffix"]),
                                         int(METRIC_CONFIG[_k]["decimals"])) + " vs inicio"
                if _hib is None:
                    _dc = "#52667c"
                elif (_hib and _delta > 0) or (not _hib and _delta < 0):
                    _dc = "#1f6f48"
                else:
                    _dc = "#9b3a10"
        else:
            _dt, _dc = "Sin histórico", "#52667c"
        _ds = ParagraphStyle(f"DS_{_k}", parent=cell_s, textColor=colors.HexColor(_dc))
        tbl_data.append([Paragraph(METRIC_CONFIG[_k]["label"], cell_s),
                         Paragraph(_cur, cell_s),
                         Paragraph(_dt, _ds)])
    summary_tbl = Table(tbl_data, colWidths=[120, 72, 110])
    summary_tbl.setStyle(TableStyle(tbl_style))
    left_content = [Paragraph("Resumen de medición actual", title_s), Spacer(1, 5), summary_tbl]

    # ── Right: longitudinal insights ─────────────────────────────────────────
    insights = collect_trend_insights(dataframe, metric_columns, limit=8)
    right_content: list = [Paragraph("Lectura longitudinal", title_s), Spacer(1, 5)]
    for _title, _detail in insights:
        right_content.append(Paragraph(f"<b>{_title}.</b> {_detail}", insight_s))
        right_content.append(Spacer(1, 5))

    # ── Outer 2-column panel ─────────────────────────────────────────────────
    _panel_style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#d0ddea")),
        ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#d0ddea")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (0, 0), 9),
        ("RIGHTPADDING", (1, 0), (1, 0), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    panels = Table([[left_content, right_content]], colWidths=[326, 479])
    panels.setStyle(TableStyle(_panel_style))
    return panels


def build_pdf_chart_flowable(
    image_bytes: Optional[bytes],
    width: int,
    height: int,
    styles,
    fallback_text: str,
):
    if image_bytes is None:
        return Paragraph(fallback_text, styles["BodyText"])
    return RLImage(io.BytesIO(image_bytes), width=width, height=height)


def build_pdf_comment_box(
    latest_row: pd.Series,
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    styles,
):
    heading_style = styles["Heading2"].clone("pdf_heading")
    heading_style.fontSize = 12
    heading_style.leading = 14
    heading_style.textColor = colors.HexColor("#16324f")

    body_style = styles["BodyText"].clone("pdf_body")
    body_style.fontSize = 8.2
    body_style.leading = 10.5
    body_style.textColor = colors.HexColor("#355879")

    snapshot = collect_metric_snapshot(latest_row, metric_columns, PRIMARY_KPI_KEYS + ["bmi"])[:4]
    insights = collect_trend_insights(dataframe, metric_columns, limit=3)

    content = [Paragraph("Lectura ejecutiva", heading_style), Spacer(1, 5)]
    for label, value in snapshot:
        content.append(Paragraph(f"<b>{label}:</b> {value}", body_style))
    content.append(Spacer(1, 6))
    for title, detail in insights:
        content.append(Paragraph(f"<b>{title}.</b> {detail}", body_style))
        content.append(Spacer(1, 3))

    panel = Table([[content]], colWidths=[238])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f9fc")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#cfddeb")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return panel


def build_pdf_header_panel(
    source_name: str,
    first_measurement: pd.Timestamp,
    last_measurement: pd.Timestamp,
    dataframe: pd.DataFrame,
    styles,
):
    title_style = styles["Title"].clone("pdf_header_title")
    title_style.fontSize = 18
    title_style.leading = 20
    title_style.textColor = colors.white

    subtitle_style = styles["BodyText"].clone("pdf_header_subtitle")
    subtitle_style.fontSize = 8.8
    subtitle_style.leading = 11
    subtitle_style.textColor = colors.HexColor("#eaf3fb")

    meta_style = styles["BodyText"].clone("pdf_header_meta")
    meta_style.fontSize = 8.2
    meta_style.leading = 10
    meta_style.textColor = colors.white

    content = [
        Paragraph("Informe clínico de composición corporal", title_style),
        Spacer(1, 2),
        Paragraph(
            "Resumen ejecutivo en una sola página para revisión rápida, impresión y seguimiento longitudinal.",
            subtitle_style,
        ),
        Spacer(1, 5),
        Paragraph(
            (
                f"Periodo: <b>{first_measurement:%d/%m/%Y}</b> - <b>{last_measurement:%d/%m/%Y}</b> · "
                f"Registros: <b>{len(dataframe)}</b>"
            ),
            meta_style,
        ),
    ]
    panel = Table([[content]], colWidths=[806])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#4d95d8")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#3b7fbd")),
                ("LEFTPADDING", (0, 0), (-1, -1), 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 18),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return panel


def build_pdf_kpi_strip(latest_row: pd.Series, metric_columns: Dict[str, Optional[str]], styles):
    label_style = styles["BodyText"].clone("pdf_kpi_label")
    label_style.fontSize = 7.2
    label_style.leading = 8.6
    label_style.textColor = colors.HexColor("#5b7794")

    value_style = styles["Heading2"].clone("pdf_kpi_value")
    value_style.fontSize = 12
    value_style.leading = 14
    value_style.textColor = colors.HexColor("#16324f")

    candidate_keys = [
        "weight",
        "body_fat_pct",
        "muscle_mass",
        "metabolic_age",
        "bmi",
    ]
    selected_keys = [key for key in candidate_keys if metric_columns.get(key)]
    if not selected_keys:
        return None

    cards = []
    for key in selected_keys[:5]:
        value = format_metric_value(
            latest_row.get(metric_columns[key]),
            str(METRIC_CONFIG[key]["suffix"]),
            int(METRIC_CONFIG[key]["decimals"]),
        )
        card = Table(
            [[Paragraph(METRIC_CONFIG[key]["label"].upper(), label_style)], [Paragraph(value, value_style)]],
            colWidths=[150],
        )
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#d5e1ed")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )
        cards.append(card)

    strip = Table([cards], colWidths=[160] * len(cards))
    strip.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return strip


def build_pdf_bytes(
    dataframe: pd.DataFrame,
    latest_row: pd.Series,
    metric_columns: Dict[str, Optional[str]],
    segment_family: Optional[str],
    segment_columns: Dict[Tuple[str, str], str],
    source_name: str,
) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=18,
        rightMargin=18,
        topMargin=16,
        bottomMargin=16,
    )
    styles = getSampleStyleSheet()

    first_measurement = dataframe["Fecha medición"].min()
    last_measurement = dataframe["Fecha medición"].max()

    header_panel = build_pdf_header_panel(source_name, first_measurement, last_measurement, dataframe, styles)
    kpi_strip = build_pdf_kpi_strip(latest_row, metric_columns, styles)

    summary_panels = build_pdf_summary_panels(dataframe, latest_row, metric_columns, styles)

    story = [header_panel, Spacer(1, 9)]
    if kpi_strip is not None:
        story.extend([kpi_strip, Spacer(1, 9)])
    story.extend([summary_panels, Spacer(1, 10)])

    # Individual chart per metric, 2-column grid
    _chart_keys = [k for k in PRIMARY_KPI_KEYS + SECONDARY_KPI_KEYS if metric_columns.get(k)]
    _CW, _CH = 394, 158  # chart width / height in points

    for _i in range(0, len(_chart_keys), 2):
        _pair = _chart_keys[_i:_i + 2]
        _cells = []
        for _key in _pair:
            _img = build_pdf_line_chart_image(
                dataframe,
                metric_columns[_key],
                METRIC_CONFIG[_key]["label"],
                SOFT_COLORS[_key],
            )
            _cells.append(
                RLImage(io.BytesIO(_img), width=_CW, height=_CH) if _img
                else Paragraph(f"Sin datos: {METRIC_CONFIG[_key]['label']}", styles["BodyText"])
            )
        if len(_cells) == 1:
            _cells.append("")
        _row = Table([_cells], colWidths=[_CW + 9, _CW + 4])
        _row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 9),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(_row)
        story.append(Spacer(1, 7))

    document.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def render_metric_chart(dataframe: pd.DataFrame, column: str, label: str, color: str) -> None:
    figure = create_metric_line_figure(dataframe, column, label, color)
    if figure is None:
        return
    st.plotly_chart(figure, use_container_width=True)


def render_metric_group_chart(
    dataframe: pd.DataFrame,
    metric_columns: Dict[str, Optional[str]],
    metric_keys: List[str],
    title: str,
    empty_message: str,
) -> None:
    figure = create_metric_group_figure(dataframe, metric_columns, metric_keys, title)
    if figure is None:
        st.info(empty_message)
        return
    st.plotly_chart(figure, use_container_width=True)


def render_composition_chart(dataframe: pd.DataFrame, metric_columns: Dict[str, Optional[str]]) -> None:
    render_metric_group_chart(
        dataframe=dataframe,
        metric_columns=metric_columns,
        metric_keys=COMPOSITION_TREND_KEYS,
        title="Evolución de la composición corporal",
        empty_message="No se detectaron suficientes columnas de composición corporal para graficar.",
    )


def render_metabolic_chart(dataframe: pd.DataFrame, metric_columns: Dict[str, Optional[str]]) -> None:
    render_metric_group_chart(
        dataframe=dataframe,
        metric_columns=metric_columns,
        metric_keys=METABOLIC_TREND_KEYS,
        title="Panel metabólico y energético",
        empty_message="No se detectaron métricas metabólicas adicionales para graficar.",
    )


def render_segmental_chart(
    dataframe: pd.DataFrame, family: Optional[str], segment_columns: Dict[Tuple[str, str], str]
) -> None:
    chart_df, radar_rows, title = build_segmental_chart_data(dataframe, family, segment_columns)
    if chart_df is None:
        st.info("El archivo no incluye datos segmentales comparables de brazos o piernas.")
        return
    left_col, right_col = st.columns([1.15, 1])
    with left_col:
        figure = create_segmental_bar_figure(chart_df, title)
        st.plotly_chart(figure, use_container_width=True)
    with right_col:
        radar_figure = create_segmental_radar_figure(radar_rows, title)
        if radar_figure is not None:
            st.plotly_chart(radar_figure, use_container_width=True)


def build_display_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    display_df = dataframe.copy()
    display_df["Fecha medición"] = display_df["Fecha medición"].dt.strftime("%d/%m/%Y %H:%M")
    return display_df


# ── Shared inline CSS for st.html() table renders ──────────────────────────
# st.html() renders inside an isolated shadow DOM, so page-level <style> tags
# are not inherited. This constant is embedded directly into every HTML string.
_TANITA_TABLE_CSS = """
<style>
  * { box-sizing: border-box; font-family: 'Inter', 'Segoe UI', sans-serif; }
  .tanita-tracker-table { width: 100%; border-collapse: collapse; font-size: 0.88rem;
        background: #ffffff; border: 1px solid #c9d9ea;
        border-radius: 10px; overflow: hidden;
        box-shadow: 0 6px 18px rgba(22,50,79,0.07); margin-bottom: 6px; }
  .tanita-tracker-table th, .tanita-tracker-table td { padding: 0.55rem 0.45rem; border: 1px solid #d3dfeb; text-align: center; }
  .tanita-tracker-table th { background: #edf5fc; color: #16324f; font-size: 0.78rem; font-weight: 700; }
  .tanita-tracker-table .metric-label-cell { text-align: left; background: #f4f8fc; color: #16324f;
             font-weight: 700; min-width: 170px; font-size: 0.83rem; padding-left: 0.75rem; }
  .tanita-tracker-table .metric-label-cell .lang-es { display: block; color: #16324f; font-size: 0.82rem; font-weight: 700; }
  .tanita-tracker-table .metric-label-cell .lang-en { display: block; color: #5e7e9b; font-size: 0.71rem; font-weight: 500; }
  .tanita-tracker-table .metric-label-cell .lang-fr { display: block; color: #7b94ad; font-size: 0.71rem; font-weight: 500; }
  .tanita-ref-table { width: 100%; border-collapse: collapse; font-size: 0.78rem;
        background: #ffffff; border: 1px solid #d3dfeb;
        border-radius: 8px; overflow: hidden; margin-bottom: 4px; }
  .tanita-ref-table th, .tanita-ref-table td { padding: 0.38rem 0.45rem; border: 1px solid #e6edf4; text-align: center; }
  .tanita-ref-table th { background: #f0f6fc; color: #355879; font-weight: 700; }
  .tanita-ref-table.compact-table td { padding: 0.22rem 0.38rem; }
  .ref-highlight-row { background-color: #f0f7ff !important; font-weight: 600; }
  .ref-highlight-row td { border-top: 1px solid #85bbf0 !important;
               border-bottom: 1px solid #85bbf0 !important;
               color: #0d2c4c !important; }
  .ref-highlight-cell { background: linear-gradient(135deg,#2e77b7 0%,#4da0dc 100%) !important;
             color: #fff !important; font-weight: 700 !important; }
  .report-pill { display: inline-block; border-radius: 999px; padding: 0.2rem 0.55rem;
                 font-size: 0.78rem; font-weight: 700; border: 1px solid transparent; }
  .report-pill.up { background: rgba(74,170,131,0.14); color: #1f6f48; border-color: rgba(74,170,131,0.24); }
  .report-pill.down { background: rgba(70,127,192,0.14); color: #235e94; border-color: rgba(70,127,192,0.22); }
  .report-pill.warn { background: rgba(224,107,60,0.13); color: #9b3a10; border-color: rgba(224,107,60,0.25); }
  .report-pill.neutral { background: rgba(148,163,184,0.14); color: #52667c; border-color: rgba(148,163,184,0.2); }
</style>
"""


def get_body_fat_table_html(gender: str, age: int, fat_pct: Optional[float]) -> str:
    is_female = gender.lower() in ["femenino", "female", "mujer", "f"]

    def get_row_data(
        row_is_female, age_min, age_max, under_lim, healthy_min, healthy_max, over_min, over_max, obese_min
    ):
        row_is_active = (is_female == row_is_female) and (age_min <= age <= age_max)
        user_col = -1
        if row_is_active and fat_pct is not None:
            if fat_pct < healthy_min:
                user_col = 0
            elif fat_pct <= healthy_max:
                user_col = 1
            elif fat_pct <= over_max:
                user_col = 2
            else:
                user_col = 3
        return {
            "gender": "Femenino" if row_is_female else "Masculino",
            "age": f"{age_min}-{age_max}",
            "under": f"&lt;{under_lim}%",
            "healthy": f"{healthy_min}% - {healthy_max}%",
            "over": f"{over_min}% - {over_max}%",
            "obese": f"&gt;={obese_min}%",
            "active": row_is_active,
            "user_col": user_col,
        }

    rows_data = [
        get_row_data(True, 18, 39, 21, 21, 33, 33, 39, 39),
        get_row_data(True, 40, 59, 23, 23, 34, 34, 40, 40),
        get_row_data(True, 60, 99, 24, 24, 36, 36, 42, 42),
        get_row_data(False, 18, 39, 8, 8, 20, 20, 25, 25),
        get_row_data(False, 40, 59, 11, 11, 22, 22, 28, 28),
        get_row_data(False, 60, 99, 13, 13, 25, 25, 30, 30),
    ]

    html = """
    <table class="tanita-ref-table">
        <thead>
            <tr>
                <th>Género</th>
                <th>Edad</th>
                <th>Bajo (-)</th>
                <th>Saludable (0)</th>
                <th>Elevado (+)</th>
                <th>Obeso (++)</th>
            </tr>
        </thead>
        <tbody>
    """
    for r in rows_data:
        row_class = " class='ref-highlight-row'" if r["active"] else ""
        html += f"<tr{row_class}>"
        html += f"<td>{r['gender']}</td>"
        html += f"<td>{r['age']}</td>"
        for i, col_key in enumerate(["under", "healthy", "over", "obese"]):
            cell_class = ""
            if r["active"] and r["user_col"] == i:
                cell_class = " class='ref-highlight-cell'"
            html += f"<td{cell_class}>{r[col_key]}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    return html


def get_body_water_table_html(gender: str, water_pct: Optional[float]) -> str:
    is_female = gender.lower() in ["femenino", "female", "mujer", "f"]

    f_active = is_female
    f_highlight = f_active and water_pct is not None and 45.0 <= water_pct <= 60.0
    f_user_col = -1
    if f_active and water_pct is not None:
        if water_pct < 45.0:
            f_user_col = 0
        elif water_pct <= 60.0:
            f_user_col = 1
        else:
            f_user_col = 2

    m_active = not is_female
    m_highlight = m_active and water_pct is not None and 50.0 <= water_pct <= 65.0
    m_user_col = -1
    if m_active and water_pct is not None:
        if water_pct < 50.0:
            m_user_col = 0
        elif water_pct <= 65.0:
            m_user_col = 1
        else:
            m_user_col = 2

    html = """
    <table class="tanita-ref-table">
        <thead>
            <tr>
                <th>Género</th>
                <th>Bajo</th>
                <th>Recomendado</th>
                <th>Alto</th>
            </tr>
        </thead>
        <tbody>
    """
    f_row_class = " class='ref-highlight-row'" if f_active else ""
    html += f"<tr{f_row_class}><td>Femenino</td>"
    html += f"<td{' class=\"ref-highlight-cell\"' if f_user_col==0 else ''}>&lt; 45%</td>"
    html += f"<td{' class=\"ref-highlight-cell\"' if f_user_col==1 else ''}>45% - 60%</td>"
    html += f"<td{' class=\"ref-highlight-cell\"' if f_user_col==2 else ''}>&gt; 60%</td></tr>"

    m_row_class = " class='ref-highlight-row'" if m_active else ""
    html += f"<tr{m_row_class}><td>Masculino</td>"
    html += f"<td{' class=\"ref-highlight-cell\"' if m_user_col==0 else ''}>&lt; 50%</td>"
    html += f"<td{' class=\"ref-highlight-cell\"' if m_user_col==1 else ''}>50% - 65%</td>"
    html += f"<td{' class=\"ref-highlight-cell\"' if m_user_col==2 else ''}>&gt; 65%</td></tr>"

    html += "</tbody></table>"
    return html


def get_visceral_fat_table_html(visceral_fat: Optional[float]) -> str:
    user_col = -1
    if visceral_fat is not None:
        if visceral_fat <= 12:
            user_col = 0
        else:
            user_col = 1

    html = f"""
    <table class="tanita-ref-table">
        <thead>
            <tr>
                <th>Nivel</th>
                <th>Descripción</th>
                <th>Rango</th>
            </tr>
        </thead>
        <tbody>
            <tr{' class="ref-highlight-row"' if user_col==0 else ''}>
                <td>1 - 12</td>
                <td>Saludable (Normal)</td>
                <td><span class="report-pill {'up' if user_col==0 else 'neutral'}">Saludable</span></td>
            </tr>
            <tr{' class="ref-highlight-row"' if user_col==1 else ''}>
                <td>13 - 59</td>
                <td>Efecto Excesivo (Alto)</td>
                <td><span class="report-pill {'down' if user_col==1 else 'neutral'}">Exceso</span></td>
            </tr>
        </tbody>
    </table>
    """
    return html


def get_physique_table_html(physique_rating: Optional[float]) -> str:
    ratings = [
        (1, "Físico oculto", "Hidden obese"),
        (2, "Obeso", "Obese"),
        (3, "Robusto / Sólido", "Solidly-built"),
        (4, "Bajo en ejercicio", "Under exercised"),
        (5, "Estándar / Normal", "Standard"),
        (6, "Estándar Musculoso", "Standard Muscular"),
        (7, "Delgado", "Thin"),
        (8, "Delgado y musculoso", "Thin & muscular"),
        (9, "Muy musculoso", "Very Muscular"),
    ]

    html = """
    <table class="tanita-ref-table compact-table">
        <thead>
            <tr>
                <th>Val</th>
                <th>Clasificación</th>
                <th>Equivalencia Oficial</th>
            </tr>
        </thead>
        <tbody>
    """
    for val, name, desc in ratings:
        is_user = physique_rating is not None and int(round(physique_rating)) == val
        row_class = " class='ref-highlight-row'" if is_user else ""
        cell_class = " class='ref-highlight-cell'" if is_user else ""
        html += f"<tr{row_class}>"
        html += f"<td{cell_class}>{val}</td>"
        html += f"<td>{name}</td>"
        html += f"<td>{desc}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    return html


def get_bone_mass_table_html(gender: str, weight: Optional[float], bone_mass: Optional[float]) -> str:
    is_female = gender.lower() in ["femenino", "female", "mujer", "f"]

    def get_row_data(row_is_female, w_min, w_max, expected_val):
        row_is_active = is_female == row_is_female
        if row_is_active and weight is not None:
            if w_min is None:
                row_is_active = weight < w_max
            elif w_max is None:
                row_is_active = weight >= w_min
            else:
                row_is_active = w_min <= weight < w_max
        else:
            row_is_active = False

        user_match = row_is_active and bone_mass is not None

        w_label = (
            f"&lt; {w_max} kg" if w_min is None else (f"&gt;= {w_min} kg" if w_max is None else f"{w_min} - {w_max} kg")
        )
        return {
            "gender": "Femenino" if row_is_female else "Masculino",
            "weight_class": w_label,
            "expected": f"{expected_val:.2f} kg",
            "active": row_is_active,
            "user_match": user_match,
        }

    rows_data = [
        get_row_data(True, None, 50, 1.95),
        get_row_data(True, 50, 75, 2.40),
        get_row_data(True, 75, None, 2.95),
        get_row_data(False, None, 65, 2.65),
        get_row_data(False, 65, 95, 3.29),
        get_row_data(False, 95, None, 3.69),
    ]

    html = """
    <table class="tanita-ref-table">
        <thead>
            <tr>
                <th>Género</th>
                <th>Peso Corporal (W)</th>
                <th>Masa Ósea Estimada</th>
            </tr>
        </thead>
        <tbody>
    """
    for r in rows_data:
        row_class = " class='ref-highlight-row'" if r["active"] else ""
        cell_class = " class='ref-highlight-cell'" if r["user_match"] else ""
        html += f"<tr{row_class}>"
        html += f"<td>{r['gender']}</td>"
        html += f"<td>{r['weight_class']}</td>"
        html += f"<td{cell_class}>{r['expected']}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    return html


def generate_tanita_tracker_table_html(dataframe: pd.DataFrame, metric_columns: Dict[str, Optional[str]]) -> str:
    num_cols = 12
    df_len = len(dataframe)
    if df_len <= num_cols:
        rows_to_show = dataframe
        num_padding = num_cols - df_len
    else:
        rows_to_show = dataframe.iloc[-num_cols:]
        num_padding = 0

    cols_data = []
    for _, row in rows_to_show.iterrows():
        dt = row["Fecha medición"]
        date_str = dt.strftime("%d/%m") if not pd.isna(dt) else ""
        time_str = dt.strftime("%H:%M") if not pd.isna(dt) else ""

        def get_val(key, decimals=1):
            col = metric_columns.get(key)
            if col and col in row and not pd.isna(row[col]):
                val = float(row[col])
                return f"{val:.0f}" if decimals == 0 else f"{val:.{decimals}f}"
            return ""

        weight = get_val("weight", 1)
        fat = get_val("body_fat_pct", 1)
        water = get_val("total_body_water_pct", 1)
        visceral = get_val("visceral_fat", 0)
        muscle = get_val("muscle_mass", 1)
        physique = get_val("physique_rating", 0)
        bone = get_val("bone_mass", 1)
        metabolic_age = get_val("metabolic_age", 0)
        bmi = get_val("bmi", 1)
        abdominal = get_val("abdominal_circumference", 1)
        bmr = get_val("bmr", 0)
        dci = get_val("daily_calorie_intake", 0)

        cols_data.append(
            {
                "date": date_str,
                "time": time_str,
                "weight": weight,
                "fat": fat,
                "water": water,
                "visceral": visceral,
                "muscle": muscle,
                "physique": physique,
                "bone": bone,
                "metabolic_age": metabolic_age,
                "bmi": bmi,
                "abdominal": abdominal,
                "bmr": bmr,
                "dci": dci,
            }
        )

    for _ in range(num_padding):
        cols_data.append(
            {
                "date": "&nbsp;&nbsp;/&nbsp;&nbsp;",
                "time": "&nbsp;&nbsp;:&nbsp;&nbsp;",
                "weight": "",
                "fat": "",
                "water": "",
                "visceral": "",
                "muscle": "",
                "physique": "",
                "bone": "",
                "metabolic_age": "",
                "bmi": "",
                "abdominal": "",
                "bmr": "",
                "dci": "",
            }
        )

    html = """
    <table class="tanita-tracker-table">
        <thead>
            <tr>
                <th class="metric-label-cell">
                    <div class="lang-es">Fecha / Hora</div>
                    <div class="lang-en">Date / Time</div>
                </th>
    """
    for col in cols_data:
        html += f"""
                <th>
                    <div>{col['date']}</div>
                    <div style="font-weight: normal; margin-top: 3px; font-size: 0.75rem; color: #5e7e9b;">{col['time']}</div>
                </th>
        """
    html += """
            </tr>
        </thead>
        <tbody>
    """

    # Rows
    # 1. Body Fat %
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">% Grasa Corporal</div>
                    <div class="lang-en">Body Fat %</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['fat']}</td>"
    html += "</tr>"

    # 2. Weight
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Peso (kg)</div>
                    <div class="lang-en">Weight (kg)</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['weight']}</td>"
    html += "</tr>"

    # 3. Body Water %
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">% Agua Corporal</div>
                    <div class="lang-en">Body Water %</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['water']}</td>"
    html += "</tr>"

    # 4. Visceral Fat Rating
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Grasa Visceral</div>
                    <div class="lang-en">Visceral Fat</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['visceral']}</td>"
    html += "</tr>"

    # 5. Muscle Mass (kg)
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Masa Muscular (kg)</div>
                    <div class="lang-en">Muscle Mass (kg)</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['muscle']}</td>"
    html += "</tr>"

    # 6. Physique Rating
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Clasificación Física</div>
                    <div class="lang-en">Physique Rating</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['physique']}</td>"
    html += "</tr>"

    # 7. Bone Mass (kg)
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Masa Ósea (kg)</div>
                    <div class="lang-en">Bone Mass (kg)</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['bone']}</td>"
    html += "</tr>"

    # 8. Metabolic Age
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Edad Metabólica</div>
                    <div class="lang-en">Metabolic Age</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['metabolic_age']}</td>"
    html += "</tr>"

    # 9. BMI
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">IMC</div>
                    <div class="lang-en">BMI</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['bmi']}</td>"
    html += "</tr>"

    # 10. Abdominal Circumference (cm)
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Perímetro Abdominal (cm)</div>
                    <div class="lang-en">Abdominal Circumference (cm)</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['abdominal']}</td>"
    html += "</tr>"

    # 11. BMR (kcal)
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Metabolismo Basal (kcal)</div>
                    <div class="lang-en">BMR (kcal)</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['bmr']}</td>"
    html += "</tr>"

    # 12. Daily Calorie Intake (kcal)
    html += """
            <tr>
                <td class="metric-label-cell">
                    <div class="lang-es">Ingesta Calórica (kcal)</div>
                    <div class="lang-en">Daily Calorie Intake (kcal)</div>
                </td>
    """
    for col in cols_data:
        html += f"<td>{col['dci']}</td>"
    html += "</tr>"

    html += """
        </tbody>
    </table>
    """
    return html


# --- PDF Generation helper functions for Tanita Official Form ---


def make_pdf_body_fat_table(gender, age, fat_pct, styles):
    is_female = gender.lower() in ["femenino", "female", "mujer", "f"]
    header_style = ParagraphStyle(
        "HeaderStyleBFT",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=4,
        leading=5,
        textColor=colors.HexColor("#355879"),
        alignment=1,
    )
    label_style = ParagraphStyle(
        "LabelStyleBFT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style = ParagraphStyle(
        "ValStyleBFT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style_white = ParagraphStyle(
        "ValStyleBFTWhite", parent=val_style, fontName="Helvetica-Bold", textColor=colors.white
    )

    data = [
        [
            Paragraph("<b>Género</b>", header_style),
            Paragraph("<b>Edad</b>", header_style),
            Paragraph("<b>Bajo (-)</b>", header_style),
            Paragraph("<b>Sano (0)</b>", header_style),
            Paragraph("<b>Alto (+)</b>", header_style),
            Paragraph("<b>Obeso (++)</b>", header_style),
        ]
    ]
    rows_def = [
        (True, 18, 39, 21, 21, 33, 33, 39, 39),
        (True, 40, 59, 23, 23, 34, 34, 40, 40),
        (True, 60, 99, 24, 24, 36, 36, 42, 42),
        (False, 18, 39, 8, 8, 20, 20, 25, 25),
        (False, 40, 59, 11, 11, 22, 22, 28, 28),
        (False, 60, 99, 13, 13, 25, 25, 30, 30),
    ]
    t_style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dfeb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f6fc")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for row_idx, (r_female, a_min, a_max, under_lim, h_min, h_max, o_min, o_max, ob_min) in enumerate(
        rows_def, start=1
    ):
        g_label = "Fem" if r_female else "Masc"
        a_label = f"{a_min}-{a_max}"
        under_val = f"<{under_lim}%"
        healthy_val = f"{h_min}-{h_max}%"
        over_val = f"{o_min}-{o_max}%"
        obese_val = f">={ob_min}%"
        row_active = (is_female == r_female) and (a_min <= age <= a_max)
        user_col = -1
        if row_active and fat_pct is not None:
            if fat_pct < h_min:
                user_col = 2
            elif fat_pct <= h_max:
                user_col = 3
            elif fat_pct <= o_max:
                user_col = 4
            else:
                user_col = 5
        if row_active:
            t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#f0f7ff")))
        if user_col != -1:
            t_style.append(("BACKGROUND", (user_col, row_idx), (user_col, row_idx), colors.HexColor("#2e77b7")))

        def get_cell_para(text, col_idx):
            style = val_style_white if (user_col == col_idx) else label_style if col_idx < 2 else val_style
            return Paragraph(text, style)

        data.append(
            [
                get_cell_para(g_label, 0),
                get_cell_para(a_label, 1),
                get_cell_para(under_val, 2),
                get_cell_para(healthy_val, 3),
                get_cell_para(over_val, 4),
                get_cell_para(obese_val, 5),
            ]
        )
    t = Table(data, colWidths=[24, 28, 40, 47, 47, 46])
    t.setStyle(TableStyle(t_style))
    return t


def make_pdf_water_visceral_stack(gender, water_pct, visceral_fat, styles):
    is_female = gender.lower() in ["femenino", "female", "mujer", "f"]
    header_style = ParagraphStyle(
        "HeaderStyleWVT",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=4,
        leading=5,
        textColor=colors.HexColor("#355879"),
        alignment=1,
    )
    label_style = ParagraphStyle(
        "LabelStyleWVT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style = ParagraphStyle(
        "ValStyleWVT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style_white = ParagraphStyle(
        "ValStyleWVTWhite", parent=val_style, fontName="Helvetica-Bold", textColor=colors.white
    )

    water_data = [[Paragraph("<b>Género</b>", header_style), Paragraph("<b>Agua Recomendada</b>", header_style)]]
    w_t_style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dfeb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f6fc")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    f_active = is_female
    f_highlight = f_active and water_pct is not None and 45.0 <= water_pct <= 60.0
    if f_active:
        w_t_style.append(("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f0f7ff")))
    if f_highlight:
        w_t_style.append(("BACKGROUND", (1, 1), (1, 1), colors.HexColor("#2e77b7")))
    water_data.append(
        [Paragraph("Fem (♀)", label_style), Paragraph("45% - 60%", val_style_white if f_highlight else val_style)]
    )

    m_active = not is_female
    m_highlight = m_active and water_pct is not None and 50.0 <= water_pct <= 65.0
    if m_active:
        w_t_style.append(("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#f0f7ff")))
    if m_highlight:
        w_t_style.append(("BACKGROUND", (1, 2), (1, 2), colors.HexColor("#2e77b7")))
    water_data.append(
        [Paragraph("Masc (♂)", label_style), Paragraph("50% - 65%", val_style_white if m_highlight else val_style)]
    )
    water_table = Table(water_data, colWidths=[55, 89])
    water_table.setStyle(TableStyle(w_t_style))

    visc_data = [[Paragraph("<b>Nivel</b>", header_style), Paragraph("<b>Clasificación</b>", header_style)]]
    v_t_style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dfeb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f6fc")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    v_healthy_active = visceral_fat is not None and visceral_fat <= 12
    v_excess_active = visceral_fat is not None and visceral_fat > 12
    if v_healthy_active:
        v_t_style.append(("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#2e77b7")))
    if v_excess_active:
        v_t_style.append(("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#d9534f")))
    visc_data.append(
        [
            Paragraph("1 - 12", val_style_white if v_healthy_active else val_style),
            Paragraph("Saludable", val_style_white if v_healthy_active else val_style),
        ]
    )
    visc_data.append(
        [
            Paragraph("13 - 59", val_style_white if v_excess_active else val_style),
            Paragraph("Exceso", val_style_white if v_excess_active else val_style),
        ]
    )
    visc_table = Table(visc_data, colWidths=[55, 89])
    visc_table.setStyle(TableStyle(v_t_style))

    water_visc_data = [
        [Paragraph("<b>Agua corporal</b>", header_style)],
        [water_table],
        [Spacer(1, 4)],
        [Paragraph("<b>Grasa visceral</b>", header_style)],
        [visc_table],
    ]
    water_visc_nested_table = Table(water_visc_data, colWidths=[144])
    water_visc_nested_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return water_visc_nested_table


def make_pdf_physique_table(physique_rating, styles):
    header_style = ParagraphStyle(
        "HeaderStylePT",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=4,
        leading=5,
        textColor=colors.HexColor("#355879"),
        alignment=1,
    )
    label_style = ParagraphStyle(
        "LabelStylePT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=0,
    )
    val_style = ParagraphStyle(
        "ValStylePT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style_white = ParagraphStyle(
        "ValStylePTWhite", parent=val_style, fontName="Helvetica-Bold", textColor=colors.white
    )
    label_style_white = ParagraphStyle(
        "LabelStylePTWhite", parent=label_style, fontName="Helvetica-Bold", textColor=colors.white
    )

    data = [
        [
            Paragraph("<b>Val</b>", header_style),
            Paragraph("<b>Clasificación</b>", header_style),
            Paragraph("<b>Equivalencia</b>", header_style),
        ]
    ]
    ratings = [
        (1, "Físico oculto", "Hidden obese"),
        (2, "Obeso", "Obese"),
        (3, "Robusto / Sólido", "Solidly-built"),
        (4, "Bajo en ejercicio", "Under exercised"),
        (5, "Estándar / Normal", "Standard"),
        (6, "Estándar Musculoso", "Standard Muscular"),
        (7, "Delgado", "Thin"),
        (8, "Delgado y musculoso", "Thin & muscular"),
        (9, "Muy musculoso", "Very Muscular"),
    ]
    t_style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dfeb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f6fc")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
    for val, name, desc in ratings:
        is_user = physique_rating is not None and int(round(physique_rating)) == val
        row_idx = val
        if is_user:
            t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#2e77b7")))

        def get_cell_para(text, is_label=False):
            if is_user:
                return Paragraph(text, label_style_white if is_label else val_style_white)
            else:
                return Paragraph(text, label_style if is_label else val_style)

        data.append([get_cell_para(str(val), False), get_cell_para(name, True), get_cell_para(desc, True)])
    t = Table(data, colWidths=[17, 92, 95])
    t.setStyle(TableStyle(t_style))
    return t


def make_pdf_bone_table(gender, weight, bone_mass, styles):
    is_female = gender.lower() in ["femenino", "female", "mujer", "f"]
    header_style = ParagraphStyle(
        "HeaderStyleBT",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=4,
        leading=5,
        textColor=colors.HexColor("#355879"),
        alignment=1,
    )
    label_style = ParagraphStyle(
        "LabelStyleBT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style = ParagraphStyle(
        "ValStyleBT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=3.5,
        leading=4.5,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    val_style_white = ParagraphStyle(
        "ValStyleBTWhite", parent=val_style, fontName="Helvetica-Bold", textColor=colors.white
    )

    data = [
        [
            Paragraph("<b>Género</b>", header_style),
            Paragraph("<b>Peso (W)</b>", header_style),
            Paragraph("<b>Masa Ósea</b>", header_style),
        ]
    ]
    rows_def = [
        (True, None, 50, 1.95),
        (True, 50, 75, 2.40),
        (True, 75, None, 2.95),
        (False, None, 65, 2.65),
        (False, 65, 95, 3.29),
        (False, 95, None, 3.69),
    ]
    t_style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dfeb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f6fc")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for row_idx, (r_female, w_min, w_max, expected_val) in enumerate(rows_def, start=1):
        g_label = "Fem" if r_female else "Masc"
        if w_min is None:
            w_label = f"< {w_max} kg"
        elif w_max is None:
            w_label = f">= {w_min} kg"
        else:
            w_label = f"{w_min} - {w_max} kg"
        expected_label = f"{expected_val:.2f} kg"
        row_active = is_female == r_female
        if row_active and weight is not None:
            if w_min is None:
                row_active = weight < w_max
            elif w_max is None:
                row_active = weight >= w_min
            else:
                row_active = w_min <= weight < w_max
        else:
            row_active = False
        user_match = row_active and bone_mass is not None
        if row_active:
            t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#f0f7ff")))
        if user_match:
            t_style.append(("BACKGROUND", (2, row_idx), (2, row_idx), colors.HexColor("#2e77b7")))

        def get_cell_para(text, is_match):
            style = val_style_white if is_match else label_style
            return Paragraph(text, style)

        data.append(
            [get_cell_para(g_label, False), get_cell_para(w_label, False), get_cell_para(expected_label, user_match)]
        )
    t = Table(data, colWidths=[32, 72, 69])
    t.setStyle(TableStyle(t_style))
    return t


def make_pdf_tracking_table(dataframe, metric_columns, styles, segment_family=None, segment_columns=None):
    num_cols = 12
    df_len = len(dataframe)
    if df_len <= num_cols:
        rows_to_show = dataframe
        num_padding = num_cols - df_len
    else:
        rows_to_show = dataframe.iloc[-num_cols:]
        num_padding = 0

    title_style = ParagraphStyle(
        "TrackHeaderT",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor("#16324f"),
        alignment=0,
    )
    val_header_style = ParagraphStyle(
        "TrackValHeaderT",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    value_style = ParagraphStyle(
        "TrackValueT",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )

    header_p = Paragraph("<b>Date / Time</b><br/>" "<font color='#5e7e9b' size='5'>Fecha / Hora</font>", title_style)
    row_headers = [header_p]
    for _, row in rows_to_show.iterrows():
        dt = row["Fecha medición"]
        d_str = dt.strftime("%d/%m") if not pd.isna(dt) else ""
        t_str = dt.strftime("%H:%M") if not pd.isna(dt) else ""
        cell_p = Paragraph(f"<b>{d_str}</b><br/><font color='#5e7e9b' size='5.5'>{t_str}</font>", val_header_style)
        row_headers.append(cell_p)
    for _ in range(num_padding):
        cell_p = Paragraph("<b> / </b><br/><font color='#5e7e9b' size='5.5'> : </font>", val_header_style)
        row_headers.append(cell_p)
    table_data = [row_headers]

    def add_metric_row(en, es, key, decimals=1):
        _col = metric_columns.get(key)
        if not _col:
            return
        label_p = Paragraph(f"<b>{en}</b><br/>" f"<font color='#5e7e9b' size='5'>{es}</font>", title_style)
        row = [label_p]
        for _, r in rows_to_show.iterrows():
            if _col in r and not pd.isna(r[_col]):
                val = float(r[_col])
                val_str = f"{val:.0f}" if decimals == 0 else f"{val:.{decimals}f}"
                row.append(Paragraph(val_str, value_style))
            else:
                row.append(Paragraph("", value_style))
        for _ in range(num_padding):
            row.append(Paragraph("", value_style))
        table_data.append(row)

    def add_col_row(en, es, col, decimals=1):
        label_p = Paragraph(f"<b>{en}</b><br/>" f"<font color='#5e7e9b' size='5'>{es}</font>", title_style)
        row = [label_p]
        for _, r in rows_to_show.iterrows():
            if col in r and not pd.isna(r[col]):
                val = float(r[col])
                val_str = f"{val:.0f}" if decimals == 0 else f"{val:.{decimals}f}"
                row.append(Paragraph(val_str, value_style))
            else:
                row.append(Paragraph("", value_style))
        for _ in range(num_padding):
            row.append(Paragraph("", value_style))
        table_data.append(row)

    add_metric_row("Body Fat %", "% Grasa Corporal", "body_fat_pct", 1)
    add_metric_row("Weight (kg)", "Peso (kg)", "weight", 1)
    add_metric_row("Body Water %", "% Agua Corporal", "total_body_water_pct", 1)
    add_metric_row("Visceral Fat", "Grasa Visceral", "visceral_fat", 0)
    add_metric_row("Muscle Mass (kg)", "Masa Muscular (kg)", "muscle_mass", 1)
    add_metric_row("Physique Rating", "Clasificación Física", "physique_rating", 0)
    add_metric_row("Bone Mass (kg)", "Masa Ósea (kg)", "bone_mass", 1)
    add_metric_row("Metabolic Age", "Edad Metabólica", "metabolic_age", 0)
    add_metric_row("BMI", "IMC", "bmi", 1)
    add_metric_row("Abdominal Circumference (cm)", "Perímetro Abdominal (cm)", "abdominal_circumference", 1)
    add_metric_row("BMR (kcal)", "Metabolismo Basal (kcal)", "bmr", 0)
    add_metric_row("Daily Calorie Intake (kcal)", "Ingesta Calórica (kcal)", "daily_calorie_intake", 0)

    if segment_family and segment_columns:
        _fam_en = "Fat" if segment_family == "fat" else "Muscle"
        _fam_es = "Grasa" if segment_family == "fat" else "Masa Musc"
        _part_map = {"arm": ("Arm", "Brazo"), "leg": ("Leg", "Pierna"), "trunk": ("Trunk", "Tronco")}
        _side_map = {"left": ("L", "Izq"), "right": ("R", "Der"), "center": ("C", "Cen")}
        for (part, side), col in sorted(segment_columns.items()):
            p_en, p_es = _part_map.get(part, (part.title(), part.title()))
            s_en, s_es = _side_map.get(side, (side.title(), side.title()))
            add_col_row(f"{_fam_en} {p_en} {s_en}", f"{_fam_es} {p_es} {s_es}", col, 1)

    t_style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d9ea")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf5fc")),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f4f8fc")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    t = Table(table_data, colWidths=[140] + [55] * 12)
    t.setStyle(TableStyle(t_style))
    return t


def build_official_pdf_bytes(
    dataframe, metric_columns, gender, age, latest_row, name, source_name, segment_family=None, segment_columns=None
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=15, bottomMargin=15
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "OfficialTitleMain",
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "OfficialSubTitleMain",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#33597c"),
        alignment=1,
    )
    photocopy_style = ParagraphStyle(
        "OfficialPhotocopyMain",
        fontName="Helvetica-Oblique",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#5e7e9b"),
        alignment=1,
    )
    ref_title_style = ParagraphStyle(
        "RefTitleMain",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#16324f"),
        alignment=1,
    )

    story = []

    # Header
    story.append(
        Paragraph(
            "ESTABLECE TUS OBJETIVOS Y REGISTRA TU PROGRESO · SET YOUR TARGETS AND TRACK YOUR PROGRESS",
            title_style,
        )
    )
    story.append(Spacer(1, 8))

    # Metadata
    meta_style = ParagraphStyle(
        "MetaTextMain", fontName="Helvetica", fontSize=7.5, leading=9, textColor=colors.HexColor("#16324f"), alignment=1
    )
    _gender_label = (
        "Femenino / Female" if gender.lower() in ["femenino", "female", "mujer", "f"] else "Masculino / Male"
    )
    meta_data = [
        [
            Paragraph(f"<b>Paciente / Patient:</b> {name}", meta_style),
            Paragraph(f"<b>Género / Gender:</b> {_gender_label}", meta_style),
            Paragraph(f"<b>Edad / Age:</b> {age} años / years", meta_style),
        ]
    ]
    meta_table = Table(meta_data, colWidths=[267, 267, 266])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfddeb")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 8))

    # Main tracking table
    tracking_table = make_pdf_tracking_table(
        dataframe, metric_columns, styles, segment_family=segment_family, segment_columns=segment_columns
    )
    story.append(tracking_table)
    story.append(Spacer(1, 10))

    # Reference Section Header
    story.append(Paragraph("TABLAS DE REFERENCIA OFICIALES / OFFICIAL REFERENCE TABLES", ref_title_style))
    story.append(Spacer(1, 4))

    # Reference Tables
    fat_pct = latest_row.get(metric_columns.get("body_fat_pct")) if "body_fat_pct" in metric_columns else None
    water_pct = (
        latest_row.get(metric_columns.get("total_body_water_pct")) if "total_body_water_pct" in metric_columns else None
    )
    visceral_fat = latest_row.get(metric_columns.get("visceral_fat")) if "visceral_fat" in metric_columns else None
    physique_rating = (
        latest_row.get(metric_columns.get("physique_rating")) if "physique_rating" in metric_columns else None
    )
    bone_mass = latest_row.get(metric_columns.get("bone_mass")) if "bone_mass" in metric_columns else None
    weight = latest_row.get(metric_columns.get("weight")) if "weight" in metric_columns else None

    fat_table = make_pdf_body_fat_table(gender, age, fat_pct, styles)
    water_visc_table = make_pdf_water_visceral_stack(gender, water_pct, visceral_fat, styles)
    physique_table = make_pdf_physique_table(physique_rating, styles)
    bone_table = make_pdf_bone_table(gender, weight, bone_mass, styles)

    _ref_title_style = ParagraphStyle(
        "RefCellTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#16324f"),
        alignment=0,
    )

    def _titled(title_es, title_en, table):
        return [Paragraph(f"<b>{title_es}</b> / {title_en}", _ref_title_style), Spacer(1, 5), table]

    ref_data = [[
        _titled("% Grasa Corporal", "Body Fat %", fat_table),
        _titled("Agua Corporal · Grasa Visceral", "Body Water % · Visceral Fat", water_visc_table),
        _titled("Clasificación Física", "Physique Rating", physique_table),
        _titled("Masa Ósea", "Bone Mass", bone_table),
    ]]
    ref_grid = Table(ref_data, colWidths=[244, 156, 216, 185])
    ref_grid.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(ref_grid)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


render_section_header(
    "Carga y procesamiento",
    "Selecciona un paciente en el panel lateral o sube el CSV exportado por la báscula.",
)

# ── Determine data source and load bytes ────────────────────────────────────
_file_bytes: Optional[bytes] = None
source_name: str = "DATA.CSV"

if _source_mode == "folder" and _selected_folder is not None:
    _csv_candidates = [_selected_folder / "DATAX.CSV", _selected_folder / "DATA.CSV"]
    _csv_path = next((p for p in _csv_candidates if p.exists()), None)
    if _csv_path is None:
        st.error(f"No se encontró DATA.CSV ni DATAX.CSV en «{_selected_folder.name}».")
        st.stop()
    _file_bytes = _csv_path.read_bytes()
    source_name = _csv_path.name
    st.success(f"📂 Datos de **{_selected_folder.name}** · `{source_name}`")
else:
    uploaded_file = st.file_uploader("Cargar CSV", type=["csv"])
    if uploaded_file is None:
        st.info(
            "Sube el archivo csv para activar el dashboard. "
            "El diseño está preparado para imprimir con Ctrl+P en formato clínico."
        )
        st.stop()
    if Path(uploaded_file.name).name.upper() not in {"DATAX.CSV", "DATA.CSV"}:
        st.warning(
            "El archivo cargado no se llama DATAX.CSV ni DATA.CSV. "
            "Se procesará igualmente si el formato es compatible."
        )
    _file_bytes = uploaded_file.getvalue()
    source_name = uploaded_file.name

try:
    processed_df, metadata = process_csv(_file_bytes)
except Exception as exc:
    st.error(str(exc))
    st.stop()

metric_columns: Dict[str, Optional[str]] = metadata["metric_columns"]
segment_family: Optional[str] = metadata["segment_family"]
segment_columns: Dict[Tuple[str, str], str] = metadata["segment_columns"]
latest_row = processed_df.iloc[-1]

detected_labels = [DISPLAY_NAMES[key] for key, column in metric_columns.items() if column]
render_report_header(source_name, processed_df, detected_labels)

# Botones de exportación destacados en la parte superior
export_col1, export_col2, export_col3 = st.columns([1, 1, 1])
with export_col1:
    st.download_button(
        "Descargar Excel",
        data=build_excel_bytes(processed_df),
        file_name="tanita_procesado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
with export_col2:
    st.download_button(
        "Ficha Oficial (PDF)",
        data=build_official_pdf_bytes(
            dataframe=processed_df,
            metric_columns=metric_columns,
            gender=user_gender,
            age=user_age,
            latest_row=latest_row,
            name=user_name,
            source_name=source_name,
            segment_family=segment_family,
            segment_columns=segment_columns,
        ),
        file_name=f"tanita_ficha_{user_name.lower().replace(' ', '_')}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
with export_col3:
    st.download_button(
        "Informe Gráfico (PDF)",
        data=build_pdf_bytes(
            dataframe=processed_df,
            latest_row=latest_row,
            metric_columns=metric_columns,
            segment_family=segment_family,
            segment_columns=segment_columns,
            source_name=source_name,
        ),
        file_name=f"tanita_graficas_{user_name.lower().replace(' ', '_')}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

st.write("")

# Estructuración de la UI en Pestañas
tab_ficha, tab_graficos, tab_tabla = st.tabs(
    ["📋 Ficha de Seguimiento Oficial", "📊 Gráficas de Evolución", "🗂️ Datos Completos"]
)

with tab_ficha:
    render_section_header(
        "Ficha de Seguimiento",
        "Estructura oficial de la báscula con los últimos 12 registros de seguimiento.",
    )
    tracker_html = generate_tanita_tracker_table_html(processed_df, metric_columns)
    render_table_html(tracker_html)

    render_section_header(
        "Rangos de Referencia Oficiales",
        "Valores de referencia y clasificación clínica de Tanita. Tu última medición está destacada.",
    )

    # Extraer valores de última medición de forma segura
    def get_latest_val(key):
        col = metric_columns.get(key)
        if col and col in latest_row and not pd.isna(latest_row[col]):
            return float(latest_row[col])
        return None

    latest_fat = get_latest_val("body_fat_pct")
    latest_water = get_latest_val("total_body_water_pct")
    latest_visceral = get_latest_val("visceral_fat")
    latest_physique = get_latest_val("physique_rating")
    latest_bone = get_latest_val("bone_mass")
    latest_weight = get_latest_val("weight")

    ref_col1, ref_col2 = st.columns([1.3, 1.0])
    with ref_col1:
        st.markdown("**Porcentaje de Grasa Corporal (Body Fat %)**")
        render_table_html(get_body_fat_table_html(user_gender, user_age, latest_fat))

        st.markdown("**Masa Ósea Estimada (Bone Mass)**")
        render_table_html(get_bone_mass_table_html(user_gender, latest_weight, latest_bone))

    with ref_col2:
        st.markdown("**Agua Corporal (Body Water %)**")
        render_table_html(get_body_water_table_html(user_gender, latest_water))

        st.markdown("**Grasa Visceral**")
        render_table_html(get_visceral_fat_table_html(latest_visceral))

        st.markdown("**Clasificación Física (Physique Rating)**")
        render_table_html(get_physique_table_html(latest_physique))

with tab_graficos:
    render_summary_panels(processed_df, latest_row, metric_columns)

    render_section_header(
        "Indicadores clave",
        "Panel principal con los últimos valores corporales y métricas complementarias del analizador.",
    )
    prev_row = processed_df.iloc[-2] if len(processed_df) >= 2 else None
    render_metric_cards(latest_row, metric_columns, PRIMARY_KPI_KEYS, columns_per_row=4, prev_row=prev_row)

    if any(metric_columns.get(key) for key in SECONDARY_KPI_KEYS):
        render_section_header(
            "Métricas complementarias",
            "Variables metabólicas y de composición adicionales detectadas en el archivo Tanita.",
        )
        render_metric_cards(latest_row, metric_columns, SECONDARY_KPI_KEYS, columns_per_row=4, prev_row=prev_row)

    render_section_header(
        "Evolución longitudinal",
        "Gráfica individual por métrica detectada en el archivo.",
    )
    _all_chart_keys = [k for k in PRIMARY_KPI_KEYS + SECONDARY_KPI_KEYS if metric_columns.get(k)]
    for _i in range(0, len(_all_chart_keys), 2):
        _chunk = _all_chart_keys[_i:_i + 2]
        _chart_cols = st.columns(len(_chunk))
        for _col, _key in zip(_chart_cols, _chunk):
            with _col:
                render_metric_chart(
                    processed_df,
                    metric_columns[_key],
                    METRIC_CONFIG[_key]["label"],
                    SOFT_COLORS[_key],
                )

    if segment_family and segment_columns:
        render_section_header("Análisis segmental", "Distribución de masa por segmentos corporales.")
        render_segmental_chart(processed_df, segment_family, segment_columns)

with tab_tabla:
    render_section_header(
        "Tabla procesada",
        "Vista tabular final para validación, exportación y revisión de todos los registros interpretados.",
    )
    _can_edit = _source_mode == "folder" and _csv_path is not None
    if _can_edit:
        st.caption(
            "Edición en línea habilitada · modifica celdas y pulsa **Guardar en CSV** para persistir los cambios."
        )
        _metric_edit_cols = [c for c in metric_columns.values() if c and c in processed_df.columns]
        _edit_view = processed_df[["Fecha medición"] + _metric_edit_cols].copy()
        _edit_view["Fecha medición"] = _edit_view["Fecha medición"].dt.strftime("%d/%m/%Y %H:%M")
        _edited = st.data_editor(
            _edit_view,
            use_container_width=True,
            hide_index=True,
            height=400,
            column_config={"Fecha medición": st.column_config.TextColumn("Fecha medición", disabled=True)},
        )
        if st.button("💾 Guardar cambios en CSV", type="primary"):
            _save_df = processed_df.drop(columns=["Fecha medición"]).copy()
            for _col in _metric_edit_cols:
                if _col in _edited.columns:
                    _save_df[_col] = _edited[_col].values
            _save_df.to_csv(_csv_path, index=False)
            process_csv.clear()
            st.success("Cambios guardados en CSV.")
            st.rerun()
    else:
        st.dataframe(build_display_dataframe(processed_df), use_container_width=True, hide_index=True, height=380)
