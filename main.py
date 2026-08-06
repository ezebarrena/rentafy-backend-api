from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/")
def get_al30():
    response = requests.get("https://data912.com/live/arg_bonds")

    bonds = response.json()

    for bond in bonds:
        if bond["symbol"] == "AL30":
            return {
                "symbol": bond["symbol"],
                "price": bond["c"]
            }

    return {"error": "Bono no encontrado"}