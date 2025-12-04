# ============================================
# Asistente Proactivo de Ahorro - Backend (MVP)
# ============================================
# Funcionalidades:
# - Ingesta de recibos y dataset histórico
# - Predicción de gasto por comercio (ARIMA)
# - Recomendación de alternativas cercanas y más baratas
# - Geofencing simple (radio y distancia)
# - Registro de feedback para reforzar recomendaciones

from flask import Flask, request, jsonify
from datetime import datetime
from collections import defaultdict
import pandas as pd
import numpy as np
from geopy.distance import geodesic
from sklearn.metrics.pairwise import cosine_similarity
from statsmodels.tsa.arima.model import ARIMA
import warnings
warnings.filterwarnings("ignore")

app = Flask(__name__)

# ---------------------------
# Memoria en RAM (demo)
# ---------------------------
# Recibos: lista de dicts {fecha, monto, comercio, lat, lon, categoria}
RECIBOS = []
# Modelos ARIMA por comercio
MODELOS_TS = {}
# Historico de precios por comercio (promedio y últimos)
PRECIO_PROMEDIO = defaultdict(lambda: 0.0)
# Catálogo de comercios con ubicación y precio (simulado)
COMERCIOS = {
    "Starbucks Centro": {"lat": 21.299, "lon": -100.516, "precio_prom": 120.0, "categoria": "Comida"},
    "Competencia X": {"lat": 21.301, "lon": -100.513, "precio_prom": 100.0, "categoria": "Comida"},
    "Café Local": {"lat": 21.303, "lon": -100.511, "precio_prom": 95.0, "categoria": "Comida"},
    "Farmacias del Ahorro": {"lat": 21.300, "lon": -100.518, "precio_prom": 150.0, "categoria": "Salud"},
    "Uber": {"lat": 21.298, "lon": -100.520, "precio_prom": 80.0, "categoria": "Transporte"}
}
# Feedback de aceptación de recomendaciones
FEEDBACK = []  # cada item: {timestamp, comercio_sugerido, aceptada: bool}

# ---------------------------
# Utilidades
# ---------------------------
def to_dt(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.strptime(s, "%d/%m/%Y %H:%M") if " " in s else datetime.strptime(s, "%d/%m/%Y")

def distancia_km(p1, p2):
    return geodesic((p1["lat"], p1["lon"]), (p2["lat"], p2["lon"])).km

def actualizar_promedios():
    if not RECIBOS:
        return
    df = pd.DataFrame(RECIBOS)
    for comercio, sub in df.groupby("comercio"):
        PRECIO_PROMEDIO[comercio] = float(sub["monto"].mean())

def construir_serie(comercio):
    df = pd.DataFrame([r for r in RECIBOS if r["comercio"] == comercio])
    if df.empty:
        return None
    df = df.sort_values("fecha")
    # Serie diaria: suma por día
    serie = df.groupby(df["fecha"].dt.date)["monto"].sum()
    serie = serie.asfreq("D", fill_value=0.0)
    return serie

def entrenar_arima(comercio):
    serie = construir_serie(comercio)
    if serie is None or len(serie) < 7:  # mínimo datos
        return None
    try:
        model = ARIMA(serie.values, order=(1,1,1)).fit()
        MODELOS_TS[comercio] = {"model": model, "last_index": serie.index[-1]}
        return MODELOS_TS[comercio]
    except Exception:
        return None

def predecir_gasto(comercio, pasos=7):
    m = MODELOS_TS.get(comercio)
    if m is None:
        m = entrenar_arima(comercio)
        if m is None:
            prom = PRECIO_PROMEDIO.get(comercio, 0.0)
            return {"comercio": comercio, "prediccion_diaria": [prom]*pasos, "confianza_aprox": 0.4}
    pred = m["model"].forecast(steps=pasos)
    # Estimar confianza simple basada en varianza residual
    resid = m["model"].resid
    var = np.var(resid) if len(resid) > 0 else 1.0
    confianza = float(np.clip(1.0 / (1.0 + var), 0.3, 0.9))
    return {"comercio": comercio, "prediccion_diaria": [float(x) for x in pred], "confianza_aprox": confianza}

def recomendar_alternativas(lat, lon, comercio_objetivo, radio_km=1.5, top_n=3):
    # Buscar comercios de la misma categoría cercanos con precio menor
    cat = COMERCIOS.get(comercio_objetivo, {}).get("categoria")
    origen = {"lat": lat, "lon": lon}
    candidatos = []
    for nombre, info in COMERCIOS.items():
        if nombre == comercio_objetivo:
            continue
        if cat and info["categoria"] != cat:
            continue
        d = distancia_km(origen, info)
        if d <= radio_km:
            ahorro = max(0.0, COMERCIOS[comercio_objetivo]["precio_prom"] - info["precio_prom"])
            candidatos.append({
                "comercio": nombre,
                "dist_km": round(d, 3),
                "precio_prom": info["precio_prom"],
                "ahorro_estimado": round(ahorro, 2)
            })
    candidatos = sorted(candidatos, key=lambda x: ( -x["ahorro_estimado"], x["dist_km"]))
    return candidatos[:top_n]

def tasa_aceptacion():
    if not FEEDBACK:
        return 0.0
    aceptadas = sum(1 for f in FEEDBACK if f["aceptada"])
    return round(100.0 * aceptadas / len(FEEDBACK), 2)

# ---------------------------
# Endpoints
# ---------------------------
@app.route("/ingresar_recibo", methods=["POST"])
def ingresar_recibo():
    data = request.json
    fecha = to_dt(data.get("fecha"))
    monto = float(data.get("monto"))
    comercio = data.get("comercio")
    categoria = data.get("categoria", "Desconocida")
    lat = float(data.get("lat", 21.300))
    lon = float(data.get("lon", -100.515))
    RECIBOS.append({
        "fecha": fecha, "monto": monto, "comercio": comercio,
        "lat": lat, "lon": lon, "categoria": categoria
    })
    actualizar_promedios()
    entrenar_arima(comercio)  # entrenamiento incremental por comercio
    return jsonify({"mensaje": "Recibo almacenado", "promedio_comercio": PRECIO_PROMEDIO[comercio]})

@app.route("/prediccion", methods=["GET"])
def prediccion():
    comercio = request.args.get("comercio")
    pasos = int(request.args.get("pasos", 7))
    if not comercio:
        return jsonify({"error": "Falta 'comercio'"}), 400
    pred = predecir_gasto(comercio, pasos=pasos)
    return jsonify(pred)

@app.route("/recomendar", methods=["GET"])
def recomendar():
    comercio_obj = request.args.get("comercio_objetivo")
    lat = float(request.args.get("lat", 21.300))
    lon = float(request.args.get("lon", -100.515))
    radio = float(request.args.get("radio_km", 1.5))
    if not comercio_obj or comercio_obj not in COMERCIOS:
        return jsonify({"error": "Comercio objetivo inválido"}), 400
    sugerencias = recomendar_alternativas(lat, lon, comercio_obj, radio_km=radio)
    # Mensaje estilo alerta proactiva
    alerta = [
        f"Ahorra hasta ${s['ahorro_estimado']} en {s['comercio']} (a {s['dist_km']} km, precio ~${s['precio_prom']})"
        for s in sugerencias if s["ahorro_estimado"] > 0
    ]
    return jsonify({"alternativas": sugerencias, "alertas": alerta})

@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.json
    comercio_sugerido = data.get("comercio_sugerido")
    aceptada = bool(data.get("aceptada", False))
    FEEDBACK.append({"timestamp": datetime.now().isoformat(), "comercio_sugerido": comercio_sugerido, "aceptada": aceptada})
    return jsonify({"mensaje": "Feedback registrado", "tasa_aceptacion_%": tasa_aceptacion()})

@app.route("/metricas", methods=["GET"])
def metricas():
    # Métricas simples del MVP
    df = pd.DataFrame(RECIBOS) if RECIBOS else pd.DataFrame(columns=["fecha","monto","comercio","categoria"])
    total_recibos = len(df)
    ahorro_estimado_mensual = 0.0
    # Estimar ahorro: diferencia media entre objetivo y alternativa top si existe (simulación)
    if total_recibos > 0:
        cats = df["categoria"].value_counts().to_dict()
    else:
        cats = {}
    return jsonify({
        "total_recibos": total_recibos,
        "tasa_aceptacion_%": tasa_aceptacion(),
        "categorias_conteo": cats,
        "modelos_entrenados": list(MODELOS_TS.keys())
    })

# ---------------------------
# Ejemplo de datos de prueba
# ---------------------------
def cargar_demo():
    base = [
        {"fecha": "01/12/2025 08:00", "monto": 120.0, "comercio": "Starbucks Centro", "lat": 21.299, "lon": -100.516, "categoria": "Comida"},
        {"fecha": "02/12/2025 08:05", "monto": 118.0, "comercio": "Starbucks Centro", "lat": 21.299, "lon": -100.516, "categoria": "Comida"},
        {"fecha": "03/12/2025 08:02", "monto": 121.0, "comercio": "Starbucks Centro", "lat": 21.299, "lon": -100.516, "categoria": "Comida"},
        {"fecha": "04/12/2025 08:01", "monto": 119.0, "comercio": "Starbucks Centro", "lat": 21.299, "lon": -100.516, "categoria": "Comida"},
        {"fecha": "01/12/2025 19:20", "monto": 80.0, "comercio": "Uber", "lat": 21.298, "lon": -100.520, "categoria": "Transporte"},
        {"fecha": "02/12/2025 19:30", "monto": 82.0, "comercio": "Uber", "lat": 21.298, "lon": -100.520, "categoria": "Transporte"},
    ]
    for r in base:
        RECIBOS.append({"fecha": to_dt(r["fecha"]), **{k:v for k,v in r.items() if k!="fecha"}})
    actualizar_promedios()
    for comercio in set([r["comercio"] for r in RECIBOS]):
        entrenar_arima(comercio)

# ---------------------------
# Arranque
# ---------------------------
if __name__ == "__main__":
    cargar_demo()
    app.run(debug=True)
